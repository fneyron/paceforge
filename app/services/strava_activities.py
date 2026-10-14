"""Strava → Activity, one way for every sync (the first import, the hourly poll, the webhook, « Synchroniser »): the
row's fields from Strava's list or detail answer, the newest activities imported until a page is all known, and the
athlete's time zone learnt from them."""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.services.activity_sources import adopt_watch_twin
from app.services.timezones import zone_from_strava

# what only the detail answer (GET /activities/{id}) carries: the first import reads it after the list
DETAIL_FIELDS = ("splits_metric", "best_efforts", "laps", "max_watts", "weighted_average_watts", "suffer_score",
                 "calories")
PER_PAGE = 30
# the athlete's zone: held by at least ZONE_MAJORITY of their last ZONE_SAMPLE activities that carry one
ZONE_SAMPLE, ZONE_MAJORITY = 30, 20


def parse_date(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def activity_from_strava(data: dict, user_id: int) -> Activity:
    """The row of a Strava activity, from its list summary or its detail (what the summary lacks stays empty)."""
    return Activity(
        strava_activity_id=data["id"],
        user_id=user_id,
        sport_type=data.get("sport_type") or data.get("type") or "Unknown",
        name=data.get("name") or "Untitled",
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
        raw_data=data,
    )


def apply_detail(activity: Activity, detail: dict) -> None:
    """A row saved from the list, completed by the activity's detail."""
    for field in DETAIL_FIELDS:
        setattr(activity, field, detail.get(field))
    activity.raw_data = detail


async def is_known(db: AsyncSession, strava_id: int) -> bool:
    return bool((await db.execute(
        select(func.count(Activity.id)).where(Activity.strava_activity_id == strava_id))).scalar())


async def save_new(db: AsyncSession, user: User, data: dict) -> Activity | None:
    """A Strava activity not saved yet: its row, or the watch's row of the same outing taken over
    (activity_sources); None when it is already there."""
    if not data.get("id") or await is_known(db, data["id"]):
        return None
    return await adopt_watch_twin(db, activity_from_strava(data, user.id))


async def import_recent(db: AsyncSession, user: User, strava, max_pages: int) -> int:
    """The athlete's newest activities (Strava lists them by start, newest first), page after page until one is all
    known, `max_pages` at most: whatever its start, an activity uploaded late is among them. Returns how many were
    saved."""
    saved = 0
    for page in range(1, max_pages + 1):
        batch = await strava.get_recent_activities(user, per_page=PER_PAGE, page=page)
        new = 0
        for data in batch:
            if await save_new(db, user, data) is not None:
                new += 1
        saved += new
        if not new or len(batch) < PER_PAGE:
            break
    return saved


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
