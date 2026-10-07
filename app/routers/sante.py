"""Santé: « Aujourd'hui | Sommeil » from the athlete's COROS or Garmin nights,
their sessions and their morning check-in (app.services.sante).

Syncing on demand lives here only (one button for every linked watch);
Réglages manage the links (status, last sync, errors, disconnect). The old
views (Entraînement, Course, Tendances) moved to Activités and the race page:
their links redirect there.
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


MOVED = {"entrainement": "/activities", "tendances": "/activities"}


async def _moved(db: AsyncSession, user: User, vue: str) -> str | None:
    """Where an old Santé view lives now (Course: the next race's page, else
    the one run in the last 14 days, else Activités)."""
    if vue in MOVED:
        return MOVED[vue]
    if vue == "course":
        from app.services.sante import _races, athlete_today

        nxt, last, _ = await _races(db, user.id, await athlete_today(db, user.id))
        race = nxt or last
        return f"/simulator/routes/{race.id}#prep" if race else "/activities"
    return None


@router.get("/sante", response_class=HTMLResponse)
async def sante_page(
    request: Request,
    vue: str | None = None,
    r: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if vue and (to := await _moved(db, user, vue)):
        return RedirectResponse(to, status_code=302)
    status = await coros.coros_status(db, user.id)
    garmin_link = await garmin.garmin_status(db, user.id)
    try:
        page = await health_page(db, user.id, weight_kg=user.weight_kg, r=r)
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
                 "vue": "sommeil" if vue == "sommeil" else "aujourdhui",
                 "lines": _lines(((("COROS", status), ("Garmin", garmin_link))), page)},
    )


@router.get("/sante/sommeil", response_class=HTMLResponse)
async def sante_sleep_range(
    request: Request,
    r: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sommeil for another range (the range toggle swaps it in place; without
    scripts the toggle is a plain GET of /sante?vue=sommeil&r=…)."""
    if not request.headers.get("HX-Request"):
        return RedirectResponse(f"/sante?vue=sommeil&r={r or ''}", status_code=303)
    try:
        page = await health_page(db, user.id, r=r, parts=("sleep",))
    except Exception:
        logger.exception("Santé › Sommeil failed for user %d", user.id)
        return HTMLResponse('<div id="sommeil-range"><p class="pf-note mt-4">Impossible d\'afficher tes nuits pour '
                            "l'instant. Réessaie dans quelques minutes.</p></div>")
    return templates.TemplateResponse(request, "partials/sante_sleep_range.html", context={"page": page})


async def data_version(db: AsyncSession, user_id: int) -> str:
    """What the page was drawn from: a new or rewritten day changes it."""
    from sqlalchemy import func

    from app.models.health import HealthMetric

    n, last = (await db.execute(select(func.count(HealthMetric.id), func.max(HealthMetric.updated_at)).where(
        HealthMetric.user_id == user_id))).one()
    return f"{n}-{last.timestamp() if last else 0:.0f}"


def _lines(links, page) -> list[dict]:
    """One line per linked watch: « COROS · synchro il y a 25 min », and
    « cette nuit pas encore reçue » while last night is missing (last night's
    length is printed once, in Sommeil; errors and the link itself are
    Réglages')."""
    out = []
    states = (page or {}).get("night_state") or {}
    for name, link in links:
        if not link.get("connected") or link.get("needs_reauth"):
            continue
        parts = [f"synchro {link['last_sync_ago']}"] if link.get("last_sync_ago") else []
        st = states.get(name)
        if st and st["state"] == "pending" and page and page.get("has_watch_data"):
            parts.append("cette nuit pas encore reçue")
        if parts:
            out.append({"name": name, "night": " · ".join(parts)})
    return out


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
    """The morning check-in: one row a day. `feel` 1 mieux · 2 comme d'habitude
    · 3 moins bien; with « moins bien », `why` (jambes, fatigue, malade, stress:
    legs, fatigue, sick, stress) or `toggle` one of them (a chip); `alcohol`
    1/0 tags last night (« alcool hier »), apart. `legs` 1/0 is the older
    « jambes lourdes » toggle (kept as why legs). From the page (htmx) the
    answer swaps the view in place: the decision updates, the check-in folds
    to « Noté : … — modifier »."""
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
    det["legs_heavy"] = "legs" in reasons  # what the readers written before v3 look at
    det.setdefault("alcohol", False)
    if answered:
        det.pop("answered", None)  # a reply (rows without the key are replies)
    row.details = det
    await db.commit()
    if request.headers.get("HX-Request"):
        page = await health_page(db, user.id, today=today, parts=("today",))
        return templates.TemplateResponse(request, "partials/sante_today.html", context={
            "page": page, "swap": True, "open_feel": row.value == 3 and (feel == 3 or toggle in FEEL_WHY)})
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
