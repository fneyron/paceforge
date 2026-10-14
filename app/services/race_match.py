"""The race's activity, found by itself — one rule, no guess.

The débrief lays the activity the athlete recorded on race day over the plan. Rather than a picker,
that activity is found from the race sheet (date, start) and the course (distance):

* the race must have a date and must have started; its start is ``race_date`` at
  ``start_hour:start_minute`` (06:00 when unset) on the race's own clock — the one the plan's
  passage times, cut-offs and weather read, wherever the race is;
* candidates are the athlete's activities of the route's sport family (foot: Run, TrailRun,
  VirtualRun; bike: the rides) whose start, on the clock of where it was recorded, falls in
  [race start − 3 h, race start + 30 h] — a watch started early, a wave an hour late or a 100-miler
  running into the next day all fit. An activity's own clock is its UTC start moved by the offset
  its source stored (Strava's and COROS's ``utc_offset``, Garmin's ``startTimeLocal`` −
  ``startTimeGMT``); without one, the athlete's latest offset, else Paris;
* whose distance is within 15 % of the course's (the rule the explicit link enforces too): never
  0 km, a false start or a manual entry without distance;
* not among the ids the athlete set aside (``route.params_json["result_excluded"]``).

Several candidates → the longest (the race is the longest effort of the day); the same distance to
the metre → the one flagged as a race (Strava's ``workout_type``: 1 on foot, 10 on the bike), then the
one with splits, then the earliest upload (lowest id). None → None: no match is an honest
« not found », never the closest run of the month.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.route import Route, RouteCheckpoint

logger = logging.getLogger(__name__)

RUN_TYPES = ["Run", "TrailRun", "VirtualRun"]
BIKE_TYPES = ["Ride", "VirtualRide", "GravelRide", "EBikeRide", "MountainBikeRide"]
RACE_FLAG = {"run": 1, "bike": 10}  # Strava's workout_type for a race, per family
PARIS = ZoneInfo("Europe/Paris")
BEFORE = timedelta(hours=3)
AFTER = timedelta(hours=30)
MAX_OFFSET = timedelta(hours=14)  # no clock is further from UTC
DISTANCE_TOL = 0.15  # |activity − course| / course


def sport_types(route: Route) -> list[str]:
    return BIKE_TYPES if route.sport_type == "bike" else RUN_TYPES


def race_flag(route: Route) -> int:
    return RACE_FLAG["bike" if route.sport_type == "bike" else "run"]


def race_day(route: Route) -> date | None:
    try:
        return date.fromisoformat(str(route.race_date)[:10]) if route.race_date else None
    except ValueError:
        return None


def race_start(route: Route) -> datetime | None:
    """The start on the race's own clock (naive): the race sheet's date and time."""
    day = race_day(route)
    if day is None:
        return None
    sh = route.start_hour if route.start_hour is not None else 6
    return datetime(day.year, day.month, day.day, sh, route.start_minute or 0)


def excluded_ids(route: Route) -> set[int]:
    """The activities the athlete said were not the race's (« Ce n'est pas la bonne activité »)."""
    return {int(i) for i in (route.params_json or {}).get("result_excluded") or []}


def distance_mismatch(route: Route, activity: Activity) -> str | None:
    """Why this activity cannot be the race (no distance, or off by more than 15 %), else None."""
    act_km = float(activity.distance or 0) / 1000
    route_km = float(route.total_distance_km or 0)
    if not route_km:
        return None
    if not act_km:
        return "Cette activité n'a pas de distance : elle n'est pas associée."
    if abs(act_km - route_km) / route_km > DISTANCE_TOL:
        return f"Cette activité fait {act_km:.0f} km, la course {route_km:.0f} km : ce n'est pas le même parcours, elle n'est pas associée."
    return None


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def activity_offset(activity: Activity) -> timedelta | None:
    """The UTC offset of the clock the activity was recorded on, when its source stored it."""
    from app.services.sante_training import _float, _offset

    x = activity.raw_data or {}
    o = _offset(_float(x.get("utc_offset")), x.get("startTimeLocal"), x.get("startTimeGMT"))
    return timedelta(seconds=o) if o is not None else None


def local_start(activity: Activity, fallback: timedelta) -> datetime:
    """The activity's start on its own clock (naive)."""
    return (_aware(activity.start_date) + (activity_offset(activity) or fallback)).replace(tzinfo=None)


async def athlete_offset(db: AsyncSession, user_id: int, route: Route) -> timedelta:
    """The athlete's UTC offset (latest sessions, as Santé reads it), else Paris at the race's start."""
    from app.services.sante_training import utc_offset

    o = await utc_offset(db, user_id)
    if o is not None:
        return timedelta(seconds=o)
    start = race_start(route) or datetime.now()
    return start.replace(tzinfo=PARIS).utcoffset() or timedelta(0)


def not_run_yet(route: Route, now: datetime, offset: timedelta) -> bool:
    """True while the start has not come on the athlete's clock (``now`` is UTC)."""
    start = race_start(route)
    return start is not None and (_aware(now) + offset).replace(tzinfo=None) < start


async def find_race_activity(db: AsyncSession, user_id: int, route: Route, now: datetime | None = None,
                             offset: timedelta | None = None) -> Activity | None:
    """The race's activity by the rule above, or None. ``offset``: the athlete's, when the caller
    already read it (one query a page)."""
    start = race_start(route)
    if start is None:
        return None
    now = now or datetime.now(timezone.utc)
    if offset is None:
        offset = await athlete_offset(db, user_id, route)
    if not_run_yet(route, now, offset):
        return None
    lo, hi = start - BEFORE, start + AFTER
    # a wide UTC pre-filter (no clock is 14 h from UTC), the rule itself on each activity's own clock
    rows = (await db.execute(select(Activity).where(
        Activity.user_id == user_id, Activity.sport_type.in_(sport_types(route)),
        Activity.start_date >= (lo - MAX_OFFSET).replace(tzinfo=timezone.utc),
        Activity.start_date <= (hi + MAX_OFFSET).replace(tzinfo=timezone.utc),
    ))).scalars().all()
    out = excluded_ids(route)
    fits = [a for a in rows
            if a.id not in out and a.start_date and lo <= local_start(a, offset) <= hi and distance_mismatch(route, a) is None]
    flag = race_flag(route)

    def key(a: Activity) -> tuple:
        from app.services.sante_training import _int

        return (round(a.distance or 0), _int((a.raw_data or {}).get("workout_type")) == flag, bool(a.splits_metric), -a.id)

    return max(fits, key=key, default=None)


async def link_result(db: AsyncSession, route: Route, activity: Activity, user_id: int, profile=None) -> dict:
    """Store the activity as the race's result: the real passage time at each checkpoint, the real
    time (stops included), and the personal fatigue tilt it measures. ``profile``: the athlete's
    gradient profile when the caller already built it."""
    from app.services.debrief import race_elapsed_s
    from app.services.race_simulator import actual_passage_times

    cp_result = await db.execute(
        select(RouteCheckpoint).where(RouteCheckpoint.route_id == route.id).order_by(RouteCheckpoint.distance_km)
    )
    cps = [{"name": cp.name, "distance_km": cp.distance_km} for cp in cp_result.scalars().all()]
    if activity.splits_metric:
        actual, total_actual_s = actual_passage_times(activity.splits_metric, cps, route.total_distance_km)
    else:
        # no splits → the total only (still useful)
        actual, total_actual_s = [], int(activity.moving_time or activity.elapsed_time or 0)

    # One-shot personal fatigue calibration, on every matched checkpoint: the fresh→fade tilt whose
    # plan, run on the runner's own finish time (as the curve was fitted), best reproduces his
    # cumulative passage times. Shrunk toward the neutral tilt with few checkpoints and hard-clamped:
    # one race adjusts, never dominates. Stored with the curve it was measured against. (The first
    # checkpoint alone read +4 % at km 7 of the 2026 Transjeju and missed the +31 min at km 58 that
    # the runner then made up.)
    fatigue = {}
    if actual and total_actual_s and route.sport_type != "bike" and route.course_json:
        try:
            from app.services.race_calibration import measure_fatigue_tilt
            from app.services.race_simulator import FATIGUE_MODEL, build_athlete_gradient_profile

            if profile is None:
                profile = await build_athlete_gradient_profile(db, user_id)
            sh = route.start_hour if route.start_hour is not None else 6
            measured = measure_fatigue_tilt(route.course_json, profile, cps, actual, total_actual_s, sh, route.start_minute or 0,
                                            **await race_clock(db, user_id, route))
            if measured:
                fatigue = {"fatigue_tilt": measured["tilt"], "fatigue_tilt_raw": measured["raw"],
                           "fatigue_model": FATIGUE_MODEL, "fatigue_tilt_cps": measured["n"]}
        except Exception:
            logger.exception("fatigue tilt calibration failed")

    route.result_activity_id = activity.id
    route.result_json = {
        "activity_id": activity.id,
        "activity_name": activity.name,
        "activity_date": activity.start_date.strftime("%d/%m/%Y") if activity.start_date else "",
        "total_actual_s": total_actual_s,
        # the real time, stops included: what « Mes courses » and the débrief compare with the plan's finish
        "total_elapsed_s": race_elapsed_s(activity.splits_metric, activity.elapsed_time, activity.moving_time),
        "actual": actual,
        **fatigue,
    }
    await db.flush()
    return route.result_json


async def race_clock(db: AsyncSession, user_id: int, route: Route | None, coords: list | None = None,
                     race_date: str | None = None, weather: dict | None = None, start_hour: int | None = None,
                     start_minute: int | None = None) -> dict:
    """{race_date, utc_offset_s} for night by the sun (race_simulator.predict_course): the race date and its
    clock's UTC offset at the start (the start point's own time zone, which the weather carries, else the
    forecast's offset, else the athlete's zone when the course lies in its band, else the longitude's:
    race_simulator.race_utc_offset_s); {} without a date or a trace (the fixed 21:00-06:00 window). Reads the
    route only: no lookup, nothing written (a page renders it). ``coords``: the course's trace when the caller
    has it parsed. The page's own values, not saved yet, win when given: ``race_date`` (« » for none),
    ``weather`` ({tz, utc_offset_s, date} of the weather it fetched), ``start_hour`` / ``start_minute``."""
    from app.models.user import User
    from app.services.race_simulator import race_utc_offset_s
    from app.services.timezones import zone_of

    saved = route.weather_json if route is not None and isinstance(route.weather_json, dict) else {}
    rd = (route.race_date if route is not None else None) if race_date is None else (race_date or None)
    if coords is None:
        coords = ((route.course_json or {}).get("route_coords") if route is not None else None) or []
    if not rd or not coords or len(coords[0]) < 2:
        return {}
    tz = (await db.execute(select(User.timezone).where(User.id == user_id))).scalar_one_or_none()
    w = weather if weather is not None else saved
    wx_off = w.get("utc_offset_s") if str(w.get("date") or "")[:10] == str(rd)[:10] else None
    zone = w.get("tz") if isinstance(w.get("tz"), str) else saved.get("tz")  # a place's zone: the saved one serves too
    start_zone = zone if isinstance(zone, str) else None  # weather.get_weather_forecast
    sh = start_hour if start_hour is not None else (route.start_hour if route is not None else None)
    sm = start_minute if start_minute is not None else ((route.start_minute or 0) if route is not None else 0)
    off = race_utc_offset_s(rd, float(coords[0][1]), wx_off, zone_of(tz), start_zone=start_zone, start_hour=sh,
                            start_minute=sm or 0)
    return {"race_date": rd, "utc_offset_s": off} if off is not None else {}


async def auto_link(db: AsyncSession, user_id: int, route: Route, now: datetime | None = None,
                    offset: timedelta | None = None, profile=None) -> Activity | None:
    """Find the race's activity and link it; the activity, or None when none fits."""
    act = await find_race_activity(db, user_id, route, now=now, offset=offset)
    if act is not None:
        await link_result(db, route, act, user_id, profile=profile)
    return act
