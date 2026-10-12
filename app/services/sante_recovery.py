"""Residual training exposure used as a precaution in the morning index.

This is an explicit product model, not measured muscle fatigue. Ordinary
sessions accumulate and decay; HR load and sport-specific volume retain
separate personal references. No ACWR injury threshold, fitted recovery time,
future session or inferred rest day is used. Missing intensity stays estimated.
See docs/recovery-cumulative.md for assumptions, evidence and validation.
"""
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from app.services import nights as nt, sante_training as st

VERSION = "cumulative-1"
# (H) All constants below are product heuristics, not clinical cutoffs.
REFERENCE_DAYS, REFERENCE_GAP = 42, 7
WARMUP_DAYS, MIN_REFERENCE_DAYS = 14, 28
MIN_SESSIONS, MIN_ACTIVE_WEEKS = 8, 4
MEMORY_DAYS = 28
MAX_REDUCTION = 40.0  # training alone never creates the red (<40) state
CHANNELS = {"cardio": ("Effort global", 2.0), "run": ("Course à pied", 3.0),
            "strength": ("Musculation", 3.0), "volume": ("Durée totale", 2.0)}
STRENGTH = frozenset({"WeightTraining", "Crossfit"})
MIN_HR_COVERAGE = 0.8


@dataclass(frozen=True)
class Exposure:
    id: int
    local_end: datetime
    utc_end: datetime
    doses: dict[str, float]
    estimated: bool


def cutoff(nights: dict, day: date) -> tuple[datetime, bool]:
    """Measured wake when available; midnight otherwise, never an invented wake.

    Known night offsets compare UTC instants (including sessions from another
    zone). Legacy records without offsets use local clocks conservatively.
    Sessions after the selected wake only affect a subsequent morning.
    """
    n = nights.get(day)
    end = n.end if n and n.end and n.end.date() == day else datetime.combine(day, time())
    utc = bool(n and n.tz is not None and -840 <= n.tz <= 840)
    if utc:
        end = (end - timedelta(minutes=n.tz)).replace(tzinfo=timezone.utc)
    return end, utc


def _end(s: st.Session, utc: bool) -> datetime:
    return (st._utc(s.start) + timedelta(minutes=st.effort_minutes(s))) if utc else st.local_end(s)


def exposures(sessions: list, nights: dict, day: date) -> list[Exposure]:
    """Compare completed activities using the same prior HR reference.

    The shared session loader already removes copies and false starts. A
    repeated ID is also counted once. Unreadable HR keeps its duration-based
    estimate and is labelled as such; strength is not judged by its low HR.
    """
    at, utc = cutoff(nights, day)
    known = sorted({s.id: s for s in sessions
                    if math.isfinite(s.minutes) and s.minutes > 0 and _end(s, utc) <= at}.values(),
                   key=lambda s: (_end(s, utc), s.id))
    start = day - timedelta(days=REFERENCE_DAYS + REFERENCE_GAP + WARMUP_DAYS + MEMORY_DAYS)
    reference_day = day - timedelta(days=REFERENCE_GAP + 1)
    reference = [s for s in known if st.local_end(s).date() <= reference_day]
    rest = nt.rest_hr(nights, reference_day)
    loads = st.loads(known, reference_day, rest, st.hr_max(reference, reference_day))
    out = []
    for s in known:
        end = st.local_end(s)
        if end.date() < start:
            continue
        load, measured = loads[s.id]
        doses = {"volume": s.minutes}
        if s.sport not in st.BY_TIME:
            doses["cardio"] = load
        if s.sport in st.RUN_TYPES:
            doses["run"] = s.minutes
        elif s.sport in STRENGTH:
            doses["strength"] = s.minutes
        if all(math.isfinite(v) and v >= 0 for v in doses.values()):
            out.append(Exposure(s.id, end, _end(s, True), doses, not measured))
    return out


def residual(rows: list[Exposure], nights: dict, day: date, key: str, tau: float,
             measured_only: bool = False) -> tuple[float, float]:
    """Sum decaying contributions, plus the share based on estimated intensity.

    Tapering the negligible tail removes a hard cliff at the 28-day cutoff.
    tau is a smoothing constant, not a promised recovery duration.
    """
    at, utc = cutoff(nights, day)
    total = estimated = 0.0
    for r in rows:
        if measured_only and r.estimated:
            continue
        age = (at - (r.utc_end if utc else r.local_end)).total_seconds() / 86400
        if 0 <= age < MEMORY_DAYS:
            weight = math.exp(-age / tau) * min(1.0, MEMORY_DAYS - age)
            value = r.doses.get(key, 0.0) * weight
            total += value
            if r.estimated:
                estimated += value
    return total, estimated / total if total else 0.0


def assessment(sessions: list, nights: dict, day: date) -> dict:
    rows = exposures(sessions, nights, day)
    out = {"version": VERSION, "cap": None, "channels": [], "provisional": False,
           "recorded": len(rows), "word": "Aucune activité reçue", "tone": "none", "intensity_used": False,
           "intensity_partial": False}
    if not rows:
        return out
    lo, hi = day - timedelta(days=REFERENCE_GAP + REFERENCE_DAYS), day - timedelta(days=REFERENCE_GAP)
    for key, (label, tau) in CHANNELS.items():
        channel_rows = [r for r in rows if key in r.doses]
        if not channel_rows:
            continue
        first = min(r.local_end.date() for r in channel_rows)
        ref_days = [day - timedelta(days=k) for k in range(REFERENCE_GAP + REFERENCE_DAYS, REFERENCE_GAP, -1)
                    if day - timedelta(days=k) >= first + timedelta(days=WARMUP_DAYS)]
        current, estimated = residual(rows, nights, day, key, tau, measured_only=key == "cardio")
        if key == "cardio":
            # Coverage is a share of recorded time, not of an imputed HR dose.
            _, estimated = residual(channel_rows, nights, day, "volume", tau)
        previous, _ = residual(rows, nights, day - timedelta(days=1), key, tau, measured_only=key == "cardio")
        reference = [r for r in channel_rows if lo <= r.local_end.date() < hi]
        weeks = {r.local_end.date() - timedelta(days=r.local_end.weekday()) for r in reference}
        ready = (len(ref_days) >= MIN_REFERENCE_DAYS and len(reference) >= MIN_SESSIONS
                 and len(weeks) >= MIN_ACTIVE_WEEKS)
        reference_minutes = sum(r.doses["volume"] for r in reference)
        reference_estimated = (sum(r.doses["volume"] for r in reference if r.estimated) / reference_minutes
                               if reference_minutes else 1.0)
        # An incomplete current recording retains the observed lower bound.
        # Adding an unmeasured workout must never erase an existing precaution.
        # A mostly unmeasured baseline cannot support an HR comparison at all.
        intensity_usable = key != "cardio" or reference_estimated <= 1-MIN_HR_COVERAGE
        channel = {"key": key, "label": label, "current": current, "previous": previous,
                   "tau": tau, "reference_days": len(ref_days), "reference_sessions": len(reference),
                   "estimated_share": estimated if key == "cardio" else 1.0,
                   "reference_estimated_share": reference_estimated, "intensity_usable": intensity_usable,
                   "ready": ready, "cap": None, "usual": None}
        if ready:
            values = [residual(rows, nights, d, key, tau)[0] for d in ref_days]
            usual = statistics.quantiles(values, n=4, method="inclusive")[2]
            # A typical session guards against tiny denominators in sparse histories.
            scale = max(usual, statistics.median(r.doses[key] for r in reference))
            excess = max(0.0, current - usual) / scale if scale > 0 else 0.0
            reduction = MAX_REDUCTION if key == "cardio" else 20.0
            cap = 100 - reduction * (-math.expm1(-excess))
            # Volume-only and mostly imputed load can apply a moderate precaution,
            # but cannot alone classify the athlete as poorly recovered.
            channel.update(usual=usual, cap=cap if cap < 99.5 and intensity_usable else None)
        out["channels"].append(channel)
    available = [c for c in out["channels"] if c["cap"] is not None]
    out["provisional"] = any(not c["ready"] for c in out["channels"])
    out["intensity_used"] = any(c["key"] == "cardio" and c["ready"] and c["intensity_usable"]
                                and c["current"] > 0
                                for c in out["channels"])
    out["intensity_partial"] = any(c["key"] == "cardio" and c["estimated_share"] > 1-MIN_HR_COVERAGE
                                   for c in out["channels"])
    if available:
        # These are correlated exposures, not additive independent penalties.
        cap = min(c["cap"] for c in available)
        declining = all(c["current"] < c["previous"] for c in available)
        out.update(cap=cap, word="En baisse" if declining else "Plus que d’habitude",
                   tone="warn" if cap < 90 else "accent")
    else:
        out["word"] = "Repères en cours" if out["provisional"] else "Dans tes habitudes"
    return out
