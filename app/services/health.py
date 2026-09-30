"""Health data: one value per day and metric, and the fitness ("forme") signal.

Values arrive from the athlete's COROS watch (app.services.coros, source
"COROS") as raw samples (HealthSample, local wall-clock times), aggregated to
one value per day and metric (HealthMetric):
- hrv    : mean of the readings taken between 22:00 the evening before and
           10:00 that morning; falls back to the whole calendar day when no
           overnight reading exists. A COROS night value (overnight RMSSD)
           replaces readings on another scale that day.
- rhr    : the last resting heart rate written that day.
- sleep  : minutes asleep during the night ending that morning (samples starting
           between 18:00 the day before and 12:00; afternoon naps are left out),
           plus the core/deep/REM/awake/in-bed split, one source per night.
- weight : last weigh-in of the day (kg).
- vo2max : last estimate of the day.

Older rows may come from the Apple Health import PaceForge had before.
"""
import logging
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


# ── storage + daily aggregation ─────────────────────────────────────────────

RAW_RETENTION_DAYS = 400
_INSERT_CHUNK = 2000


def _affected_dates(s: Sample) -> list[date]:
    d = s.start.date()
    if s.metric == "hrv":
        return [d, d + timedelta(days=1)] if s.start.hour >= 22 else [d]
    if s.metric == "sleep":
        night = sleep_night_of(s.start)
        return [night] if night else []
    return [d]


def sleep_night_of(t: datetime) -> date | None:
    """Night a sleep sample belongs to, named by its wake-up day. None = afternoon nap."""
    if t.hour < 12:
        return t.date()
    if t.hour >= 18:
        return t.date() + timedelta(days=1)
    return None


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
    `samples` carry .start_at/.end_at/.value/.source/.kind (rows or look-alikes)."""
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


# HRV is not one number across devices: Apple Health stores SDNN, COROS its
# overnight RMSSD — for the same night, often twice as high. A day therefore
# takes one source (COROS when it has a value, stored with source "COROS";
# Apple days keep no source), and the fitness signal compares only days on the
# scale of the latest one (hrv_same_scale): never a COROS week against an
# Apple baseline.
RMSSD_SOURCES = ("COROS",)


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


def _sleep_night(samples, d: date) -> dict | None:
    lo = datetime.combine(d - timedelta(days=1), time(18, 0))
    hi = datetime.combine(d, time(12, 0))
    night = [s for s in samples if lo <= s.start_at < hi]
    if not night:
        return None
    by_source: dict[str, list] = defaultdict(list)
    for s in night:
        by_source[s.source].append(s)

    def score(items):
        staged = any(s.kind in ("core", "deep", "rem") for s in items)
        asleep = _union_minutes([(s.start_at, s.end_at) for s in items if s.kind in ASLEEP_KINDS])
        in_bed = _union_minutes([(s.start_at, s.end_at) for s in items if s.kind == "in_bed"])
        return (staged, asleep, in_bed)

    source, items = max(by_source.items(), key=lambda kv: score(kv[1]))
    per_kind = {k: round(_union_minutes([(s.start_at, s.end_at) for s in items if s.kind == k]))
                for k in ("core", "deep", "rem", "awake", "in_bed")}
    asleep_ivs = [(s.start_at, s.end_at) for s in items if s.kind in ASLEEP_KINDS]
    asleep = _union_minutes(asleep_ivs)
    details = {k: v for k, v in per_kind.items() if v}
    if asleep > 0:
        details["bedtime"] = min(a for a, _ in asleep_ivs).strftime("%H:%M")
        details["wake"] = max(b for _, b in asleep_ivs).strftime("%H:%M")
        value = asleep
    elif per_kind["in_bed"]:
        value = per_kind["in_bed"]  # phone only: time in bed is all there is
        details["from_in_bed"] = True
    else:
        return None
    return {"value": round(value), "n": len(items), "source": source or None, "details": details}


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
    - the HRV SD is floored at 5 % of the mean;
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
        "hrv_7d": None, "hrv_baseline": None, "hrv_sd": None, "hrv_delta_pct": None,
        "rhr_7d": None, "rhr_baseline": None, "rhr_delta_bpm": None,
        "sleep_avg_min": None, "sleep_baseline_min": None, "sleep_delta_min": None,
    }
    strong, mild, reasons = 0, 0, []
    usable = False
    hrv_high = False

    recent, base = split("hrv")
    if recent:
        out["hrv_7d"] = round(statistics.fmean(recent), 1)
    if len(recent) >= MIN_RECENT_DAYS and len(base) >= MIN_BASELINE_DAYS:
        usable = True
        mean = statistics.fmean(base)
        # a very steady baseline must not turn a 2 % dip into a signal (day-to-day
        # overnight HRV normally varies 10–15 %)
        sd = max(statistics.stdev(base), 0.05 * mean)
        r = statistics.fmean(recent)
        out.update(hrv_baseline=round(mean, 1), hrv_sd=round(sd, 1),
                   hrv_delta_pct=round((r / mean - 1) * 100, 1))
        if r < mean - sd:
            strong += 1
            reasons.append("VFC nettement sous ta normale")
        elif r < mean - 0.5 * sd:
            mild += 1
            reasons.append("VFC un peu sous ta normale")
        elif r > mean + 0.5 * sd:
            hrv_high = True

    recent, base = split("rhr")
    if recent:
        out["rhr_7d"] = round(statistics.fmean(recent), 1)
    if len(recent) >= MIN_RECENT_DAYS and len(base) >= MIN_BASELINE_DAYS:
        usable = True
        mean = statistics.fmean(base)
        delta = statistics.fmean(recent) - mean
        out.update(rhr_baseline=round(mean, 1), rhr_delta_bpm=round(delta, 1))
        if delta >= 5:
            strong += 1
            reasons.append(f"FC au repos +{delta:.0f} bpm")
        elif delta >= 3:
            mild += 1
            reasons.append(f"FC au repos +{delta:.0f} bpm")

    recent, base = split("sleep")
    if len(recent) >= MIN_RECENT_DAYS:
        avg = statistics.fmean(recent)
        out["sleep_avg_min"] = round(avg)
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
    out.update(status=status, label=FORM_LABELS[status], reasons=reasons,
               days_of_data=len(seen), as_of=today)
    return out


async def _daily_series(db: AsyncSession, user_id: int, lo: date, hi: date,
                        metrics=("hrv", "rhr", "sleep")) -> tuple[dict[str, dict[date, float]], set[str]]:
    """{metric: {day: value}} (HRV on one scale only) and the sources behind it."""
    rows = await db.execute(
        select(HealthMetric.metric, HealthMetric.date, HealthMetric.value, HealthMetric.source)
        .where(HealthMetric.user_id == user_id, HealthMetric.metric.in_(metrics),
               HealthMetric.date >= lo, HealthMetric.date <= hi)
    )
    series: dict[str, dict[date, float]] = defaultdict(dict)
    hrv_sources: dict[date, str | None] = {}
    sources: set[str] = set()
    for metric, d, v, source in rows.all():
        series[metric][d] = v
        if metric == "hrv":
            hrv_sources[d] = source
        sources.add("COROS" if source in RMSSD_SOURCES else "Apple Santé")
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


# ── dashboard card ──────────────────────────────────────────────────────────

SPARK_DAYS = 30
SPARK_W, SPARK_H = 120, 30


def sparkline(series: dict[date, float], today: date, baseline: float | None = None,
              sd: float | None = None, days: int = SPARK_DAYS, fmt: str = "{:.0f}") -> dict | None:
    """Plain-SVG geometry for one metric over the last `days` days: the line
    (broken where a day is missing), the last 7 days drawn heavier, the
    baseline and its ±1 SD band, one hover target per day."""
    lo_day = today - timedelta(days=days - 1)
    pts = [(i, series.get(lo_day + timedelta(days=i))) for i in range(days)]
    vals = [v for _, v in pts if v is not None]
    if len(vals) < 2:
        return None
    lo, hi = min(vals), max(vals)
    if baseline is not None:
        band = sd or 0
        lo, hi = min(lo, baseline - band), max(hi, baseline + band)
    span = (hi - lo) or 1
    pad = 3

    def xy(i, v):
        x = i * (SPARK_W / (days - 1))
        y = pad + (1 - (v - lo) / span) * (SPARK_H - 2 * pad)
        return round(x, 1), round(y, 1)

    def path(from_i: int) -> str:
        d, pen = [], False
        for i, v in pts:
            if i < from_i or v is None:
                pen = False
                continue
            x, y = xy(i, v)
            d.append(f"{'L' if pen else 'M'}{x} {y}")
            pen = True
        return " ".join(d)

    recent_from = days - RECENT_DAYS
    out = {
        "w": SPARK_W, "h": SPARK_H,
        "all": path(0),
        "recent": path(recent_from - 1 if pts[recent_from - 1][1] is not None else recent_from),
        "recent_x": round(recent_from * SPARK_W / (days - 1), 1),
        "hits": [],
    }
    last_i = max(i for i, v in pts if v is not None)
    out["last"] = xy(last_i, pts[last_i][1])
    if baseline is not None:
        out["base_y"] = xy(0, baseline)[1]
        if sd:
            y_top, y_bot = xy(0, baseline + sd)[1], xy(0, baseline - sd)[1]
            out["band"] = (y_top, round(y_bot - y_top, 1))
    step = SPARK_W / (days - 1)
    for i, v in pts:
        d = lo_day + timedelta(days=i)
        label = d.strftime("%d/%m") + " · " + (fmt.format(v) if v is not None else "—")
        out["hits"].append((round(max(0, i * step - step / 2), 1), round(step, 1), label))
    return out


def fmt_minutes(m: float | None) -> str:
    if m is None:
        return "—"
    m = int(round(m))
    return f"{m // 60}h{m % 60:02d}"


async def form_card(db: AsyncSession, user_id: int) -> dict | None:
    """Everything the fitness card draws; None when no health data ever arrived."""
    latest = (await db.execute(
        select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id)
    )).scalar()
    if latest is None:
        return None
    today = _today(latest)
    lo = today - timedelta(days=RECENT_DAYS + BASELINE_DAYS)
    series, sources = await _daily_series(db, user_id, lo, today)
    form = compute_form(series, today)

    def tone(bad: bool, mild: bool) -> str:
        return "bad" if bad else ("mild" if mild else "neutral")

    hrv_d, rhr_d, sl_d = form["hrv_delta_pct"], form["rhr_delta_bpm"], form["sleep_delta_min"]
    sd_pct = (form["hrv_sd"] / form["hrv_baseline"] * 100) if form["hrv_sd"] and form["hrv_baseline"] else None
    metrics = [
        {
            "key": "hrv", "label": "VFC", "hint": "variabilité cardiaque, la nuit",
            "value": f"{form['hrv_7d']:.0f}" if form["hrv_7d"] is not None else "—", "unit": "ms",
            "base": f"{form['hrv_baseline']:.0f} ms" if form["hrv_baseline"] is not None else None,
            "delta": f"{hrv_d:+.0f} %" if hrv_d is not None else None,
            "tone": tone(sd_pct is not None and hrv_d < -sd_pct,
                         sd_pct is not None and hrv_d < -sd_pct / 2),
            "spark": sparkline(series.get("hrv", {}), today, form["hrv_baseline"], form["hrv_sd"]),
        },
        {
            "key": "rhr", "label": "FC au repos", "hint": "plus bas = mieux",
            "value": f"{form['rhr_7d']:.0f}" if form["rhr_7d"] is not None else "—", "unit": "bpm",
            "base": f"{form['rhr_baseline']:.0f} bpm" if form["rhr_baseline"] is not None else None,
            "delta": f"{rhr_d:+.0f} bpm" if rhr_d is not None else None,
            "tone": tone(rhr_d is not None and rhr_d >= 5, rhr_d is not None and rhr_d >= 3),
            "spark": sparkline(series.get("rhr", {}), today, form["rhr_baseline"]),
        },
        {
            "key": "sleep", "label": "Sommeil", "hint": "par nuit",
            "value": fmt_minutes(form["sleep_avg_min"]) if form["sleep_avg_min"] is not None else "—",
            "unit": "",
            "base": fmt_minutes(form["sleep_baseline_min"]) if form["sleep_baseline_min"] is not None else None,
            "delta": (("+" if sl_d >= 0 else "−") + f"{abs(sl_d)} min") if sl_d is not None else None,
            "tone": tone(False, form["sleep_avg_min"] is not None and (
                form["sleep_avg_min"] < SHORT_SLEEP_MIN or (sl_d is not None and sl_d < -45))),
            "spark": sparkline({d: v / 60 for d, v in series.get("sleep", {}).items()}, today,
                               form["sleep_baseline_min"] / 60 if form["sleep_baseline_min"] else None,
                               fmt="{:.1f} h"),
        },
    ]
    return {"form": form, "metrics": metrics, "latest": latest,
            "sources": " et ".join(sorted(sources)) or "Apple Santé"}


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
