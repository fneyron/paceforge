import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.activity import Activity
from app.models.generated_plan import GeneratedPlan
from app.models.user import User
from app.schemas.activity import ActivitySummary
from app.services.claude import ClaudeService
from app.services.training_load import calculate_training_load

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["workout"])

SPORT_LABELS = {
    "running": "Course a pied",
    "trail": "Trail",
    "cycling": "Velo",
    "swimming": "Natation",
    "triathlon": "Triathlon",
}


@router.get("/workout", response_class=HTMLResponse)
async def workout_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    training_load = await calculate_training_load(db, user.id, now)

    # Load saved plans
    result = await db.execute(
        select(GeneratedPlan)
        .where(GeneratedPlan.user_id == user.id)
        .order_by(GeneratedPlan.created_at.desc())
        .limit(20)
    )
    saved_plans = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "workout.html",
        context={
            "user": user,
            "training_load": training_load,
            "sport_labels": SPORT_LABELS,
            "saved_plans": saved_plans,
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
    training_load = await calculate_training_load(db, user.id, now)

    result = await db.execute(
        select(Activity)
        .where(Activity.user_id == user.id)
        .order_by(Activity.start_date.desc())
        .limit(10)
    )
    recent = result.scalars().all()
    recent_data = [
        {
            "sport_type": a.sport_type, "distance": a.distance,
            "moving_time": a.moving_time, "start_date": a.start_date,
            "average_speed": a.average_speed, "average_heartrate": a.average_heartrate,
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

        # Save to DB
        plan = GeneratedPlan(
            user_id=user.id,
            plan_type="workout",
            sport=sport,
            goal=goal.strip() or None,
            content_json=workout.model_dump(),
        )
        db.add(plan)
        await db.flush()
        logger.info("Workout %d saved for user %d", plan.id, user.id)

        return templates.TemplateResponse(
            request,
            "partials/workout_card.html",
            context={"workout": workout, "sport_labels": SPORT_LABELS},
        )
    except Exception:
        logger.exception("Failed to generate workout")
        return HTMLResponse(
            '<div class="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">'
            "La generation a echoue. L'IA est peut-etre surchargee, reessayez dans quelques secondes.</div>"
        )


@router.post("/partials/workout/plan", response_class=HTMLResponse)
async def generate_plan(
    request: Request,
    sport: str = Form(...),
    duration_weeks: int = Form(default=8),
    goal: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.training_zones import estimate_training_zones

    now = datetime.now(timezone.utc)
    training_load = await calculate_training_load(db, user.id, now)
    zones = await estimate_training_zones(db, user.id)

    result = await db.execute(
        select(Activity)
        .where(Activity.user_id == user.id)
        .order_by(Activity.start_date.desc())
        .limit(10)
    )
    recent = result.scalars().all()
    recent_data = []
    for a in recent:
        summary = ActivitySummary(
            id=a.id, strava_activity_id=a.strava_activity_id,
            sport_type=a.sport_type, name=a.name, start_date=a.start_date,
            distance=a.distance, moving_time=a.moving_time,
            average_speed=a.average_speed, average_heartrate=a.average_heartrate,
            total_elevation_gain=a.total_elevation_gain,
        )
        recent_data.append({
            "name": a.name, "sport_type": a.sport_type,
            "distance_km": summary.distance_km,
            "pace_formatted": summary.pace_formatted,
            "average_heartrate": a.average_heartrate,
        })

    race_goal = None
    if user.race_name and user.race_date and user.race_date > now:
        race_goal = {
            "name": user.race_name,
            "date": user.race_date.strftime("%d/%m/%Y"),
            "distance_km": user.race_distance_km,
            "days_remaining": (user.race_date - now).days,
        }

    try:
        claude = ClaudeService()
        plan = await claude.generate_training_plan(
            sport=sport, duration_weeks=duration_weeks,
            goal=goal.strip() or None,
            training_load=training_load.model_dump(),
            recent_activities=recent_data,
            race_goal=race_goal, zones=zones,
        )

        # Save to DB
        saved = GeneratedPlan(
            user_id=user.id,
            plan_type="training_plan",
            sport=sport,
            goal=goal.strip() or None,
            content_json=plan.model_dump(),
        )
        db.add(saved)
        await db.flush()
        logger.info("Training plan %d saved for user %d", saved.id, user.id)

        return templates.TemplateResponse(
            request, "partials/training_plan_card.html",
            context={"plan": plan},
        )
    except Exception:
        logger.exception("Failed to generate training plan")
        return HTMLResponse(
            '<div class="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">'
            "La generation du plan a echoue. L'IA est peut-etre surchargee, reessayez dans quelques secondes.</div>"
        )


@router.delete("/api/workout/plans/{plan_id}")
async def delete_plan(
    plan_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(GeneratedPlan).where(GeneratedPlan.id == plan_id, GeneratedPlan.user_id == user.id)
    )
    plan = result.scalar_one_or_none()
    if plan:
        await db.delete(plan)
        await db.flush()
    return JSONResponse({"ok": True})
