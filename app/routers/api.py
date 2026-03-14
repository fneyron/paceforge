import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.exceptions import ActivityNotFoundError, AnalysisNotFoundError
from app.models.activity import Activity
from app.models.analysis import Analysis
from app.models.user import User
from app.schemas.activity import ActivityDetail, ActivitySummary
from app.schemas.analysis import AnalysisResponse
from app.tasks.analysis import process_new_activity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api"])


@router.get("/activities", response_model=list[ActivitySummary])
async def list_activities(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    sport_type: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Activity).where(Activity.user_id == user.id)

    if sport_type:
        query = query.where(Activity.sport_type == sport_type)

    query = (
        query.order_by(Activity.start_date.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )

    result = await db.execute(query)
    activities = result.scalars().all()

    return [
        ActivitySummary(
            id=a.id,
            strava_activity_id=a.strava_activity_id,
            sport_type=a.sport_type,
            name=a.name,
            start_date=a.start_date,
            distance=a.distance,
            moving_time=a.moving_time,
            average_speed=a.average_speed,
            average_heartrate=a.average_heartrate,
            total_elevation_gain=a.total_elevation_gain,
            has_analysis=a.analysis is not None,
        )
        for a in activities
    ]


@router.get("/activities/{activity_id}", response_model=ActivityDetail)
async def get_activity(
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

    return ActivityDetail.model_validate(activity)


@router.get("/activities/{activity_id}/analysis", response_model=AnalysisResponse)
async def get_analysis(
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

    if not activity.analysis:
        raise AnalysisNotFoundError(activity_id)

    return AnalysisResponse.model_validate(activity.analysis)


@router.post("/activities/{activity_id}/analyze")
async def trigger_analysis(
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

    return {"status": "queued", "activity_id": activity_id}
