"""What Strava's webhook says, checked with Strava before anything changes (anyone can post to the webhook): a new
or edited activity is read with the athlete's token and must be theirs, a deletion must be one (Strava no longer
has it), a revocation must be one (Strava refuses the athlete's access)."""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.celery_app import celery_app
from app.config import settings
from app.exceptions import StravaAPIError, StravaRateLimitError, StravaTokenError
from app.models.activity import Activity
from app.models.user import User
from app.services import strava_state as state

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    name="paceforge.sync_activity_from_webhook",
)
def sync_activity_from_webhook(self, activity_id: int, owner_id: int, aspect_type: str = "create",
                               subscription_id: int | None = None) -> dict:
    """An activity event of Strava's webhook (create, update, delete); `subscription_id` when the web could not
    tell yet whether it is the shared app's (checked first). When it cannot be done after its retries, the athlete
    is polled again by the next round (not for an event of a subscription never confirmed)."""
    logger.info("Webhook sync: %s of activity %d for athlete %d", aspect_type, activity_id, owner_id)
    try:
        return asyncio.run(_run_webhook_sync(activity_id, owner_id, aspect_type, subscription_id))
    except Exception as exc:
        if self.request.retries < self.max_retries:
            logger.warning("Webhook sync failed for activity %d (attempt %d/%d)", activity_id,
                           self.request.retries + 1, self.max_retries + 1, exc_info=True)
            raise self.retry(exc=exc, countdown=_retry_in(exc, self.default_retry_delay))
        logger.exception("Webhook sync gave up on activity %d of athlete %d", activity_id, owner_id)
        if subscription_id is None:
            _poll_again(owner_id)
        return {"status": "failed"}


class SubscriptionUnknown(StravaAPIError):
    """Strava could not say (now) which subscription the shared app has."""

    def __init__(self) -> None:
        super().__init__("The shared app's webhook subscription could not be read", status_code=503)


def _retry_in(exc: Exception, default: int) -> int:
    if isinstance(exc, StravaRateLimitError):
        return 15 * 60  # Strava's short window turns within a quarter hour
    if isinstance(exc, SubscriptionUnknown):
        from app.services.strava import SHARED_SUB_RETRY_S

        return SHARED_SUB_RETRY_S  # a failed lookup is not tried again before
    return default


async def _confirmed(subscription_id: int | None) -> bool:
    """An event queued before its subscription was known: the shared app's, as Strava says? (An app-level
    request at most, before any athlete's; raises SubscriptionUnknown when Strava cannot say now.)"""
    if subscription_id is None:
        return True
    from app.services.strava import shared_subscription_matches

    ok = await shared_subscription_matches(subscription_id)
    if ok is None:
        raise SubscriptionUnknown()
    if ok:
        state.app_webhook_reached(settings.STRAVA_CLIENT_ID)
    else:
        logger.warning("Webhook: subscription %d is not the shared app's, event ignored", subscription_id)
    return ok


def _poll_again(owner_id: int) -> None:
    async def forget():
        from app.database import get_task_session

        async with get_task_session() as db:
            user_id = (await db.execute(select(User.id).where(User.strava_athlete_id == owner_id))).scalar()
        if user_id:
            state.webhook_forget(user_id)
    try:
        asyncio.run(forget())
    except Exception:
        logger.warning("Could not ask the round to poll athlete %d again", owner_id, exc_info=True)


async def _linked_user(db, owner_id: int) -> User | None:
    user = (await db.execute(
        select(User).where(
            User.strava_athlete_id == owner_id,
            User.strava_credentials_valid.is_(True),
            User.strava_client_id.isnot(None),
            User.strava_refresh_token.isnot(None),  # disconnected links have none
        )
    )).scalar_one_or_none()
    return user if user and user.has_strava_app else None


def _owner_of(data: dict) -> int | None:
    return (data.get("athlete") or {}).get("id")


async def _run_webhook_sync(activity_id: int, owner_id: int, aspect_type: str = "create",
                            subscription_id: int | None = None) -> dict:
    from app.database import get_task_session
    from app.services.activity_env import enrich
    from app.services.strava import StravaService, link_intact
    from app.services.strava_activities import adopt_zone, save_new

    if not await _confirmed(subscription_id):
        return {"status": "unknown_subscription"}
    async with get_task_session() as db:
        user = await _linked_user(db, owner_id)
        if not user:
            logger.warning("Webhook: no user found for athlete %d", owner_id)
            return {"status": "no_user"}
        if subscription_id is not None and user.strava_client_id != settings.STRAVA_CLIENT_ID:
            return {"status": "unknown_subscription"}  # confirmed for the shared app: not this athlete's app
        strava = StravaService.for_user(db, user)
        row = (await db.execute(select(Activity).where(Activity.strava_activity_id == activity_id))).scalar()
        if row is not None and row.user_id != user.id:
            logger.warning("Webhook: activity %d belongs to another user", activity_id)
            return {"status": "not_his"}
        try:
            if aspect_type == "delete":
                result = await _deleted(db, user, strava, row, activity_id)
            elif aspect_type == "create" and row is not None:
                logger.info("Webhook: activity %d already in DB", activity_id)
                result = {"status": "already_exists"}
            else:
                try:
                    data = await strava.get_activity(user, activity_id)
                except StravaAPIError as exc:
                    if exc.status_code not in (403, 404):
                        raise
                    logger.info("Webhook: activity %d not on Strava (%d)", activity_id, exc.status_code)
                    return {"status": "gone"}  # deleted meanwhile, or not visible: nothing to try again
                if not data:
                    logger.warning("Webhook: could not fetch activity %d from Strava", activity_id)
                    return {"status": "fetch_failed"}
                owner = _owner_of(data)
                if owner is not None and owner != user.strava_athlete_id:
                    # an event naming someone else's (public) activity: not this athlete's
                    logger.warning("Webhook: activity %d is athlete %s's, not %d's", activity_id, owner, owner_id)
                    return {"status": "not_his"}
                if row is not None:  # edited on Strava: its name and sport
                    row.name = data.get("name") or row.name
                    row.sport_type = data.get("sport_type") or data.get("type") or row.sport_type
                    result = {"status": "updated", "name": row.name}
                else:  # new (an edit of one never received is saved too)
                    activity = await save_new(db, user, data)
                    if activity is None:
                        return {"status": "already_exists"}
                    await db.flush()
                    await enrich(db, user.id)  # its altitude and weather (Open-Meteo; never fails the sync)
                    await adopt_zone(db, user)
                    result = {"status": "saved", "name": activity.name}
            user.last_activity_poll_at = datetime.now(timezone.utc)  # Réglages' « Dernière synchro »
            await db.commit()
        except StravaTokenError:
            if link_intact(user):  # Strava's token endpoint failed, nothing refused: Celery tries again
                await db.rollback()
                raise
            await db.commit()  # what the refresh found (revoked: forgotten; keys refused: flagged) is kept
            return {"status": "reconnect"}
        except Exception:
            await db.rollback()
            raise
    state.webhook_reached(user.id)
    logger.info("Webhook sync: activity %d %s for user %d", activity_id, result["status"], user.id)
    return result


async def _deleted(db, user: User, strava, row: Activity | None, activity_id: int) -> dict:
    """Deleted on Strava (it answers 404 for it): a Strava-only row goes; a watch's row of the outing stays, without
    its Strava id."""
    if row is None:
        return {"status": "unknown"}
    try:
        await strava.get_activity(user, activity_id)
        logger.warning("Webhook: activity %d is still on Strava, not deleted", activity_id)
        return {"status": "still_on_strava"}
    except StravaAPIError as exc:
        if exc.status_code != 404:
            raise
    if row.garmin_activity_id or row.coros_activity_id:
        row.strava_activity_id = None
        return {"status": "unlinked"}
    await db.execute(delete(Activity).where(Activity.id == row.id))  # its analysis goes with it (ON DELETE CASCADE)
    return {"status": "deleted"}


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    name="paceforge.strava_deauthorized",
)
def strava_deauthorized(self, owner_id: int, subscription_id: int | None = None) -> dict:
    """The athlete revoked PaceForge on Strava (webhook « authorized: false »)."""
    try:
        return asyncio.run(_run_deauthorized(owner_id, subscription_id))
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=_retry_in(exc, self.default_retry_delay))
        logger.exception("Strava revocation of athlete %d not applied", owner_id)
        return {"status": "failed"}


async def _run_deauthorized(owner_id: int, subscription_id: int | None = None) -> dict:
    """Once Strava confirms it (it refuses the athlete's access), the link is forgotten: the tokens and the athlete
    id, so the Strava account can be linked again, here or to another account. The activities already received stay,
    as when the athlete disconnects from Réglages (privacy policy: deleting the account deletes them)."""
    from app.database import get_task_session
    from app.services.strava import StravaService, _forget_link, link_intact

    if not await _confirmed(subscription_id):
        return {"status": "unknown_subscription"}
    async with get_task_session() as db:
        user = (await db.execute(select(User).where(User.strava_athlete_id == owner_id))).scalar_one_or_none()
        if user is None:
            return {"status": "no_user"}
        if subscription_id is not None and user.strava_client_id != settings.STRAVA_CLIENT_ID:
            return {"status": "unknown_subscription"}
        if user.strava_refresh_token and user.has_strava_app:
            try:
                await StravaService.for_user(db, user).get_athlete(user)
                logger.warning("Webhook: athlete %d still authorizes PaceForge, revocation ignored", owner_id)
                return {"status": "still_authorized"}
            except StravaTokenError:
                if link_intact(user):  # Strava's token endpoint failed: not a refusal, Celery asks again
                    raise
                if user.strava_refresh_token:  # the app's keys refused (flagged), not a revocation
                    await db.commit()
                    return {"status": "unverified"}
            except StravaAPIError as exc:
                if exc.status_code != 401:
                    raise
        _forget_link(user)
        user.strava_athlete_id = None
        await db.commit()
        user_id = user.id
    state.webhook_forget(user_id)
    logger.info("Strava revoked by athlete %d: link forgotten for user %d", owner_id, user_id)
    return {"status": "forgotten"}
