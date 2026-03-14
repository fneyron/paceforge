import asyncio
import logging
from datetime import datetime, timezone

from celery import Task
from sqlalchemy import select

from app.celery_app import celery_app
from app.models.user import User

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    name="paceforge.process_new_activity",
)
def process_new_activity(
    self: Task,
    owner_strava_id: int,
    strava_activity_id: int,
) -> dict:
    """
    Full analysis pipeline: fetch from Strava, compute training load,
    analyze with Claude, store results. Auto-posts comment if enabled.
    """
    logger.info(
        "Processing activity %d for athlete %d (attempt %d/%d)",
        strava_activity_id,
        owner_strava_id,
        self.request.retries + 1,
        self.max_retries + 1,
    )

    try:
        result = asyncio.run(
            _run_analysis(owner_strava_id, strava_activity_id)
        )
        logger.info(
            "Analysis complete for activity %d: analysis_id=%s, comment_posted=%s",
            strava_activity_id,
            result.get("analysis_id"),
            result.get("comment_posted"),
        )
        return result
    except Exception as exc:
        logger.exception(
            "Failed to process activity %d (attempt %d)",
            strava_activity_id,
            self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


async def _run_analysis(owner_strava_id: int, strava_activity_id: int) -> dict:
    from app.database import async_session_factory
    from app.services.analysis import AnalysisOrchestrator
    from app.services.strava import StravaService

    async with async_session_factory() as db:
        try:
            orchestrator = AnalysisOrchestrator(db)
            analysis = await orchestrator.process_activity(
                owner_strava_id=owner_strava_id,
                strava_activity_id=strava_activity_id,
            )

            # Auto-post comment if user preference is enabled
            result_user = await db.execute(
                select(User).where(User.strava_athlete_id == owner_strava_id)
            )
            user = result_user.scalar_one_or_none()
            comment_posted = False

            if user and user.auto_post_comments:
                try:
                    strava = StravaService(db)
                    await strava.post_comment(
                        user, strava_activity_id, analysis.strava_comment
                    )
                    analysis.comment_posted = True
                    analysis.comment_posted_at = datetime.now(timezone.utc)
                    comment_posted = True
                    logger.info(
                        "Auto-posted comment for activity %d", strava_activity_id
                    )
                except Exception:
                    logger.exception(
                        "Auto-post comment failed for activity %d",
                        strava_activity_id,
                    )

            await db.commit()
            return {
                "analysis_id": analysis.id,
                "activity_id": analysis.activity_id,
                "status": "completed",
                "comment_posted": comment_posted,
            }
        except Exception:
            await db.rollback()
            raise
