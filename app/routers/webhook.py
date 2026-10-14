import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models.user import User
from app.schemas.strava import StravaWebhookEvent
from app.services import strava_state
from app.services.strava import shared_subscription_check

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])


def _is_valid_verify_token(token: str) -> bool:
    """Check verify token against static config or dynamic Redis tokens."""
    # Static fallback (dev/testing)
    if token == settings.STRAVA_WEBHOOK_VERIFY_TOKEN:
        return True

    # Dynamic per-user tokens stored in Redis during subscription creation
    try:
        import redis as redis_lib
        r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        key = f"strava_webhook_verify:{token}"
        result = r.get(key)
        if result:
            r.delete(key)  # One-time use
            r.close()
            return True
        r.close()
    except Exception:
        logger.exception("Failed to check webhook verify token in Redis")

    return False


@router.get("/webhook/strava")
async def strava_webhook_validation(
    mode: str = Query(alias="hub.mode"),
    challenge: str = Query(alias="hub.challenge"),
    verify_token: str = Query(alias="hub.verify_token"),
):
    """Strava webhook subscription validation."""
    if mode != "subscribe" or not _is_valid_verify_token(verify_token):
        logger.warning("Invalid webhook validation (mode=%s)", mode)  # never the token: it is a secret
        return JSONResponse(status_code=403, content={"error": "Forbidden"})

    logger.info("Strava webhook validated")
    return {"hub.challenge": challenge}


def _kind(event: StravaWebhookEvent) -> str | None:
    if event.object_type == "activity" and event.aspect_type in ("create", "update", "delete"):
        return event.aspect_type
    if event.object_type == "athlete" and str(event.updates.get("authorized", "")).lower() == "false":
        return "deauthorize"
    return None


async def _athlete_of(db: AsyncSession, event: StravaWebhookEvent) -> tuple[User | None, bool]:
    """The PaceForge user of the event's athlete, when the event comes through a subscription PaceForge knows for
    that athlete's app, and whether it is known yet: the athlete's own app's (users.strava_webhook_subscription_id,
    also the shared app's when it was an athlete's own app first), or the shared app's (STRAVA_SUBSCRIPTION_ID, else
    as Strava last gave it). A shared app's id not known yet, or another one: (user, False), its task asks Strava
    first (strava.shared_subscription_matches). (None, False) otherwise."""
    user = (await db.execute(select(User).where(User.strava_athlete_id == event.owner_id))).scalar_one_or_none()
    if user is None or not user.strava_client_id:
        return None, False
    known = await db.scalar(select(func.count(User.id)).where(
        User.strava_client_id == user.strava_client_id, User.strava_webhook_subscription_id == event.subscription_id))
    if known:
        return user, True
    if not (settings.strava_shared_ready and user.strava_client_id == settings.STRAVA_CLIENT_ID):
        return None, False
    check = shared_subscription_check(event.subscription_id)
    return (user, bool(check)) if check is not False else (None, False)


@router.post("/webhook/strava")
async def strava_webhook_event(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive Strava webhook events. Must respond within 2 seconds: the work is queued. Anyone can post here, so an
    event is only queued when it comes through a subscription PaceForge knows for that athlete's app, and the task
    checks it with Strava before changing anything (tasks/webhook_sync)."""
    try:
        event = StravaWebhookEvent(**(await request.json()))
    except Exception:
        logger.warning("Invalid webhook event payload ignored")
        return JSONResponse(status_code=200, content={"status": "ignored"})

    kind = _kind(event)
    user, verified = await _athlete_of(db, event) if kind else (None, False)
    if user is None:
        logger.info("Strava webhook: %s %s of athlete %d ignored (subscription %d)", event.object_type,
                    event.aspect_type, event.owner_id, event.subscription_id)
        return JSONResponse(status_code=200, content={"status": "ignored"})

    logger.info("Strava webhook: %s %d for user %d, enqueuing", kind, event.object_id, user.id)
    if verified:  # the app's webhook works: its quiet athletes need a daily poll only
        strava_state.app_webhook_reached(user.strava_client_id)
    # the shared app's subscription not known yet: the task checks it with Strava before anything else
    check = {} if verified else {"subscription_id": event.subscription_id}
    # Enqueue async, but never let a broker hiccup break the webhook ACK —
    # Strava expects a fast 200 or it retries/disables the subscription.
    try:
        from app.tasks.webhook_sync import strava_deauthorized, sync_activity_from_webhook

        if kind == "deauthorize":
            strava_deauthorized.delay(event.owner_id, **check)
        elif kind == "create":  # the previous worker's message (a deploy starts the new web first)
            sync_activity_from_webhook.delay(event.object_id, event.owner_id, **check)
        else:
            sync_activity_from_webhook.delay(event.object_id, event.owner_id, kind, **check)
    except Exception:
        logger.exception("Failed to enqueue webhook sync (broker unavailable?)")

    return JSONResponse(status_code=200, content={"status": "ok"})
