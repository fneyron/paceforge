"""Activity detail page — light version (laps, splits).

The AI-analysis flow that used to live here is gone; this page must never
promise it. What it does offer: a bridge to the simulator
("Comparer à un parcours"), which is what feeds the prediction calibration.
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.exceptions import ActivityNotFoundError
from app.models.activity import Activity
from app.models.route import Route
from app.models.user import User
from app.schemas.activity import ActivityDetail
from app.services.activity_dedupe import sport_group
from app.services.viz import fr_num

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fr_num"] = fr_num

router = APIRouter(tags=["activity"])

# A saved route is a plausible match for this outing if distances agree.
_COMPARE_DISTANCE_TOL = 0.35
_COMPARE_MAX = 5
# The detail renders these schema fields and the computed summary. Provider
# payloads, GPS streams and legacy AI analyses can be much larger than the page.
_DETAIL_COLUMNS = tuple(getattr(Activity, name) for name in ActivityDetail.model_fields
                        if name not in {"has_analysis", "is_duplicate", "is_false_start", "best_efforts"}) + (
    Activity.computed_metrics,
)


@router.get("/activity/{activity_id}", response_class=HTMLResponse)
async def activity_detail(
    request: Request,
    activity_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(*_DETAIL_COLUMNS).where(Activity.id == activity_id, Activity.user_id == user.id)
    )
    activity = result.one_or_none()
    if not activity:
        raise ActivityNotFoundError(activity_id)

    detail = ActivityDetail.model_validate(activity)

    # Routes worth comparing against (foot activities only), closest first: their id, name and distance (a
    # course_json weighs up to 300 KiB a route; the link needs none of it).
    compare_routes: list = []
    if sport_group(activity.sport_type) in ("run", "trail") and activity.distance:
        dist_km = activity.distance / 1000
        routes_q = await db.execute(
            select(Route.id, Route.name, Route.total_distance_km)
            .where(Route.user_id == user.id, Route.sport_type.notin_(("bike", "triathlon")))
        )
        candidates = [
            r for r in routes_q.all()
            if r.total_distance_km
            and abs(r.total_distance_km - dist_km) / max(r.total_distance_km, 1.0) <= _COMPARE_DISTANCE_TOL
        ]
        candidates.sort(key=lambda r: abs(r.total_distance_km - dist_km))
        compare_routes = candidates[:_COMPARE_MAX]

    return templates.TemplateResponse(
        request, "activity_detail.html",
        context={
            "user": user,
            "activity": detail,
            "compare_routes": compare_routes,
        },
    )
