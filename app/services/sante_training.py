"""The training model, from the sessions alone (Strava, Garmin, COROS): no
night needed, so it works for an athlete who never wears the watch to bed.
No brand value is read (v4.4: Strava's Relative Effort and the load model
built on it are gone; nothing drew it since Santé v4.1).

- the weeks exactly as Activités counts them (UTC Monday, duplicates and false
  starts left out), so a bar's total is the week's total there;
- heart rate at easy pace on flat easy runs (Theil–Sen HR = a + b·speed), the
  model of Activités › FC en footing and Santé › Forme (Nuuttila 2022;
  Vesterinen 2014), read from their km splits after the first 10 min on flat
  ground (± 2 %) when the athlete's runs have them;
- the big efforts (Santé v4, owner 2026-10-08: « tu te bases que sur les
  activités passées pour juger de la récupération »): each activity sized by
  its own time, stops included, whatever it was (a race marked on Strava is
  just an activity; activities chained without a real stop are one effort),
  and the recovery window it opens (`efforts`, `effort_window`); a Longue run
  like a race keeps it longer, for every user (v4.4, R3: marked so on
  Strava, rated 8/10 or more there, or its average HR at 80 % of the reserve);
- each activity's heart-rate load (owner, 2026-10-09: « Oui vas-y »,
  research_ind_train.md): its minutes weighted by their share of the
  heart-rate reserve (Banister 1991), from its laps when it has them, one
  pipeline for the Entraînement dial, the Charge accumulée and Activités
  (sante_load); shown only against the athlete's own usual week.
Everything is computed on the fly; nothing is written to Activity. Activités
(training_view), the race page (race_prep) and Santé read it.
"""
import math
import statistics
import threading
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import Integer, String, and_, case, cast, column, func, literal, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.services import activity_env as env
from app.services.activity_dedupe import find_duplicate_ids, is_false_start

HISTORY_DAYS = 730
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
    workout_type: int | None
    temp: float | None
    name: str = ""
    elapsed: float = 0.0  # minutes, stops included (a race's real time)
    offset: float | None = None  # s east of UTC (None: unknown): the session's local clock is start + offset
    zone_offset: float = 0.0  # s east of UTC of the athlete's own zone at its start: its clock when `offset` is None
    rpe: float | None = None  # Strava's perceived exertion (1–10), when the athlete rated it (its detail only)
    indoor: bool = False  # a treadmill, a home trainer, a virtual ride (activity_env.is_indoor)
    located: bool = False  # it carries a GPS position, start or end (Strava, Garmin, COROS)
    alt: float | None = None  # m, the ground altitude where it ended (Open-Meteo, looked up in the sync)
    elev_low: float | None = None  # m, its lowest point (Strava elev_low)
    feels: float | None = None  # °C, the apparent temperature at its start (Open-Meteo, looked up in the sync)
    segs: tuple | None = None  # ((minutes, average HR), …) of its laps or splits, once read (nights.read_segments)
    splits: tuple | None = None  # ((minutes, km, average HR, grade), …) of its km splits, once read (nights.km_splits)

    @property
    def end_day(self) -> date:
        """The local day it ended, stops included: an overnight race is its finish's day, as its recovery window's
        (Effort.day). Santé's 7 days and usual weeks count a session there (owner, 2026-10-09: the Transjeju, started
        on 02/10 at 21:00, ran 14 of its 17 hours on 03/10)."""
        return (local_start(self) + timedelta(minutes=self.elapsed)).date()


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def monday(dt: datetime) -> datetime:
    """The UTC Monday 00:00 of a moment's week (the race page's, race_prep, until it reads the athlete's zone:
    Activités' weeks are local_monday's)."""
    dt = _utc(dt)
    return (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def local_monday(dt: datetime, zone) -> date:
    """The Monday of a moment's week on the athlete's clock (`zone`: timezones.athlete_zone): Activités' weeks run
    Monday to Sunday there, a run at 00:30 on a Monday is that week's (2026-10-14: they were UTC Mondays)."""
    d = _utc(dt).astimezone(zone).date()
    return d - timedelta(days=d.weekday())


_CACHE: dict[int, tuple[tuple, list[Session]]] = {}
_CACHE_SIZE = 256
RAW_KEYS = ("workout_type", "average_temp", "utc_offset", "startTimeLocal", "startTimeGMT", "perceived_exertion",
            "elev_low", "trainer", *env.POSITION_KEYS, env.ENV_KEY)


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    f = _float(v)
    return int(f) if f is not None else None


def _rpe(v) -> float | None:
    """Strava's perceived exertion, 1–10; None when unrated or odd."""
    f = _float(v)
    return f if f is not None and 1 <= f <= 10 else None


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
    JSON fields cost a read of every activity's raw_data). A session whose UTC offset is unknown is dated on the
    athlete's own clock (users.timezone: timezones.athlete_zone), never on UTC's; its `offset` stays None (it says
    nothing of a time zone change)."""
    from app.models.user import User
    from app.services import timezones

    zone = timezones.athlete_zone(await db.get(User, user_id))
    since = datetime.combine(today - timedelta(days=days), datetime.min.time(), tzinfo=timezone.utc)
    where = (Activity.user_id == user_id, Activity.start_date >= since)
    # the sport's family weighs differently, so a re-import from one family to another is seen
    family = case((Activity.sport_type.in_(RUNS), 1), (Activity.sport_type.in_(FOOT), 1_000),
                  (Activity.sport_type.in_(BIKE), 1_000_000), else_=1_000_000_000)
    # raw_data's stored size: a lookup written on an activity (activity_env) is a change too; the laps' and splits'
    # too (what load_hr_segments keeps with the sessions), measured the same way, without detoasting them
    pg = db.get_bind().dialect.name == "postgresql"
    stored = func.pg_column_size if pg else func.length

    def whole(col, scale=1):  # an integer sum: PostgreSQL's float sums may differ in their last digits run to run
        return func.sum(cast(func.round(func.coalesce(col, 0) * scale), Integer))
    key = (today, days, str(zone), *(await db.execute(  # what a sync can add or rewrite in place
        select(func.count(Activity.id), func.max(Activity.id), func.max(Activity.created_at),
               func.sum(env.raw_size(db)), func.sum(stored(Activity.laps)), func.sum(stored(Activity.splits_metric)),
               func.max(Activity.start_date), func.sum(Activity.moving_time), func.sum(Activity.elapsed_time),
               whole(Activity.distance), whole(Activity.total_elevation_gain, 10),
               whole(Activity.average_heartrate, 10), whole(Activity.max_heartrate, 10),
               func.sum(family), func.sum(func.length(Activity.sport_type)),
               func.sum(func.length(func.coalesce(Activity.name, "")))).where(*where))).one())
    hit = _CACHE.get(user_id)
    if hit and hit[0] == key:
        return [replace(s) for s in hit[1]]
    # splits that hold data (Strava writes JSON null when there are none): the richer copy, as on Activités
    has_splits = and_(Activity.splits_metric.is_not(None), cast(Activity.splits_metric, String).not_in(("null", "[]")))
    cols = (Activity.id, Activity.start_date, Activity.sport_type, Activity.moving_time, Activity.distance,
            Activity.total_elevation_gain, Activity.average_speed, Activity.average_heartrate,
            Activity.max_heartrate, Activity.name, Activity.elapsed_time)
    if pg:
        # one read of each raw_data (every ->> would detoast it again)
        obj = case((func.jsonb_typeof(Activity.raw_data) == "object", Activity.raw_data),
                   else_=literal({}, JSONB))
        x = func.jsonb_to_record(obj).table_valued(
            *(column(k, String) for k in RAW_KEYS)).render_derived(name="raw", with_types=True)
        q = select(*cols, *(x.c[k] for k in RAW_KEYS), has_splits).select_from(Activity).join(x, true())
    else:
        q = select(*cols, *(Activity.raw_data[k].as_string() for k in RAW_KEYS), has_splits)
    n = len(cols)
    rows = [(r, dict(zip(RAW_KEYS, r[n:n + len(RAW_KEYS)])), bool(r[-1]))
            for r in (await db.execute(q.where(*where).order_by(Activity.start_date))).all()]

    @dataclass
    class _Row:  # what activity_dedupe reads (a heart rate, splits: the richer copy is kept, as on Activités)
        id: int
        start_date: datetime
        sport_type: str
        distance: float
        moving_time: int
        splits_metric: bool
        average_heartrate: float | None

    raw = [_Row(r.id, _utc(r.start_date), r.sport_type, r.distance or 0, r.moving_time or 0, split,
                r.average_heartrate) for r, _, split in rows]
    skip = find_duplicate_ids(raw) | {a.id for a in raw if is_false_start(a)}
    out = []
    for r, x, _ in rows:
        if r.id in skip or not r.moving_time:
            continue
        start = _utc(r.start_date)
        known = _offset(_float(x["utc_offset"]), x["startTimeLocal"], x["startTimeGMT"])
        clock = start.astimezone(zone).utcoffset().total_seconds()
        offset = known if known is not None else clock
        speed = r.average_speed if r.average_speed else ((r.distance or 0) / r.moving_time)
        looked = env.env_of(x)
        out.append(Session(
            id=r.id, start=start, day=(start + timedelta(seconds=offset)).date(), sport=r.sport_type,
            minutes=r.moving_time / 60, dplus=r.total_elevation_gain or 0, km=(r.distance or 0) / 1000,
            speed=speed or None, hr=r.average_heartrate or None, hr_peak=r.max_heartrate or None,
            workout_type=_int(x["workout_type"]), temp=_float(x["average_temp"]), name=r.name or "",
            elapsed=max(r.elapsed_time or 0, r.moving_time) / 60, offset=known, zone_offset=clock,
            rpe=_rpe(x["perceived_exertion"]),
            indoor=env.is_indoor(r.sport_type, x), located=env.located(x), alt=_float(looked.get("alt")),
            elev_low=_float(x["elev_low"]), feels=_float(looked.get("feels"))))
    if len(_CACHE) >= _CACHE_SIZE:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[user_id] = (key, out)
    return [replace(s) for s in out]


_SEGS: dict[int, tuple[tuple, dict[int, tuple]]] = {}  # user → (load_sessions' key, {id: (its segs, splits)})


async def load_hr_segments(db: AsyncSession, user_id: int, sessions: list[Session], since: date) -> None:
    """`segs` on every session with an average HR from `since` on (nights.read_segments: its laps, else its km
    splits, as (minutes, average HR)), what the Entraînement dial weighs. Kept per worker with the sessions: read
    again only once a sync added or rewrote an activity, its laps or its splits (load_sessions' key; a year of laps
    is a heavy read). Kept under the key seen before the read, and only while it still holds."""
    from app.services.nights import read_segments

    key = (_CACHE.get(user_id) or (None,))[0]
    kept = _SEGS.get(user_id)
    memo = kept[1] if key is not None and kept and kept[0] == key else {}
    want = [s for s in sessions if s.hr and s.day >= since]
    for s in want:
        if s.segs is None and s.id in memo:
            s.segs, s.splits = memo[s.id]
    await read_segments(db, want)
    if key is not None and (_CACHE.get(user_id) or (None,))[0] == key:
        if user_id not in _SEGS and len(_SEGS) >= _CACHE_SIZE:
            _SEGS.pop(next(iter(_SEGS)))
        _SEGS[user_id] = (key, {**memo, **{s.id: (s.segs, s.splits) for s in want}})


async def utc_offset(db: AsyncSession, user_id: int, now: datetime | None = None) -> float | None:
    """Latest known location: an activity offset or a more recent HealthKit night zone."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    from app.models.health import HealthMetric

    rows = (await db.execute(
        select(Activity.raw_data["utc_offset"].as_string(), Activity.raw_data["startTimeLocal"].as_string(),
               Activity.raw_data["startTimeGMT"].as_string(), Activity.start_date).where(Activity.user_id == user_id)
        .order_by(Activity.start_date.desc()).limit(10))).all()
    known = next(((o, _utc(at)) for a,b,c,at in rows if (o := _offset(_float(a),b,c)) is not None), None)
    night = await db.scalar(select(HealthMetric).where(HealthMetric.user_id == user_id,
        HealthMetric.metric == "sleep", HealthMetric.source.startswith("Apple Santé:"))
        .order_by(HealthMetric.date.desc(), HealthMetric.updated_at.desc()).limit(1))
    if night:
        try:
            end = _utc(datetime.fromisoformat(night.details["utc_end"]))
            if not known or end > known[1]:
                return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(night.details["timezone"])).utcoffset().total_seconds()
        except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
            pass
    return known[0] if known else None


async def athlete_today(db: AsyncSession, user_id: int, now: datetime | None = None,
                        latest: date | None = None) -> date:
    """The athlete's date, the one Santé, Activités and the race page read: the clock in the athlete's own zone
    (users.timezone, else Europe/Paris: timezones.athlete_today; owner, 2026-10-14: scientific basis — it was the
    latest activity's fixed UTC offset, a day off after the 25 October clock change or a trip); the watch's day
    when it is already on tomorrow (a zone not yet updated after flying east)."""
    from app.models.health import HealthMetric
    from app.models.mobile import MobileDaily
    from app.models.user import User
    from app.services import timezones

    now = now or datetime.now(timezone.utc)
    if latest is None:
        latest = (await db.execute(
            select(func.max(HealthMetric.date)).where(HealthMetric.user_id == user_id))).scalar()
    mobile_day = await db.scalar(select(func.max(MobileDaily.date)).where(MobileDaily.user_id == user_id))
    latest = max((d for d in (latest, mobile_day) if d is not None), default=None)
    today = timezones.athlete_today(await db.get(User, user_id), now)
    if latest and today < latest <= today + timedelta(days=1):  # the watch is already on tomorrow
        today = latest
    return today


# ── heart-rate bounds ───────────────────────────────────────────────────────

def hr_max(sessions: list[Session], today: date) -> float:
    """98th percentile of the sessions' peaks over the 12 months up to `today`
    (wrist spikes left out by the percentile), clamped to 150–215; 190
    without enough (COROS's own sessions have theirs from their laps, v4.4)."""
    lo = today - timedelta(days=365)
    peaks = sorted(s.hr_peak for s in sessions if s.hr_peak and lo <= s.day <= today)
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


# ── heart-rate load: one pipeline for every view (owner, 2026-10-14: scientific basis) ──────────────────────
# Each minute of an activity weighs x·0.64·e^(1.92x), x its share of the heart-rate reserve (Banister 1991; Morton,
# Fitz-Clarke & Banister 1990): an easy minute at 60 % of the reserve about 1.2, a hard one at 90 % about 3.2.
# Internal load is the consensus construct (Impellizzeri 2019; Bourdon 2017), yet never validated as an absolute
# dose (Passfield 2022): Santé shows it only against the athlete's own usual week, as a ratio, never as a number (no
# TRIMP, no points). No brand load is read. One set of bounds a day (rest: the median sleeping HR of 60 days, else
# 50; max: the 98th percentile of the sessions' peaks over 12 months, else 190) weighs every session of every view
# that day: the Entraînement dial, the Charge accumulée curves and Activités' 7 / 28 days (sante_load).
LOAD_A, LOAD_B = 0.64, 1.92  # (H) Banister 1991's men's constants: no sex is known to the app (women's: LOAD_WOMEN)
LOAD_WOMEN = (0.86, 1.67)  # Banister 1991's women's constants, read once the athlete's sex is known
SEG_COVER = 0.8  # (H) laps (else km splits) with HR over 80 % of the moving time: their load, scaled to it
HR_FLOOR, HR_OVER_MAX = 40, 10  # (H) an average under 40 bpm or over max + 10 bpm: a glitch, no HR
RUN_MIN_HRR = 0.3  # (H) a run under 30 % of the heart-rate reserve: an optical dropout, no HR
RUN_TYPES = RUNS | {"VirtualRun"}
# (H) HR under-reads strength and studio work (Polar's Training Load Pro; WHOOP added a muscular load): by time
BY_TIME = frozenset({"WeightTraining", "Crossfit", "Workout", "Yoga", "Pilates"})  # (H)
RATE_DAYS, RATE_MIN = 365, 5  # (H) the athlete's own load per minute: 12 months, 5 sessions read from their HR
RATE_DEFAULT = 1.2  # (H) else an easy minute's (60 % of the reserve)


def load_constants(sex: str | None = None) -> tuple[float, float]:
    """Banister's (a, b): the women's (0.86, 1.67) for « F », else the men's (0.64, 1.92), the app's default (no
    sex field)."""
    return LOAD_WOMEN if sex == "F" else (LOAD_A, LOAD_B)


def minute_load(hr: float, rest: float, peak: float, sex: str | None = None) -> float:
    """One minute's weight at `hr` (Banister 1991): x·a·e^(b·x), x = (hr − rest) ÷ (peak − rest) kept in
    [0, 1]."""
    a, b = load_constants(sex)
    x = min(1.0, max(0.0, (hr - rest) / max(peak - rest, 1.0)))
    return x * a * math.exp(b * x)


def hr_readable(s: Session, rest: float, peak: float) -> bool:
    """Its average HR can weigh its minutes (H): from 40 bpm to max + 10, and a run at 30 % of the reserve at least
    (under it, an optical dropout); never a strength, Workout, yoga or Pilates session (BY_TIME)."""
    if not s.hr or s.sport in BY_TIME or not HR_FLOOR <= s.hr <= peak + HR_OVER_MAX:
        return False
    return s.sport not in RUN_TYPES or (s.hr - rest) / max(peak - rest, 1.0) >= RUN_MIN_HRR


class _Memo:
    """A small thread-safe LRU (the page's pure computation runs in a worker thread)."""

    def __init__(self, size: int):
        self.size, self.data, self.lock = size, {}, threading.Lock()

    def get(self, key, make):
        with self.lock:
            if key in self.data:
                return self.data[key]
        value = make()
        with self.lock:
            if len(self.data) >= self.size:
                self.data.pop(next(iter(self.data)))
            self.data[key] = value
        return value


_LOADS = _Memo(20_000)  # session load per (session, bounds): a page reads each session once a day


def session_load(s: Session, rest: float, peak: float, sex: str | None = None) -> float | None:
    """One activity's heart-rate load (Banister 1991): its laps', else its km splits' (`segs`, laps or splits with a
    plausible HR) minutes × each one's minute weight, scaled to its moving time when they cover 80 % of it at least
    (H), else its moving minutes at its average HR (summed per segment, a load reads ≈ 9 % higher, more on hard
    sessions: García-Ramos 2015). None when its HR cannot be read (hr_readable): `loads` counts it by time.
    Memoized per (session, its HR fields, bounds)."""
    key = (s.id, s.sport, s.minutes, s.hr, s.segs, rest, peak, sex)
    try:
        hash(key)
    except TypeError:  # segments given as lists: no memo
        return _session_load(s, rest, peak, sex)
    return _LOADS.get(key, lambda: _session_load(s, rest, peak, sex))


def _session_load(s: Session, rest: float, peak: float, sex: str | None) -> float | None:
    if not hr_readable(s, rest, peak):
        return None
    segs = [(m, hr) for m, hr in s.segs or () if HR_FLOOR <= hr <= peak + HR_OVER_MAX]
    covered = sum(m for m, _ in segs)
    if covered and covered >= SEG_COVER * s.minutes:
        return s.minutes * sum(m * minute_load(hr, rest, peak, sex) for m, hr in segs) / covered
    return s.minutes * minute_load(s.hr, rest, peak, sex)


def load_family(sport: str) -> str:
    """Whose minutes a session without a readable HR borrows (H): the runs (road, trail, treadmill), the rides
    (BIKE), else its own sport."""
    return "run" if sport in RUN_TYPES else "bike" if sport in BIKE else sport


def loads(sessions: list[Session], today: date, rest: float, peak: float,
          sex: str | None = None) -> dict[int, tuple[float, bool]]:
    """{session id: (its heart-rate load, read from its HR)}, every session with the same bounds, today's (H: a new
    max or resting HR never moves the dial by itself). A session whose HR cannot be read, and every strength,
    Workout, yoga or Pilates session, counts its moving minutes at the athlete's own median load per minute (H):
    over the 12 months up to `today`, that of the sessions read from their HR in its family (load_family, 5 at
    least), else of all of them (a strength session: always all of them), else 1.2 (an easy minute). Without any
    HR, every minute weighs the same: the dial is the time's."""
    own = {s.id: session_load(s, rest, peak, sex) for s in sessions}
    lo = today - timedelta(days=RATE_DAYS)
    per = defaultdict(list)
    for s in sessions:
        if own[s.id] is not None and s.minutes > 0 and lo < s.day <= today:
            per[load_family(s.sport)].append(own[s.id] / s.minutes)
    every = [r for rates in per.values() for r in rates]
    base = statistics.median(every) if len(every) >= RATE_MIN else RATE_DEFAULT
    rate = {f: statistics.median(rates) for f, rates in per.items() if len(rates) >= RATE_MIN}
    return {s.id: (own[s.id], True) if own[s.id] is not None else (s.minutes * rate.get(load_family(s.sport), base),
                                                                    False)
            for s in sessions}


# ── weeks (as Activités counts them) ────────────────────────────────────────

def weeks(sessions: list[Session], now: datetime, n: int = 12, zone=timezone.utc) -> list[dict]:
    """The last `n` weeks, oldest first, the current one last: Monday to Sunday on the athlete's clock (`zone`,
    timezones.athlete_zone: Activités' list's weeks; UTC when none is given)."""
    this_monday = local_monday(now, zone)
    by: dict[date, list[Session]] = defaultdict(list)
    for s in sessions:
        by[local_monday(s.start, zone)].append(s)
    out = []
    for i in range(n - 1, -1, -1):
        m = this_monday - timedelta(weeks=i)
        ss = by.get(m, [])
        longest = max(ss, key=lambda s: s.minutes, default=None)
        out.append({"monday": m, "key": m.strftime("%Y-%m-%d"), "weeks_ago": i, "current": i == 0,
                    "minutes": sum(s.minutes for s in ss), "dplus": sum(s.dplus for s in ss),
                    "count": len(ss),
                    "longest": longest.minutes if longest else 0.0, "longest_id": longest.id if longest else None,
                    "longest_day": longest.day if longest else None,
                    "href": f"/activities?page={i // 6 + 1}#week-{m.strftime('%Y-%m-%d')}"})
    return out


def training_days(sessions: list[Session], today: date, until: date | None = None,
                  *, missing_days: set[date] | None = None) -> dict:
    """Seven calendar days of recorded moving time, on each session's local finish day.

    ``until`` is the real calendar day when the sleep cycle still reads yesterday.
    Sessions have already been deduplicated by ``load_sessions``. A zero means no
    recorded session, never proven rest. Only explicitly unavailable days draw a
    gap; without provider coverage metadata, absence alone cannot prove missing
    data. A measured session survives a partial sync and is labelled as partial.
    """
    from app.services import viz

    until = until or today
    days = [until - timedelta(days=6 - i) for i in range(7)]
    by: dict[date, list[Session]] = defaultdict(list)
    for s in sessions:
        if days[0] <= s.end_day <= until:
            by[s.end_day].append(s)
    unavailable = missing_days or set()
    values, readouts, arias = [], [], []
    for d in days:
        recorded = by[d]
        minutes = sum(s.minutes for s in recorded)
        if recorded:
            word = f"{len(recorded)} séance{'s' if len(recorded) > 1 else ''}"
            if d in unavailable:
                word += " · données partielles"
            values.append(minutes)
            readouts.append([viz.hm(minutes), word, viz.d_short(d)])
            arias.append(f"{viz.d_long(d)} : {viz.hm_long(minutes)} enregistrées, {word}.")
        elif d in unavailable:
            values.append(None)
            readouts.append(["—", "Données indisponibles", viz.d_short(d)])
            arias.append(f"{viz.d_long(d)} : données indisponibles.")
        else:
            values.append(0)
            readouts.append(["0 min", "Aucune séance enregistrée", viz.d_short(d)])
            arias.append(f"{viz.d_long(d)} : aucune séance enregistrée.")
    total = sum(v for v in values if v is not None)
    summary = f"Entraînement sur 7 jours : durée enregistrée {viz.hm_long(total)}."
    if unavailable.intersection(days):
        summary += " Certaines données sont indisponibles."
    chart = viz.day_bars("entrainement", days, values, readouts=readouts, arias=arias,
                         min_top=30, today=6, summary=summary)
    chart = viz.rest(chart, [viz.hm(total), "7 derniers jours", ""], summary)
    chart.update(duration=viz.hm(total), minutes=total, count=sum(map(len, by.values())),
                 days=7, since=days[0], until=until)
    return chart


# ── heart rate at easy pace (Nuuttila 2022) ─────────────────────────────────

MIN_FIT_RUNS = 6  # (H) cool runs for the Theil–Sen slope
EASY_FIT_DAYS = 365  # (H) one Theil–Sen slope over the cool runs of 12 months
EASY_WINDOW, EASY_MIN, EASY_BPM, FLAG_DAYS = 28, 3, 3, 14  # (H) the drawn band; Nuuttila 2022's 3–4 bpm edge
FLAG_REF_DAYS, FLAG_REF_MIN = 14, 2  # (H) each run against the cool runs of the 14 days before it (Nuuttila 2022)
LINE_DAYS, LINE_RUNS = 42, 6  # (H) the line needs ≥ 6 qualifying runs in 6 weeks (evidence row 16)
HOT_C = 25  # (H) °C felt at the start (Open-Meteo's apparent temperature), else the device's (R4)
EASY_KM, EASY_MIN_MIN, EASY_MAX_MIN, EASY_DPLUS_PER_KM, EASY_HR_SHARE = 5, 25, 150, 12, 0.82  # (H) a flat easy run
# owner, 2026-10-14: scientific basis — a run's HR and speed from its km splits after its first 10 min (the warm-up's
# rising HR left out) whose grade is within ± 2 % (Vesterinen 2014: HR at a fixed submaximal speed on flat ground);
# 2 km of them at least (H)
STEADY_AFTER_MIN, FLAT_GRADE, STEADY_MIN_KM = 10, 0.02, 2


def easy_runs(sessions: list[Session], peak: float) -> list[Session]:
    """Flat easy runs (H): 5 km and more, 25–150 min, D+ ≤ 12 m/km, average
    HR ≤ 82 % of HRmax, not a race nor a workout. Hot runs stay in (Activités
    draws them hollow); they are kept out of the slope and the normal."""
    return [s for s in sessions
            if s.sport in RUNS and s.hr and s.speed and s.km >= EASY_KM and EASY_MIN_MIN <= s.minutes <= EASY_MAX_MIN
            and s.dplus <= EASY_DPLUS_PER_KM * s.km and s.hr <= EASY_HR_SHARE * peak and s.workout_type not in (1, 3)]


def is_hot(s: Session) -> bool:
    """A hot run (H, v4.4 R4): 25 °C or more felt at its start place and hour (Open-Meteo's apparent
    temperature, looked up in the sync: every user with a GPS run), else its device's temperature as before
    (Strava average_temp: the wrist reads body heat too); never indoors (a treadmill, a home trainer). Heat
    raises HR at a given pace (Racinais 2015): a hot run is drawn hollow, out of the slope and the normal;
    never a score nor a recovery modifier."""
    if s.indoor:
        return False
    t = s.feels if s.feels is not None else s.temp
    return t is not None and t >= HOT_C


def comparable(s: Session) -> tuple[float, float] | None:
    """(HR, speed in m/s) of a run from its km splits (Session.splits) after its first 10 minutes, those within ± 2 %
    of grade (a COROS lap carries no grade: the run's own flatness gate holds, easy_runs), their time-weighted HR and
    their distance over their time; None under 2 km of them (STEADY_MIN_KM, H)."""
    elapsed = minutes = km = beats = 0.0
    for m, k, hr, grade in getattr(s, "splits", None) or ():
        start, elapsed = elapsed, elapsed + m
        if start < STEADY_AFTER_MIN or hr is None or (grade is not None and abs(grade) > FLAT_GRADE):
            continue
        minutes, km, beats = minutes + m, km + k, beats + m * hr
    if km < STEADY_MIN_KM or minutes <= 0:
        return None
    return beats / minutes, km * 1000 / (minutes * 60)


def _easy_sample(sessions: list[Session], today: date, peak: float) -> tuple[list[Session], list[Session], dict, bool]:
    """The exact sample shared by the fitted model and its collection progress, and each run's (HR, speed): from
    its steady flat splits (comparable) when 6 cool runs of the 12 months have them, every run read that way; else
    from every run's own averages (no splits stored: a Garmin-only athlete) — one method per model, never mixed."""
    runs = sorted((s for s in easy_runs(sessions, peak)
                   if today - timedelta(days=EASY_FIT_DAYS) < s.day <= today),
                  key=lambda s: s.start)
    points = {s.id: comparable(s) for s in runs}
    split = [s for s in runs if points[s.id]]
    if sum(1 for s in split if not is_hot(s)) >= MIN_FIT_RUNS:
        return split, [s for s in split if not is_hot(s)], points, True
    return runs, [s for s in runs if not is_hot(s)], {s.id: (s.hr, s.speed) for s in runs}, False


def _line_runs(cool: list[Session], today: date) -> list[Session]:
    return [s for s in cool if today - timedelta(days=LINE_DAYS) < s.day <= today]


def fitness_progress(sessions: list[Session], today: date, peak: float) -> dict:
    """Actual comparable runs in the same 42-day window that unlocks the fitness line.

    Like ``training_view.footing``, call this with completed sessions. Heat,
    intensity, distance, duration, terrain and heart-rate requirements are the
    model's own filters; no fitted slope is needed just to count its inputs.
    """
    _, cool, _, _ = _easy_sample(sessions, today, peak)
    count = len(_line_runs(cool, today))
    return {"count": count, "required": LINE_RUNS, "remaining": max(0, LINE_RUNS - count),
            "ready": count >= LINE_RUNS,
            "label": f"{count}/{LINE_RUNS} sorties comparables", "days": LINE_DAYS}


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
    """The one easy-pace model Activités (A3) and Santé (the « FC à allure comparable » card) share: each flat easy
    run's HR (its steady flat splits when the athlete's runs have them: _easy_sample) moved to the athlete's
    reference pace (one Theil–Sen slope over the cool runs of 12 months; the pace is their median speed, to 5 s/km),
    and each day's normal, the median of the cool runs of the 28 days before (3 at least, H). None under 6 cool
    runs (H). {runs, cool (by start), value: {id: bpm}, pace: s/km, centre: day → bpm | None, steady: splits read}."""
    runs, cool, points, steady = _easy_sample(sessions, today, peak)
    if len(cool) < MIN_FIT_RUNS:
        return None
    _, b = theil_sen([points[s.id][1] for s in cool], [points[s.id][0] for s in cool])
    pace = round(1000 / statistics.median(points[s.id][1] for s in cool) / 5) * 5
    ref = 1000 / pace
    value = {s.id: points[s.id][0] - b * (points[s.id][1] - ref) for s in runs}
    cdays = [s.day for s in cool]

    def centre(d: date) -> float | None:
        prior = [value[s.id] for s in cool[bisect_left(cdays, d - timedelta(days=EASY_WINDOW)):bisect_left(cdays, d)]]
        return statistics.median(prior) if len(prior) >= EASY_MIN else None

    return {"runs": runs, "cool": cool, "value": value, "pace": pace, "centre": centre,
            "steady": steady}


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
    return bool(model) and len(_line_runs(model["cool"], today)) >= LINE_RUNS


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


# ── big efforts: the windows Santé reads (v4.2; a reason of the daily state since 2026-10-14) ────────────
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
# owner, 2026-10-14: scientific basis — the window is a reason of the daily state, no cap any more (the 35/45/65
# caps and their interpolation had no basis): orange through D+n, the lengths read from the studies (« fenêtres
# indicatives », H): a Longue D+1, run like a race D+2 (power still −18 % at D+2 after a marathon: Petersen 2007), a
# Très longue D+3, an Ultra D+5 (fatigue and soreness back by D+5: Fazackerley 2019; 400 m still 12 % slower at D+5:
# Hoffman 2017), ≥ 24 h D+7; red only on D+0 and D+1 after an Ultra (26 % slower at D+3: Hoffman 2017). The day it
# ends (D+0, once uploaded) is already in it: a big outing done today is never « pas de grosse sortie ».
EFFORT_WINDOWS = {"ultra": 5, "very_long": 3, "long": 1}  # (H) the last day of the window, D+n
ULTRA_RED_LAST = 1  # (H) red on D+0 and D+1 after an Ultra
KINDS = ("ultra", "very_long", "long")  # the bigger effort first: two windows open, the bigger one names it
LONG_RACE_LAST = 2  # (H) a Longue run like a race: through D+2 (Petersen 2007; owner, 2026-10-14: scientific basis)
# v4.4 (R3): run like a race, whoever measured it: marked so on Strava, or rated 8/10 or more there (session RPE:
# Foster 2001; Haddad 2017), or its average HR ≥ 80 % of the heart-rate reserve as of its day (nights.VIGOROUS_HRR,
# the « vigorous » test: harder efforts bring more damage, Martínez-Navarro 2021b; Bernat-Adell 2021)
INTENSE_RPE = 8  # (H)
ULTRA_LONG_MIN, ULTRA_LONG_LAST = 24 * 60, 7  # (H) an Ultra of 24 h or more: through D+7 (owner, 2026-10-14)
NIGHT_SPAN = (time(1), time(5))  # (H) an effort running from 01:00 to 05:00 local covered a night (no main sleep)
RACE_TYPES = (1, 11)  # Strava's workout_type of a race (a run's, a ride's): part of the activity, never a plan
# v4.3 (owner: « je ne comprends pas »): no unusual-climb modifier (M1) and no back-to-back days (M3) any more. Each
# activity, or chain of activities less than 30 min apart, is its own effort; overlapping windows already extend
# the recovery (effort_window keeps the strongest of the windows open)
LOWER = {"ultra": "very_long", "very_long": "long", "long": None}  # M4: a low-impact sport one class lower (H)
# the nights after an effort, by its duration whatever the sport (M4): D+1 → D+n never fire the illness alert
# (they count in the bands like any night, owner 2026-10-08) (H): a Longue night D+1 (« après une sortie
# longue »), a Très longue D+1 → D+3 (« après grosse sortie »: Hynynen 2010, nightly HR at 130 % after a
# marathon), an Ultra D+1 → D+4 (« après ultra »: sleep fragmented through night 4, Fachan 2026; normal
# wakefulness after 2.3 days, Kishi 2024)
NIGHT_TAGS = {"long": ("long", 1), "very_long": ("big", 3), "ultra": ("ultra", 4)}  # (H)
AFTER_ULTRA_DAYS = 21  # (H) Activités: a raised easy-pace HR is « après ultra » (Chambers 1998, n = 8: to day 25)


@dataclass(frozen=True)
class Effort:
    """An activity (or activities chained without a real stop) big enough to open a recovery window or to keep
    its nights from firing the illness alert. D is `day`: the day before the first morning after it — the local
    day it ended, or the day before when it ended in the night (before 06:00, H; nights.anchor_efforts moves D
    there too when the athlete slept after it and woke the same day). Its window is D+0 (once uploaded) → D+`last`
    (EFFORT_WINDOWS, the modifiers applied); its nights by `nights` (NIGHT_TAGS)."""
    session_id: int
    kind: str | None  # the class: ultra | very_long | long; None (M4: a low-impact 3–6 h) opens no window
    minutes: float  # its own time, stops included (first start → last end when chained)
    end: datetime  # local, naive
    day: date
    name: str = ""
    start_day: date | None = None  # the local day it started (its day on Activités)
    nights: str | None = None  # the class its nights follow: by its duration whatever the sport (M4)
    last: int | None = None  # the last day of its window, D+n (EFFORT_WINDOWS, with its modifiers); None: none
    ids: frozenset = frozenset()  # the activities it is made of (a past day knew the chain whole, or a part of it)

    @property
    def big(self) -> bool:
        """6 h or more by its duration: never a « spike » on Activités, nights D+1 → D+3 at least never fire the
        illness alert."""
        return self.nights in ("ultra", "very_long")


def effort_minutes(s: Session) -> float:
    """An activity's own time, stops included; the moving time when the watch
    was left running (elapsed over twice the moving time, H)."""
    elapsed = s.elapsed or s.minutes
    return s.minutes if elapsed > STOPPED_WATCH * s.minutes else elapsed


def local_start(s: Session) -> datetime:
    """The session's start on its own local clock (naive): its UTC offset, else the athlete's zone's."""
    offset = s.offset if s.offset is not None else getattr(s, "zone_offset", 0.0)
    return (_utc(s.start) + timedelta(seconds=offset or 0)).replace(tzinfo=None)


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


def raced_nights(efs) -> frozenset:
    """The wake days whose night an effort ran through (01:00 → 05:00 local covered: through_night): no main sleep
    then, its naps only (the sleep need's debt: sante_sleep.sleep_need; Kishi 2024: a night spent racing is a real
    debt)."""
    out = set()
    for e in efs:
        start = e.end - timedelta(minutes=e.minutes)
        d = start.date()
        while d <= e.end.date():
            if start <= datetime.combine(d, NIGHT_SPAN[0]) and e.end >= datetime.combine(d, NIGHT_SPAN[1]):
                out.add(d)
            d += timedelta(days=1)
    return frozenset(out)


@dataclass
class _Unit:
    """Activities that are one effort (chains): their first start → last end, local."""
    sessions: list
    start: datetime
    end: datetime

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

    def raced(self, bounds) -> bool:
        """Run like a race (H, R3): marked so on Strava, rated ≥ 8/10 there, or its average HR (by time, over
        its parts with one) ≥ rest + 80 % of (peak − rest), the athlete's bounds as of the day it started
        (`bounds(day)` → (rest, peak); None: no heart-rate test)."""
        if self.race or any((s.rpe or 0) >= INTENSE_RPE for s in self.sessions):
            return True
        timed = [(s.minutes, s.hr) for s in self.sessions if s.hr and s.minutes]
        if bounds is None or not timed:
            return False
        from app.services.nights import VIGOROUS_HRR

        rest, peak = bounds(self.first.day)
        return sum(m * hr for m, hr in timed) / sum(m for m, _ in timed) >= rest + VIGOROUS_HRR * (peak - rest)


def hr_bounds(sessions: list[Session], rest_of):
    """`bounds(day)` → (rest, peak) as of that day: `rest_of(day)` (nights.rest_hr on the nights: the median
    nightly HR of the 60 days up to it, else 50) and hr_max of the sessions up to it; each day computed once.
    The same whichever later day reads it: a past day of the score's history sees what today sees."""
    memo: dict[date, tuple[float, float]] = {}

    def bounds(d: date) -> tuple[float, float]:
        if d not in memo:
            memo[d] = (rest_of(d), hr_max(sessions, d))
        return memo[d]
    return bounds


def window_last(kind: str | None, minutes: float, race: bool) -> int | None:
    """The last day of the class's window, D+n, with its modifiers (H): a Longue run like a race D+2; an Ultra of
    24 h or more D+7 (function back only at D+16 after 37 h: Millet 2011). No D+ scaling (CK does not follow the
    descent: Lecina 2024; duration, not elevation, shapes fatigue: Giandolini 2016b). None: no window."""
    if kind is None:
        return None
    if kind == "long" and race:
        return LONG_RACE_LAST
    if kind == "ultra" and minutes >= ULTRA_LONG_MIN:
        return ULTRA_LONG_LAST
    return EFFORT_WINDOWS[kind]


def _effort(u: _Unit, bounds=None) -> Effort | None:
    """One effort from one unit (an activity, or a chain), or None. Its class by its time (and the legs rule on
    foot), one lower when no part is on foot (M4: cycling 230 km gave « only modest » damage, Koller 1998;
    troponin about half as often after cycling, Shave 2007); its nights by its time whatever the sport. A chain
    is named and linked after its first activity. `bounds`: hr_bounds, for a Longue's heart-rate test (R3)."""
    minutes = u.minutes
    nights = _kind(minutes, u.dplus if u.foot else 0)
    if nights is None:
        return None
    kind = nights if u.foot else LOWER[nights]
    last = window_last(kind, minutes, kind == "long" and u.raced(bounds))
    return Effort(u.first.id, kind, minutes, u.end, effort_day(u.end), u.first.name, u.first.day, nights,
                  last, frozenset(s.id for s in u.sessions))


def effort_of(s: Session) -> Effort | None:
    """One activity alone as an effort (see _effort), or None (H)."""
    return _effort(_Unit([s], local_start(s), local_end(s)))


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


def efforts(sessions: list[Session], rest_of=None) -> list[Effort]:
    """The big efforts among the sessions, by end: each activity, or chain of
    activities without a real stop (< 30 min apart, H), is its own effort,
    sized from its first start to its last end (stops included), named and
    linked after its first activity; a Longue run like a race (_Unit.raced)
    and an Ultra of 24 h or more set its window (window_last); a low-impact effort is one
    class lower (M4) but its nights follow its time. Two efforts close
    together keep their own windows: the strongest of those open holds each
    day (effort_window). `rest_of(day)`: the athlete's resting HR as of a day
    (nights.rest_hr on the nights) for a Longue's heart-rate test; None: no
    such test (the night tags and Activités read only the classes)."""
    # only the units that can be an effort: 3 h and more, or the legs rule
    units = [_Unit(run, start, end) for run, start, end in _chains(sessions)
             if end - start >= timedelta(minutes=EFFORT_LONG)
             or sum(s.dplus for s in run if s.sport in FOOT) >= EFFORT_DPLUS]
    bounds = hr_bounds(sessions, rest_of) if rest_of is not None else None
    return sorted((e for u in units if (e := _effort(u, bounds))), key=lambda e: e.end)


def effort_window(efs: list[Effort], d: date) -> dict | None:
    """The window that holds day `d` (D+0 → D+last) (H), a reason of the daily state (C7; owner, 2026-10-14:
    scientific basis — it was a cap on the score): red (`tone` danger) on D+0 and D+1 after an Ultra, orange
    (warn) else; the strongest of those open: red first, then the bigger effort (its class), then the latest.
    {effort, days (d − D), ago (d − the day it started: as Activités dates it), tone, last, until, text}; None
    outside any. An effort without a class (M4, 3–6 h) opens none."""
    best = None
    for e in efs:
        k = (d - e.day).days
        if k < 0 or e.kind is None or e.last is None or k > e.last:
            continue
        tone = "danger" if e.kind == "ultra" and k <= ULTRA_RED_LAST else "warn"
        key = (tone != "danger", KINDS.index(e.kind), k)
        if best is None or key < best[0]:
            best = (key, {"effort": e, "days": k, "ago": (d - (e.start_day or e.day)).days, "tone": tone,
                          "last": e.last, "until": e.day + timedelta(days=e.last),
                          "text": f"fenêtre indicative jusqu'à J+{e.last}"})
    return best[1] if best else None


def after_ultra(efs: list[Effort], d: date) -> Effort | None:
    """The Ultra (by its class) whose D+1 → D+21 (H) holds day `d`, if any: Activités annotates a raised
    easy-pace HR « après ultra » instead of flagging it (Chambers 1998, n = 8: HR at fixed speeds higher to
    day 25)."""
    return next((e for e in reversed(efs) if e.kind == "ultra" and 1 <= (d - e.day).days <= AFTER_ULTRA_DAYS), None)
