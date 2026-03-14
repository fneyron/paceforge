import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.activity import Activity
from app.models.user import User
from app.services.claude import ClaudeService
from app.services.training_load import calculate_training_load

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["workout"])

SPORT_LABELS = {
    "running": "Course à pied",
    "trail": "Trail",
    "cycling": "Vélo",
    "swimming": "Natation",
    "triathlon": "Triathlon",
}


@router.get("/workout", response_class=HTMLResponse)
async def workout_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Determine main sport from recent activities
    now = datetime.now(timezone.utc)
    training_load = await calculate_training_load(db, user.id, now)

    return templates.TemplateResponse(
        request,
        "workout.html",
        context={
            "user": user,
            "training_load": training_load,
            "sport_labels": SPORT_LABELS,
        },
    )


@router.post("/partials/workout/generate", response_class=HTMLResponse)
async def generate_workout(
    request: Request,
    sport: str = Form(...),
    goal: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)

    # Training load
    training_load = await calculate_training_load(db, user.id, now)

    # Recent activities
    result = await db.execute(
        select(Activity)
        .where(Activity.user_id == user.id)
        .order_by(Activity.start_date.desc())
        .limit(10)
    )
    recent = result.scalars().all()
    recent_data = [
        {
            "sport_type": a.sport_type,
            "distance": a.distance,
            "moving_time": a.moving_time,
            "start_date": a.start_date,
            "average_speed": a.average_speed,
            "average_heartrate": a.average_heartrate,
            "total_elevation_gain": a.total_elevation_gain,
        }
        for a in recent
    ]

    try:
        claude = ClaudeService()
        workout = await claude.generate_workout(
            sport=sport,
            goal=goal.strip() or None,
            training_load=training_load.model_dump(),
            recent_activities=recent_data,
        )

        return templates.TemplateResponse(
            request,
            "partials/workout_card.html",
            context={
                "workout": workout,
                "sport_labels": SPORT_LABELS,
            },
        )
    except Exception:
        logger.exception("Failed to generate workout")
        return templates.TemplateResponse(
            request,
            "partials/workout_card.html",
            context={
                "error": "La génération a échoué. Veuillez réessayer.",
                "sport_labels": SPORT_LABELS,
            },
        )
