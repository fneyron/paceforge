import logging
import math

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.features import cycling_enabled
from app.models.activity import Activity
from app.models.user import User
from app.services import coros as coros_service
from app.services import garmin as garmin_service
from app.services.coros import coros_status
from app.services.garmin import garmin_status
from app.services.strava import disconnect as strava_disconnect_link
from app.services.strava import strava_status

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["cycling_enabled"] = cycling_enabled

router = APIRouter(tags=["settings"])


async def _settings_context(request: Request, user: User, db: AsyncSession, **flags) -> dict:
    """Everything the settings page needs — shared by every handler that
    re-renders it."""
    total = (await db.execute(select(func.count(Activity.id)).where(Activity.user_id == user.id))).scalar() or 0
    ftp_est = None
    if cycling_enabled():  # the FTP field only shows with the bike planner
        from app.services.power_calculator import ftp_for_user

        try:
            ftp_est = await ftp_for_user(db, user)
        except Exception:
            logger.exception("FTP estimate failed on the settings page")

    flags.setdefault("ftp_est", ftp_est)
    flags.setdefault("coros", await coros_status(db, user.id))
    flags.setdefault("garmin", await garmin_status(db, user.id))
    flags.setdefault("strava", strava_status(user))
    return {"request": request, "user": user, "activity_count": total, **flags}


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # one-time flashes from the COROS actions (Post/Redirect/Get)
    ctx = await _settings_context(
        request, user, db,
        coros_ok=request.session.pop("coros_ok", None),
        coros_error=request.session.pop("coros_error", None),
        garmin_ok=request.session.pop("garmin_ok", None),
        strava_ok=request.session.pop("strava_ok", None),
        strava_error=request.session.pop("strava_error", None),
        garmin_error=request.session.pop("garmin_error", None),
    )
    return templates.TemplateResponse(request, "settings.html", context=ctx)


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    weight_kg: str = Form(default=""),
    ftp_watts: str = Form(default=""),
    health_source: str | None = Form(default=None),
):
    if health_source is not None and health_source not in ("auto", "COROS", "Garmin"):
        ctx = await _settings_context(request, user, db, health_source_bad=True)
        return templates.TemplateResponse(request, "settings.html", context=ctx, status_code=422)
    weight = _to_float(weight_kg)
    if weight_kg.strip() and (weight is None or not math.isfinite(weight) or not 30 <= weight <= 200):
        ctx = await _settings_context(request, user, db, weight_bad=True)
        return templates.TemplateResponse(request, "settings.html", context=ctx, status_code=422)
    user.weight_kg = weight
    if health_source is not None:
        user.health_source = None if health_source == "auto" else health_source
    ftp_bad = False
    # Without the FTP field (cycling off) the form doesn't send it: keep the saved value.
    if cycling_enabled():
        ftp = _to_float(ftp_watts)
        ftp_bad = bool(ftp) and not (50 <= ftp <= 600)
        user.ftp_watts = ftp if ftp and 50 <= ftp <= 600 else None
    await db.flush()
    logger.info("Settings updated for user %d", user.id)

    ctx = await _settings_context(request, user, db, saved=not ftp_bad, ftp_bad=ftp_bad)
    return templates.TemplateResponse(request, "settings.html", context=ctx)


# « Synchroniser maintenant », per linked watch (Santé shows no sync status: owner, 2026-10-08)
_WATCHES = {"coros": (coros_service, coros_status), "garmin": (garmin_service, garmin_status)}


@router.post("/settings/{watch}/sync", response_class=HTMLResponse)
async def watch_sync(
    watch: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sync one linked watch now; the answer is the new content of its
    #{watch}-sync-state (partials/watch_sync_state.html): the last sync and how
    it went. A link that is gone or must be reconnected reloads the page (its
    block then shows how to reconnect)."""
    if watch not in _WATCHES:
        return HTMLResponse("", status_code=404)
    service, status_of = _WATCHES[watch]
    conn = await service.connection_for(db, user.id)
    if conn is None or conn.needs_reauth:
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    if watch == "garmin":
        await garmin_service.queue_sync(db, conn)
        return templates.TemplateResponse(request, "partials/watch_sync_state.html", context={
            "request": request, "w": await garmin_status(db, user.id), "key": watch})
    outcome = await service.run_sync(db, conn)
    if conn.needs_reauth:  # refused while syncing: reconnect
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    if outcome is None:
        msg = "Une synchro est déjà en cours : réessaie dans une minute."
    elif outcome.get("ok"):
        res = outcome.get("result") or {}
        msg = ("Synchro faite : tes nouvelles données sont dans Santé." if res.get("inserted") or res.get("updated")
               else "Synchro faite : rien de nouveau.")
    else:
        msg = None  # « Dernière synchro échouée : … » says it
    return templates.TemplateResponse(request, "partials/watch_sync_state.html", context={
        "request": request, "w": await status_of(db, user.id), "key": watch, "msg": msg})


@router.get("/settings/garmin/sync", response_class=HTMLResponse)
async def garmin_sync_state(request: Request, user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    status = await garmin_status(db, user.id)
    if not status["connected"] or status["needs_reauth"]:
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "partials/watch_sync_state.html", context={
        "request": request, "w": status, "key": "garmin"})


def _to_float(raw: str) -> float | None:
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None



@router.post("/settings/strava/disconnect")
async def strava_disconnect(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.has_strava_linked:
        await strava_disconnect_link(db, user)
    request.session["strava_ok"] = "Strava déconnecté. Les activités déjà reçues restent."
    return RedirectResponse(url="/settings#strava", status_code=303)


@router.post("/settings/delete-account")
async def delete_account(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    confirmation: str = Form(...),
):
    """Delete user account and all associated data."""
    if confirmation.strip().upper() != "SUPPRIMER":
        ctx = await _settings_context(
            request, user, db, delete_error="Tape SUPPRIMER pour confirmer."
        )
        return templates.TemplateResponse(request, "settings.html", context=ctx)

    user_id = user.id
    logger.warning("User %d (%s) requested account deletion", user_id, user.email)

    # Delete all user data (cascades handle most, but be explicit)
    from app.models.analysis import Analysis
    from app.models.chat_message import ChatMessage
    from app.models.coros import CorosConnection
    from app.models.garmin import GarminConnection
    from app.models.generated_plan import GeneratedPlan
    from app.models.health import HealthMetric, HealthSample
    from app.models.nutrition import NutritionProduct
    from app.models.oauth_attempt import OAuthAttempt
    from app.models.route import Route
    from app.models.weekly_digest import WeeklyDigest

    # Delete in order (foreign key constraints)
    await db.execute(delete(Analysis).where(
        Analysis.activity_id.in_(
            select(Activity.id).where(Activity.user_id == user_id)
        )
    ))
    await db.execute(delete(Activity).where(Activity.user_id == user_id))
    await db.execute(delete(ChatMessage).where(ChatMessage.user_id == user_id))
    await db.execute(delete(GeneratedPlan).where(GeneratedPlan.user_id == user_id))
    await db.execute(delete(Route).where(Route.user_id == user_id))
    await db.execute(delete(WeeklyDigest).where(WeeklyDigest.user_id == user_id))
    await db.execute(delete(HealthSample).where(HealthSample.user_id == user_id))
    await db.execute(delete(HealthMetric).where(HealthMetric.user_id == user_id))
    await db.execute(delete(CorosConnection).where(CorosConnection.user_id == user_id))
    await db.execute(delete(GarminConnection).where(GarminConnection.user_id == user_id))
    await db.execute(delete(NutritionProduct).where(NutritionProduct.user_id == user_id))
    await db.execute(delete(OAuthAttempt).where(OAuthAttempt.user_id == user_id))
    await db.execute(delete(User).where(User.id == user_id))
    await db.flush()

    request.session.clear()
    logger.warning("Account %d deleted successfully", user_id)

    return RedirectResponse(url="/?account_deleted=1", status_code=302)
