"""Santé: recovery, training load, trends and fitness from the athlete's COROS or Garmin watch.

Syncing on demand lives here only (one button for every linked watch);
Réglages manage the links (status, last sync, errors, disconnect).
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
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
    vue: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    status = await coros.coros_status(db, user.id)
    garmin_link = await garmin.garmin_status(db, user.id)
    try:
        page = await health_page(db, user.id, weight_kg=user.weight_kg)
    except Exception:  # shown as an error, never as "connect your watch"
        logger.exception("Santé page failed for user %d", user.id)
        page = None
    # last night not there yet (and the watch hasn't sent today's day either): look again sooner
    states = (page or {}).get("night_state") or {}
    night_missing = any(link.get("connected") and states.get(name, {}).get("state") == "pending"
                        for name, link in (("COROS", status), ("Garmin", garmin_link)))
    auto_sync = _sync_stale(user.id, ((coros, status), (garmin, garmin_link)),
                            STALE_NIGHT if night_missing else STALE_AFTER)
    return templates.TemplateResponse(
        request, "sante.html",
        context={"user": user, "coros": status, "garmin": garmin_link, "page": page, "auto_sync": auto_sync,
                 "sync_v": await data_version(db, user.id) if auto_sync else None, "sync_n": 0,
                 "vue": vue, "lines": _lines(((("COROS", status), ("Garmin", garmin_link))), page)},
    )


async def data_version(db: AsyncSession, user_id: int) -> str:
    """What the page was drawn from: a new or rewritten day changes it."""
    from sqlalchemy import func

    from app.models.health import HealthMetric

    n, last = (await db.execute(select(func.count(HealthMetric.id), func.max(HealthMetric.updated_at)).where(
        HealthMetric.user_id == user_id))).one()
    return f"{n}-{last.timestamp() if last else 0:.0f}"


def _lines(links, page) -> list[dict]:
    """One line per linked watch: has last night arrived? (the link itself,
    its last sync and errors, are Réglages')"""
    out = []
    states = (page or {}).get("night_state") or {}
    words = {"received": "cette nuit : reçue ({txt})", "pending": "cette nuit : pas encore reçue",
             "none": "pas de nuit mesurée (montre pas portée ?)"}
    for name, link in links:
        if not link.get("connected") or link.get("needs_reauth"):
            continue
        st = states.get(name)
        if st and page and page.get("has_watch_data"):
            out.append({"name": name, "night": words[st["state"]].format(txt=st.get("txt", ""))})
    return out


@router.post("/sante/feel")
async def sante_feel(
    request: Request,
    feel: int | None = Form(default=None),
    legs: int | None = Form(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The morning check-in: one row a day (how you feel, heavy legs)."""
    from app.models.health import HealthMetric
    from app.services.sante import athlete_today

    today = await athlete_today(db, user.id)
    row = (await db.execute(select(HealthMetric).where(
        HealthMetric.user_id == user.id, HealthMetric.metric == "feel", HealthMetric.date == today))).scalar_one_or_none()
    if row is None:
        row = HealthMetric(user_id=user.id, date=today, metric="feel", value=2, source="PaceForge", n_samples=1,
                           details={"legs_heavy": False})
        db.add(row)
    if feel in (1, 2, 3):
        row.value = feel
    if legs in (0, 1):
        row.details = {**(row.details or {}), "legs_heavy": bool(legs)}
    await db.commit()
    if request.headers.get("HX-Request"):
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return RedirectResponse("/sante", status_code=303)


_WATCHES = (("COROS", coros), ("Garmin", garmin))
STALE_AFTER = timedelta(hours=1)
STALE_NIGHT = timedelta(minutes=30)  # last night still missing: it may have been uploaded since
MAX_POLLS = 40  # 3 s apart: the page stops waiting after 2 min
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
    v: str = "",
    n: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Polled, quietly, while the opening sync runs: the page reloads only when
    the sync brought something new; it stops waiting after 2 min."""
    try:
        links = [await coros.coros_status(db, user.id), await garmin.garmin_status(db, user.id)]
        if any(link.get("syncing") for link in links) and n < MAX_POLLS:
            return templates.TemplateResponse(request, "partials/sante_sync.html", context={
                "request": request, "auto_sync": True, "sync_v": v, "sync_n": n + 1})
        if v and await data_version(db, user.id) != v:
            return HTMLResponse("", headers={"HX-Refresh": "true"})
    except Exception:  # never leave the page waiting on an error
        logger.exception("Santé sync status failed for user %d", user.id)
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
