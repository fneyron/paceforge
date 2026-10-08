"""The training model, from the sessions alone (Strava, Garmin): no
night needed, so it works for an athlete who never wears the watch to bed.

- session load: Strava's Relative Effort (zone time, good on intervals) put on
  the Banister TRIMP scale, else TRIMP from the average heart rate, else the
  duration at the athlete's own load per minute for that kind of sport;
- fond (CTL, 42 days) and fatigue (ATL, 7 days) as exponential averages of the
  daily load (the model only: no page draws it since Santé v4.1, the owner
  wanting recovery on Santé alone, never a fatigue model on Activités);
- the weeks exactly as Activités counts them (UTC Monday, duplicates and false
  starts left out), so a bar's total is the week's total there;
- heart rate at easy pace on flat easy runs (Theil–Sen HR = a + b·speed), the
  model of Activités › FC en footing (Nuuttila 2022);
- the big efforts (Santé v4, owner 2026-10-08: « tu te bases que sur les
  activités passées pour juger de la récupération »): each activity sized by
  its own time, stops included, whatever it was (a race marked on Strava is
  just an activity; activities chained without a real stop are one effort),
  and the recovery window it opens (`efforts`, `effort_window`).
Everything is computed on the fly; nothing is written to Activity. Activités
(training_view), the race page (race_prep) and Santé read it.
"""
import math
import statistics
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import String, and_, case, cast, column, func, literal, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.services.activity_dedupe import find_duplicate_ids, is_false_start

HISTORY_DAYS = 730
CTL_DAYS, ATL_DAYS = 42, 7  # (H) a TrainingPeaks convention (evidence row 14)
MIN_HISTORY_DAYS, MIN_SESSIONS = 42, 6  # (H) 6 weeks and 6 sessions before fond et fatigue
FOOT = {"Run", "TrailRun", "VirtualRun", "Hike", "Walk", "BackcountrySki", "NordicSki", "Snowshoe"}
RUNS = {"Run", "TrailRun"}
BIKE = {"Ride", "VirtualRide", "EBikeRide", "GravelRide", "MountainBikeRide"}


@dataclass
class Session:
    id: int
    start: datetime  # UTC
    day: date  # the athlete's local day
    sport: str
    minutes: float
    dplus: float
    km: float
    speed: float | None  # m/s
    hr: float | None
    hr_peak: float | None
    suffer: float | None
    workout_type: int | None
    temp: float | None
    name: str = ""
    load: float = 0.0
    elapsed: float = 0.0  # minutes, stops included (a race's real time)
    offset: float | None = None  # s east of UTC (None: unknown): the session's local clock is start + offset
    elev_high: float | None = None  # m, the highest point (Strava elev_high, Garmin maxElevation)
    segs: tuple | None = None  # ((minutes, average HR), …) of its laps or splits, once read (nights.load_segments)


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def monday(dt: datetime) -> datetime:
    """The UTC Monday 00:00 of a session's week — Activités' week."""
    dt = _utc(dt)
    return (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


_CACHE: dict[int, tuple[tuple, list[Session]]] = {}
_CACHE_SIZE = 256
RAW_KEYS = ("workout_type", "average_temp", "utc_offset", "startTimeLocal", "startTimeGMT", "elev_high",
            "maxElevation")


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    f = _float(v)
    return int(f) if f is not None else None


def _offset(strava: float | None, local: str | None, gmt: str | None) -> float | None:
    """A session's UTC offset (s): Strava's utc_offset, else Garmin's
    startTimeLocal − startTimeGMT."""
    if strava is not None and abs(strava) <= 14 * 3600:
        return strava
    try:
        delta = (datetime.strptime(str(local)[:19], "%Y-%m-%d %H:%M:%S")
                 - datetime.strptime(str(gmt)[:19], "%Y-%m-%d %H:%M:%S")).total_seconds()
    except (TypeError, ValueError):
        return None
    return delta if abs(delta) <= 14 * 3600 else None


async def load_sessions(db: AsyncSession, user_id: int, today: date, days: int = HISTORY_DAYS) -> list[Session]:
    """The sessions of the last `days` days, duplicates and false starts left
    out exactly as on Activités. Kept per worker until a session is added (the
    JSON fields cost a read of every activity's raw_data)."""
    since = datetime.combine(today - timedelta(days=days), datetime.min.time(), tzinfo=timezone.utc)
    where = (Activity.user_id == user_id, Activity.start_date >= since)
    # the sport's family weighs differently, so a re-import from one family to another is seen
    family = case((Activity.sport_type.in_(RUNS), 1), (Activity.sport_type.in_(FOOT), 1_000),
                  (Activity.sport_type.in_(BIKE), 1_000_000), else_=1_000_000_000)
    key = (today, days, *(await db.execute(  # what a sync can add or rewrite in place
        select(func.count(Activity.id), func.max(Activity.id), func.max(Activity.created_at),
               func.max(Activity.start_date), func.sum(Activity.moving_time), func.sum(Activity.elapsed_time),
               func.sum(Activity.distance), func.sum(Activity.total_elevation_gain),
               func.sum(func.coalesce(Activity.average_heartrate, 0)),
               func.sum(func.coalesce(Activity.max_heartrate, 0)), func.count(Activity.suffer_score),
               func.sum(family), func.sum(func.length(Activity.sport_type)),
               func.sum(func.length(func.coalesce(Activity.name, "")))).where(*where))).one())
    hit = _CACHE.get(user_id)
    if hit and hit[0] == key:
        return [replace(s) for s in hit[1]]
    # splits that hold data (Strava writes JSON null when there are none): the richer copy, as on Activités
    has_splits = and_(Activity.splits_metric.is_not(None), cast(Activity.splits_metric, String).not_in(("null", "[]")))
    cols = (Activity.id, Activity.start_date, Activity.sport_type, Activity.moving_time, Activity.distance,
            Activity.total_elevation_gain, Activity.average_speed, Activity.average_heartrate,
            Activity.max_heartrate, Activity.suffer_score, Activity.name, Activity.elapsed_time)
    if db.get_bind().dialect.name == "postgresql":
        # one read of each raw_data (every ->> would detoast it again)
        obj = case((func.jsonb_typeof(Activity.raw_data) == "object", Activity.raw_data),
                   else_=literal({}, JSONB))
        x = func.jsonb_to_record(obj).table_valued(
            *(column(k, String) for k in RAW_KEYS)).render_derived(name="raw", with_types=True)
        q = select(*cols, *(x.c[k] for k in RAW_KEYS), has_splits).select_from(Activity).join(x, true())
    else:
        q = select(*cols, *(Activity.raw_data[k].as_string() for k in RAW_KEYS), has_splits)
    rows = [(*r[:11], _int(r[12]), _float(r[13]), _float(r[14]), r[15], r[16], r[19], r[11],
             _float(r[17]) if _float(r[17]) is not None else _float(r[18]))
            for r in (await db.execute(q.where(*where).order_by(Activity.start_date))).all()]

    @dataclass
    class _Row:  # what activity_dedupe reads (splits: the richer copy is kept, as on Activités)
        id: int
        start_date: datetime
        sport_type: str
        distance: float
        moving_time: int
        splits_metric: bool

    raw = [_Row(r[0], _utc(r[1]), r[2], r[4] or 0, r[3] or 0, bool(r[16])) for r in rows]
    skip = find_duplicate_ids(raw) | {a.id for a in raw if is_false_start(a)}
    out = []
    for r in rows:
        if r[0] in skip or not r[3]:
            continue
        start = _utc(r[1])
        known = _offset(r[13], r[14], r[15])
        offset = known or 0
        speed = r[6] if r[6] else ((r[4] or 0) / r[3] if r[3] else None)
        out.append(Session(
            id=r[0], start=start, day=(start + timedelta(seconds=offset)).date(), sport=r[2],
            minutes=r[3] / 60, dplus=r[5] or 0, km=(r[4] or 0) / 1000, speed=speed or None,
            hr=r[7] or None, hr_peak=r[8] or None, suffer=r[9] or None, workout_type=r[11], temp=r[12],
            name=r[10] or "", elapsed=max(r[17] or 0, r[3]) / 60, offset=known, elev_high=r[18]))
    if len(_CACHE) >= _CACHE_SIZE:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[user_id] = (key, out)
    return [replace(s) for s in out]


async def utc_offset(db: AsyncSession, user_id: int) -> float | None:
    """The athlete's UTC offset (s) from their latest sessions (Strava or Garmin), if any."""
    rows = (await db.execute(
        select(Activity.raw_data["utc_offset"].as_string(), Activity.raw_data["startTimeLocal"].as_string(),
               Activity.raw_data["startTimeGMT"].as_string()).where(Activity.user_id == user_id)
        .order_by(Activity.start_date.desc()).limit(10))).all()
    return next((o for o in (_offset(_float(a), b, c) for a, b, c in rows) if o is not None), None)


async def athlete_today(db: AsyncSession, user_id: int, now: datetime | None = None,
                        latest: date | None = None) -> date:
    """The athlete's date, the one Santé, Activités and the race page read: the
    server's clock moved by their UTC offset (latest session), else the
    watch's latest day; the watch's day when it is already on tomorrow."""
    from app.models.health import HealthMetric
    from app.services.health import _today as watch_today

    now = now or datetime.now(timezone.utc)
    if latest is None:
        latest = (await db.execute(
            select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    offset = await utc_offset(db, user_id)
    today = (now + timedelta(seconds=offset)).date() if offset is not None else watch_today(latest)
    if latest and today < latest <= today + timedelta(days=1):  # the watch is already on tomorrow
        today = latest
    return today


# ── heart-rate bounds and session load ──────────────────────────────────────

def hr_max(sessions: list[Session], today: date) -> float:
    """98th percentile of the sessions' peaks over 12 months (wrist spikes
    left out by the percentile), clamped to 150–215; 190 without enough."""
    lo = today - timedelta(days=365)
    peaks = sorted(s.hr_peak for s in sessions if s.hr_peak and s.day >= lo)
    if len(peaks) < 10:
        return 190.0
    return min(215.0, max(150.0, peaks[min(len(peaks) - 1, math.ceil(0.98 * len(peaks)) - 1)]))


def hr_rest(night_rhr: dict[date, float], day_min: dict[date, float], today: date) -> float:
    """Median nightly resting HR over 60 days, else the median lowest daytime
    HR over 30 days, else 50."""
    for series, days in ((night_rhr, 60), (day_min, 30)):
        vals = [v for d, v in series.items() if today - timedelta(days=days) < d <= today and 25 <= v <= 100]
        if len(vals) >= 3:
            return statistics.median(vals)
    return 50.0


def trimp(minutes: float, hr: float, rest: float, peak: float) -> float:
    """Banister TRIMP from the average heart rate (male constants for all: only
    ratios are shown, so the constant barely matters)."""
    x = min(1.0, max(0.0, (hr - rest) / max(peak - rest, 1)))
    return minutes * x * 0.64 * math.exp(1.92 * x)


def _family(sport: str) -> str:
    return "foot" if sport in FOOT else "bike" if sport in BIKE else "other"


def set_loads(sessions: list[Session], rest: float, peak: float) -> None:
    """Each session's load on the TRIMP scale (see the module docstring)."""
    pairs = [trimp(s.minutes, s.hr, rest, peak) / s.suffer for s in sessions if s.hr and s.suffer]
    k = statistics.median(pairs) if len(pairs) >= 10 else 1.0
    per_min: dict[str, list[float]] = defaultdict(list)
    for s in sessions:
        if s.suffer:
            s.load = s.suffer * k
        elif s.hr:
            s.load = trimp(s.minutes, s.hr, rest, peak)
        else:
            continue
        per_min[_family(s.sport)].append(s.load / s.minutes)
    rate = {f: statistics.median(v) for f, v in per_min.items() if len(v) >= 5}
    for s in sessions:
        if not (s.suffer or s.hr):
            fam = _family(s.sport)
            s.load = s.minutes * rate.get(fam, 1.2 if fam == "foot" else 0.9)


# ── fond and fatigue ────────────────────────────────────────────────────────

def daily_loads(sessions: list[Session]) -> dict[date, float]:
    out: dict[date, float] = defaultdict(float)
    for s in sessions:
        out[s.day] += s.load
    return dict(out)


def fitness(daily: dict[date, float], today: date) -> dict[date, tuple[float, float]]:
    """{day: (CTL, ATL)} from the first session day to today."""
    if not daily:
        return {}
    d, ctl, atl, out = min(daily), 0.0, 0.0, {}
    while d <= today:
        load = daily.get(d, 0.0)
        ctl += (load - ctl) / CTL_DAYS
        atl += (load - atl) / ATL_DAYS
        out[d] = (ctl, atl)
        d += timedelta(days=1)
    return out


# ── weeks (as Activités counts them) ────────────────────────────────────────

def weeks(sessions: list[Session], now: datetime, n: int = 12) -> list[dict]:
    """The last `n` weeks, oldest first, the current one last."""
    this_monday = monday(now)
    by: dict[datetime, list[Session]] = defaultdict(list)
    for s in sessions:
        by[monday(s.start)].append(s)
    out = []
    for i in range(n - 1, -1, -1):
        m = this_monday - timedelta(weeks=i)
        ss = by.get(m, [])
        longest = max(ss, key=lambda s: s.minutes, default=None)
        out.append({"monday": m.date(), "key": m.strftime("%Y-%m-%d"), "weeks_ago": i, "current": i == 0,
                    "minutes": sum(s.minutes for s in ss), "dplus": sum(s.dplus for s in ss),
                    "load": sum(s.load for s in ss), "count": len(ss),
                    "longest": longest.minutes if longest else 0.0, "longest_id": longest.id if longest else None,
                    "longest_day": longest.day if longest else None,
                    "href": f"/activities?page={i // 6 + 1}#week-{m.strftime('%Y-%m-%d')}"})
    return out


# ── heart rate at easy pace (Nuuttila 2022) ─────────────────────────────────

MIN_FIT_RUNS = 6  # (H) cool runs for the Theil–Sen slope
EASY_FIT_DAYS = 365  # (H) one Theil–Sen slope over the cool runs of 12 months
EASY_WINDOW, EASY_MIN, EASY_BPM, FLAG_DAYS = 28, 3, 3, 14  # (H) the drawn band; Nuuttila 2022's 3–4 bpm edge
FLAG_REF_DAYS, FLAG_REF_MIN = 14, 2  # (H) each run against the cool runs of the 14 days before it (Nuuttila 2022)
LINE_DAYS, LINE_RUNS = 42, 6  # (H) the line needs ≥ 6 qualifying runs in 6 weeks (evidence row 16)
HOT_C = 25  # (H)
EASY_KM, EASY_MIN_MIN, EASY_MAX_MIN, EASY_DPLUS_PER_KM, EASY_HR_SHARE = 5, 25, 150, 12, 0.82  # (H) a flat easy run


def easy_runs(sessions: list[Session], peak: float) -> list[Session]:
    """Flat easy runs (H): 5 km and more, 25–150 min, D+ ≤ 12 m/km, average
    HR ≤ 82 % of HRmax, not a race nor a workout. Hot runs stay in (Activités
    draws them hollow); they are kept out of the slope and the normal."""
    return [s for s in sessions
            if s.sport in RUNS and s.hr and s.speed and s.km >= EASY_KM and EASY_MIN_MIN <= s.minutes <= EASY_MAX_MIN
            and s.dplus <= EASY_DPLUS_PER_KM * s.km and s.hr <= EASY_HR_SHARE * peak and s.workout_type not in (1, 3)]


def is_hot(s: Session) -> bool:
    return s.temp is not None and s.temp >= HOT_C


def theil_sen(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """(a, b) of y = a + b·x: median of the pairwise slopes, median intercept
    (on 300 evenly spread points at most: the pairs grow as n²)."""
    if len(xs) > 300:
        step = len(xs) / 300
        idx = [int(k * step) for k in range(300)]
        xs, ys = [xs[k] for k in idx], [ys[k] for k in idx]
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i]) for i in range(len(xs)) for j in range(i + 1, len(xs))
              if abs(xs[j] - xs[i]) > 1e-9]
    b = statistics.median(slopes) if slopes else 0.0
    return statistics.median(y - b * x for x, y in zip(xs, ys)), b


def easy_model(sessions: list[Session], today: date, peak: float) -> dict | None:
    """The one easy-pace model Activités (A3) and Santé (the « FC en footing »
    tile, R8, Reprise) share: each flat easy run's HR moved to the athlete's
    reference pace (one Theil–Sen slope over the cool runs of 12 months; the
    pace is their median speed, to 5 s/km), and each day's normal, the median
    of the cool runs of the 28 days before (3 at least, H). None under 6
    cool runs (H). {runs, cool (by start), value: {id: bpm}, pace: s/km,
    centre: day → bpm | None}."""
    runs = sorted((s for s in easy_runs(sessions, peak) if today - timedelta(days=EASY_FIT_DAYS) < s.day <= today),
                  key=lambda s: s.start)
    cool = [s for s in runs if not is_hot(s)]
    if len(cool) < MIN_FIT_RUNS:
        return None
    _, b = theil_sen([s.speed for s in cool], [s.hr for s in cool])
    pace = round(1000 / statistics.median(s.speed for s in cool) / 5) * 5
    ref = 1000 / pace
    value = {s.id: s.hr - b * (s.speed - ref) for s in runs}
    cdays = [s.day for s in cool]

    def centre(d: date) -> float | None:
        prior = [value[s.id] for s in cool[bisect_left(cdays, d - timedelta(days=EASY_WINDOW)):bisect_left(cdays, d)]]
        return statistics.median(prior) if len(prior) >= EASY_MIN else None

    return {"runs": runs, "cool": cool, "value": value, "pace": pace, "centre": centre}


def easy_deltas(model: dict | None) -> list[tuple[date, float]]:
    """[(day, bpm over that day's normal)] of the cool runs that have a normal."""
    if not model:
        return []
    out = []
    for s in model["cool"]:
        c = model["centre"](s.day)
        if c is not None:
            out.append((s.day, model["value"][s.id] - c))
    return out


def line_ok(model: dict | None, today: date) -> bool:
    """Activités › FC en footing is drawn (and its flag read) only with ≥ 6
    qualifying runs in the last 6 weeks (H, evidence row 16)."""
    return bool(model) and sum(1 for s in model["cool"] if today - timedelta(days=LINE_DAYS) < s.day <= today) \
        >= LINE_RUNS


def prior(model: dict, s: Session) -> float | None:
    """The normal a run is flagged against: the median of the cool runs of the
    14 days before it, 2 at least (H; Nuuttila 2022: the previous 2 weeks)."""
    vals = [model["value"][x.id] for x in model["cool"] if s.day - timedelta(days=FLAG_REF_DAYS) <= x.day < s.day]
    return statistics.median(vals) if len(vals) >= FLAG_REF_MIN else None


def easy_watch(model: dict | None, today: date) -> dict | None:
    """« à surveiller » (H; Nuuttila 2022's 3–4 bpm): the 2 latest cool runs,
    both in the last 14 days, each ≥ 3 bpm above the median of the cool runs
    of the 14 days before it (prior). {deltas: [(day, bpm)] of those runs that
    have a normal (1 or 2), value: their mean, flag}; None without such a run,
    or under 6 qualifying runs in 6 weeks (line_ok)."""
    if not line_ok(model, today):
        return None
    last2 = [s for s in model["cool"] if today - timedelta(days=FLAG_DAYS) < s.day <= today][-2:]
    deltas = []
    for s in last2:
        c = prior(model, s)
        if c is not None:
            deltas.append((s.day, model["value"][s.id] - c))
    if not deltas:
        return None
    return {"deltas": deltas, "value": statistics.fmean(v for _, v in deltas),
            "flag": len(deltas) == 2 and all(v >= EASY_BPM for _, v in deltas)}


# ── big efforts: the recovery windows Santé reads (v4.2) ────────────────────
# Santé judges recovery from past activities only (owner, 2026-10-08): a planned race is never read. Each
# activity is sized by its own time, stops included (elapsed), whatever it was. No official body gives a number of
# days (Kellmann 2018: « a clear categorization based on specific time frames cannot be provided »; Schwellnus 2016;
# Meeusen 2013): every class, window and modifier below is (H), « fenêtres indicatives » anchored on
# research_efforts.md §b. The night data never shorten a window (autonomic recovery comes before muscle recovery:
# HRV back at D+2, soreness to D+5, Fazackerley 2019; 400 m still 12 % slower at D+5, Hoffman 2017a); no age or sex
# adjustment (evidence too thin: Easthope 2010; Besson 2021).
EFFORT_ULTRA, EFFORT_VERY_LONG, EFFORT_LONG = 600, 360, 180  # (H) minutes, stops included: ≥ 10 h, 6–10 h, ≥ 3 h
EFFORT_DPLUS = 1500  # (H) m on foot: « long » whatever its time (1 264 m of descent: 2-day deficits, Giandolini 2016a)
STOPPED_WATCH = 2  # (H) elapsed over twice the moving time: a watch left running, the moving time counts
CHAIN_GAP = timedelta(minutes=30)  # (H) an activity starting this soon after the last one ended: the same effort
DAWN = time(6)  # (H) an effort ending before 06:00 ended in the night: that morning is the first one after it
# class → ((last day from D+1, cap on the score's raw value), …), the Charge récente sub-score (H). The day it
# ends (D+0, once it is uploaded) already reads D+1's cap: a big outing done today is never « pas de grosse sortie »
EFFORT_RULES = {"ultra": (((3, 40), (10, 65)), 20),  # (H) 400 m 26 % slower at D+3, 12 % at D+5 (Hoffman 2017a)
                "very_long": (((2, 45), (5, 65)), 30),  # (H) fatigue and soreness back by D+5 (Fazackerley 2019)
                "long": (((3, 65),), 50)}  # (H) power still −18 % at D+2 after a marathon (Petersen 2007)
LONG_RACE_LAST = 5  # (H) a Longue marked as a race on Strava: 65 until D+5 (CK back after 144 h: Bernat-Adell 2021)
ULTRA_LONG_MIN, ULTRA_LONG_LAST = 24 * 60, 13  # (H) an ultra ≥ 24 h, or run through a night: 65 until D+13
NIGHT_SPAN = (time(1), time(5))  # (H) an effort running from 01:00 to 05:00 local covered a night (no main sleep)
RACE_TYPES = (1, 11)  # Strava's workout_type of a race (a run's, a ride's): part of the activity, never a plan
# M1, an unusual climb (H): D+ ≥ 1.5 × the largest single-activity D+ on foot of the 8 weeks before (that largest
# ≥ 200 m) → the 65 window 2 days longer; the repeated-bout protection fades by ≈ 9 weeks (Bontemps 2020); D+ stands
# for D− (loops), the descent being the damaging part (Koller 1998)
CLIMB_RATIO, CLIMB_MIN_M, CLIMB_DAYS, CLIMB_EXTRA = 1.5, 200, 56, 2  # (H)
LOWER = {"ultra": "very_long", "very_long": "long", "long": None}  # M4: a low-impact sport one class lower (H)
# the nights after an effort, by its duration whatever the sport (M4): D+1 → D+n out of the bands and of the
# illness alert (H): a Longue night D+1 (« après une sortie longue »), a Très longue D+1 → D+3 (« après grosse
# sortie »: Hynynen 2010, nightly HR at 130 % after a marathon), an Ultra D+1 → D+4 (« après ultra »: sleep
# fragmented through night 4, Fachan 2026; normal wakefulness after 2.3 days, Kishi 2024)
NIGHT_TAGS = {"long": ("long", 1), "very_long": ("big", 3), "ultra": ("ultra", 4)}  # (H)
ULTRA_TAIL_MIN, ULTRA_TAIL = 20 * 60, (5, 7)  # (H) ≥ 20 h: D+5 → D+7 out of the band too, never of the alert
# (after 100 miles SDNN still −7 % at D+7: Paech 2021)
AFTER_ULTRA_DAYS = 21  # (H) Activités: a raised easy-pace HR is « après ultra » (Chambers 1998, n = 8: to day 25)


@dataclass(frozen=True)
class Effort:
    """An activity (or activities chained without a real stop, or back-to-back days of 3 h and more: M3) big
    enough to open a recovery window or to keep its nights out of the normal. D is `day`: the day before the
    first morning after it — the local day it ended, or the day before when it ended in the night (before 06:00,
    H; nights.anchor_efforts moves D there too when the athlete slept after it and woke the same day). Its window
    is D+0 (once uploaded) → its last cap's day (`caps`, the modifiers applied); its nights by `nights`
    (NIGHT_TAGS, and D+5 → D+7 when `tail`)."""
    session_id: int
    kind: str | None  # the class: ultra | very_long | long; None (M4: a low-impact 3–6 h) opens no window
    minutes: float  # its own time, stops included (first start → last end when chained; M3: its days' sum)
    end: datetime  # local, naive
    day: date
    name: str = ""
    start_day: date | None = None  # the local day it started (its day on Activités)
    nights: str | None = None  # the class its nights follow: by its duration whatever the sport (M4)
    caps: tuple = ()  # ((last day from D+1, cap), …) of its window, the modifiers applied (H)
    load: int | None = None  # its Charge récente sub-score (H)
    tail: bool = False  # ≥ 20 h: nights D+5 → D+7 out of the band too (H)
    ids: frozenset = frozenset()  # the activities it is made of (a past day knew it whole, or saw a smaller one)

    @property
    def big(self) -> bool:
        """6 h or more by its duration: never a « spike » on Activités, nights D+1 → D+3 at least out of the normal."""
        return self.nights in ("ultra", "very_long")


def effort_minutes(s: Session) -> float:
    """An activity's own time, stops included; the moving time when the watch
    was left running (elapsed over twice the moving time, H)."""
    elapsed = s.elapsed or s.minutes
    return s.minutes if elapsed > STOPPED_WATCH * s.minutes else elapsed


def local_start(s: Session) -> datetime:
    """The session's start on its own local clock (naive)."""
    return (_utc(s.start) + timedelta(seconds=s.offset or 0)).replace(tzinfo=None)


def local_end(s: Session) -> datetime:
    """The session's end on its own local clock (naive), stops included (effort_minutes)."""
    return local_start(s) + timedelta(minutes=effort_minutes(s))


def _span(s: Session) -> tuple[datetime, datetime]:
    start = local_start(s)
    return start, start + timedelta(minutes=effort_minutes(s))


def effort_day(end: datetime) -> date:
    """D, the day before the first morning after an effort ending at `end`
    (local): the end's day, or the day before when it ended before 06:00 (H)."""
    return end.date() - timedelta(days=1) if end.time() < DAWN else end.date()


def _kind(minutes: float, dplus_on_foot: float) -> str | None:
    """ultra (≥ 10 h), very_long (6–10 h), long (≥ 3 h, or ≥ 1 500 m D+ on foot:
    the legs rule), else None (H)."""
    return ("ultra" if minutes >= EFFORT_ULTRA else "very_long" if minutes >= EFFORT_VERY_LONG
            else "long" if minutes >= EFFORT_LONG or dplus_on_foot >= EFFORT_DPLUS else None)


def through_night(start: datetime, end: datetime) -> bool:
    """The effort ran from 01:00 to 05:00 local on one night (H): a night without a main sleep."""
    d = start.date()
    while d <= end.date():
        if start <= datetime.combine(d, NIGHT_SPAN[0]) and end >= datetime.combine(d, NIGHT_SPAN[1]):
            return True
        d += timedelta(days=1)
    return False


@dataclass
class _Unit:
    """Activities that are one effort (chains): their first start → last end, local."""
    sessions: list
    start: datetime
    end: datetime
    climb: bool = False  # M1

    @property
    def first(self) -> Session:
        return self.sessions[0]

    @property
    def minutes(self) -> float:
        if len(self.sessions) == 1:
            return effort_minutes(self.first)
        return (self.end - self.start).total_seconds() / 60

    @property
    def foot(self) -> bool:
        """On foot when any part is (a triathlon chain runs): M4 lowers only low-impact efforts."""
        return any(s.sport in FOOT for s in self.sessions)

    @property
    def dplus(self) -> float:
        return sum(s.dplus for s in self.sessions if s.sport in FOOT)

    @property
    def race(self) -> bool:
        return any(s.workout_type in RACE_TYPES for s in self.sessions)


def _climbs(units: list[_Unit], sessions: list[Session]) -> None:
    """M1 on each unit, in place: its D+ on foot ≥ 1.5 × the largest single-activity D+ on foot of the 8 weeks
    before it, when that largest is ≥ 200 m (H). Only the units that can be a Longue or a Très longue are read."""
    if not any(u.dplus for u in units):
        return
    foot = sorted((local_start(s), s.dplus) for s in sessions if s.sport in FOOT and s.dplus)
    starts = [t for t, _ in foot]
    for u in units:
        m, dplus = u.minutes, u.dplus
        if not dplus or m >= EFFORT_ULTRA or (m < EFFORT_LONG and dplus < EFFORT_DPLUS):
            continue
        lo, hi = bisect_left(starts, u.start - timedelta(days=CLIMB_DAYS)), bisect_left(starts, u.start)
        top = max((d for _, d in foot[lo:hi]), default=0.0)
        u.climb = top >= CLIMB_MIN_M and dplus >= CLIMB_RATIO * top


def _rules(kind: str | None, minutes: float, race: bool, climb: bool, night: bool) -> tuple[tuple, int | None]:
    """The class's window with the modifiers (H): a Longue marked as a race → 65 until D+5; an Ultra ≥ 24 h or
    run through a night → 65 until D+13 (function back only at D+16 after 37 h: Millet 2011; jump height down to
    day 18 after 90 km: Chambers 1998); an unusual climb (M1) on a Longue or a Très longue → its 65 window 2 days
    longer. No D+ scaling of an Ultra (CK does not follow the descent: Lecina 2024; duration, not elevation,
    shapes fatigue: Giandolini 2016b)."""
    if kind is None:
        return (), None
    caps, load = EFFORT_RULES[kind]
    *head, (last, cap) = caps
    if kind == "long" and race:
        last = LONG_RACE_LAST
    if kind == "ultra" and (minutes >= ULTRA_LONG_MIN or night):
        last = ULTRA_LONG_LAST
    if climb and kind in ("long", "very_long"):
        last += CLIMB_EXTRA
    return (*head, (last, cap)), load


def _effort(units: list[_Unit]) -> Effort | None:
    """One effort from its units (one chain, or back-to-back days: M3), or None. Its class by its time (and the
    legs rule on foot), one lower when no part is on foot (M4: cycling 230 km gave « only modest » damage, Koller
    1998; troponin about half as often after cycling, Shave 2007); its nights by its time whatever the sport. A
    chain is named and linked after its first activity, back-to-back days after their longest one."""
    one = len(units) == 1
    minutes = units[0].minutes if one else sum(u.minutes for u in units)
    foot = any(u.foot for u in units)
    nights = _kind(minutes, sum(u.dplus for u in units) if foot else 0)
    if nights is None:
        return None
    kind = nights if foot else LOWER[nights]
    named = units[0] if one else max(units, key=lambda u: u.minutes)
    end = max(u.end for u in units)
    caps, load = _rules(kind, minutes, any(u.race for u in units), foot and any(u.climb for u in units),
                        any(through_night(u.start, u.end) for u in units))
    return Effort(named.first.id, kind, minutes, end, effort_day(end), named.first.name, named.first.day, nights,
                  caps, load, nights == "ultra" and minutes >= ULTRA_TAIL_MIN,
                  frozenset(s.id for u in units for s in u.sessions))


def effort_of(s: Session, sessions=()) -> Effort | None:
    """One activity alone as an effort (see _effort), or None (H); `sessions`: its history, for M1."""
    u = _Unit([s], local_start(s), local_end(s))
    _climbs([u], list(sessions))
    return _effort([u])


def chains(sessions: list[Session]) -> list[list[Session]]:
    """The sessions in runs that are one effort: each starts at most 30 min (H)
    after the previous one ended (a watch restarted at an aid station, a
    battery swapped: one ultra saved as two activities), by start."""
    return [run for run, _, _ in _chains(sessions)]


def _chains(sessions: list[Session]) -> list[tuple[list[Session], datetime, datetime]]:
    """chains, each with its first start and last end (local), every span computed once."""
    out: list[tuple[list[Session], datetime, datetime]] = []
    for start, end, s in sorted(((*_span(s), s) for s in sessions), key=lambda t: t[0]):
        if out and start - out[-1][2] <= CHAIN_GAP:
            run, first, last = out[-1]
            run.append(s)
            out[-1] = (run, first, max(last, end))
        else:
            out.append(([s], start, end))
    return out


def back_to_back(units: list[_Unit]) -> list[list[_Unit]]:
    """M3 (H; peripheral fatigue recovers more slowly after 4 × 40 km than after the same course in one go:
    Besson 2020): the units of 3 h and more on consecutive days — each starting at the latest the day after the
    previous one's D — are one effort when they span 2 days at least (a shorter activity between them changes
    nothing); every other unit stays alone."""
    groups: list[list[_Unit]] = []
    for u in sorted((u for u in units if u.minutes >= EFFORT_LONG), key=lambda u: u.start):
        if groups and u.start.date() <= effort_day(groups[-1][-1].end) + timedelta(days=1):
            groups[-1].append(u)
        else:
            groups.append([u])
    out = [[u] for u in units if u.minutes < EFFORT_LONG]
    for g in groups:
        if len(g) > 1 and len({u.start.date() for u in g}) == 1:  # one day only: not « consecutive days »
            out.extend([u] for u in g)
        else:
            out.append(g)
    return out


def efforts(sessions: list[Session]) -> list[Effort]:
    """The big efforts among the sessions, by end: chains (activities without a
    real stop) are one unit, sized from the first start to the last end (stops
    included), named and linked after the first; back-to-back days of 3 h and
    more are one effort (M3); an unusual climb (M1), a race flag and a night
    run through set the window (_rules); a low-impact effort is one class
    lower (M4) but its nights follow its time."""
    # only the units that can be an effort: 3 h and more, or the legs rule (a shorter one never is, M3 included)
    units = [_Unit(run, start, end) for run, start, end in _chains(sessions)
             if end - start >= timedelta(minutes=EFFORT_LONG)
             or sum(s.dplus for s in run if s.sport in FOOT) >= EFFORT_DPLUS]
    _climbs(units, sessions)
    out = [e for g in back_to_back(units) if (e := _effort(g))]
    return sorted(out, key=lambda e: e.end)


def effort_window(efs: list[Effort], d: date) -> dict | None:
    """The recovery window that holds day `d` (D+0 → its last cap's day (H);
    D+0 reads D+1's cap), the one with the lowest cap on that day, then the
    lowest Charge, then the latest: {effort, days (d − D), ago (d − the day it
    started: what the page says, as Activités dates it), cap, load (the lowest
    Charge of every window open on `d`: a second effort never raises it),
    until}; None outside any. An effort without a class (M4, 3–6 h) opens
    none."""
    best, lowest = None, None
    for e in efs:
        k = (d - e.day).days
        if k < 0 or e.kind is None:
            continue
        cap = next((c for last, c in e.caps if max(k, 1) <= last), None)
        if cap is None:
            continue
        lowest = e.load if lowest is None else min(lowest, e.load)
        key = (cap, e.load, k)
        if best is None or key < best[0]:
            best = (key, {"effort": e, "days": k, "ago": (d - (e.start_day or e.day)).days, "cap": cap,
                          "load": e.load, "until": e.day + timedelta(days=e.caps[-1][0])})
    if best is None:
        return None
    best[1]["load"] = lowest
    return best[1]


def after_ultra(efs: list[Effort], d: date) -> Effort | None:
    """The Ultra (by its class) whose D+1 → D+21 (H) holds day `d`, if any: Activités annotates a raised
    easy-pace HR « après ultra » instead of flagging it (Chambers 1998, n = 8: HR at fixed speeds higher to
    day 25)."""
    return next((e for e in reversed(efs) if e.kind == "ultra" and 1 <= (d - e.day).days <= AFTER_ULTRA_DAYS), None)
