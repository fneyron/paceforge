"""Health data: one value per day and metric, PaceForge's own nightly values.

The athlete's COROS watch (app.services.coros, source "COROS") and Garmin watch
(app.services.garmin, source "Garmin") give, per night (named by its wake-up
day), values PaceForge computes from their raw data and writes once per day
(store_daily → HealthMetric):
- sleep      : minutes asleep in the MAIN sleep episode (naps never count in it);
               {main_start, main_end: local ISO "YYYY-MM-DDTHH:MM", period (min,
               awake included), tz (min east of UTC, when known), timeline (True
               when real stage intervals were stored: Garmin sleepLevels),
               bedtime, wake ("HH:MM", what older readers use), daily (COROS's
               own « Daily Sleep (incl. naps) », a cross-check only)}
- nap        : minutes asleep in the day's naps (a nap belongs to the day it
               ends); {period, windows: [["2026-10-07T06:42", "2026-10-07T09:07"]],
               legacy (COROS « includes legacy reported durations »)}. Rows
               written before 2026-10 keep « HH:MM » windows: read them with
               nap_windows().
- hrv        : PaceForge's nightly HRV, exp(mean ln RMSSD) of the raw readings
               inside the main window; {n, tz, method: "ln_mean_main"}. Rows
               without that method are the watch's own average (written before
               2026-10): never read as PaceForge's.
- hr_night   : nightly heart rate; {min, max, method, nap_day}. Garmin: mean of
               the sleepHeartRate readings inside the main window ("points");
               COROS: its « Sleep HR » line, which sits in the summary of the
               main sleep ("coros_sleep_summary", see docs/sante-v3-data-notes.md).
- resp_night : overnight respiration (Garmin only); {method}.
- hr_day     : average heart rate of the day; {min, max} (a « watch worn » marker)
- steps      : steps of the day; {kcal, exercise (min)} (same)
- feel       : the morning check-in (source "PaceForge"): 1 mieux, 2 comme
               d'habitude, 3 moins bien; {why: [legs, fatigue, sick, stress],
               alcohol: bool} (older rows: {legs_heavy}; read them with feel_of()).

Brand values (load, recovery, stress, fitness, hrv_norm, body_battery,
sleep_score, vo2max, the watch's daytime resting HR "rhr") are no longer read
nor written; rows already stored stay (Réglages still counts them).

Raw samples (HealthSample, local wall-clock times, kept 400 days) remain for
sources that send only samples: aggregate_days() turns them into daily values.
The watches' own sleep samples are Garmin's real stage intervals, kept as the
hypnogram's timeline only: they are never aggregated (the watch's main window
is the night).

Older rows may come from the Apple Health import PaceForge had before.
"""
import logging
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric, HealthSample

logger = logging.getLogger(__name__)

METRICS = ("hrv", "rhr", "sleep", "weight", "vo2max")
METRIC_LABELS = {
    "hrv": "VFC",
    "rhr": "FC au repos",
    "sleep": "Sommeil",
    "weight": "Poids",
    "vo2max": "VO2 max",
}
# what a watch writes per day now (store_daily)
NIGHT_METRICS = ("sleep", "nap", "hrv", "hr_night", "resp_night")
# brand values: no longer read nor written; stored rows stay until a later purge
BRAND_METRICS = ("load", "recovery", "stress", "fitness", "hrv_norm", "body_battery", "sleep_score", "vo2max",
                 "rhr")
DAILY_METRICS = NIGHT_METRICS + ("hr_day", "steps", "feel") + BRAND_METRICS
# what Réglages lists, in this order (brand rows already stored included: « what is synced »)
DAILY_LABELS = {"sleep": "Sommeil", "nap": "Siestes", "hrv": "VFC", "hr_night": "FC de nuit",
                "resp_night": "Respiration", "hr_day": "FC du jour", "steps": "Pas", "feel": "Ressenti",
                "load": "Charge", "recovery": "Récupération", "stress": "Stress", "fitness": "Niveau",
                "hrv_norm": "Normale VFC", "body_battery": "Body Battery", "sleep_score": "Score de sommeil",
                "vo2max": "VO2 max", "rhr": "FC au repos"}
WATCH_SOURCES = ("COROS", "Garmin")
HRV_METHOD = "ln_mean_main"


@dataclass
class Sample:
    metric: str
    kind: str  # sleep stage, "" for quantities
    start: datetime  # local wall clock, naive
    end: datetime
    value: float  # sleep: minutes
    source: str = ""


_RANGES = {"hrv": (5, 300), "rhr": (25, 140), "weight": (25, 300), "vo2max": (10, 95)}
ASLEEP_KINDS = ("asleep", "core", "deep", "rem")


# ── local times as stored ───────────────────────────────────────────────────

def iso_min(t: datetime) -> str:
    """datetime(2026, 10, 7, 6, 42) → '2026-10-07T06:42' (local, naive)."""
    return t.strftime("%Y-%m-%dT%H:%M")


def parse_local(s) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s)).replace(tzinfo=None, second=0, microsecond=0)
    except (TypeError, ValueError):
        return None


def _hhmm(s) -> time | None:
    try:
        h, m = (int(x) for x in str(s).split(":")[:2])
        return time(h, m)
    except (TypeError, ValueError):
        return None


def nap_windows(day: date, details: dict | None) -> list[tuple[datetime, datetime]]:
    """The local (start, end) of a `nap` row's windows. New rows hold ISO
    datetimes; older ones « HH:MM » pairs, read as ending on `day` and starting
    the day before when the start is later than the end."""
    out = []
    for w in (details or {}).get("windows") or []:
        if not isinstance(w, (list, tuple)) or len(w) != 2:
            continue
        a, b = parse_local(w[0]), parse_local(w[1])
        if a is None or b is None:
            ta, tb = _hhmm(w[0]), _hhmm(w[1])
            if ta is None or tb is None:
                continue
            b = datetime.combine(day, tb)
            a = datetime.combine(day - timedelta(days=1) if ta > tb else day, ta)
        if b > a:
            out.append((a, b))
    return out


def main_window(day: date, details: dict | None) -> tuple[datetime, datetime] | None:
    """The local (start, end) of a `sleep` row's main window: its ISO bounds,
    else (older rows) « HH:MM » bedtime and wake, the wake on `day`."""
    det = details or {}
    a, b = parse_local(det.get("main_start")), parse_local(det.get("main_end"))
    if a and b and b > a:
        return a, b
    ta, tb = _hhmm(det.get("bedtime")), _hhmm(det.get("wake"))
    if ta is None or tb is None or det.get("from_in_bed"):
        return None
    b = datetime.combine(day, tb)
    a = datetime.combine(day - timedelta(days=1) if ta > tb else day, ta)
    return (a, b) if b > a else None


# ── naps: what counts as one (H) ────────────────────────────────────────────

NAP_MAX = timedelta(hours=6)


def nap_guard(day: date, windows, main: tuple[datetime, datetime] | None = None) -> list[tuple[datetime, datetime]]:
    """The windows that can be a nap of `day` (H, PaceForge's own guards):
    - it lasts more than 0 and at most 6 h;
    - it ends between 12:00 the day before and 23:59 that day (COROS keeps
      legacy naps dated 1982);
    - it does not overlap the main window (the night is never counted twice)."""
    lo, hi = datetime.combine(day - timedelta(days=1), time(12, 0)), datetime.combine(day, time(23, 59, 59))
    out = []
    for a, b in windows or []:
        if not (timedelta(0) < b - a <= NAP_MAX and lo <= b <= hi):
            continue
        if main and a < main[1] and b > main[0]:
            continue
        out.append((a, b))
    return sorted(out)


def nap_daily(day: date, asleep: float | None, period: float | None, windows,
              main: tuple[datetime, datetime] | None = None, legacy: bool = False) -> "Daily | None":
    """One `nap` value for `day` from what a watch says (minutes asleep, period,
    windows), the guards applied. When some windows go, the minutes asleep
    shrink in proportion; when they all go, so does the nap (its minutes can't
    be trusted). Without any window (Garmin's napTimeSeconds alone) the minutes
    are kept as they are."""
    if not isinstance(asleep, (int, float)) or not 1 <= asleep <= 600:
        return None
    windows = list(windows or [])
    kept = nap_guard(day, windows, main)
    if windows and not kept:
        return None
    span = lambda ws: sum((b - a).total_seconds() for a, b in ws) / 60  # noqa: E731
    if windows and len(kept) < len(windows):
        asleep = round(asleep * span(kept) / span(windows))
        period = round(span(kept))
        if asleep < 1:
            return None
    period = period if isinstance(period, (int, float)) and asleep <= period <= 720 else (
        round(span(kept)) if kept else None)
    details = {"period": period, "windows": [[iso_min(a), iso_min(b)] for a, b in kept]}
    if legacy:
        details["legacy"] = True
    return Daily("nap", day, round(asleep), details)


# ── the check-in ────────────────────────────────────────────────────────────

FEEL_WHY = ("legs", "fatigue", "sick", "stress")


def feel_of(value: float | None, details: dict | None) -> dict | None:
    """{value: 1 mieux | 2 comme d'habitude | 3 moins bien, why: [...], alcohol}
    from a `feel` row, older rows ({legs_heavy}) included."""
    if value is None:
        return None
    det = details or {}
    why = [w for w in det.get("why") or [] if w in FEEL_WHY]
    if det.get("legs_heavy") and "legs" not in why:
        why.append("legs")
    return {"value": int(value), "why": why, "alcohol": bool(det.get("alcohol"))}


# ── storage + daily aggregation ─────────────────────────────────────────────

RAW_RETENTION_DAYS = 400
_INSERT_CHUNK = 2000


def _affected_dates(s: Sample) -> list[date]:
    d = s.start.date()
    if s.source in WATCH_SOURCES and s.metric in ("sleep", "hrv"):
        return []  # a watch's night comes whole (store_daily); its intervals are a timeline only
    if s.metric == "hrv":
        return [d, d + timedelta(days=1)] if s.start.hour >= 22 else [d]
    if s.metric == "sleep":
        e = s.end.date()
        return [e, e + timedelta(days=1)]  # the episode it starts may end the next day
    return [d]


async def store_samples(db: AsyncSession, user_id: int, samples: list[Sample]) -> dict:
    """Idempotent upsert of raw samples (key: metric, start, source, stage), then
    the touched days are aggregated again."""
    keyed: dict[tuple, Sample] = {}
    for s in samples:
        keyed[(s.metric, s.start, s.source, s.kind)] = s
    inserted = updated = 0
    affected: dict[str, set[date]] = defaultdict(set)
    by_metric: dict[str, list[Sample]] = defaultdict(list)
    for s in keyed.values():
        by_metric[s.metric].append(s)
        affected[s.metric].update(_affected_dates(s))

    for metric, items in by_metric.items():
        lo = min(s.start for s in items)
        hi = max(s.start for s in items)
        rows = await db.execute(
            select(HealthSample.id, HealthSample.start_at, HealthSample.source, HealthSample.kind,
                   HealthSample.value, HealthSample.end_at)
            .where(HealthSample.user_id == user_id, HealthSample.metric == metric,
                   HealthSample.start_at >= lo, HealthSample.start_at <= hi)
        )
        existing = {(r.start_at, r.source, r.kind): r for r in rows.all()}
        to_insert, to_update = [], []
        for s in items:
            row = existing.get((s.start, s.source, s.kind))
            if row is None:
                to_insert.append({"user_id": user_id, "metric": metric, "kind": s.kind,
                                  "start_at": s.start, "end_at": s.end, "value": s.value,
                                  "source": s.source})
            elif abs(row.value - s.value) > 1e-6 or row.end_at != s.end:
                to_update.append({"id": row.id, "value": s.value, "end_at": s.end})
        for i in range(0, len(to_insert), _INSERT_CHUNK):
            await db.execute(insert(HealthSample), to_insert[i:i + _INSERT_CHUNK])
        if to_update:
            await db.execute(update(HealthSample), to_update)
        inserted += len(to_insert)
        updated += len(to_update)

    days = await reaggregate(db, user_id, affected)
    cutoff = datetime.now() - timedelta(days=RAW_RETENTION_DAYS)
    await db.execute(delete(HealthSample).where(
        HealthSample.user_id == user_id, HealthSample.start_at < cutoff))
    await db.flush()
    return {
        "received": len(samples),
        "inserted": inserted,
        "updated": updated,
        "unchanged": len(keyed) - inserted - updated,
        "by_metric": {m: len(v) for m, v in by_metric.items()},
        "days": days,
    }


@dataclass
class Daily:
    metric: str  # one of DAILY_METRICS
    day: date
    value: float
    details: dict | None = None


async def store_daily(db: AsyncSession, user_id: int, rows: list[Daily], source: str) -> dict:
    """Idempotent upsert of values that are already one per day (key: metric,
    day). Returns {inserted, updated, by_metric}."""
    keyed = {(r.metric, r.day): r for r in rows}
    inserted = updated = 0
    by_metric: dict[str, int] = defaultdict(int)
    for metric in sorted({m for m, _ in keyed}):
        items = [r for (m, _), r in keyed.items() if m == metric]
        by_metric[metric] = len(items)
        existing = {m.date: m for m in (await db.execute(select(HealthMetric).where(
            HealthMetric.user_id == user_id, HealthMetric.metric == metric,
            HealthMetric.date >= min(r.day for r in items),
            HealthMetric.date <= max(r.day for r in items)))).scalars()}
        for r in items:
            row = existing.get(r.day)
            if row is None:
                db.add(HealthMetric(user_id=user_id, date=r.day, metric=metric, value=r.value,
                                    source=source, details=r.details, n_samples=1))
                inserted += 1
            elif abs(row.value - r.value) > 1e-6 or row.details != r.details or row.source != source:
                row.value, row.details, row.source = r.value, r.details, source
                updated += 1
    await db.flush()
    return {"inserted": inserted, "updated": updated, "by_metric": dict(by_metric)}


async def nights_upgraded(db: AsyncSession, user_id: int, source: str) -> bool:
    """False while a watch's nights exist only in the format written before
    2026-10 (no main window, the watch's HRV average, no nightly HR): its sync
    then reads the history again once, so the band starts from PaceForge's own
    values."""
    base = (HealthMetric.user_id == user_id, HealthMetric.source == source, HealthMetric.metric == "sleep")
    if not (await db.execute(select(func.count(HealthMetric.id)).where(*base))).scalar():
        return True
    new = (await db.execute(select(func.count(HealthMetric.id)).where(
        *base, HealthMetric.details["main_start"].as_string().is_not(None)))).scalar()
    return bool(new)


async def reaggregate(db: AsyncSession, user_id: int, affected: dict[str, set[date]]) -> dict[str, int]:
    """Recompute the daily value of every (metric, day) touched. Returns days written per metric."""
    written: dict[str, int] = {}
    for metric, dates in affected.items():
        if not dates:
            continue
        lo = datetime.combine(min(dates) - timedelta(days=1), time(0, 0))
        hi = datetime.combine(max(dates) + timedelta(days=1), time(0, 0))
        rows = (await db.execute(
            select(HealthSample).where(
                HealthSample.user_id == user_id, HealthSample.metric == metric,
                HealthSample.start_at >= lo, HealthSample.start_at < hi)
        )).scalars().all()
        daily = aggregate_days(metric, rows, dates)
        dates_list = sorted(dates)
        for i in range(0, len(dates_list), 500):
            await db.execute(delete(HealthMetric).where(
                HealthMetric.user_id == user_id, HealthMetric.metric == metric,
                HealthMetric.date.in_(dates_list[i:i + 500])))
        new_rows = [{"user_id": user_id, "date": d, "metric": metric, "value": v["value"],
                     "source": v.get("source"), "details": v.get("details"), "n_samples": v["n"]}
                    for d, v in sorted(daily.items())]
        for i in range(0, len(new_rows), _INSERT_CHUNK):
            await db.execute(insert(HealthMetric), new_rows[i:i + _INSERT_CHUNK])
        written[metric] = len(new_rows)
    return written


def aggregate_days(metric: str, samples, dates) -> dict[date, dict]:
    """{day: {value, n, source?, details?}} for each requested day that has data.
    `samples` carry .start_at/.end_at/.value/.source/.kind (rows or look-alikes).
    A watch's sleep and HRV samples are left out: its nights come whole."""
    if metric in ("sleep", "hrv"):
        samples = [s for s in samples if s.source not in WATCH_SOURCES]
    out: dict[date, dict] = {}
    for d in dates:
        if metric == "hrv":
            v = _hrv_day(samples, d)
        elif metric == "sleep":
            v = _sleep_night(samples, d)
        else:
            v = _last_of_day(samples, d)
        if v:
            out[d] = v
    return out


def _dedupe_by_minute(samples) -> list:
    """The same reading pushed by the Shortcut and found again in the export
    (different source names) counts once."""
    seen: dict[datetime, object] = {}
    for s in samples:
        seen.setdefault(s.start_at.replace(second=0, microsecond=0), s)
    return list(seen.values())


# HRV is not one number across devices: Apple Health stores SDNN, COROS and
# Garmin their overnight RMSSD — for the same night, often twice as high. A day
# therefore takes one source (a watch when it has a value, stored with its name;
# Apple days keep no source), and the fitness signal compares only days on the
# scale of the latest one (hrv_same_scale): never a COROS week against an
# Apple baseline.
RMSSD_SOURCES = ("COROS", "Garmin")


def _hrv_day(samples, d: date) -> dict | None:
    lo = datetime.combine(d - timedelta(days=1), time(22, 0))
    hi = datetime.combine(d, time(10, 0))
    night = [s for s in samples if lo <= s.start_at < hi]
    pool = night or [s for s in samples if s.start_at.date() == d]
    rmssd = [s for s in pool if s.source in RMSSD_SOURCES]
    pool = _dedupe_by_minute(rmssd or pool)
    if not pool:
        return None
    out = {"value": round(statistics.fmean(s.value for s in pool), 1), "n": len(pool),
           "details": {"window": "nuit" if night else "journée"}}
    if rmssd:
        out["source"] = rmssd[0].source
    return out


def hrv_same_scale(values: dict[date, float], sources: dict[date, str | None]) -> dict[date, float]:
    """The HRV days measured on the same scale as the latest one (see RMSSD_SOURCES)."""
    if not values:
        return values
    rmssd = lambda d: sources.get(d) in RMSSD_SOURCES  # noqa: E731
    latest = rmssd(max(values))
    return {d: v for d, v in values.items() if rmssd(d) == latest}


def _last_of_day(samples, d: date) -> dict | None:
    day = [s for s in samples if s.start_at.date() == d]
    if not day:
        return None
    last = max(day, key=lambda s: s.start_at)
    return {"value": round(last.value, 2), "n": len(day), "source": last.source or None}


def _union_minutes(intervals: list[tuple[datetime, datetime]]) -> float:
    total, cur_s, cur_e = 0.0, None, None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
        elif e > cur_e:
            cur_e = e
    if cur_e is not None:
        total += (cur_e - cur_s).total_seconds()
    return total / 60


EPISODE_GAP = timedelta(minutes=60)  # (H) a wake of an hour or more ends a sleep episode
MAIN_ENDS_BY = time(14, 0)  # (H) the main episode of day D ends before 14:00 that day


def sleep_episodes(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Intervals merged into episodes: a gap of 60 min or more starts a new one (H)."""
    out: list[list[datetime]] = []
    for a, b in sorted(intervals):
        if out and a - out[-1][1] < EPISODE_GAP:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def main_episode(intervals, d: date) -> tuple[datetime, datetime] | None:
    """The night of day D for a source that sends only samples: the longest
    episode ending on D before 14:00 (H). The others are naps, never merged
    into the night (an hour rule would merge a morning nap into it)."""
    cands = [(a, b) for a, b in sleep_episodes(intervals)
             if b.date() == d and b.time() <= MAIN_ENDS_BY]
    return max(cands, key=lambda ab: ab[1] - ab[0], default=None)


def _sleep_night(samples, d: date) -> dict | None:
    by_source: dict[str, list] = defaultdict(list)
    for s in samples:
        by_source[s.source].append(s)
    nights: dict[str, list] = {}
    for source, items in by_source.items():
        main = main_episode([(s.start_at, s.end_at) for s in items], d)
        if main:
            nights[source] = [s for s in items if s.start_at < main[1] and s.end_at > main[0]]
    if not nights:
        return None

    def score(items):
        staged = any(s.kind in ("core", "deep", "rem") for s in items)
        asleep = _union_minutes([(s.start_at, s.end_at) for s in items if s.kind in ASLEEP_KINDS])
        in_bed = _union_minutes([(s.start_at, s.end_at) for s in items if s.kind == "in_bed"])
        return (staged, asleep, in_bed)

    source, items = max(nights.items(), key=lambda kv: score(kv[1]))
    per_kind = {k: round(_union_minutes([(s.start_at, s.end_at) for s in items if s.kind == k]))
                for k in ("core", "deep", "rem", "awake", "in_bed")}
    asleep_ivs = [(s.start_at, s.end_at) for s in items if s.kind in ASLEEP_KINDS]
    asleep = _union_minutes(asleep_ivs)
    details = {k: v for k, v in per_kind.items() if v}
    if asleep > 0:
        a, b = min(a for a, _ in asleep_ivs), max(b for _, b in asleep_ivs)
        details.update(bedtime=a.strftime("%H:%M"), wake=b.strftime("%H:%M"), main_start=iso_min(a),
                       main_end=iso_min(b))
        value = asleep
    elif per_kind["in_bed"]:
        value = per_kind["in_bed"]  # phone only: time in bed is all there is
        details["from_in_bed"] = True
    else:
        return None
    return {"value": round(value), "n": len(items), "source": source or None, "details": details}


async def drop_stale_intervals(db: AsyncSession, user_id: int, source: str,
                               nights: dict[date, tuple[datetime, datetime]], samples: list[Sample]) -> int:
    """A night's stage intervals move when the watch revises it: the intervals
    of a re-read night (around its main window) that are no longer produced go,
    or old and new ones would add up. Returns how many went."""
    keep = {(s.start, s.kind) for s in samples if s.metric == "sleep"}
    n = 0
    for _, (a, b) in nights.items():
        rows = await db.execute(select(HealthSample.id, HealthSample.start_at, HealthSample.kind).where(
            HealthSample.user_id == user_id, HealthSample.metric == "sleep", HealthSample.source == source,
            HealthSample.start_at >= a - timedelta(hours=6), HealthSample.start_at < b + timedelta(hours=2)))
        stale = [r.id for r in rows.all() if (r.start_at, r.kind) not in keep]
        if stale:
            await db.execute(delete(HealthSample).where(HealthSample.id.in_(stale)))
            n += len(stale)
    return n


# ── fitness signal ──────────────────────────────────────────────────────────

RECENT_DAYS = 7
BASELINE_DAYS = 60
MIN_BASELINE_DAYS = 14
MIN_RECENT_DAYS = 3
SHORT_SLEEP_MIN = 6 * 60

FORM_LABELS = {
    "fresh": "Bien récupéré",
    "ok": "Forme normale",
    "watch": "À surveiller",
    "fatigue": "Fatigue probable",
    "unknown": "Pas encore assez de données",
}


def compute_form(days: dict[str, dict[date, float]], today: date) -> dict:
    """Last 7 days against the 60 days before them (the baseline).

    Rule (the classic HRV-guided-training reading: Plews, Buchheit, Kiviniemi):
    - strong signals, each enough for "fatigue probable":
        7-day HRV mean below baseline mean − 1 SD; 7-day resting HR ≥ baseline + 5 bpm
    - mild signals: HRV below mean − ½ SD; resting HR ≥ baseline + 3 bpm;
        sleep below 6 h a night on average, or 45 min under its own baseline
    - HRV is read on ln(RMSSD), its SD floored at 0.05 (≈ 5 %); the band and
      the means are given back in ms;
    - fatigue = one strong or two mild; watch = one mild; fresh = none and HRV
      above mean + ½ SD; ok = none. Unknown until HRV or resting HR has
      14 baseline days and 3 recent days.
    """
    recent_lo = today - timedelta(days=RECENT_DAYS - 1)
    base_hi = recent_lo - timedelta(days=1)
    base_lo = base_hi - timedelta(days=BASELINE_DAYS - 1)

    def split(metric):
        series = days.get(metric, {})
        recent = [v for d, v in series.items() if recent_lo <= d <= today]
        base = [v for d, v in series.items() if base_lo <= d <= base_hi]
        return recent, base

    out: dict = {
        "hrv_7d": None, "hrv_baseline": None, "hrv_sd": None, "hrv_z": None, "hrv_band": None, "hrv_delta_pct": None,
        "rhr_7d": None, "rhr_baseline": None, "rhr_sd": None, "rhr_delta_bpm": None,
        "sleep_avg_min": None, "sleep_baseline_min": None, "sleep_delta_min": None,
    }
    strong, mild, reasons = 0, 0, []
    usable = False
    hrv_high = False

    # HRV on ln(RMSSD): it is skewed, and its noise grows with its level (Plews 2013)
    recent, base = split("hrv")
    recent, base = [math.log(v) for v in recent if v > 0], [math.log(v) for v in base if v > 0]
    if recent:
        out["hrv_7d"] = round(math.exp(statistics.fmean(recent)), 1)
    if len(recent) >= MIN_RECENT_DAYS and len(base) >= MIN_BASELINE_DAYS:
        usable = True
        mean = statistics.fmean(base)
        # a very steady baseline must not turn a 2 % dip into a signal (day-to-day
        # overnight HRV normally varies 10–15 %)
        sd = max(statistics.stdev(base), 0.05)
        r = statistics.fmean(recent)
        z = (r - mean) / sd
        out.update(hrv_baseline=round(math.exp(mean), 1), hrv_sd=round(sd, 3), hrv_z=round(z, 2),
                   hrv_band=(round(math.exp(mean - 0.5 * sd), 1), round(math.exp(mean + 0.5 * sd), 1)),
                   hrv_delta_pct=round((math.exp(r - mean) - 1) * 100, 1))
        if z < -1:
            strong += 1
            reasons.append("VFC nettement sous ta normale")
        elif z < -0.5:
            mild += 1
            reasons.append("VFC un peu sous ta normale")
        elif z > 0.5:
            hrv_high = True

    recent, base = split("rhr")
    if recent:
        out["rhr_7d"] = round(statistics.fmean(recent), 1)
    if len(recent) >= MIN_RECENT_DAYS and len(base) >= MIN_BASELINE_DAYS:
        usable = True
        mean = statistics.fmean(base)
        delta = statistics.fmean(recent) - mean
        # SD floored at 1.5 bpm: a very steady baseline must not make 2 bpm "ill"
        out.update(rhr_baseline=round(mean, 1), rhr_sd=round(max(statistics.stdev(base), 1.5), 1),
                   rhr_delta_bpm=round(delta, 1))
        if delta >= 5:
            strong += 1
            reasons.append(f"FC au repos +{delta:.0f} bpm")
        elif delta >= 3:
            mild += 1
            reasons.append(f"FC au repos +{delta:.0f} bpm")

    recent, base = split("sleep")
    if recent:  # shown from one night on, like HRV and resting HR
        out["sleep_avg_min"] = round(statistics.fmean(recent))
    if len(recent) >= MIN_RECENT_DAYS:  # …but a signal only from three
        avg = statistics.fmean(recent)
        base_mean = statistics.fmean(base) if len(base) >= MIN_BASELINE_DAYS else None
        if base_mean is not None:
            out.update(sleep_baseline_min=round(base_mean), sleep_delta_min=round(avg - base_mean))
        if avg < SHORT_SLEEP_MIN or (base_mean is not None and avg < base_mean - 45):
            mild += 1
            reasons.append("nuits courtes")

    if not usable:
        status = "unknown"
    elif strong or mild >= 2:
        status = "fatigue"
    elif mild == 1:
        status = "watch"
    elif hrv_high:
        status = "fresh"
    else:
        status = "ok"

    seen = set()
    for metric in ("hrv", "rhr", "sleep"):
        seen.update(d for d in days.get(metric, {}) if base_lo <= d <= today)
    # what the verdict waits for: the best of HRV and resting HR
    nights = [split(m) for m in ("hrv", "rhr")]
    out.update(status=status, label=FORM_LABELS[status], reasons=reasons,
               days_of_data=len(seen), as_of=today,
               nights_recent=max(len(r) for r, _ in nights), nights_base=max(len(b) for _, b in nights))
    return out


async def _daily_series(db: AsyncSession, user_id: int, lo: date, hi: date,
                        metrics=("hrv", "hr_night", "sleep")) -> tuple[dict[str, dict[date, float]], set[str]]:
    """{metric: {day: value}} (HRV on one scale only, PaceForge's own nightly
    value for the watches; nightly HR under the key "rhr" the fitness signal
    reads) and the sources behind it."""
    rows = await db.execute(
        select(HealthMetric.metric, HealthMetric.date, HealthMetric.value, HealthMetric.source, HealthMetric.details)
        .where(HealthMetric.user_id == user_id, HealthMetric.metric.in_(metrics),
               HealthMetric.date >= lo, HealthMetric.date <= hi)
    )
    series: dict[str, dict[date, float]] = defaultdict(dict)
    hrv_sources: dict[date, str | None] = {}
    sources: set[str] = set()
    for metric, d, v, source, det in rows.all():
        if metric == "hrv" and source in WATCH_SOURCES and (det or {}).get("method") != HRV_METHOD:
            continue  # the watch's own average (older rows): never read as PaceForge's
        key = "rhr" if metric == "hr_night" else metric
        series[key][d] = v
        if metric == "hrv":
            hrv_sources[d] = source
        sources.add(source if source in RMSSD_SOURCES else "Apple Santé")
    if "hrv" in series:
        series["hrv"] = hrv_same_scale(series["hrv"], hrv_sources)
    return series, sources


def _today(latest: date | None) -> date:
    """The server runs on UTC while the data is on the athlete's clock: a morning
    reading in Asia can be dated 'tomorrow' for the server. Take the later one."""
    today = datetime.now(timezone.utc).date()
    if latest and today < latest <= today + timedelta(days=1):
        return latest
    return today


async def current_form(db: AsyncSession, user_id: int, today: date | None = None) -> dict:
    """{status, label, reasons, hrv_delta_pct, rhr_delta_bpm, sleep_avg_min,
    days_of_data, …} — meant to be read later by the race model (estimates,
    heart-rate caps) as well as by the dashboard card."""
    if today is None:
        latest = (await db.execute(
            select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id)
        )).scalar()
        today = _today(latest)
    lo = today - timedelta(days=RECENT_DAYS + BASELINE_DAYS)
    series, _ = await _daily_series(db, user_id, lo, today)
    return compute_form(series, today)


def fmt_minutes(m: float | None) -> str:
    if m is None:
        return "—"
    m = int(round(m))
    return f"{m // 60}h{m % 60:02d}"


# ── helpers ─────────────────────────────────────────────────────────────────

def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _recent(dt: datetime | None, hours: int = 36) -> bool:
    return bool(dt) and datetime.now(timezone.utc) - _as_utc(dt) < timedelta(hours=hours)


def _ago(dt: datetime | None) -> str | None:
    if not dt:
        return None
    minutes = int((datetime.now(timezone.utc) - _as_utc(dt)).total_seconds() // 60)
    if minutes < 1:
        return "à l'instant"
    if minutes < 60:
        return f"il y a {minutes} min"
    if minutes < 48 * 60:
        return f"il y a {minutes // 60} h"
    return f"il y a {minutes // 1440} j"
