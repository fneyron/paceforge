import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db, get_optional_user
from app.models.activity import Activity
from app.models.analysis import Analysis
from app.models.user import User
from app.schemas.activity import ActivitySummary
from app.services.readiness import calculate_race_readiness
from app.services.strava import StravaService
from app.services.training_load import calculate_training_load

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["dashboard"])

ACTIVITIES_PER_PAGE = 20


@router.get("/", response_class=HTMLResponse)
async def landing(
    request: Request,
    error: str | None = None,
    user: User | None = Depends(get_optional_user),
):
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(
        request, "login.html", context={"error": error}
    )


@router.get("/landing", response_class=HTMLResponse)
async def landing_page(request: Request, error: str | None = None):
    """Page marketing, accessible même connecté."""
    return templates.TemplateResponse(
        request, "login.html", context={"error": error, "force_public": True}
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # Trigger initial sync for existing users who haven't done it
    if not user.initial_sync_done:
        from app.tasks.initial_sync import initial_sync
        initial_sync.delay(user.id)
        logger.info("Triggered initial sync for existing user %d", user.id)

    # Only sync from Strava if user has linked + configured their app
    if user.has_strava_linked and user.has_own_strava_app:
        last_sync = request.session.get("last_strava_sync")
        if not last_sync or (now.timestamp() - last_sync) > 300:
            await _sync_recent_activities(user, db)
            request.session["last_strava_sync"] = now.timestamp()

    # Check if initial sync is in progress
    sync_in_progress = not user.initial_sync_done

    # Get activities with analysis status
    activities = await _get_activities_page(db, user.id, page=1)

    # Training load
    training_load = await calculate_training_load(db, user.id, now)

    # Race readiness (if goal is set)
    readiness = None
    if user.race_date and user.race_distance_km and user.race_date > now:
        readiness = await calculate_race_readiness(
            db, user.id, user.race_date, user.race_distance_km
        )

    # Latest weekly digest
    from app.models.weekly_digest import WeeklyDigest
    from sqlalchemy import select as sa_select
    digest_result = await db.execute(
        sa_select(WeeklyDigest)
        .where(WeeklyDigest.user_id == user.id)
        .order_by(WeeklyDigest.week_start.desc())
        .limit(1)
    )
    latest_digest = digest_result.scalar_one_or_none()

    return templates.TemplateResponse(
        request, "dashboard.html",
        context={
            "user": user,
            "activities": activities,
            "training_load": training_load,
            "readiness": readiness,
            "latest_digest": latest_digest,
            "sync_in_progress": sync_in_progress,
            "strava_not_linked": not user.has_strava_linked,
            "strava_no_app": user.has_strava_linked and not user.has_own_strava_app,
            "credentials_invalid": user.has_own_strava_app and not user.strava_credentials_valid,
            "page": 1,
            "has_more": len(activities) >= ACTIVITIES_PER_PAGE,
        },
    )


@router.get("/partials/sync-status", response_class=HTMLResponse)
async def sync_status(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns empty HTML when sync is done (removes the banner)."""
    # Refresh user from DB
    await db.refresh(user)
    if user.initial_sync_done:
        # Sync complete — return a script that reloads the page to show new data
        return HTMLResponse(
            '<script>window.location.reload();</script>'
        )
    # Still syncing — keep the banner with polling
    return templates.TemplateResponse(
        request,
        "partials/sync_banner.html",
        context={"sync_in_progress": True},
    )


@router.get("/partials/activities", response_class=HTMLResponse)
async def activities_partial(
    request: Request,
    page: int = Query(default=1, ge=1),
    sport_type: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    activities = await _get_activities_page(db, user.id, page, sport_type)
    return templates.TemplateResponse(
        request, "partials/activity_list.html",
        context={
            "activities": activities,
            "page": page,
            "has_more": len(activities) >= ACTIVITIES_PER_PAGE,
        },
    )


@router.get("/partials/activity/{activity_id}/row", response_class=HTMLResponse)
async def activity_row_partial(
    request: Request,
    activity_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Activity)
        .where(Activity.id == activity_id, Activity.user_id == user.id)
    )
    activity = result.scalar_one_or_none()
    if not activity:
        return HTMLResponse("")

    summary = _activity_to_summary(activity)
    return templates.TemplateResponse(
        request, "partials/activity_row.html",
        context={"activity": summary},
    )


async def _get_activities_page(
    db: AsyncSession, user_id: int, page: int = 1, sport_type: str | None = None
) -> list[ActivitySummary]:
    from sqlalchemy.orm import selectinload

    query = select(Activity).options(selectinload(Activity.analysis)).where(Activity.user_id == user_id)

    if sport_type:
        query = query.where(Activity.sport_type == sport_type)

    query = (
        query.order_by(Activity.start_date.desc())
        .offset((page - 1) * ACTIVITIES_PER_PAGE)
        .limit(ACTIVITIES_PER_PAGE)
    )

    result = await db.execute(query)
    activities = result.scalars().all()

    return [_activity_to_summary(a) for a in activities]


def _activity_to_summary(activity: Activity) -> ActivitySummary:
    return ActivitySummary(
        id=activity.id,
        strava_activity_id=activity.strava_activity_id,
        sport_type=activity.sport_type,
        name=activity.name,
        start_date=activity.start_date,
        distance=activity.distance,
        moving_time=activity.moving_time,
        average_speed=activity.average_speed,
        average_heartrate=activity.average_heartrate,
        total_elevation_gain=activity.total_elevation_gain,
        has_analysis=activity.analysis is not None,
    )


async def _sync_recent_activities(user: User, db: AsyncSession) -> None:
    """Sync recent activities from Strava. Paginates until we find existing ones."""
    try:
        strava = StravaService.for_user(db, user)
        page = 1
        total_synced = 0

        while page <= 5:  # Max 5 pages (150 activities) per sync
            strava_activities = await strava.get_recent_activities(
                user, per_page=30, page=page
            )
            if not strava_activities:
                break

            all_known = True
            for data in strava_activities:
                strava_id = data.get("id")
                if not strava_id:
                    continue

                # Check if already exists
                result = await db.execute(
                    select(func.count(Activity.id)).where(
                        Activity.strava_activity_id == strava_id
                    )
                )
                if result.scalar() > 0:
                    continue

                all_known = False
                from datetime import datetime
                start_date = data.get("start_date")
                if isinstance(start_date, str):
                    start_date = datetime.fromisoformat(
                        start_date.replace("Z", "+00:00")
                    )

                activity = Activity(
                    strava_activity_id=strava_id,
                    user_id=user.id,
                    sport_type=data.get("sport_type", data.get("type", "Unknown")),
                    name=data.get("name", "Untitled"),
                    start_date=start_date,
                    distance=data.get("distance", 0),
                    moving_time=data.get("moving_time", 0),
                    elapsed_time=data.get("elapsed_time", 0),
                    total_elevation_gain=data.get("total_elevation_gain", 0),
                    average_speed=data.get("average_speed"),
                    max_speed=data.get("max_speed"),
                    average_heartrate=data.get("average_heartrate"),
                    max_heartrate=data.get("max_heartrate"),
                    average_cadence=data.get("average_cadence"),
                    average_watts=data.get("average_watts"),
                    raw_data=data,
                )
                db.add(activity)
                total_synced += 1

            # If all activities on this page were already known, stop paginating
            if all_known:
                break
            page += 1

        await db.flush()
        if total_synced:
            logger.info(
                "Synced %d new activities from Strava for user %d",
                total_synced, user.id,
            )
    except Exception:
        logger.exception("Failed to sync activities from Strava")
