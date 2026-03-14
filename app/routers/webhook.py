import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.schemas.strava import StravaWebhookEvent
from app.tasks.analysis import process_new_activity

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])


@router.get("/webhook/strava")
async def strava_webhook_validation(
    mode: str = Query(alias="hub.mode"),
    challenge: str = Query(alias="hub.challenge"),
    verify_token: str = Query(alias="hub.verify_token"),
):
    """Strava webhook subscription validation."""
    if mode != "subscribe" or verify_token != settings.STRAVA_WEBHOOK_VERIFY_TOKEN:
        logger.warning("Invalid webhook validation: mode=%s, token=%s", mode, verify_token)
        return JSONResponse(status_code=403, content={"error": "Forbidden"})

    logger.info("Strava webhook validated")
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
