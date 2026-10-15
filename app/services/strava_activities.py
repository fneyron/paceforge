"""Strava → Activity, one way for every sync (the first import, the hourly poll, the webhook, « Synchroniser »): the
row's fields from Strava's list or detail answer, the newest activities imported until a page is all known, and the
athlete's time zone learnt from them."""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.services.activity_sources import adopt_watch_twin
from app.services.timezones import zone_from_strava

# what only the detail answer (GET /activities/{id}) carries: the first import reads it after the list
DETAIL_FIELDS = ("splits_metric", "best_efforts", "laps", "max_watts", "weighted_average_watts", "suffer_score",
                 "calories")
# the detail's lists that have their own columns: never kept in raw_data as well (a third of its size)
COLUMN_KEYS = ("splits_metric", "best_efforts", "laps")
PER_PAGE = 30
DETAIL_BACKLOG = 5  # a poll completes this many activities saved from the list only with their detail
DETAIL_ASKED = "paceforge_detail_asked"  # in raw_data: its detail was asked once (got, or none shown): not again
# the athlete's zone: held by at least ZONE_MAJORITY of their last ZONE_SAMPLE activities that carry one
ZONE_SAMPLE, ZONE_MAJORITY = 30, 20


def parse_date(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def without_columns(data: dict) -> dict:
    """Strava's answer without what the row's own columns hold (splits, laps, best efforts)."""
    return {k: v for k, v in data.items() if k not in COLUMN_KEYS}


def activity_from_strava(data: dict, user_id: int) -> Activity:
    """The row of a Strava activity, from its list summary or its detail (what the summary lacks stays empty)."""
    return Activity(
        strava_activity_id=data["id"],
        user_id=user_id,
        sport_type=data.get("sport_type") or data.get("type") or "Workout",  # Strava's own catch-all sport
        name=data.get("name") or "Sans titre",
        start_date=parse_date(data.get("start_date")),
        distance=data.get("distance") or 0,
        moving_time=data.get("moving_time") or 0,
        elapsed_time=data.get("elapsed_time") or 0,
        total_elevation_gain=data.get("total_elevation_gain") or 0,
        average_speed=data.get("average_speed"),
        max_speed=data.get("max_speed"),
        average_heartrate=data.get("average_heartrate"),
        max_heartrate=data.get("max_heartrate"),
        average_cadence=data.get("average_cadence"),
        average_watts=data.get("average_watts"),
        **{field: data.get(field) for field in DETAIL_FIELDS},
        raw_data=without_columns(data),
    )


def apply_detail(activity: Activity, detail: dict) -> None:
    """A row saved from the list, completed by the activity's detail: a field the detail has (a field it lacks
    keeps what is there, a watch's power, calories or laps on a merged row). PaceForge's own entries in raw_data
    (« paceforge_… »: the lookups, the races' D+) are kept: asked again, they would cost requests again."""
    for field in DETAIL_FIELDS:
        if detail.get(field) is not None:
            setattr(activity, field, detail[field])
    ours = {k: v for k, v in (activity.raw_data or {}).items() if k.startswith("paceforge")}
    activity.raw_data = {**without_columns(detail), **ours}


async def is_known(db: AsyncSession, strava_id: int) -> bool:
    return bool((await db.execute(
        select(func.count(Activity.id)).where(Activity.strava_activity_id == strava_id))).scalar())


async def save_new(db: AsyncSession, user: User, data: dict) -> Activity | None:
    """A Strava activity not saved yet: its row, or the watch's row of the same outing taken over
    (activity_sources); None when it is already there."""
    if not data.get("id") or await is_known(db, data["id"]):
        return None
    return await adopt_watch_twin(db, activity_from_strava(data, user.id))


async def import_recent(db: AsyncSession, user: User, strava, max_pages: int, progress: dict | None = None) -> int:
    """The athlete's newest activities (Strava lists them by start, newest first), page after page until one is all
    known, `max_pages` at most: whatever its start, an activity uploaded late is among them. Returns how many were
    saved; `progress["saved"]` counts them as they are, for a caller to tell what a later page's failure left."""
    progress = {"saved": 0} if progress is None else progress
    progress.setdefault("saved", 0)
    for page in range(1, max_pages + 1):
        batch = await strava.get_recent_activities(user, per_page=PER_PAGE, page=page)
        new = 0
        for data in batch:
            if await save_new(db, user, data) is not None:
                new += 1
                progress["saved"] += 1
        if not new or len(batch) < PER_PAGE:
            break
    return progress["saved"]


async def complete_details(db: AsyncSession, user: User, strava, limit: int = DETAIL_BACKLOG) -> int:
    """Activities saved from Strava's list only (a first import cut short by the app's quota, the poll,
    « Synchroniser »): `limit` of them get their detail (splits, laps, best efforts, power, calories), the newest
    races and long runs first, as the races' D+ backfill; each asked once. Stops at the app's quota backoff, a 429
    or a Strava hiccup (the next poll carries on). Returns how many were completed."""
    from app.exceptions import StravaAPIError, StravaRateLimitError, StravaTokenError
    from app.services.race_calibration import LONG_EFFORT_KM

    race = Activity.raw_data["workout_type"].as_integer() == 1
    rows = (await db.execute(
        select(Activity).where(Activity.user_id == user.id, Activity.strava_activity_id.is_not(None),
                               Activity.raw_data["resource_state"].as_integer() == 2,  # a list summary
                               Activity.raw_data[DETAIL_ASKED].as_string().is_(None))
        .order_by(case((race, 0), (Activity.distance >= LONG_EFFORT_KM * 1000, 1), else_=2),
                  Activity.start_date.desc())
        .limit(limit))).scalars().all()
    done = 0
    for activity in rows:
        if strava.backing_off():
            break
        try:
            detail = await strava.get_activity(user, activity.strava_activity_id)
        except StravaTokenError:
            raise  # the poll's to handle (a refusal, or Strava's token endpoint failing)
        except StravaRateLimitError:
            break
        except StravaAPIError as exc:
            if exc.status_code not in (403, 404):
                break
            activity.raw_data = {**(activity.raw_data or {}), DETAIL_ASKED: True}  # deleted, private: none shown
            continue
        apply_detail(activity, detail)
        activity.raw_data = {**activity.raw_data, DETAIL_ASKED: True}
        done += 1
    if rows:
        await db.flush()
    return done


async def adopt_zone(db: AsyncSession, user: User) -> None:
    """users.timezone follows the athlete's Strava activities (« (GMT+01:00) Europe/Paris »), read again at each
    sync, but only on a clear majority: a zone held by at least ZONE_MAJORITY of their last ZONE_SAMPLE activities
    that carry one. A trip or a training camp never moves it, a move does (after 20 activities there); whatever set
    it before (none yet, the default, the phone) gives way to that majority only."""
    labels = (await db.execute(
        select(Activity.raw_data["timezone"].as_string())
        .where(Activity.user_id == user.id, Activity.strava_activity_id.is_not(None),
               Activity.raw_data["timezone"].as_string().is_not(None))
        .order_by(Activity.start_date.desc()).limit(2 * ZONE_SAMPLE))).scalars().all()
    zones = [z for z in map(zone_from_strava, labels) if z][:ZONE_SAMPLE]
    if not zones:
        return
    zone, held = Counter(zones).most_common(1)[0]
    if held >= ZONE_MAJORITY and zone != user.timezone:
        user.timezone = zone
