import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.services.trends import get_pace_trends, get_personal_records, get_weekly_volume_trends

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["trends"])


@router.get("/trends", response_class=HTMLResponse)
async def trends_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    volumes = await get_weekly_volume_trends(db, user.id, weeks=12)
    paces = await get_pace_trends(db, user.id, sport_type="Run", weeks=12)
    records = await get_personal_records(db, user.id)

    # Prepare chart data
    volume_labels = [v.week_start.strftime("%d/%m") for v in volumes]
    volume_data = [v.distance_km for v in volumes]
    volume_hours = [v.duration_hours for v in volumes]
    volume_counts = [v.count for v in volumes]

    # Sport breakdown for stacked chart
    all_sports = set()
    for v in volumes:
        all_sports.update(v.sport_breakdown.keys())
    sport_datasets = {}
    for sport in sorted(all_sports):
        sport_datasets[sport] = [
            v.sport_breakdown.get(sport, 0) for v in volumes
        ]

    pace_labels = [p.week_start.strftime("%d/%m") for p in paces]
    pace_data = [p.avg_pace_min_per_km for p in paces]

    return templates.TemplateResponse(
        request,
        "trends.html",
        context={
            "user": user,
            "volumes": volumes,
            "volume_labels": volume_labels,
            "volume_data": volume_data,
            "volume_hours": volume_hours,
            "volume_counts": volume_counts,
            "sport_datasets": sport_datasets,
            "pace_labels": pace_labels,
            "pace_data": pace_data,
            "records": records,
        },
    )


@router.get("/partials/trends/pace", response_class=HTMLResponse)
async def pace_partial(
    request: Request,
    sport: str = Query(default="Run"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    paces = await get_pace_trends(db, user.id, sport_type=sport, weeks=12)
    pace_labels = [p.week_start.strftime("%d/%m") for p in paces]
    pace_data = [p.avg_pace_min_per_km for p in paces]
    is_cycling = sport in ("Ride", "VirtualRide")

    return templates.TemplateResponse(
        request,
        "partials/trends_pace.html",
        context={
            "pace_labels": pace_labels,
            "pace_data": pace_data,
            "sport": sport,
            "is_cycling": is_cycling,
        },
    )
