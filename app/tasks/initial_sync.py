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
            "Initial sync complete for user %d: %d activities synced, %d enriched",
            user_id,
            result["synced"],
            result.get("enriched", 0),
        )
        return result
    except Exception as exc:
        logger.exception(
            "Initial sync failed for user %d (attempt %d)",
            user_id,
            self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))


def _parse_strava_date(date_str):
    if isinstance(date_str, str):
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    return date_str


def _build_activity_from_data(data: dict, user_id: int) -> Activity:
    """Build an Activity model from Strava API data (list or detail)."""
    return Activity(
        strava_activity_id=data["id"],
        user_id=user_id,
        sport_type=data.get("sport_type", data.get("type", "Unknown")),
        name=data.get("name", "Untitled"),
        start_date=_parse_strava_date(data.get("start_date")),
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
        max_watts=data.get("max_watts"),
        weighted_average_watts=data.get("weighted_average_watts"),
        suffer_score=data.get("suffer_score"),
        calories=data.get("calories"),
        laps=data.get("laps"),
        splits_metric=data.get("splits_metric"),
        best_efforts=data.get("best_efforts"),
        raw_data=data,
    )


async def _run_initial_sync(user_id: int) -> dict:
    from app.database import get_task_session
    from app.services.strava import StravaService

    async with get_task_session() as db:
        try:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if not user:
                return {"error": "user_not_found", "synced": 0}

            strava = StravaService.for_user(db, user)

            # 6 months ago
            after = datetime.now(timezone.utc) - timedelta(days=SYNC_MONTHS * 30)
            after_epoch = int(after.timestamp())

            activities_data = await strava.get_all_activities_since(user, after_epoch)
            logger.info(
                "Fetched %d activities from Strava for user %d",
                len(activities_data),
                user_id,
            )

            # Phase 1: Insert activities from list endpoint
            synced = 0
            new_strava_ids = []
            for data in activities_data:
                strava_id = data.get("id")
                if not strava_id:
                    continue

                exists = await db.execute(
                    select(func.count(Activity.id)).where(
                        Activity.strava_activity_id == strava_id
                    )
                )
                if exists.scalar() > 0:
                    continue

                activity = _build_activity_from_data(data, user.id)
                db.add(activity)
                new_strava_ids.append(strava_id)
                synced += 1

            await db.flush()
            logger.info("Inserted %d new activities for user %d", synced, user_id)

            # Phase 2: Enrich with detailed data (splits, laps, best_efforts)
            enriched = 0
            for strava_id in new_strava_ids:
                try:
                    detail = await strava.get_activity(user, strava_id)

                    act_result = await db.execute(
                        select(Activity).where(
                            Activity.strava_activity_id == strava_id
                        )
                    )
                    activity = act_result.scalar_one_or_none()
                    if not activity:
                        continue

                    # Update with detailed fields
                    activity.splits_metric = detail.get("splits_metric")
                    activity.best_efforts = detail.get("best_efforts")
                    activity.laps = detail.get("laps")
                    activity.max_watts = detail.get("max_watts")
                    activity.weighted_average_watts = detail.get("weighted_average_watts")
                    activity.suffer_score = detail.get("suffer_score")
                    activity.calories = detail.get("calories")
                    activity.raw_data = detail
                    enriched += 1

                    # Commit every 10 to avoid losing progress
                    if enriched % 10 == 0:
                        await db.flush()
                        logger.info(
                            "Enriched %d/%d activities for user %d",
                            enriched, len(new_strava_ids), user_id,
                        )
                except Exception:
                    logger.warning(
                        "Failed to enrich activity %d for user %d",
                        strava_id, user_id,
                    )

            # Mark initial sync as done
            user.initial_sync_done = True
            await db.commit()

            return {
                "user_id": user_id,
                "synced": synced,
                "enriched": enriched,
                "total_fetched": len(activities_data),
            }
        except Exception:
            await db.rollback()
            raise
