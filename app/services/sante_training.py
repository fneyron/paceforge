"""The training side of Santé, from the sessions alone (Strava, Garmin): no
night needed, so it works for an athlete who never wears the watch to bed.

- session load: Strava's Relative Effort (zone time, good on intervals) put on
  the Banister TRIMP scale, else TRIMP from the average heart rate, else the
  duration at the athlete's own load per minute for that kind of sport;
- fond (CTL, 42 days) and fatigue (ATL, 7 days) as exponential averages of the
  daily load; « fatigue % » = ATL / CTL − 1, a picture of the last week against
  the athlete's habit — never an injury risk (Impellizzeri 2020);
- the weeks exactly as Activités counts them (UTC Monday, duplicates and false
  starts left out), so a bar's total is the week's total there;
- the legs: hours and D+ on foot over 72 h (heart-rate scores miss the muscle
  damage of long descents);
- heart rate at easy pace on flat easy runs (Theil–Sen HR = a + b·speed): a
  fatigue marker over 10 days and a fitness curve over months (Nuuttila 2022).
Everything is computed on the fly; nothing is written to Activity.
"""
import math
import statistics
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import String, and_, case, cast, column, func, literal, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.services.activity_dedupe import find_duplicate_ids, is_false_start

HISTORY_DAYS = 730
CTL_DAYS, ATL_DAYS = 42, 7
MIN_HISTORY_DAYS, MIN_SESSIONS, MIN_CTL = 42, 6, 20
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


# ── heart-rate bounds and session load ──────────────────────────────────────

def hr_max(sessions: list[Session], today: date) -> float:
    """98th percentile of the sessions' peaks over 12 months (wrist spikes
    left out by the percentile), clamped to 150–215; 190 without enough."""
    peaks = sorted(s.hr_peak for s in sessions if s.hr_peak and s.day >= today - timedelta(days=365))
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


def fatigue_pct(ctl: float, atl: float) -> float | None:
    return round((atl / ctl - 1) * 100) if ctl > 0 else None


FATIGUE_BANDS = (  # (upper bound %, key, word)
    (-25, "rested", "très reposé"), (-5, "fresh", "frais"), (10, "balanced", "équilibré"),
    (30, "build", "en construction"), (math.inf, "loaded", "très chargé"))


def fatigue_band(pct: float) -> tuple[str, str]:
    for hi, key, word in FATIGUE_BANDS:
        if pct < hi:
            return key, word
    return FATIGUE_BANDS[-1][1:]


def form(sessions: list[Session], today: date) -> dict | None:
    """Today's fond and fatigue, and the last 84 days of fatigue %; None
    until 6 weeks of history with 6 sessions and a fond of 20 a day."""
    daily = daily_loads(sessions)
    series = fitness(daily, today)
    if not series or (today - min(series)).days < MIN_HISTORY_DAYS or len(sessions) < MIN_SESSIONS:
        return None
    # before today's session, today is yesterday evening: a morning must not read as a rest day
    ref = today if today in daily or today == min(series) else today - timedelta(days=1)
    ctl, atl = series[ref]
    if ctl < MIN_CTL:
        return None
    pct = fatigue_pct(ctl, atl)
    start = max(min(series), today - timedelta(days=83))
    history = {d: fatigue_pct(*series[d]) for d in (start + timedelta(days=i) for i in range((today - start).days + 1))
               if (d - min(series)).days >= MIN_HISTORY_DAYS}
    history[today] = pct
    key, word = fatigue_band(pct)
    return {"ctl": ctl, "atl": atl, "pct": pct, "key": key, "word": word, "history": history, "series": series}


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


def longest_before(sessions: list[Session], day: date, days: int = 30) -> float:
    """The longest outing (minutes) of the `days` days before `day`."""
    return max((s.minutes for s in sessions if day - timedelta(days=days) <= s.day < day), default=0.0)


# ── legs ────────────────────────────────────────────────────────────────────

def _foot(s: Session) -> bool:
    return s.sport in FOOT and s.minutes >= 30


def legs(sessions: list[Session], today: date) -> dict | None:
    """Hours and D+ on foot over 72 h (today and the two days before), the
    biggest outing of the last 48 h, and the athlete's usual 72 h (P25–P75 over
    90 days)."""
    foot = [s for s in sessions if _foot(s)]
    if not [s for s in foot if s.day >= today - timedelta(days=90)]:
        return None
    by_day: dict[date, list[Session]] = defaultdict(list)
    for s in foot:
        by_day[s.day].append(s)

    def window(d: date) -> tuple[float, float]:
        ss = [s for k in range(3) for s in by_day.get(d - timedelta(days=k), [])]
        return sum(s.minutes for s in ss), sum(s.dplus for s in ss)

    hist = sorted(window(today - timedelta(days=k))[0] for k in range(1, 91))
    q = statistics.quantiles(hist, n=20) if len(hist) >= 20 else None  # 5 % steps
    p25, p75, p90 = (q[4], q[14], q[17]) if q else (None, None, None)
    minutes, dplus = window(today)
    recent = [s for s in foot if s.day >= today - timedelta(days=1)]
    big = max(recent, key=lambda s: (s.minutes, s.dplus), default=None)
    if big and (big.minutes >= 300 or big.dplus >= 2500):
        key, word = "heavy", "très chargées"
    elif (big and (big.minutes >= 180 or big.dplus >= 1500)) or (p90 is not None and minutes > p90 and minutes >= 120):
        key, word = "loaded", "chargées"
    else:
        key, word = "fresh", "fraîches"
    return {"minutes": minutes, "dplus": dplus, "key": key, "word": word, "big": big,
            "p25": p25, "p75": p75, "p95": q[18] if q else None}


# ── heart rate at easy pace ─────────────────────────────────────────────────

FIT_FROM, FIT_TO, RECENT_RUN_DAYS = 104, 15, 10
MIN_FIT_RUNS = 8


def easy_runs(sessions: list[Session], peak: float) -> list[Session]:
    """Flat easy runs: 5 km and more, 25–150 min, D+ ≤ 12 m/km, average HR ≤
    82 % of HRmax, not a race nor a workout, not hot."""
    return [s for s in sessions
            if s.sport in RUNS and s.hr and s.speed and s.km >= 5 and 25 <= s.minutes <= 150
            and s.dplus <= 12 * s.km and s.hr <= 0.82 * peak and s.workout_type not in (1, 3)
            and (s.temp is None or s.temp <= 25)]


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


def easy_hr(runs: list[Session], today: date) -> dict | None:
    """The mean residual (bpm) of the flat easy runs of the last 10 days
    against the athlete's fit on days 15–104 before today."""
    base = [s for s in runs if today - timedelta(days=FIT_FROM) <= s.day <= today - timedelta(days=FIT_TO)]
    recent = [s for s in runs if s.day > today - timedelta(days=RECENT_RUN_DAYS)]
    if len(base) < MIN_FIT_RUNS or not recent:
        return None
    a, b = theil_sen([s.speed for s in base], [s.hr for s in base])
    res = [s.hr - (a + b * s.speed) for s in recent]
    return {"delta": statistics.fmean(res), "n": len(res), "runs": recent,
            "high": sum(1 for r in res if r >= 4)}


def residuals(runs: list[Session], today: date) -> dict[int, float]:
    """{session id: residual against the 12-month fit, minus the median of the
    residuals of the 60 days before} — detrended, for the « chez toi » links."""
    year = [s for s in runs if s.day > today - timedelta(days=365)]
    if len(year) < MIN_FIT_RUNS:
        return {}
    a, b = theil_sen([s.speed for s in year], [s.hr for s in year])
    year.sort(key=lambda s: s.day)
    days = [s.day for s in year]
    raw = [s.hr - (a + b * s.speed) for s in year]
    out = {}
    for k, s in enumerate(year):
        prior = raw[bisect_left(days, s.day - timedelta(days=60)):bisect_left(days, s.day)]
        if len(prior) >= 5:
            out[s.id] = raw[k] - statistics.median(prior)
    return out


def easy_hr_months(runs: list[Session], today: date) -> dict | None:
    """Monthly HR at the athlete's reference pace (12 months, ≥ 4 runs a
    month): one Theil–Sen slope for the year, each run's HR moved to the
    reference speed, the monthly median."""
    year = [s for s in runs if s.day > today - timedelta(days=365)]
    if len(year) < MIN_FIT_RUNS:
        return None
    _, b = theil_sen([s.speed for s in year], [s.hr for s in year])
    pace = round(1000 / statistics.median(s.speed for s in year) / 5) * 5  # s/km, rounded to 5 s
    ref = 1000 / pace
    by_month: dict[tuple[int, int], list[float]] = defaultdict(list)
    for s in year:
        if (s.day.year, s.day.month) == (today.year, today.month) and today.day < 14:
            continue  # the month in progress counts from its 14th day
        by_month[(s.day.year, s.day.month)].append(s.hr - b * (s.speed - ref))
    return {"pace_s": pace, "months": {m: statistics.median(v) for m, v in by_month.items() if len(v) >= 4}}
