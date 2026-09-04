import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.crypto import encrypt_secret
from app.dependencies import get_current_user, get_db
from app.models.activity import Activity
from app.models.user import User
from app.services.activity_dedupe import sport_group

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
    return {"request": request, "user": user, "strava_stats": strava_stats, **flags}


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ctx = await _settings_context(request, user, db)
    return templates.TemplateResponse(request, "settings.html", context=ctx)


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    weight_kg: str = Form(default=""),
    weekly_volume_target_km: str = Form(default=""),
    race_name: str = Form(default=""),
    race_date: str = Form(default=""),
    race_distance_km: str = Form(default=""),
):
    user.weight_kg = _to_float(weight_kg)
    user.weekly_volume_target_km = _to_float(weekly_volume_target_km)
    user.race_name = race_name.strip() or None
    user.race_distance_km = _to_float(race_distance_km)

    if race_date.strip():
        try:
            user.race_date = datetime.strptime(race_date.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            user.race_date = None
    else:
        user.race_date = None

    await db.flush()
    logger.info("Settings updated for user %d", user.id)

    ctx = await _settings_context(request, user, db, saved=True)
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
    from app.models.generated_plan import GeneratedPlan
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
    await db.execute(delete(User).where(User.id == user_id))
    await db.flush()

    request.session.clear()
    logger.warning("Account %d deleted successfully", user_id)

    return RedirectResponse(url="/?account_deleted=1", status_code=302)
