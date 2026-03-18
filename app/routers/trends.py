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

SPORT_COLORS = {
    "Run": "#1f2937",
    "TrailRun": "#92400e",
    "Ride": "#2563eb",
    "VirtualRide": "#60a5fa",
    "GravelRide": "#1e40af",
    "Swim": "#0891b2",
    "Walk": "#65a30d",
    "Hike": "#16a34a",
    "Yoga": "#a855f7",
    "EBikeRide": "#3b82f6",
}

SPORT_LABELS = {
    "Run": "Course",
    "TrailRun": "Trail",
    "Ride": "Vélo",
    "VirtualRide": "Vélo virtuel",
    "GravelRide": "Gravel",
    "Swim": "Natation",
    "Walk": "Marche",
    "Hike": "Rando",
    "Yoga": "Yoga",
    "EBikeRide": "VAE",
}


@router.get("/trends", response_class=HTMLResponse)
async def trends_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    volumes = await get_weekly_volume_trends(db, user.id, weeks=12)
    paces = await get_pace_trends(db, user.id, sport_type="Run", weeks=12)
    records = await get_personal_records(db, user.id)

    # Period comparison: last 4 weeks vs previous 4 weeks
    comparison = None
    if len(volumes) >= 5:
        recent_4 = volumes[-4:]
        prev_4 = volumes[-8:-4] if len(volumes) >= 8 else volumes[:max(1, len(volumes)-4)]
        r_km = sum(v.distance_km for v in recent_4)
        r_hrs = sum(v.duration_hours for v in recent_4)
        r_cnt = sum(v.count for v in recent_4)
        p_km = sum(v.distance_km for v in prev_4)
        p_hrs = sum(v.duration_hours for v in prev_4)
        p_cnt = sum(v.count for v in prev_4)
        comparison = {
            "recent_km": r_km, "prev_km": p_km,
            "recent_hrs": r_hrs, "prev_hrs": p_hrs,
            "recent_cnt": r_cnt, "prev_cnt": p_cnt,
            "km_delta": ((r_km - p_km) / p_km * 100) if p_km > 0 else 0,
            "hrs_delta": ((r_hrs - p_hrs) / p_hrs * 100) if p_hrs > 0 else 0,
            "cnt_delta": ((r_cnt - p_cnt) / p_cnt * 100) if p_cnt > 0 else 0,
        }

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

    # Build legend colors for displayed sports only
    sport_colors = {s: SPORT_COLORS.get(s, "#d1d5db") for s in sorted(all_sports)}

    # Pace sport selector: only show sports the user actually has
    pace_sport_options = []
    for sport_key, sport_label in SPORT_LABELS.items():
        if sport_key in all_sports:
            pace_sport_options.append((sport_key, sport_label))

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
            "sport_colors": sport_colors,
            "sport_labels": SPORT_LABELS,
            "pace_labels": pace_labels,
            "pace_data": pace_data,
            "records": records,
            "comparison": comparison,
            "pace_sport_options": pace_sport_options,
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
