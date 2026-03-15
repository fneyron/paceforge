import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import Task
from sqlalchemy import func, select

from app.celery_app import celery_app
from app.models.activity import Activity
from app.models.user import User

logger = logging.getLogger(__name__)

SYNC_MONTHS = 6


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
    name="paceforge.initial_sync",
)
def initial_sync(self: Task, user_id: int) -> dict:
    """Fetch all activities from the last 6 months for a user."""
    logger.info(
        "Starting initial sync for user %d (attempt %d/%d)",
        user_id,
        self.request.retries + 1,
        self.max_retries + 1,
    )

    try:
        result = asyncio.run(_run_initial_sync(user_id))
        logger.info(
            "Initial sync complete for user %d: %d activities synced",
            user_id,
            result["synced"],
        )
        return result
    except Exception as exc:
        logger.exception(
            "Initial sync failed for user %d (attempt %d)",
            user_id,
            self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))


async def _run_initial_sync(user_id: int) -> dict:
    from app.database import get_task_session
    from app.services.strava import StravaService

    async with get_task_session() as db:
        try:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if not user:
                return {"error": "user_not_found", "synced": 0}

            strava = StravaService(db)

            # 6 months ago
            after = datetime.now(timezone.utc) - timedelta(days=SYNC_MONTHS * 30)
            after_epoch = int(after.timestamp())

            activities_data = await strava.get_all_activities_since(user, after_epoch)
            logger.info(
                "Fetched %d activities from Strava for user %d",
                len(activities_data),
                user_id,
            )

            synced = 0
            for data in activities_data:
                strava_id = data.get("id")
                if not strava_id:
                    continue

                # Check if already exists
                exists = await db.execute(
                    select(func.count(Activity.id)).where(
                        Activity.strava_activity_id == strava_id
                    )
                )
                if exists.scalar() > 0:
                    continue

                start_date = data.get("start_date")
                if isinstance(start_date, str):
                    start_date = datetime.fromisoformat(
                        start_date.replace("Z", "+00:00")
                    )

                activity = Activity(
                    strava_activity_id=strava_id,
                    user_id=user.id,
                    sport_type=data.get("sport_type", data.get("type", "Unknown")),
                    name=data.get("name", "Untitled"),
                    start_date=start_date,
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
                    raw_data=data,
                )
                db.add(activity)
                synced += 1

            # Mark initial sync as done
            user.initial_sync_done = True
            await db.commit()

            return {"user_id": user_id, "synced": synced, "total_fetched": len(activities_data)}
        except Exception:
            await db.rollback()
            raise
