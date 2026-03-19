import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.crypto import encrypt_secret
from app.dependencies import get_current_user, get_db
from app.models.user import User

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["settings"])

SPORT_OPTIONS = [
    ("Run", "Course à pied"),
    ("TrailRun", "Trail"),
    ("Ride", "Vélo"),
    ("Swim", "Natation"),
    ("VirtualRide", "Vélo virtuel"),
    ("Walk", "Marche"),
    ("Hike", "Randonnée"),
]


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    return templates.TemplateResponse(
        request,
        "settings.html",
        context={
            "user": user,
            "sport_options": SPORT_OPTIONS,
        },
    )


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    weight_kg: str = Form(default=""),
    auto_post_comments: str = Form(default="off"),
    preferred_sports: list[str] = Form(default=[]),
    weekly_volume_target_km: str = Form(default=""),
    race_name: str = Form(default=""),
    race_date: str = Form(default=""),
    race_distance_km: str = Form(default=""),
):
    if weight_kg.strip():
        try:
            user.weight_kg = float(weight_kg)
        except ValueError:
            user.weight_kg = None
    else:
        user.weight_kg = None

    user.auto_post_comments = auto_post_comments == "on"
    user.preferred_sports = preferred_sports if preferred_sports else None

    if weekly_volume_target_km.strip():
        try:
            user.weekly_volume_target_km = float(weekly_volume_target_km)
        except ValueError:
            user.weekly_volume_target_km = None
    else:
        user.weekly_volume_target_km = None

    user.race_name = race_name.strip() or None

    if race_date.strip():
        try:
            user.race_date = datetime.strptime(race_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            user.race_date = None
    else:
        user.race_date = None

    if race_distance_km.strip():
        try:
            user.race_distance_km = float(race_distance_km)
        except ValueError:
            user.race_distance_km = None
    else:
        user.race_distance_km = None

    await db.flush()
    logger.info("Settings updated for user %d", user.id)

    return templates.TemplateResponse(
        request,
        "settings.html",
        context={
            "user": user,
            "sport_options": SPORT_OPTIONS,
            "saved": True,
        },
    )


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

    if not client_id or not client_secret:
        return templates.TemplateResponse(
            request,
            "settings.html",
            context={
                "user": user,
                "sport_options": SPORT_OPTIONS,
                "credentials_error": "Client ID et Client Secret sont requis.",
            },
        )

    user.strava_client_id = client_id
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

    return templates.TemplateResponse(
        request,
        "settings.html",
        context={
            "user": user,
            "sport_options": SPORT_OPTIONS,
            "credentials_saved": True,
        },
    )


@router.post("/settings/delete-account")
async def delete_account(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    confirmation: str = Form(...),
):
    """Delete user account and all associated data."""
    if confirmation != "SUPPRIMER":
        return templates.TemplateResponse(
            request, "settings.html",
            context={
                "user": user, "sport_options": SPORT_OPTIONS,
                "delete_error": "Tape SUPPRIMER pour confirmer.",
            },
        )

    user_id = user.id
    logger.warning("User %d (%s) requested account deletion", user_id, user.email)

    # Delete all user data (cascades handle most, but be explicit)
    from app.models.activity import Activity
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
