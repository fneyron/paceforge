import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models.user import User
from app.schemas.strava import StravaWebhookEvent
from app.services import strava_state
from app.services.auth import client_ip, over_quota
from app.services.strava import refresh_shared_subscription, shared_subscription_check

logger = logging.getLogger(__name__)

_starting: set[asyncio.Task] = set()  # the start's lookup, held until done


@asynccontextmanager
async def _warm_up(app):  # noqa: ARG001
    """The web's start (this router's lifespan, merged into the app's): the shared app's subscription id read from
    Strava in the background, never waited for and failing silently, so the first events after a deploy are
    checked at once and a create queued as the previous worker takes it."""
    task = asyncio.create_task(_read_shared_subscription())
    _starting.add(task)
    task.add_done_callback(_starting.discard)
    yield
    task.cancel()


async def _read_shared_subscription() -> None:
    try:
        if strava_state.claim("shared_sub:starting", 60):  # one of the web's processes asks
            await refresh_shared_subscription()
    except Exception:
        logger.debug("Strava: the shared app's subscription not read at start", exc_info=True)


router = APIRouter(tags=["webhook"], lifespan=_warm_up)


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


ACK = {"status": "ok"}  # one reply whatever the event: nothing here tells which athletes or subscriptions are known


def _event_key(event: StravaWebhookEvent) -> str:
    fields = (event.object_type, event.aspect_type, event.object_id, event.owner_id, event.subscription_id,
              event.event_time, json.dumps(event.updates, sort_keys=True, default=str))
    return hashlib.sha256("|".join(map(str, fields)).encode()).hexdigest()[:32]


@router.post("/webhook/strava")
async def strava_webhook_event(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive Strava webhook events. Must respond within 2 seconds: the work is queued. Anyone can post here, so an
    event is only queued when it comes through a subscription PaceForge knows for that athlete's app, and the task
    checks it with Strava before changing anything (tasks/webhook_sync). Every answer is the same (ACK): a probe
    learns nothing. An event seen within a day already (Strava's retries, a replay) is acknowledged, not queued
    again."""
    if await over_quota("webhook", ip=client_ip(request)):  # Strava sends a few a day; a flood is someone else
        return JSONResponse(status_code=429, content=ACK)
    try:
        event = StravaWebhookEvent(**(await request.json()))
    except Exception:
        logger.warning("Invalid webhook event payload ignored")
        return JSONResponse(status_code=200, content=ACK)
    if not strava_state.event_first_seen(_event_key(event)):
        logger.info("Strava webhook: %s %s of athlete %d seen already, ignored", event.object_type, event.aspect_type,
                    event.owner_id)
        return JSONResponse(status_code=200, content=ACK)

    kind = _kind(event)
    user, verified = await _athlete_of(db, event) if kind else (None, False)
    if user is None:
        logger.info("Strava webhook: %s %s of athlete %d ignored (subscription %d)", event.object_type,
                    event.aspect_type, event.owner_id, event.subscription_id)
        return JSONResponse(status_code=200, content=ACK)

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

    return JSONResponse(status_code=200, content=ACK)
