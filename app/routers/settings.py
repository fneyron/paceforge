import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.config import settings as app_settings
from app.crypto import encrypt_secret
from app.dependencies import get_current_user, get_db
from app.models.activity import Activity
from app.models.user import User
from app.services.activity_dedupe import sport_group
from app.services.coros import coros_status
from app.services.health import (
    METRIC_LABELS,
    METRICS,
    ExportError,
    health_status,
    new_api_key,
    parse_export,
    store_samples,
)

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["settings"])

_FAMILY_ORDER = ["Course", "Trail", "Vélo", "Natation", "Autre"]


def _family_label(sport_type: str | None) -> str:
    if sport_type == "Swim":
        return "Natation"
    return {"run": "Course", "trail": "Trail", "bike": "Vélo"}.get(sport_group(sport_type), "Autre")


async def _settings_context(request: Request, user: User, db: AsyncSession, **flags) -> dict:
    """Everything the settings page needs — shared by every handler that
    re-renders it, so the Strava inventory never vanishes after a save."""
    stats_q = await db.execute(
        select(
            func.count(Activity.id),
            func.count(Activity.id).filter(Activity.splits_metric.is_not(None)),
            func.min(Activity.start_date),
            func.max(Activity.start_date),
        ).where(Activity.user_id == user.id)
    )
    total, with_splits, first_date, last_date = stats_q.one()
    from app.services.power_calculator import ftp_for_user

    try:
        ftp_est = await ftp_for_user(db, user)
    except Exception:
        logger.exception("FTP estimate failed on the settings page")
        ftp_est = None

    by_sport_q = await db.execute(
        select(Activity.sport_type, func.count(Activity.id))
        .where(Activity.user_id == user.id)
        .group_by(Activity.sport_type)
    )
    families: dict[str, int] = {}
    for sport_type, n in by_sport_q.all():
        label = _family_label(sport_type)
        families[label] = families.get(label, 0) + n

    strava_stats = {
        "total": total or 0,
        "with_splits": with_splits or 0,
        "first_date": first_date,
        "last_date": last_date,
        "families": [(f, families[f]) for f in _FAMILY_ORDER if families.get(f)],
    }
    flags.setdefault("ftp_est", ftp_est)
    flags.setdefault("health", await health_status(db, user))
    flags.setdefault("health_api_url", _public_base(request) + "/api/health/samples")
    flags.setdefault("coros", await coros_status(db, user.id))
    return {"request": request, "user": user, "strava_stats": strava_stats, **flags}


def _public_base(request: Request) -> str:
    """The address the iPhone must call: the configured public URL, or the one
    this page was reached on (dev)."""
    base = (app_settings.BASE_URL or "").rstrip("/")
    if base and "localhost" not in base and "127.0.0.1" not in base:
        return base
    return str(request.base_url).rstrip("/")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # one-time flashes from the Apple Santé key and COROS actions (Post/Redirect/Get:
    # a page reload must never mint a second key behind the athlete's back)
    ctx = await _settings_context(
        request, user, db,
        new_health_key=request.session.pop("new_health_key", None),
        health_key_revoked=request.session.pop("health_key_revoked", False),
        coros_ok=request.session.pop("coros_ok", None),
        coros_error=request.session.pop("coros_error", None),
    )
    return templates.TemplateResponse(request, "settings.html", context=ctx)


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    weight_kg: str = Form(default=""),
    ftp_watts: str = Form(default=""),
):
    user.weight_kg = _to_float(weight_kg)
    ftp = _to_float(ftp_watts)
    ftp_bad = bool(ftp) and not (50 <= ftp <= 600)
    user.ftp_watts = ftp if ftp and 50 <= ftp <= 600 else None
    await db.flush()
    logger.info("Settings updated for user %d", user.id)

    ctx = await _settings_context(request, user, db, saved=not ftp_bad, ftp_bad=ftp_bad)
    return templates.TemplateResponse(request, "settings.html", context=ctx)


def _to_float(raw: str) -> float | None:
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@router.post("/settings/strava-credentials", response_class=HTMLResponse)
async def update_strava_credentials(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    client_id: str = Form(...),
    client_secret: str = Form(...),
):
    """Update the user's Strava API app credentials."""
    client_id = client_id.strip()
    client_secret = client_secret.strip()

    # Secret may be left blank to keep the current one (only the ID changes).
    if not client_id or (not client_secret and not user.has_own_strava_app):
        ctx = await _settings_context(
            request, user, db, credentials_error="Client ID et Client Secret sont requis."
        )
        return templates.TemplateResponse(request, "settings.html", context=ctx)

    user.strava_client_id = client_id
    if client_secret:
        user.strava_client_secret_encrypted = encrypt_secret(client_secret)
    user.strava_credentials_valid = True
    await db.flush()

    logger.info("Strava credentials updated for user %d", user.id)

    # Re-create webhook subscription with new credentials
    try:
        from app.services.strava import StravaService
        strava = StravaService.for_user(db, user)

        # Delete old subscription if exists
        if user.strava_webhook_subscription_id:
            await strava.delete_webhook_subscription(user.strava_webhook_subscription_id)

        sub_id = await strava.create_webhook_subscription(user)
        if sub_id:
            user.strava_webhook_subscription_id = sub_id
            await db.flush()
    except Exception:
        logger.exception("Failed to update webhook for user %d", user.id)

    ctx = await _settings_context(request, user, db, credentials_saved=True)
    return templates.TemplateResponse(request, "settings.html", context=ctx)


# ── Apple Santé ─────────────────────────────────────────────────────────────

@router.post("/settings/health/key")
async def generate_health_key(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """New personal key (the old one stops working). Stored hashed; the key
    itself rides in the session to the next page view only, then is gone."""
    key, key_hash, prefix = new_api_key()
    user.health_key_hash = key_hash
    user.health_key_prefix = prefix
    user.health_key_created_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info("Apple Health key (re)generated for user %d", user.id)
    request.session["new_health_key"] = key
    return RedirectResponse(url="/settings#apple-sante", status_code=303)


@router.post("/settings/health/key/revoke")
async def revoke_health_key(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.health_key_hash = None
    user.health_key_prefix = None
    user.health_key_created_at = None
    await db.flush()
    logger.info("Apple Health key revoked for user %d", user.id)
    request.session["health_key_revoked"] = True
    return RedirectResponse(url="/settings#apple-sante", status_code=303)


@router.get("/settings/health/status", response_class=HTMLResponse)
async def health_status_partial(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The « Vérifier la réception » button: when the last push arrived."""
    ctx = {"request": request, "health": await health_status(db, user), "checked": True}
    return templates.TemplateResponse(request, "partials/health_status.html", context=ctx)


MAX_EXPORT_BYTES = 2 * 1024 ** 3  # zipped exports of heavy watch users run to a few hundred MB
_CHUNK = 1024 * 1024


@router.post("/settings/health/import", response_class=HTMLResponse)
async def import_health_export(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Apple Health export.zip (or export.xml): the page sends the file as the
    raw body, streamed here to a temp file; a plain form upload works too.
    The XML is then read as a stream in a worker thread."""
    fd, path = tempfile.mkstemp(prefix="pf-health-", suffix=".upload")
    error, counts, result = None, None, None
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            if request.headers.get("content-type", "").startswith("multipart/form-data"):
                form = await request.form(max_files=1, max_fields=5)
                upload = form.get("file")
                if upload is not None and hasattr(upload, "file"):
                    await run_in_threadpool(shutil.copyfileobj, upload.file, out, _CHUNK)
                    size = out.tell()
            else:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_EXPORT_BYTES:
                        break
                    out.write(chunk)
        if size > MAX_EXPORT_BYTES:
            error = "Fichier trop gros (2 Go max)."
        elif size == 0:
            error = "Aucun fichier reçu."
        else:
            samples, counts = await run_in_threadpool(parse_export, path)
            result = await store_samples(db, user.id, samples)
            logger.info("Apple Health export for user %d: %d bytes, %s", user.id, size, counts)
    except ExportError as e:
        error = str(e)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    rows = []
    if counts is not None:
        rows = [(METRIC_LABELS[m], counts.get(m, 0), (result or {}).get("days", {}).get(m, 0))
                for m in METRICS]
    ctx = {"request": request, "error": error, "rows": rows,
           "rejected": (counts or {}).get("rejected", 0),
           "total": sum(n for _, n, _ in rows), "result": result}
    return templates.TemplateResponse(request, "partials/health_import_result.html", context=ctx)


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
    from app.models.generated_plan import GeneratedPlan
    from app.models.health import HealthMetric, HealthSample
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
    await db.execute(delete(User).where(User.id == user_id))
    await db.flush()

    request.session.clear()
    logger.warning("Account %d deleted successfully", user_id)

    return RedirectResponse(url="/?account_deleted=1", status_code=302)
