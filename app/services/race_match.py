"""The race's Strava activity, found by itself — one rule, no guess.

The débrief lays the activity the athlete recorded on race day over the plan. Rather than a picker,
that activity is found from the race sheet (date, start) and the course (distance):

* the race must have a date and must not be ahead; its start is ``race_date`` at
  ``start_hour:start_minute`` (06:00 when unset), read in Europe/Paris, the app's clock (Strava's
  ``start_date`` is UTC);
* candidates are the athlete's activities of the route's sport family (foot: Run, TrailRun,
  VirtualRun; bike: the rides) whose start falls in [race start − 3 h, race start + 30 h] — a watch
  started early, a wave an hour late or a 100-miler running into the next day all fit;
* whose distance is within 15 % of the course's (the rule the explicit link enforces too);
* not among the ids the athlete set aside (``route.params_json["result_excluded"]``).

Several candidates → the longest (the race is the longest effort of the day); the same distance to
the metre → the one Strava flags as a race (``workout_type`` 1), then the one with splits. None →
None: no match is an honest « not found », never the closest run of the month.
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
PARIS = ZoneInfo("Europe/Paris")
BEFORE = timedelta(hours=3)
AFTER = timedelta(hours=30)
DISTANCE_TOL = 0.15  # |activity − course| / course


def sport_types(route: Route) -> list[str]:
    return BIKE_TYPES if route.sport_type == "bike" else RUN_TYPES


def race_day(route: Route) -> date | None:
    try:
        return date.fromisoformat(str(route.race_date)[:10]) if route.race_date else None
    except ValueError:
        return None


def race_start(route: Route) -> datetime | None:
    """The start as a UTC instant: the race sheet's date and time, read in Europe/Paris."""
    day = race_day(route)
    if day is None:
        return None
    sh = route.start_hour if route.start_hour is not None else 6
    sm = route.start_minute or 0
    return datetime(day.year, day.month, day.day, sh, sm, tzinfo=PARIS).astimezone(timezone.utc)


def excluded_ids(route: Route) -> set[int]:
    """The activities the athlete said were not the race's (« Ce n'est pas la bonne activité »)."""
    return {int(i) for i in (route.params_json or {}).get("result_excluded") or []}


def distance_mismatch(route: Route, activity: Activity) -> str | None:
    """Why this activity cannot be the race (its distance is off by more than 15 %), else None."""
    act_km = float(activity.distance or 0) / 1000
    route_km = float(route.total_distance_km or 0)
    if route_km and act_km and abs(act_km - route_km) / route_km > DISTANCE_TOL:
        return f"Cette activité fait {act_km:.0f} km, la course {route_km:.0f} km : ce n'est pas le même parcours, elle n'est pas associée."
    return None


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def find_race_activity(db: AsyncSession, user_id: int, route: Route, today: date | None = None) -> Activity | None:
    """The race's activity by the rule above, or None."""
    start = race_start(route)
    if start is None or race_day(route) > (today or date.today()):
        return None
    lo, hi = start - BEFORE, start + AFTER
    rows = (await db.execute(select(Activity).where(
        Activity.user_id == user_id, Activity.sport_type.in_(sport_types(route)),
        Activity.start_date >= lo, Activity.start_date <= hi,
    ))).scalars().all()
    out = excluded_ids(route)
    fits = [a for a in rows
            if a.id not in out and a.start_date and lo <= _aware(a.start_date) <= hi and distance_mismatch(route, a) is None]
    return max(fits, key=lambda a: (round(a.distance or 0), (a.raw_data or {}).get("workout_type") == 1, bool(a.splits_metric)), default=None)


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
            measured = measure_fatigue_tilt(route.course_json, profile, cps, actual, total_actual_s, sh, route.start_minute or 0)
            if measured:
                fatigue = {"fatigue_tilt": measured["tilt"], "fatigue_model": FATIGUE_MODEL, "fatigue_tilt_cps": measured["n"]}
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


async def auto_link(db: AsyncSession, user_id: int, route: Route) -> Activity | None:
    """Find the race's activity and link it; the activity, or None when none fits."""
    act = await find_race_activity(db, user_id, route)
    if act is not None:
        await link_result(db, route, act, user_id)
    return act
