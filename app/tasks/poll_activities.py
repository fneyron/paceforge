"""The hourly Strava round, the webhook's fallback (the webhook brings each activity as it is uploaded), and each
athlete's poll, in a task and a transaction of its own."""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.celery_app import celery_app
from app.exceptions import StravaTokenError
from app.models.user import User
from app.services import strava_state as state

logger = logging.getLogger(__name__)

POLL_PAGES = 3  # 90 activities: what an athlete uploads between two rounds, a late upload included
POLL_CLAIM_S = 2 * 3600  # an athlete's poll queued or running is not queued again (a lost one is, after this)


@celery_app.task(name="paceforge.poll_new_activities")
def poll_new_activities() -> dict:
    """The hourly round: queues the poll of each athlete the webhook has not reached for 24 h (and of every one
    not polled for 24 h: an event may be lost), and the first imports due again after a failure."""
    try:
        result = asyncio.run(_run_poll())
        logger.info("Strava round: %d polls and %d first imports queued",
                    result["users_checked"], result["initial_requeued"])
        return result
    except Exception:
        logger.exception("Strava round failed")
        return {"users_checked": 0, "initial_requeued": 0, "error": "failed"}


async def _run_poll() -> dict:
    """Linked athletes only (a disconnected link has no refresh token). The app's quota first: past 80 % of a
    Strava window, nobody of that app is polled until it turns (StravaService._note_quota)."""
    from app.database import get_task_session
    from app.services.strava import backing_off, refresh_shared_subscription
    from app.tasks.initial_sync import initial_sync

    try:  # the shared app's webhook subscription id, for the webhook to check its events at once
        await refresh_shared_subscription()
    except Exception:
        logger.warning("Strava: the shared app's subscription was not refreshed", exc_info=True)
    async with get_task_session() as db:
        users = (await db.execute(select(User).where(
            User.strava_credentials_valid.is_(True),
            User.strava_client_id.isnot(None),
            User.strava_refresh_token.isnot(None),  # disconnected links have none
        ))).scalars().all()

    queued = requeued = 0
    for user in users:
        if not user.has_strava_app or backing_off(user.strava_client_id):
            continue
        if not user.initial_sync_done:
            # a first import that failed (it stays undone): again once its wait is over
            if state.initial_due(user.id):
                try:
                    initial_sync.delay(user.id)
                    requeued += 1
                except Exception:
                    logger.exception("Initial sync could not be queued again for user %d", user.id)
            continue
        if state.webhook_recent(user.id) and state.polled_recently(user.id):
            continue  # the webhook brings their activities; polled once a day all the same (an event may be lost)
        if not state.claim(f"poll:{user.id}", POLL_CLAIM_S):
            continue
        try:
            poll_strava_user.delay(user.id)
            queued += 1
        except Exception:
            state.release(f"poll:{user.id}")
            logger.exception("Strava poll could not be queued for user %d", user.id)
    return {"users_checked": queued, "initial_requeued": requeued}


@celery_app.task(name="paceforge.poll_strava_user", acks_late=True)
def poll_strava_user(user_id: int) -> dict:
    try:
        return asyncio.run(_poll_user(user_id))
    except Exception:
        logger.exception("Strava poll failed for user %d", user_id)
        return {"user_id": user_id, "error": "failed"}
    finally:
        state.release(f"poll:{user_id}")


async def _poll_user(user_id: int) -> dict:
    """One athlete's poll, in its own transaction (a failure rolls back this athlete's import only): their newest
    activities until a page is all known, whatever their start (one is often uploaded hours after it started), a few
    races' D+, the activities' lookups; `last_activity_poll_at` says when (Réglages' « Dernière synchro »)."""
    from app.database import get_task_session
    from app.services.activity_env import enrich
    from app.services.race_calibration import backfill_race_dplus
    from app.services.strava import StravaService, link_intact
    from app.services.strava_activities import adopt_zone, import_recent

    async with get_task_session() as db:
        user = await db.get(User, user_id)
        if not (user and user.initial_sync_done and user.strava_credentials_valid and user.strava_refresh_token
                and user.has_strava_app):
            return {"user_id": user_id, "status": "skipped"}
        strava = StravaService.for_user(db, user)
        if strava.backing_off():
            return {"user_id": user_id, "status": "backoff"}
        try:
            new = await import_recent(db, user, strava, POLL_PAGES)
            await db.flush()
            if not strava.backing_off():
                # the races' D+ from their altitude stream, a few per poll (new races, and the backlog)
                try:
                    await backfill_race_dplus(db, user, strava, limit=3)
                except StravaTokenError:
                    raise
                except Exception:
                    logger.warning("Race D+ backfill failed for user %d", user.id, exc_info=True)
            # each new outdoor activity's altitude and weather (Open-Meteo), the backlog a few at a time
            await db.flush()
            await enrich(db, user.id)
            await adopt_zone(db, user)
            user.last_activity_poll_at = datetime.now(timezone.utc)
            await db.commit()
            state.polled(user_id)
        except StravaTokenError:
            if link_intact(user):  # Strava's token endpoint failed, nothing refused: the next round tries again
                await db.rollback()
                raise
            await db.commit()  # what the refresh found (revoked: forgotten; keys refused: flagged) is kept
            return {"user_id": user_id, "status": "reconnect"}
        except Exception:
            await db.rollback()
            raise
    if new:
        logger.info("Strava poll saved %d new activities for user %d", new, user_id)
    return {"user_id": user_id, "new_activities": new}
