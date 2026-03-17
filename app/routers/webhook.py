import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.schemas.strava import StravaWebhookEvent
from app.tasks.analysis import process_new_activity

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
        logger.warning("Invalid webhook validation: mode=%s, token=%s", mode, verify_token)
        return JSONResponse(status_code=403, content={"error": "Forbidden"})

    logger.info("Strava webhook validated with token %s", verify_token[:20])
    return {"hub.challenge": challenge}


@router.post("/webhook/strava")
async def strava_webhook_event(request: Request):
    """Receive Strava webhook events. Must respond within 2 seconds."""
    body = await request.json()
    logger.info("Strava webhook event: %s", body)

    try:
        event = StravaWebhookEvent(**body)
    except Exception:
        logger.exception("Invalid webhook event payload")
        return JSONResponse(status_code=200, content={"status": "ignored"})

    if event.object_type == "activity" and event.aspect_type == "create":
        logger.info(
            "New activity %d from athlete %d — dispatching analysis",
            event.object_id,
            event.owner_id,
        )
        process_new_activity.delay(
            owner_strava_id=event.owner_id,
            strava_activity_id=event.object_id,
        )

    return JSONResponse(status_code=200, content={"status": "ok"})
