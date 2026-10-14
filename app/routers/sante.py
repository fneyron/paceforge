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

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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
    context = await _health_context(db, user, r)
    return templates.TemplateResponse(request, "sante.html", context=context)


async def _health_context(db: AsyncSession, user: User, r: str | None = None) -> dict:
    status, garmin_link = await link_state(db, user.id)
    try:
        page = await health_page(db, user.id, r=r)
    except Exception:  # shown as an error, never as "connect your watch"
        logger.exception("Santé page failed for user %d", user.id)
        page = None
    # Retry sooner while last night, or COROS's separately published HRV, is pending.
    states = (page or {}).get("night_state") or {}
    night_missing = any(link.get("connected") and (states.get(name, {}).get("state") == "pending"
                        or states.get(name, {}).get("hrv_pending"))
                        for name, link in (("COROS", status), ("Garmin", garmin_link)))
    auto_sync = _sync_stale(user.id, ((coros, status), (garmin, garmin_link)),
                            STALE_NIGHT if night_missing else STALE_AFTER)
    return {"user": user, "coros": status, "garmin": garmin_link, "page": page, "auto_sync": auto_sync,
            "sync_v": await data_version(db, user.id) if auto_sync else None, "sync_n": 0}


def _flags(conn, service) -> tuple[bool, str | None]:
    """(needs_reauth, last_error) of a watch link as its service says them: a COROS link whose tokens no configured
    key decrypts must be reconnected (coros.tokens_readable: « La connexion à COROS a expiré », computed when read,
    never stored), its stored error in today's wording (coros._said) — coros_status's own rule, without its
    measurement counts."""
    if service is coros:
        readable = coros.tokens_readable(conn)
        return conn.needs_reauth or not readable, coros._said(conn.last_error) if readable else coros.EXPIRED
    return conn.needs_reauth, conn.last_error


def _link(conn, service, retryable=()) -> dict:
    """What Santé reads of a watch link (the page's sync issue line, the opening sync): connected, needs_reauth,
    last_sync_at, syncing (claimed within the service's CLAIM_TTL), last_error, retryable_partial (a transient
    partial import with a recorded sync: retried on opening, as coros_status says)."""
    if conn is None:
        return {"connected": False}
    claimed = conn.sync_claimed_at
    needs_reauth, error = _flags(conn, service)
    return {"connected": True, "needs_reauth": needs_reauth, "last_sync_at": conn.last_sync_at,
            "syncing": bool(claimed and datetime.now(timezone.utc) - _utc(claimed) < service.CLAIM_TTL),
            "last_error": error,
            "retryable_partial": error in retryable and conn.last_sync_at is not None}


async def link_state(db: AsyncSession, user_id: int) -> tuple[dict, dict]:
    """(COROS's, Garmin's) link state from the link rows alone (the full coros_status / garmin_status count every
    stored measurement: Réglages', never needed here, on every load and every 3-s poll)."""
    retryable = (coros.PARTIAL, coros.HRV_PARTIAL, coros.SLEEP_PARTIAL, coros.PHASES_PARTIAL)
    return (_link(await coros.connection_for(db, user_id), coros, retryable),
            _link(await garmin.connection_for(db, user_id), garmin))


@router.get("/accueil")
async def legacy_home(user: User = Depends(get_current_user)):
    """Keep bookmarks and older installed apps working after merging the home into Santé."""
    return RedirectResponse("/sante", status_code=302)


@router.get("/sante/sources", response_class=HTMLResponse)
async def sante_sources(request: Request, user: User = Depends(get_current_user)):
    """The references of Santé's « Comment je calcule » fold (v4.3: the fold keeps a few plain bullets, their
    sources are here): « Récupération » then « Sommeil », each the official texts first, then the studies, as links."""
    from app.services import sante_score, sante_sleep

    sections = [("Récupération", "recuperation", sante_score.linked(sante_score.REFS)),
                ("Sommeil", "sommeil", sante_score.linked(sante_sleep.REFS))]
    return templates.TemplateResponse(request, "sante_sources.html", context={"user": user, "sections": sections})


@router.get("/sante/aide", response_class=HTMLResponse)
async def sante_help(request: Request, user: User = Depends(get_current_user)):
    from app.services import sante_score

    return templates.TemplateResponse(request, "sante_help.html",
                                      context={"user": user, "method": sante_score.typo(sante_score.METHOD)})


@router.get("/sante/sommeil")
async def sante_sleep_range(request: Request, r: str | None = None, user: User = Depends(get_current_user)):
    """The old Sommeil range swap: the one page, its Sommeil section, the range kept."""
    return _away(request, _landing("sommeil", r))


async def data_version(db: AsyncSession, user_id: int) -> str:
    """Version of Santé: its rows, the phone's, the activities (activity-only syncs too; integer sums, so the hash
    is stable on PostgreSQL, whose float sums may differ in their last digits from one run to the next) and each
    watch link's state (needs_reauth, an error or not: a sync that only failed reloads the page, which then says
    so; never its last sync time: a sync that brought nothing changes nothing)."""
    from hashlib import sha256

    from sqlalchemy import func

    from app.models.activity import Activity
    from app.models.health import HealthMetric
    from app.models.mobile import MobileDaily
    from app.services.sante_training import int_sum as whole  # a NaN or ±∞ (PostgreSQL cannot cast it) counts as 0

    n, last = (await db.execute(select(func.count(HealthMetric.id), func.max(HealthMetric.updated_at)).where(
        HealthMetric.user_id == user_id))).one()
    version = f"{n}-{last.isoformat() if last else ''}"
    count, updated = (await db.execute(select(func.count(MobileDaily.id), func.max(MobileDaily.updated_at)).where(
        MobileDaily.user_id == user_id))).one()
    version += f"-{count}-{updated.isoformat() if updated else ''}"
    # Activity-only watch syncs must also refresh Santé; totals catch rewrites.
    counts = (await db.execute(select(func.count(Activity.id), func.max(Activity.id), whole(Activity.moving_time),
                                      whole(Activity.distance), whole(Activity.average_heartrate, 10),
                                      whole(Activity.max_heartrate), whole(Activity.total_elevation_gain),
                                      whole(Activity.elapsed_time)).where(Activity.user_id == user_id))).one()
    version += "-" + "-".join(str(int(v or 0)) for v in counts)
    for _, service in _WATCHES:
        conn = await service.connection_for(db, user_id)
        if conn is None:
            version += "-none"
            continue
        needs_reauth, error = _flags(conn, service)
        version += f"-{int(bool(needs_reauth))}{int(error is not None)}"
    return sha256(version.encode()).hexdigest()


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
    """DEPRECATED (2026-10-14): kept for a grace period, to be removed once no client posts here any more.

    The morning check-in of v3, kept harmlessly: the page no longer asks it
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


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def _claimed(conn, service) -> bool:
    return bool(conn.sync_claimed_at and datetime.now(timezone.utc) - _utc(conn.sync_claimed_at) < service.CLAIM_TTL)


def _fresh_at(link: dict, within: timedelta) -> bool:
    at = link.get("last_sync_at")
    if at is None:
        return False
    at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at
    return datetime.now(timezone.utc) - at < within


def _sync_stale(user_id: int, links, stale_after: timedelta) -> bool:
    """Opening Santé syncs, in the background, every watch not synced within
    `stale_after`. Only a transient partial import with a recorded attempt time
    retries after a failure; auth and unreadable responses need attention in
    Réglages. True when a sync is running, so the page waits for it."""
    running = False
    for service, link in links:
        if not link.get("connected") or link.get("needs_reauth"):
            continue
        if link.get("syncing"):
            running = True
        elif (not link.get("last_error") or link.get("retryable_partial")) and not _fresh_at(link, stale_after):
            running = service.schedule_sync(user_id) or running
    return running


@router.get("/sante/sync-status", response_class=HTMLResponse)
async def sante_sync_status(
    request: Request,
    v: str = "",
    n: int = 0,
    since: datetime | None = None,
    watches: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Polled, quietly, while the opening sync runs: the page reloads only when
    the sync brought something new; it stops waiting after 2 min."""
    if request.headers.get("accept") == "application/json":
        pending, errors = [], []
        for name, service in _WATCHES:
            if name not in watches.split(','):
                continue
            conn = await service.connection_for(db, user.id)
            if conn is None:
                continue
            needs_reauth, error = _flags(conn, service)
            if needs_reauth:
                errors.append(f"Reconnecte {name} dans Réglages.")
            elif _claimed(conn, service):
                pending.append(name)
            elif error:
                errors.append(f"{name} : synchronisation incomplète. Vérifie Réglages.")
            elif since and (conn.last_sync_at is None or _utc(conn.last_sync_at) < _utc(since)):
                pending.append(name)  # queued but not yet claimed/completed
        return JSONResponse({"pending": pending, "errors": errors}, headers={"Cache-Control": "no-store"})
    try:
        links = await link_state(db, user.id)
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
    """The phone app's refresh (JSON: each linked watch queued, its progress polled on /sante/sync-status), and the
    sync button of a Santé page left open since v4 (Santé has none any more: « Synchroniser maintenant » is in
    Réglages, per watch): that page just reloads — opening Santé syncs a stale watch in the background (the
    durable worker), never inline in this request (it ran Garmin's whole sync in it)."""
    if request.headers.get("accept") == "application/json":
        if request.headers.get("x-paceforge-refresh") != "1":
            raise HTTPException(403)
        started, errors = [], []
        since = datetime.now(timezone.utc).isoformat()
        for name, service in _WATCHES:
            conn = await service.connection_for(db, user.id)
            if conn is None:
                continue
            if _flags(conn, service)[0]:
                errors.append(f"Reconnecte {name} dans Réglages.")
            elif _claimed(conn, service):
                started.append(name)
            else:
                queued = await service.queue_sync(db, conn)
                if not queued:
                    await db.refresh(conn)  # another request may have reserved it meanwhile
                if queued or _claimed(conn, service):
                    started.append(name)
                else:
                    errors.append(f"{name} : synchronisation indisponible. Réessaie depuis Réglages.")
        return JSONResponse({"watches": started, "since": since, "errors": errors}, headers={"Cache-Control": "no-store"})
    return HTMLResponse("", headers={"HX-Refresh": "true"})
