import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.celery_app import celery_app
from app.models.activity import Activity
from app.models.user import User

logger = logging.getLogger(__name__)


@celery_app.task(name="paceforge.poll_new_activities")
def poll_new_activities() -> dict:
    """Poll Strava for new activities for all active users (fallback for webhooks)."""
    logger.info("Starting activity poll for all users")
    try:
        result = asyncio.run(_run_poll())
        logger.info(
            "Poll complete: checked %d users, found %d new activities",
            result["users_checked"],
            result["new_activities"],
        )
        return result
    except Exception:
        logger.exception("Activity poll failed")
        return {"users_checked": 0, "new_activities": 0, "error": "failed"}


async def _run_poll() -> dict:
    import asyncio as aio

    from app.database import get_task_session
    from app.services.strava import StravaService

    users_checked = 0
    new_activities = 0

    async with get_task_session() as db:
        # Get all active users with valid credentials
        result = await db.execute(
            select(User).where(
                User.initial_sync_done.is_(True),
                User.strava_credentials_valid.is_(True),
                User.strava_client_id.isnot(None),
            )
        )
        users = result.scalars().all()

        for user in users:
            try:
                strava = StravaService.for_user(db, user)

                # Determine "after" timestamp
                if user.last_activity_poll_at:
                    after_epoch = int(user.last_activity_poll_at.timestamp())
                else:
                    after_epoch = int(
                        (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
                    )

                activities = await strava.get_recent_activities(user, per_page=10)

                for data in activities:
                    strava_id = data.get("id")
                    if not strava_id:
                        continue

                    # Parse start_date to check if it's after our poll window
                    start_date_str = data.get("start_date", "")
                    if isinstance(start_date_str, str) and start_date_str:
                        start_date = datetime.fromisoformat(
                            start_date_str.replace("Z", "+00:00")
                        )
                        if start_date.timestamp() < after_epoch:
                            continue

                    # Check if already in DB
                    exists = await db.execute(
                        select(func.count(Activity.id)).where(
                            Activity.strava_activity_id == strava_id
                        )
                    )
                    if exists.scalar() > 0:
                        continue

                    # New activity — save to DB
                    from datetime import datetime as _dt2
                    start_val = data.get("start_date")
                    if isinstance(start_val, str):
                        start_val = _dt2.fromisoformat(
                            start_val.replace("Z", "+00:00")
                        )
                    activity = Activity(
                        strava_activity_id=strava_id,
                        user_id=user.id,
                        sport_type=data.get("sport_type", data.get("type", "Unknown")),
                        name=data.get("name", "Untitled"),
                        start_date=start_val,
                        distance=data.get("distance", 0),
                        moving_time=data.get("moving_time", 0),
                        elapsed_time=data.get("elapsed_time", 0),
                        total_elevation_gain=data.get("total_elevation_gain", 0),
                        average_speed=data.get("average_speed"),
                        max_speed=data.get("max_speed"),
                        average_heartrate=data.get("average_heartrate"),
                        max_heartrate=data.get("max_heartrate"),
                        average_cadence=data.get("average_cadence"),
                        average_watts=data.get("average_watts"),
                        splits_metric=data.get("splits_metric"),
                        raw_data=data,
                    )
                    db.add(activity)
                    logger.info(
                        "Poll saved new activity %d for user %d: %s",
                        strava_id, user.id, activity.name,
                    )
                    new_activities += 1

                # Update poll timestamp
                user.last_activity_poll_at = datetime.now(timezone.utc)
                users_checked += 1

                # Rate limit: wait between users
                await aio.sleep(1)

            except Exception:
                logger.exception("Poll failed for user %d", user.id)
                users_checked += 1

        await db.commit()

    return {"users_checked": users_checked, "new_activities": new_activities}
