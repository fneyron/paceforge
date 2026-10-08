"""Santé: one page (v4, WHOOP/Oura-like) from the athlete's COROS or Garmin
nights and their activities (app.services.sante): no tabs, no check-in, no
planned race, no sync status.

Opening the page syncs a stale watch in the background and reloads quietly
when something new arrived (sync-status); Réglages manage the links (status,
last sync, errors, « Synchroniser maintenant », disconnect: app.routers.settings).
The old views' links (?vue=sommeil, entrainement, course, tendances) land on
the one page, at the section that holds what is left of them. An htmx call
from a page left open since an older version gets a full navigation
(HX-Redirect, HX-Refresh), never a page pasted inside the old one.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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

OLD_VIEWS = {"sommeil": "#sommeil"}  # the others land at the top (the Charge ring sums up the old Entraînement)
RANGES = ("14", "90")


def _landing(vue: str | None, r: str | None) -> str:
    """/sante (with the sleep range kept) and the section an old view's link meant."""
    return "/sante" + (f"?r={r}" if r in RANGES else "") + OLD_VIEWS.get(vue or "", "")


def _away(request: Request, url: str) -> Response:
    """A redirect, or for an htmx call (a v3 page left open: its check-in, its
    range buttons) a full navigation: htmx would follow a 303 and paste the
    whole page inside the old one."""
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


@router.get("/sante", response_class=HTMLResponse)
async def sante_page(
    request: Request,
    vue: str | None = None,
    r: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if vue is not None:  # an old view's link: the one page, at its section
        return RedirectResponse(_landing(vue, r), status_code=302)
    status = await coros.coros_status(db, user.id)
    garmin_link = await garmin.garmin_status(db, user.id)
    try:
        page = await health_page(db, user.id, r=r)
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
                 "sync_v": await data_version(db, user.id) if auto_sync else None, "sync_n": 0},
    )


@router.get("/sante/sources", response_class=HTMLResponse)
async def sante_sources(request: Request, user: User = Depends(get_current_user)):
    """The references of Santé's two folds (v4.3: the folds keep a few plain bullets, their sources are here):
    « Récupération » then « Sommeil », each the official texts first, then the studies, as links."""
    from app.services import sante_score, sante_sleep

    sections = [("Récupération", "recuperation", sante_score.linked(sante_score.REFS)),
                ("Sommeil", "sommeil", sante_score.linked(sante_sleep.REFS))]
    return templates.TemplateResponse(request, "sante_sources.html", context={"user": user, "sections": sections})


@router.get("/sante/sommeil")
async def sante_sleep_range(request: Request, r: str | None = None, user: User = Depends(get_current_user)):
    """The old Sommeil range swap: the one page, its Sommeil section, the range kept."""
    return _away(request, _landing("sommeil", r))


async def data_version(db: AsyncSession, user_id: int) -> str:
    """What the page was drawn from: a new or rewritten day changes it."""
    from sqlalchemy import func

    from app.models.health import HealthMetric

    n, last = (await db.execute(select(func.count(HealthMetric.id), func.max(HealthMetric.updated_at)).where(
        HealthMetric.user_id == user_id))).one()
    return f"{n}-{last.timestamp() if last else 0:.0f}"


@router.post("/sante/feel")
async def sante_feel(
    request: Request,
    feel: int | None = Form(default=None),
    legs: int | None = Form(default=None),
    why: list[str] | None = Form(default=None),
    toggle: str | None = Form(default=None),
    alcohol: int | None = Form(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The morning check-in of v3, kept harmlessly: the page no longer asks it
    (owner, 2026-10-08) and Santé never reads it, but an old form or a client
    still posting gets its answer stored, as before, and the page back. `feel`
    1 mieux · 2 comme d'habitude · 3 moins bien; with « moins bien », `why`
    (legs, fatigue, sick, stress) or `toggle` one of them; `alcohol` 1/0;
    `legs` 1/0 the older « jambes lourdes » toggle."""
    from app.models.health import HealthMetric
    from app.services.health import FEEL_WHY
    from app.services.sante import athlete_today

    today = await athlete_today(db, user.id)
    row = (await db.execute(select(HealthMetric).where(
        HealthMetric.user_id == user.id, HealthMetric.metric == "feel", HealthMetric.date == today))).scalar_one_or_none()
    if row is None:
        row = HealthMetric(user_id=user.id, date=today, metric="feel", value=2, source="PaceForge", n_samples=1,
                           details={"why": [], "alcohol": False, "legs_heavy": False, "answered": False})
        db.add(row)
    det = dict(row.details or {})
    reasons = [w for w in det.get("why") or [] if w in FEEL_WHY]
    if det.get("legs_heavy") and "legs" not in reasons:
        reasons.append("legs")
    answered = feel in (1, 2, 3) or why is not None or toggle in FEEL_WHY or legs in (0, 1)
    if feel in (1, 2, 3):
        row.value = feel
        if feel != 3:
            reasons = []  # the reasons go with « moins bien »
    if why is not None:
        reasons = [w for w in FEEL_WHY if w in why]
    if toggle in FEEL_WHY:
        reasons = [w for w in reasons if w != toggle] + ([] if toggle in reasons else [toggle])
    if legs in (0, 1):
        reasons = [w for w in reasons if w != "legs"] + (["legs"] if legs else [])
    if reasons:
        row.value = 3  # a reason is a « moins bien »
    if alcohol in (0, 1):
        det["alcohol"] = bool(alcohol)
    det["why"] = [w for w in FEEL_WHY if w in reasons]
    det["legs_heavy"] = "legs" in reasons
    det.setdefault("alcohol", False)
    if answered:
        det.pop("answered", None)  # a reply (rows without the key are replies)
    row.details = det
    await db.commit()
    return _away(request, "/sante")


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
    `stale_after` (not one that failed last time: Réglages say why, and sync
    on demand). True when a sync is running, so the page waits for it."""
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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The sync button of a Santé page left open since v4 (Santé has none any
    more: « Synchroniser maintenant » is in Réglages, per watch): every linked
    watch synced, then the page reloads (the new page, without the button)."""
    for _, service in _WATCHES:
        conn = await service.connection_for(db, user.id)
        if conn and not conn.needs_reauth:
            await service.run_sync(db, conn)
    return HTMLResponse("", headers={"HX-Refresh": "true"})
