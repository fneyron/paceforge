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
    from app.tasks.analysis import process_new_activity

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

                    # New activity found — dispatch analysis
                    logger.info(
                        "Poll found new activity %d for user %d",
                        strava_id, user.id,
                    )
                    process_new_activity.delay(
                        owner_strava_id=user.strava_athlete_id,
                        strava_activity_id=strava_id,
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
