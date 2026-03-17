import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.exceptions import ActivityNotFoundError
from app.models.activity import Activity
from app.models.analysis import Analysis
from app.models.user import User
from app.schemas.activity import ActivityDetail
from app.schemas.analysis import AnalysisResponse
from app.services.strava import StravaService
from app.tasks.analysis import process_new_activity

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["activity"])


@router.get("/activity/{activity_id}", response_class=HTMLResponse)
async def activity_detail(
    request: Request,
    activity_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Activity).where(Activity.id == activity_id, Activity.user_id == user.id)
    )
    activity = result.scalar_one_or_none()
    if not activity:
        raise ActivityNotFoundError(activity_id)

    detail = ActivityDetail.model_validate(activity)

    # Get analysis if exists
    analysis = None
    if activity.analysis:
        analysis = AnalysisResponse.model_validate(activity.analysis)

    return templates.TemplateResponse(
        request, "activity_detail.html",
        context={"user": user, "activity": detail, "analysis": analysis},
    )


@router.get("/partials/activity/{activity_id}/analysis", response_class=HTMLResponse)
async def activity_analysis_partial(
    request: Request,
    activity_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Activity).where(Activity.id == activity_id, Activity.user_id == user.id)
    )
    activity = result.scalar_one_or_none()
    if not activity:
        return HTMLResponse("")

    analysis = None
    if activity.analysis:
        analysis = AnalysisResponse.model_validate(activity.analysis)

    return templates.TemplateResponse(
        request, "partials/analysis_card.html",
        context={"analysis": analysis, "activity": activity},
    )


@router.post("/partials/activity/{activity_id}/post-comment", response_class=HTMLResponse)
async def post_strava_comment(
    request: Request,
    activity_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Activity).where(Activity.id == activity_id, Activity.user_id == user.id)
    )
    activity = result.scalar_one_or_none()
    if not activity or not activity.analysis:
        return HTMLResponse("<span class='text-red-500'>Erreur: analyse non trouvée</span>")

    analysis = activity.analysis
    error = None

    try:
        strava = StravaService.for_user(db, user)
        await strava.update_activity_description(
            user, activity.strava_activity_id, analysis.strava_comment
        )
        analysis.comment_posted = True
        analysis.comment_posted_at = datetime.now(timezone.utc)
        await db.flush()
        logger.info("Description updated on Strava activity %d", activity.strava_activity_id)
    except Exception as e:
        logger.exception("Failed to update description on Strava")
        error = str(e)

    return templates.TemplateResponse(
        request, "partials/comment_button.html",
        context={
            "activity": activity,
            "analysis": AnalysisResponse.model_validate(analysis),
            "error": error,
        },
    )


@router.post("/activity/{activity_id}/analyze", response_class=HTMLResponse)
async def trigger_analysis(
    request: Request,
    activity_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Activity).where(Activity.id == activity_id, Activity.user_id == user.id)
    )
    activity = result.scalar_one_or_none()
    if not activity:
        raise ActivityNotFoundError(activity_id)

    process_new_activity.delay(
        owner_strava_id=user.strava_athlete_id,
        strava_activity_id=activity.strava_activity_id,
    )

    return templates.TemplateResponse(
        request, "partials/analysis_card.html",
        context={"analysis": None, "activity": activity},
    )
