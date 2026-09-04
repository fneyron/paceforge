import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db, get_optional_user
from app.models.activity import Activity
from app.models.user import User
from app.schemas.activity import ActivitySummary
from app.services.activity_dedupe import SPORT_GROUPS, find_duplicate_ids, is_false_start
from app.services.readiness import calculate_race_readiness
from app.services.strava import StravaService
from app.services.training_load import calculate_training_load

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["dashboard"])

# The history is paginated by WEEKS (not by row count) so a week is never split
# across two "load more" chunks and weekly totals stay honest.
WEEKS_PER_PAGE = 6
FILTERS = [(None, "Tout"), ("run", "Course"), ("trail", "Trail"), ("bike", "Vélo"), ("other", "Autre")]
_FILTER_KEYS = {"run", "trail", "bike", "other"}
_MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


@router.get("/", response_class=HTMLResponse)
async def landing(
    request: Request,
    error: str | None = None,
    user: User | None = Depends(get_optional_user),
):
    if user:
        return RedirectResponse(url="/simulator", status_code=302)
    return templates.TemplateResponse(
        request, "login.html", context={"error": error}
    )


@router.get("/landing", response_class=HTMLResponse)
async def landing_page(request: Request, error: str | None = None):
    """Page marketing, accessible même connecté."""
    return templates.TemplateResponse(
        request, "login.html", context={"error": error, "force_public": True}
    )


@router.get("/dashboard")
async def dashboard(user: User = Depends(get_current_user)):
    """Legacy home: the weekly summary now lives on the activities page."""
    return RedirectResponse(url="/activities", status_code=302)


@router.get("/activities", response_class=HTMLResponse)
async def activities_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    sport: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Training log: this week + load at the top, then activities grouped by week."""
    sport = sport if sport in _FILTER_KEYS else None
    now = datetime.now(timezone.utc)

    weeks, has_more = await _week_groups(db, user.id, page, sport)
    week = await _this_week_summary(db, user.id, now)
    training_load = await calculate_training_load(db, user.id, now)

    readiness = None
    if user.race_date and user.race_distance_km and user.race_date > now:
        readiness = await calculate_race_readiness(
            db, user.id, user.race_date, user.race_distance_km
        )

    return templates.TemplateResponse(
        request, "activities.html",
        context={
            "user": user,
            "weeks": weeks,
            "has_more": has_more,
            "page": page,
            "sport": sport,
            "filters": FILTERS,
            "week": week,
            "training_load": training_load,
            "readiness": readiness,
            "last_sync": _humanize_since(user.last_activity_poll_at, now),
        },
    )


@router.get("/partials/activities", response_class=HTMLResponse)
async def activities_partial(
    request: Request,
    page: int = Query(default=1, ge=1),
    sport: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sport = sport if sport in _FILTER_KEYS else None
    weeks, has_more = await _week_groups(db, user.id, page, sport)
    return templates.TemplateResponse(
        request, "partials/activity_weeks.html",
        context={"weeks": weeks, "has_more": has_more, "page": page, "sport": sport, "oob": True},
    )


@router.post("/api/sync", response_class=HTMLResponse)
async def manual_sync(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a Strava activity sync."""
    count_before_q = await db.execute(
        select(func.count(Activity.id)).where(Activity.user_id == user.id)
    )
    count_before = count_before_q.scalar() or 0

    if user.has_strava_linked and user.has_own_strava_app:
        await _sync_recent_activities(user, db)
        request.session["last_strava_sync"] = datetime.now().timestamp()

    count_after_q = await db.execute(
        select(func.count(Activity.id)).where(Activity.user_id == user.id)
    )
    count_after = count_after_q.scalar() or 0
    new_count = max(0, count_after - count_before)

    if new_count > 0:
        # Show the result, then refresh so the new rows land in their week.
        return HTMLResponse(
            f'<span class="text-emerald-700">{new_count} nouvelle(s) activité(s) importée(s).</span>'
            "<script>setTimeout(function () { window.location.reload(); }, 700);</script>"
        )
    if not user.has_strava_linked:
        msg = "Strava non connecté."
    elif not user.has_own_strava_app:
        msg = "Configure ton app Strava dans les Réglages pour activer la sync."
    else:
        msg = "Déjà à jour — aucune nouvelle activité."
    return HTMLResponse(f"<span>{msg}</span>")


# ── helpers ─────────────────────────────────────────────────────────────────

def _monday(dt: datetime) -> datetime:
    return (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def _sport_filter(query, sport: str | None):
    if sport in SPORT_GROUPS:
        return query.where(Activity.sport_type.in_(SPORT_GROUPS[sport]))
    if sport == "other":
        known = [t for types in SPORT_GROUPS.values() for t in types]
        return query.where(Activity.sport_type.not_in(known))
    return query


def _fmt_hours(seconds: float) -> str:
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}" if h else f"{m} min"


def _week_label(monday: datetime, this_monday: datetime) -> str:
    weeks_ago = (this_monday.date() - monday.date()).days // 7
    if weeks_ago == 0:
        return "Cette semaine"
    if weeks_ago == 1:
        return "Semaine dernière"
    sunday = monday + timedelta(days=6)
    year = f" {monday.year}" if monday.year != this_monday.year else ""
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day} {_MONTHS_FR[monday.month - 1]}{year}"
    return f"{monday.day} {_MONTHS_FR[monday.month - 1]} – {sunday.day} {_MONTHS_FR[sunday.month - 1]}{year}"


def _humanize_since(dt: datetime | None, now: datetime) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    minutes = int((now - dt).total_seconds() // 60)
    if minutes < 1:
        return "moins d'une minute"
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h"
    return f"{hours // 24} j"


def _activity_to_summary(activity: Activity, duplicate_ids: frozenset | set = frozenset()) -> ActivitySummary:
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
        is_duplicate=activity.id in duplicate_ids,
        is_false_start=is_false_start(activity),
    )


async def _week_groups(
    db: AsyncSession, user_id: int, page: int, sport: str | None
) -> tuple[list[dict], bool]:
    """Activities of a WEEKS_PER_PAGE window, grouped by week (newest first)."""
    now = datetime.now(timezone.utc)
    this_monday = _monday(now)
    window_end = this_monday + timedelta(weeks=1) - timedelta(weeks=(page - 1) * WEEKS_PER_PAGE)
    window_start = window_end - timedelta(weeks=WEEKS_PER_PAGE)

    query = select(Activity).where(
        Activity.user_id == user_id,
        Activity.start_date >= window_start,
        Activity.start_date < window_end,
    )
    query = _sport_filter(query, sport).order_by(Activity.start_date.desc())
    activities = (await db.execute(query)).scalars().all()
    duplicate_ids = find_duplicate_ids(activities)

    groups: dict[str, dict] = {}
    for a in activities:
        summary = _activity_to_summary(a, duplicate_ids)
        monday = _monday(a.start_date)
        key = monday.strftime("%Y-%m-%d")
        g = groups.setdefault(key, {
            "key": key, "monday": monday, "activities": [],
            "km": 0.0, "dplus": 0.0, "seconds": 0, "count": 0,
        })
        g["activities"].append(summary)
        if not (summary.is_duplicate or summary.is_false_start):
            g["km"] += (a.distance or 0) / 1000
            g["dplus"] += a.total_elevation_gain or 0
            g["seconds"] += a.moving_time or 0
            g["count"] += 1

    weeks = []
    for key in sorted(groups, reverse=True):
        g = groups[key]
        g["hours_formatted"] = _fmt_hours(g["seconds"])
        g["label"] = _week_label(g["monday"], this_monday)
        weeks.append(g)

    older = _sport_filter(
        select(func.count(Activity.id)).where(
            Activity.user_id == user_id, Activity.start_date < window_start
        ),
        sport,
    )
    has_more = ((await db.execute(older)).scalar() or 0) > 0
    return weeks, has_more


async def _this_week_summary(db: AsyncSession, user_id: int, now: datetime) -> dict:
    """This week's totals across all sports, duplicates and false starts excluded."""
    monday = _monday(now)
    result = await db.execute(
        select(Activity)
        .where(Activity.user_id == user_id, Activity.start_date >= monday)
        .order_by(Activity.start_date.desc())
    )
    activities = result.scalars().all()
    duplicate_ids = find_duplicate_ids(activities)
    kept = [a for a in activities if a.id not in duplicate_ids and not is_false_start(a)]
    seconds = sum(a.moving_time or 0 for a in kept)
    return {
        "km": sum((a.distance or 0) / 1000 for a in kept),
        "dplus": sum(a.total_elevation_gain or 0 for a in kept),
        "hours": seconds / 3600,
        "count": len(kept),
    }


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
