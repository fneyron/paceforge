"""Where an outdoor activity ended and how hot it felt when it started (Santé
v4.4, research_data.md R2 and R4), for every user whatever brought the
activity: looked up once on Open-Meteo in the syncs and kept on the activity
(raw_data[ENV_KEY] = {"v", "alt", "feels"}), never while a page renders.

- `alt`: the ground altitude (m, Copernicus GLO-90) where the activity ended:
  Strava's end_latlng, Garmin's endLatitude/endLongitude, else where it
  started (Strava's start_latlng, Garmin's startLatitude/startLongitude,
  COROS's « Start Coordinates »). One call for up to 100 points, each point
  rounded to 3 decimals (≈ 100 m, the model's 90 m) and kept per worker, so a
  doorstep is asked once. The night after reads it (nights: « en altitude »).
- `feels`: the apparent temperature (°C) at the start point and the hour
  nearest the start, for the runs that can be easy runs (5 km and more, 25 to
  150 min: sante_training.easy_runs' gates): the forecast API with past_days
  for the last 92 days, else the ERA5 archive. One call per place (2
  decimals, ≈ 1 km) covers all its runs of those 92 days, one per place and
  month before. sante_training.is_hot reads it.
Indoor activities (a treadmill, a home trainer, a virtual ride) and those
without a position are never looked up. A timeout, a 5xx or a 429 stops the
pass and leaves the rest for the next sync; an answer without a value keeps
an empty one (asked once). Nothing here ever raises into a sync.
"""
import asyncio
import json
import logging
import time as _time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import String, case, column, func, literal, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.activity import Activity
from app.services import weather

logger = logging.getLogger(__name__)

ENV_KEY = "paceforge_env"
ENV_VERSION = 1
ENV_DAYS = 365  # the activities of the last 12 months (the easy-pace model's span)
ELEV_DECIMALS, WX_DECIMALS = 3, 2  # ≈ 100 m (the 90 m elevation model), ≈ 1 km (a weather model's cell)
ELEV_CALLS, WX_CALLS = 2, 8  # per pass: 200 points, 8 places or months
PASS_BUDGET_S = 45  # a pass never holds a sync longer
PAUSE_S = 600  # after an outage, no lookup for 10 min in this worker: the next users' syncs never wait on it
RUN_SPORTS = ("Run", "TrailRun")
RUN_KM, RUN_MIN_MIN, RUN_MAX_MIN = 5, 25, 150  # the runs that can be easy runs (sante_training.easy_runs' gates)
INDOOR_SPORTS = ("VirtualRun", "VirtualRide")
POSITION_KEYS = ("end_latlng", "start_latlng", "endLatitude", "endLongitude", "startLatitude", "startLongitude")
RAW_KEYS = ("trainer", *POSITION_KEYS, ENV_KEY)

_ELEV_CACHE: dict[tuple[float, float], float | None] = {}
_ELEV_CACHE_SIZE = 4096
_IDLE: dict[int, tuple] = {}  # user → the activities' key when a pass last found nothing to do
_paused_until = 0.0  # time.monotonic() before which Open-Meteo is left alone


# ── reading raw_data ────────────────────────────────────────────────────────

def _json(v):
    """A JSON value as the database hands it back: decoded already, or its text (SQLite, PostgreSQL ->>)."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _num(v) -> float | None:
    try:
        f = float(_json(v))
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _point(lat, lon) -> tuple[float, float] | None:
    lat, lon = _num(lat), _num(lon)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat, lon) == (0, 0):
        return None
    return lat, lon


def _pair(v) -> tuple[float, float] | None:
    v = _json(v)
    return _point(v[0], v[1]) if isinstance(v, (list, tuple)) and len(v) == 2 else None


def _end(raw: dict) -> tuple[float, float] | None:
    return _pair(raw.get("end_latlng")) or _point(raw.get("endLatitude"), raw.get("endLongitude"))


def _start(raw: dict) -> tuple[float, float] | None:
    return _pair(raw.get("start_latlng")) or _point(raw.get("startLatitude"), raw.get("startLongitude"))


def end_point(raw: dict) -> tuple[float, float] | None:
    """Where the activity ended (Strava end_latlng, Garmin endLatitude/endLongitude), else where it started
    (Strava start_latlng, which COROS's « Start Coordinates » are stored as; Garmin startLatitude/Longitude)."""
    return _end(raw) or _start(raw)


def start_point(raw: dict) -> tuple[float, float] | None:
    """Where the activity started, else where it ended."""
    return _start(raw) or _end(raw)


def located(raw: dict) -> bool:
    """The activity carries a GPS position (start or end)."""
    return end_point(raw) is not None


def is_indoor(sport: str | None, raw: dict) -> bool:
    """A treadmill, a home trainer, a virtual ride: Strava's and COROS's `trainer`, Garmin's indoor types (stored
    as `trainer` too), a virtual sport."""
    t = _json(raw.get("trainer"))
    return sport in INDOOR_SPORTS or t is True or t == 1 or (isinstance(t, str) and t.lower() == "true")


def env_of(raw: dict) -> dict:
    """The looked-up values kept on the activity ({} when none, or from an older format)."""
    env = _json(raw.get(ENV_KEY))
    return env if isinstance(env, dict) and env.get("v") == ENV_VERSION else {}


def can_be_easy_run(sport: str | None, minutes: float, km: float) -> bool:
    return sport in RUN_SPORTS and km >= RUN_KM and RUN_MIN_MIN <= minutes <= RUN_MAX_MIN


def start_hour(start: datetime) -> datetime:
    """The GMT hour nearest the start (naive)."""
    t = (start.astimezone(timezone.utc) if start.tzinfo else start).replace(tzinfo=None)
    return (t + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)


# ── the pass ────────────────────────────────────────────────────────────────

def raw_size(db: AsyncSession):
    """The stored size of raw_data, an aggregate that sees it rewritten in place (a lookup written, coordinates
    added by a re-read) without detoasting it: PostgreSQL's pg_column_size; SQLite stores JSON as text."""
    if db.get_bind().dialect.name == "postgresql":
        return func.pg_column_size(Activity.raw_data)
    return func.length(Activity.raw_data)


async def _rows(db: AsyncSession, user_id: int, since: datetime):
    """(id, start, sport, moving s, distance m, {raw key: value}) of the user's activities since `since`: one
    read of each raw_data (PostgreSQL detoasts it once, as sante_training.load_sessions)."""
    cols = (Activity.id, Activity.start_date, Activity.sport_type, Activity.moving_time, Activity.distance)
    where = (Activity.user_id == user_id, Activity.start_date >= since)
    if db.get_bind().dialect.name == "postgresql":
        obj = case((func.jsonb_typeof(Activity.raw_data) == "object", Activity.raw_data), else_=literal({}, JSONB))
        x = func.jsonb_to_record(obj).table_valued(
            *(column(k, String) for k in RAW_KEYS)).render_derived(name="raw", with_types=True)
        q = select(*cols, *(x.c[k] for k in RAW_KEYS)).select_from(Activity).join(x, true())
    else:
        q = select(*cols, *(Activity.raw_data[k].as_string() for k in RAW_KEYS))
    rows = (await db.execute(q.where(*where).order_by(Activity.start_date.desc()))).all()
    return [(r[0], r[1], r[2], r[3] or 0, r[4] or 0, dict(zip(RAW_KEYS, r[5:], strict=False))) for r in rows]


async def _key(db: AsyncSession, user_id: int, since: datetime) -> tuple:
    """What a sync changes when it adds activities, hands one to another service or rewrites one (no JSON
    read)."""
    return tuple((await db.execute(select(
        func.count(Activity.id), func.max(Activity.id), func.count(Activity.strava_activity_id),
        func.count(Activity.garmin_activity_id), func.count(Activity.coros_activity_id), func.sum(raw_size(db)))
        .where(Activity.user_id == user_id, Activity.start_date >= since))).one())


def _todo(rows) -> tuple[dict, dict]:
    """{id: point} still without an altitude, {id: (point, start hour)} runs still without a feels-like."""
    alt, wx = {}, {}
    for aid, start, sport, moving, dist, raw in rows:
        if is_indoor(sport, raw):
            continue
        env = env_of(raw)
        if "alt" not in env and (p := end_point(raw)):
            alt[aid] = p
        if "feels" not in env and can_be_easy_run(sport, moving / 60, dist / 1000) and (p := start_point(raw)):
            wx[aid] = (p, start_hour(start))
    return alt, wx


async def _altitudes(points: dict[int, tuple[float, float]], deadline: float, values: dict) -> None:
    """values[id]["alt"] for the points looked up (cached per rounded point), newest first, ELEV_CALLS at most."""
    key = {aid: (round(lat, ELEV_DECIMALS), round(lon, ELEV_DECIMALS)) for aid, (lat, lon) in points.items()}
    missing = list(dict.fromkeys(k for k in key.values() if k not in _ELEV_CACHE))
    try:
        for k in range(ELEV_CALLS):
            batch = missing[k * weather.ELEVATION_POINTS:(k + 1) * weather.ELEVATION_POINTS]
            if not batch or _time.monotonic() > deadline:
                break
            for pt, v in zip(batch, await weather.elevations(batch), strict=True):
                if len(_ELEV_CACHE) >= _ELEV_CACHE_SIZE:
                    _ELEV_CACHE.pop(next(iter(_ELEV_CACHE)))
                _ELEV_CACHE[pt] = v
    finally:  # what came before an outage is kept
        for aid, k in key.items():
            if k in _ELEV_CACHE:
                values[aid]["alt"] = _ELEV_CACHE[k]


async def _feels(runs: dict[int, tuple], today: date, deadline: float, values: dict) -> None:
    """values[id]["feels"]: the apparent temperature at the start hour of the runs looked up, one call per place
    for the last 92 days, one per place and month before; WX_CALLS at most, newest first."""
    groups: dict[tuple, list[int]] = defaultdict(list)
    for aid, ((lat, lon), hour) in runs.items():
        recent = (today - hour.date()).days < weather.PAST_DAYS
        place = (round(lat, WX_DECIMALS), round(lon, WX_DECIMALS))
        groups[(place, None if recent else (hour.year, hour.month))].append(aid)
    for n, ((place, _), ids) in enumerate(groups.items()):
        if n >= WX_CALLS or _time.monotonic() > deadline:
            break
        days = [runs[i][1].date() for i in ids]
        series = await weather.feels_like(place[0], place[1], min(days), max(days), today)
        for i in ids:
            values[i]["feels"] = series.get(runs[i][1].strftime("%Y-%m-%dT%H:00"))


async def _write(db: AsyncSession, values: dict[int, dict]) -> None:
    """Each activity's looked-up values onto its raw_data (reassigned: the JSON column is saved)."""
    for act in (await db.execute(select(Activity).where(Activity.id.in_(list(values))))).scalars():
        raw = act.raw_data if isinstance(act.raw_data, dict) else {}
        act.raw_data = {**raw, ENV_KEY: {**env_of(raw), **values[act.id], "v": ENV_VERSION}}
    await db.flush()


async def _pass(db: AsyncSession, user_id: int, now: datetime) -> dict:
    global _paused_until
    if _time.monotonic() < _paused_until:
        return {}
    since = now - timedelta(days=ENV_DAYS)
    key = await _key(db, user_id, since)
    if _IDLE.get(user_id) == key:
        return {"alt": 0, "feels": 0, "left": 0}
    rows = await _rows(db, user_id, since)
    alt_todo, wx_todo = _todo(rows)
    if not alt_todo and not wx_todo:
        _IDLE[user_id] = key
        return {"alt": 0, "feels": 0, "left": 0}
    _IDLE.pop(user_id, None)
    deadline = _time.monotonic() + PASS_BUDGET_S
    values: dict[int, dict] = defaultdict(dict)
    try:
        await _altitudes(alt_todo, deadline, values)
        await _feels(wx_todo, now.date(), deadline, values)
    except weather.LookupUnavailable as e:  # Open-Meteo is down or slow: keep what came, the rest next time
        logger.info("Open-Meteo unavailable for user %d: %s", user_id, e)
        _paused_until = _time.monotonic() + PAUSE_S
    if values:
        await _write(db, values)
    done_alt = sum(1 for v in values.values() if "alt" in v)
    done_wx = sum(1 for v in values.values() if "feels" in v)
    out = {"alt": done_alt, "feels": done_wx, "left": len(alt_todo) - done_alt + len(wx_todo) - done_wx}
    logger.info("Activity lookups for user %d: %s", user_id, out)
    return out


async def enrich(db: AsyncSession, user_id: int, now: datetime | None = None) -> dict:
    """One pass over the user's activities of the last 12 months still without their altitude or their
    feels-like, in the sync (its own savepoint): {alt, feels, left}. Never raises (but a cancellation): a
    failed lookup leaves the night untagged and the run on its device temperature until a later sync."""
    if not settings.ACTIVITY_ENV_FETCH:
        return {}
    try:
        async with db.begin_nested():
            return await _pass(db, user_id, now or datetime.now(timezone.utc))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Activity lookups failed for user %d", user_id, exc_info=True)
        return {}
