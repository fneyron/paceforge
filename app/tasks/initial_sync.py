import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from app.celery_app import celery_app
from app.exceptions import StravaRateLimitError, StravaTokenError
from app.models.user import User
from app.services import strava_state as state
from app.services.strava_activities import adopt_zone, apply_detail, save_new

logger = logging.getLogger(__name__)

SYNC_MONTHS = 24


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
    name="paceforge.initial_sync",
)
def initial_sync(self: Task, user_id: int) -> dict:
    """The athlete's first import (the last 24 months), one at a time per athlete. When it fails (Strava's quota,
    an error after its retries), the failure is kept for Réglages' status (strava_status) and the hourly round
    queues it again after 1 h, 2 h, 4 h… (24 h at most)."""
    if not state.claim(state.initial_lock(user_id), state.INITIAL_RUN_S):
        logger.info("Initial sync for user %d already running", user_id)
        return {"user_id": user_id, "status": "already_running", "synced": 0}
    logger.info(
        "Starting initial sync for user %d (attempt %d/%d)",
        user_id,
        self.request.retries + 1,
        self.max_retries + 1,
    )
    try:
        result = asyncio.run(_run_initial_sync(user_id))
    except StravaRateLimitError:
        failed = state.initial_failed(user_id, "rate_limited")
        logger.warning("Initial sync for user %d: Strava quota, queued again by the round (failure %d)",
                       user_id, failed["failures"])
        return {"user_id": user_id, "error": "rate_limited", "synced": 0}
    except Exception as exc:
        if self.request.retries < self.max_retries:
            logger.exception("Initial sync failed for user %d (attempt %d)", user_id, self.request.retries + 1)
            raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))
        failed = state.initial_failed(user_id, "failed")
        logger.exception("Initial sync failed for user %d, queued again by the round (failure %d)",
                         user_id, failed["failures"])
        return {"user_id": user_id, "error": "failed", "synced": 0}
    finally:
        state.release(state.initial_lock(user_id))
    if not result.get("error"):
        state.initial_clear(user_id)
        state.polled(user_id)
    logger.info(
        "Initial sync complete for user %d: %d activities synced, %d enriched",
        user_id,
        result["synced"],
        result.get("enriched", 0),
    )
    return result


async def _run_initial_sync(user_id: int) -> dict:
    from app.database import get_task_session
    from app.services.strava import StravaService, link_intact

    async with get_task_session() as db:
        try:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if not user:
                return {"error": "user_not_found", "synced": 0}
            if user.initial_sync_done:  # a copy the round queued meanwhile
                return {"user_id": user_id, "status": "already_done", "synced": 0}

            if not user.has_strava_linked or not user.has_strava_app or not user.strava_credentials_valid:
                return {"error": "strava_reconnect_required", "synced": 0}
            strava = StravaService.for_user(db, user)

            after = datetime.now(timezone.utc) - timedelta(days=SYNC_MONTHS * 30)
            after_epoch = int(after.timestamp())

            activities_data = await strava.get_all_activities_since(user, after_epoch)
            logger.info(
                "Fetched %d activities from Strava for user %d",
                len(activities_data),
                user_id,
            )

            # Phase 1: Insert activities from list endpoint
            new_rows = {}
            for data in activities_data:
                activity = await save_new(db, user, data)
                if activity is not None:
                    new_rows[data["id"]] = activity
            synced = len(new_rows)

            await db.flush()
            logger.info("Inserted %d new activities for user %d", synced, user_id)

            # Phase 2: Enrich with detailed data (splits, laps, best_efforts), a request each; stops when the
            # app's quota nears its limit (those rows keep the list's data)
            enriched = 0
            for strava_id, activity in new_rows.items():
                if strava.backing_off():
                    logger.info("Initial sync for user %d: Strava quota, %d of %d enriched",
                                user_id, enriched, synced)
                    break
                try:
                    detail = await strava.get_activity(user, strava_id)
                except (StravaTokenError, SoftTimeLimitExceeded):  # the worker's limit is the task's, not this one's
                    raise
                except StravaRateLimitError:
                    break
                except Exception:
                    logger.warning(
                        "Failed to enrich activity %d for user %d",
                        strava_id, user_id,
                    )
                    continue
                apply_detail(activity, detail)
                enriched += 1

                # Commit every 10 to avoid losing progress
                if enriched % 10 == 0:
                    await db.flush()
                    logger.info(
                        "Enriched %d/%d activities for user %d",
                        enriched, synced, user_id,
                    )

            # Phase 3: the races' D+ from their altitude stream (one request
            # per race; what a rate limit leaves is picked up by the poll)
            await db.flush()
            if not strava.backing_off():
                try:
                    from app.services.race_calibration import backfill_race_dplus

                    await backfill_race_dplus(db, user, strava, limit=40)
                except SoftTimeLimitExceeded:
                    raise
                except Exception:
                    logger.warning("Race D+ backfill failed for user %d", user_id, exc_info=True)

            # Phase 4: the activities' altitude and weather (Open-Meteo), the first ones; the poll does the rest
            from app.services.activity_env import enrich

            await db.flush()
            await enrich(db, user.id)
            await adopt_zone(db, user)

            # Mark initial sync as done
            user.initial_sync_done = True
            user.last_activity_poll_at = datetime.now(timezone.utc)  # Réglages' « Dernière synchro »
            await db.commit()

            return {
                "user_id": user_id,
                "synced": synced,
                "enriched": enriched,
                "total_fetched": len(activities_data),
            }
        except StravaTokenError:
            if link_intact(user):  # Strava's token endpoint failed, nothing refused: tried again
                await db.rollback()
                raise
            # what the refresh found is kept (the link revoked: forgotten; the app's keys refused: flagged), with
            # what was imported: Réglages asks to reconnect, the rounds leave the athlete alone meanwhile
            await db.commit()
            return {"error": "strava_reconnect_required", "synced": 0}
        except Exception:
            await db.rollback()
            raise
