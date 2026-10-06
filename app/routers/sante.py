"""Santé: recovery, training load, trends and fitness from the athlete's COROS or Garmin watch.

Syncing on demand lives here only (one button for every linked watch);
Réglages manage the links (status, last sync, errors, disconnect).
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.services import coros, garmin
from app.services.sante import health_page

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["sante"])


@router.get("/sante", response_class=HTMLResponse)
async def sante_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    status = await coros.coros_status(db, user.id)
    garmin_link = await garmin.garmin_status(db, user.id)
    try:
        page = await health_page(db, user.id)
    except Exception:  # shown as an error, never as "connect your watch"
        logger.exception("Santé page failed for user %d", user.id)
        page = None
    night_missing = bool(page and page["nights"]["rows"][0]["pending"])
    auto_sync = _sync_stale(user.id, ((coros, status), (garmin, garmin_link)),
                            STALE_NIGHT if night_missing else STALE_AFTER)
    return templates.TemplateResponse(
        request, "sante.html",
        context={"user": user, "coros": status, "garmin": garmin_link, "page": page, "auto_sync": auto_sync},
    )


_WATCHES = (("COROS", coros), ("Garmin", garmin))
STALE_AFTER = timedelta(hours=1)
STALE_NIGHT = timedelta(minutes=10)  # last night still missing: it may have just been uploaded
JUST_SYNCED = timedelta(minutes=3)


def _fresh_at(link: dict, within: timedelta) -> bool:
    at = link.get("last_sync_at")
    if at is None:
        return False
    at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at
    return datetime.now(timezone.utc) - at < within


def _sync_stale(user_id: int, links, stale_after: timedelta) -> bool:
    """Opening Santé syncs, in the background, every watch not synced within
    `stale_after` (not one that failed last time: « Synchroniser maintenant »
    says why). True when a sync is running, so the page waits for it."""
    running = False
    for service, link in links:
        if not link.get("connected") or link.get("needs_reauth"):
            continue
        if link.get("syncing"):
            running = True
        elif not link.get("last_error") and not _fresh_at(link, stale_after):
            running = service.schedule_sync(user_id) or running
    return running


@router.get("/sante/sync-status", response_class=HTMLResponse)
async def sante_sync_status(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Polled while the opening sync runs: the page reloads once it brought
    something, else the usual button comes back."""
    links = [await coros.coros_status(db, user.id), await garmin.garmin_status(db, user.id)]
    if any(link.get("syncing") for link in links):
        return templates.TemplateResponse(request, "partials/sante_sync.html",
                                          context={"request": request, "auto_sync": True})
    if any(_fresh_at(link, JUST_SYNCED) for link in links if link.get("connected")):
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "partials/sante_sync.html", context={"request": request})


@router.post("/sante/sync", response_class=HTMLResponse)
async def sante_sync(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """« Synchroniser maintenant »: every linked watch, one after the other.
    Something new arrived → the page reloads to show it; else how it went."""
    outcomes = []
    for name, service in _WATCHES:
        conn = await service.connection_for(db, user.id)
        if conn and not conn.needs_reauth:
            outcomes.append((name, await service.run_sync(db, conn)))
    if any(o and o.get("ok") for _, o in outcomes):
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "partials/sante_sync.html", context={
        "request": request,
        "errors": [(name, o["error"]) for name, o in outcomes if o and not o.get("ok")],
        "busy": any(o is None for _, o in outcomes),
    })
