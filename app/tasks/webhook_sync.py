import asyncio
import logging
from app.celery_app import celery_app
from app.models.activity import Activity
from app.models.user import User
from sqlalchemy import select, func

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    name="paceforge.sync_activity_from_webhook",
)
def sync_activity_from_webhook(self, activity_id: int, owner_id: int) -> dict:
    """Fetch and save a single activity triggered by Strava webhook."""
    logger.info("Webhook sync: activity %d for athlete %d", activity_id, owner_id)
    try:
        result = asyncio.run(_run_webhook_sync(activity_id, owner_id))
        return result
    except Exception as exc:
        logger.exception(
            "Webhook sync failed for activity %d (attempt %d/%d)",
            activity_id,
            self.request.retries + 1,
            self.max_retries + 1,
        )
        raise self.retry(exc=exc)


async def _run_webhook_sync(activity_id: int, owner_id: int) -> dict:
    from datetime import datetime
    from app.database import get_task_session
    from app.services.strava import StravaService

    async with get_task_session() as db:
        # Find user by Strava athlete ID
        result = await db.execute(
            select(User).where(
                User.strava_athlete_id == owner_id,
                User.strava_credentials_valid.is_(True),
                User.strava_client_id.isnot(None),
            )
        )
        user = result.scalar_one_or_none()
        if not user:
            logger.warning(
                "Webhook: no user found for athlete %d", owner_id
            )
            return {"status": "no_user"}

        # Check if already in DB
        exists = await db.execute(
            select(func.count(Activity.id)).where(
                Activity.strava_activity_id == activity_id
            )
        )
        if exists.scalar() > 0:
            logger.info("Webhook: activity %d already in DB", activity_id)
            return {"status": "already_exists"}

        # Fetch from Strava API
        strava = StravaService.for_user(db, user)
        data = await strava.get_activity(user, activity_id)
        if not data:
            logger.warning("Webhook: could not fetch activity %d from Strava", activity_id)
            return {"status": "fetch_failed"}

        # Parse start_date
        start_val = data.get("start_date")
        if isinstance(start_val, str):
            start_val = datetime.fromisoformat(start_val.replace("Z", "+00:00"))

        activity = Activity(
            strava_activity_id=activity_id,
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
        await db.commit()

        logger.info(
            "Webhook sync: saved activity %d (%s) for user %d",
            activity_id, activity.name, user.id,
        )
        return {"status": "saved", "name": activity.name}
