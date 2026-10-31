import asyncio
import logging
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_optional_user, get_db
from app.features import cycling_enabled
from app.models.activity import Activity
from app.models.user import User
from app.services import coros as coros_service
from app.services import garmin as garmin_service
from app.services import health_consent
from app.services.coros import coros_status
from app.services.garmin import garmin_status
from app.services.strava import disconnect as strava_disconnect_link
from app.services.strava import strava_status

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["cycling_enabled"] = cycling_enabled

router = APIRouter(tags=["settings"])


@router.get("/connections/{provider}", response_class=HTMLResponse)
async def connection_page(provider: str, request: Request, user: User | None = Depends(get_optional_user),
                          db: AsyncSession = Depends(get_db)):
    names = {"coros": "COROS", "garmin": "Garmin", "strava": "Strava"}
    if provider not in names:
        return HTMLResponse("", status_code=404)
    if user is None:
        return RedirectResponse(f"/auth/login?next={provider}", status_code=303)
    state = (strava_status(user) if provider == "strava" else
             await (coros_status if provider == "coros" else garmin_status)(db, user.id))
    if provider == "garmin" and not state["connected"] and not health_consent.given(user):
        # Garmin brings health data: the consent first (the phone app opens this page), before a password is typed
        return RedirectResponse(health_consent.consent_url("/connections/garmin"), status_code=303)
    return templates.TemplateResponse(request, "connection.html", context={
        "user": user, "provider": provider, "name": names[provider], "w": state,
        "ok": request.session.pop(f"{provider}_ok", None),
        "error": request.session.pop(f"{provider}_error", None),
        "connection_next": "devices",
    }, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


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
    given = health_consent.given(user)
    flags.setdefault("health_consent_on", health_consent.given_on(user) if given else None)
    # without the consent, an account holding health data may refuse and erase it (« Refuser et effacer »)
    flags.setdefault("health_held", not given and await health_consent.holds_health_data(db, user.id))
    flags.setdefault("consent_kept", health_consent.KEPT)
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
        health_ok=request.session.pop("health_ok", None),
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
    max_heart_rate: str = Form(default=""),
):
    if health_source is not None and health_source not in ("auto", "COROS", "Garmin", "Apple Santé"):
        ctx = await _settings_context(request, user, db, health_source_bad=True)
        return templates.TemplateResponse(request, "settings.html", context=ctx, status_code=422)
    weight = _to_float(weight_kg)
    if weight_kg.strip() and (weight is None or not math.isfinite(weight) or not 30 <= weight <= 200):
        ctx = await _settings_context(request, user, db, weight_bad=True)
        return templates.TemplateResponse(request, "settings.html", context=ctx, status_code=422)
    from app.services.heart_rate_zones import valid_max_hr

    max_hr_submitted = "max_heart_rate" in await request.form()
    max_hr = _to_float(max_heart_rate)
    if max_hr_submitted and max_heart_rate.strip() and not valid_max_hr(max_hr):
        ctx = await _settings_context(request, user, db, max_hr_bad=True)
        return templates.TemplateResponse(request, "settings.html", context=ctx, status_code=422)
    if max_hr_submitted:
        user.max_heart_rate = int(max_hr) if max_hr is not None else None
    if weight != user.weight_kg:  # typed now: dated, so the most recent of it and the weigh-ins wins (athlete_weight)
        user.weight_kg = weight
        user.weight_set_at = datetime.now(timezone.utc) if weight is not None else None
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


# « Synchroniser maintenant », per linked watch (Santé shows no sync status: owner, 2026-10-08). The request only
# queues the sync for the Celery worker; the state then refreshes itself while it runs (watch_sync_state.html).
_WATCHES = {"coros": (coros_service, coros_status), "garmin": (garmin_service, garmin_status)}
_RUNNING = "Synchronisation en cours…"


def _coros_msg(w: dict) -> str | None:
    """While a COROS sync runs, under « Dernière synchro … » (a first sync says it on that line already)."""
    return _RUNNING if w.get("syncing") and w.get("last_sync_at") else None


@router.post("/settings/{watch}/sync", response_class=HTMLResponse)
async def watch_sync(
    watch: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Queue a sync of one linked watch now; the answer is the new content of
    its #{watch}-sync-state (partials/watch_sync_state.html): the last sync, how
    it went, and while the worker runs it, a state polling itself (GET below).
    A link that is gone or must be reconnected reloads the page (its block then
    shows how to reconnect)."""
    if watch not in _WATCHES:
        return HTMLResponse("", status_code=404)
    service, status_of = _WATCHES[watch]
    conn = await service.connection_for(db, user.id)
    if conn is None or conn.needs_reauth or (watch == "coros" and not coros_service.tokens_readable(conn)):
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    # a press of the button: COROS reads an owed history now (Garmin keeps its own rhythm)
    queued = await (service.queue_sync(db, conn, pressed=True) if watch == "coros" else service.queue_sync(db, conn))
    w = await status_of(db, user.id)
    msg = None
    if watch == "coros":
        msg = (_coros_msg(w) if queued else "Une synchro est déjà en cours : réessaie dans une minute."
               if w["syncing"] else "La synchro n'a pas pu démarrer. Réessaie dans un instant.")
    return templates.TemplateResponse(request, "partials/watch_sync_state.html", context={
        "request": request, "w": w, "key": watch, "msg": msg})


@router.get("/settings/{watch}/sync", response_class=HTMLResponse)
async def watch_sync_state(watch: str, request: Request, user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    """The state a queued sync's block polls (every 3 s) until the worker is done."""
    if watch not in _WATCHES:
        return HTMLResponse("", status_code=404)
    status = await _WATCHES[watch][1](db, user.id)
    if not status["connected"] or status["needs_reauth"]:
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "partials/watch_sync_state.html", context={
        "request": request, "w": status, "key": watch, "msg": _coros_msg(status) if watch == "coros" else None})


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
    next: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.has_strava_linked:
        await strava_disconnect_link(db, user)
    request.session["strava_ok"] = "Strava déconnecté. Les activités déjà reçues restent."
    return RedirectResponse(url="/connections/strava" if next == "devices" else "/settings#strava", status_code=303)


REVOKE_TIMEOUT_S = 8  # per provider: a deletion never waits long on one that does not answer


async def _revoke_links(db: AsyncSession, user: User, strava: bool = True) -> None:
    """Before an account's rows go, best effort and time-bounded: PaceForge's
    access revoked at Strava (deauthorize, and the athlete's own app's webhook
    deleted) and at COROS, the Garmin link forgotten (Garmin has no
    revocation). A provider that fails or does not answer in time never stops
    the deletion: the session is then rolled back to a clean state (nothing is
    deleted yet), the explicit deletions follow. `strava=False`: the health
    sources only (the consent to health data withdrawn)."""
    user_id = user.id
    steps = []
    if strava and (user.has_strava_linked or user.strava_webhook_subscription_id):
        steps.append(("Strava", lambda: strava_disconnect_link(db, user)))
    coros_conn = await coros_service.connection_for(db, user_id)
    if coros_conn is not None:
        steps.append(("COROS", lambda: coros_service.disconnect(db, coros_conn)))
    garmin_conn = await garmin_service.connection_for(db, user_id)
    if garmin_conn is not None:
        steps.append(("Garmin", lambda: garmin_service.disconnect(db, garmin_conn)))
    clean = True
    for name, step in steps:
        try:
            await asyncio.wait_for(step(), REVOKE_TIMEOUT_S)
        except Exception:  # a timeout included
            clean = False
            logger.warning("Account deletion of user %d: %s link not revoked", user_id, name, exc_info=True)
    if not clean:
        await db.rollback()


@router.post("/settings/health-consent/withdraw")
async def withdraw_health_consent(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """« Retirer mon consentement » (GDPR art. 7.3), and « Refuser et effacer » for an account that holds health data
    without having consented (Santé's card, Réglages): COROS and Garmin disconnected as account deletion does it,
    the phones unpaired, the health data erased and the consent cleared. Activities, plans and Strava stay. Both
    pages confirm first (partials/_erase_confirm.html)."""
    from app.models.coros import CorosConnection
    from app.models.garmin import GarminConnection

    user_id, had_consented = user.id, health_consent.given(user)
    await _revoke_links(db, user, strava=False)
    # a link a provider did not let go of in time (rolled back) is forgotten all the same
    for model in (CorosConnection, GarminConnection):
        await db.execute(delete(model).where(model.user_id == user_id))
    await health_consent.erase_health_data(db, user_id)
    await db.commit()
    request.session["health_ok"] = "Consentement retiré\u00a0: tes sources sont déconnectées et tes données de santé effacées."
    if not had_consented:  # « Refuser et effacer »: nothing to withdraw
        request.session["health_ok"] = "Données de santé effacées. Tes sources sont déconnectées."
    logger.warning("User %d %s the health data consent: health data erased", user_id,
                   "withdrew" if had_consented else "refused")
    return RedirectResponse(url="/settings#donnees", status_code=303)


@router.get("/settings/export")
async def export_data(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """« Télécharger mes données »: the account's data as a ZIP of JSON files, streamed (services.data_export). Three
    an hour at most per account: each one reads every row again."""
    from app.services import data_export
    from app.services.auth import RETRY_AFTER, over_quota

    if await over_quota("data_export", user=user.id):
        return PlainTextResponse("Trop de téléchargements pour l’instant. Réessaie dans une heure.", status_code=429,
                                 headers={"Retry-After": RETRY_AFTER})
    logger.info("User %d downloads their data", user.id)
    return StreamingResponse(data_export.export_zip(db, user), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{data_export.filename()}"',
        "Cache-Control": "private, no-store",
    })


async def _delete_legacy_rows(db: AsyncSession, user_id: int) -> None:
    """The account's rows in the tables of the features switched off (services/legacy_tables), in each that exists:
    no model maps them, some have no key to users."""
    from app.services import legacy_tables

    for table in await legacy_tables.present(db):  # a name from its constant, never from the request
        await db.execute(text(f"DELETE FROM {table} WHERE {legacy_tables.of_account(table)}"), {"uid": user_id})


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
    logger.warning("User %d requested account deletion", user_id)
    await _revoke_links(db, user)

    # Every row of the athlete goes with the user: each mapped table hangs on users.id by a foreign key ON DELETE
    # CASCADE (checkpoints through routes; migration h8e9f0a1b2c3 gave the last ones theirs), the legacy tables'
    # rows are deleted by name first; tests/test_account_deletion checks that none is missing.
    await _delete_legacy_rows(db, user_id)
    await db.execute(delete(User).where(User.id == user_id))
    await db.flush()

    request.session.clear()
    logger.warning("Account %d deleted successfully", user_id)

    return RedirectResponse(url="/?account_deleted=1", status_code=302)
