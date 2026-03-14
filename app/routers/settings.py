import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

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
    auto_post_comments: str = Form(default="off"),
    preferred_sports: list[str] = Form(default=[]),
    weekly_volume_target_km: str = Form(default=""),
    race_name: str = Form(default=""),
    race_date: str = Form(default=""),
    race_distance_km: str = Form(default=""),
):
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
