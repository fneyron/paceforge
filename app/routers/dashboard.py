import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db, get_optional_user
from app.features import cycling_enabled
from app.models.activity import Activity
from app.models.user import User
from app.schemas.activity import ActivitySummary
from app.services.activity_dedupe import SPORT_GROUPS, find_duplicate_ids, is_false_start
from app.services.activity_sources import adopt_watch_twin
from app.services.model_stats import load_model_stats
from app.services.strava import StravaService
from app.services.training_load import calculate_training_load
from app.services.training_view import MEASURES, spike_ids, training_top
from app.services.viz import d_short, hm
from app.services.viz import dplus as dplus_fmt

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["cycling_enabled"] = cycling_enabled

router = APIRouter(tags=["dashboard"])

HOME = "/sante"  # where a signed-in athlete lands: « Mets Santé en premier dans l'application »

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
    if user:  # signed in: Santé is the home (owner, 2026-10-08)
        return RedirectResponse(url=HOME, status_code=302)
    return templates.TemplateResponse(
        request, "login.html", context={"error": error, "stats": load_model_stats()}
    )


@router.get("/landing", response_class=HTMLResponse)
async def landing_page(request: Request, error: str | None = None):
    """Page marketing, accessible même connecté."""
    return templates.TemplateResponse(
        request, "login.html",
        context={"error": error, "force_public": True, "stats": load_model_stats()},
    )


@router.get("/methode", response_class=HTMLResponse)
async def methode_page(request: Request, user: User | None = Depends(get_optional_user)):
    """How the plan is computed: data, model, validation, limits. Public.

    Always the public layout; ``viewer`` (not ``user``, which would switch
    base.html to the app shell) only changes the header link.
    """
    return templates.TemplateResponse(
        request, "methode.html",
        context={"viewer": user, "stats": load_model_stats()},
    )


@router.get("/dashboard")
async def dashboard(user: User = Depends(get_current_user)):
    """Legacy home (an installed app opened from an old manifest lands here): the home, Santé."""
    return RedirectResponse(url=HOME, status_code=302)


@router.get("/activities", response_class=HTMLResponse)
async def activities_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    sport: str | None = None,
    m: str | None = None,
    depuis: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Training log: the 7 / 28-day volume at the top, « Semaines » on page 1 (one measure, `m`: duree, distance
    or dplus; the sport filter applies to it and to the list), then the activities grouped by week. `depuis` (a
    day of the last month, Santé's Entraînement card's link): only the activities from that day, as the card
    counts them."""
    sport = sport if sport in _FILTER_KEYS else None
    m = m if m in {k for k, _, _ in MEASURES} else None
    now = datetime.now(timezone.utc)
    training_load = await calculate_training_load(db, user.id, now)
    recent = await _recent(db, user.id, depuis) if depuis else None
    if recent is not None:
        return templates.TemplateResponse(request, "activities.html", context={
            "user": user, "recent": recent, "training_load": training_load, "weeks": [], "has_more": False,
            "page": 1, "sport": None, "filters": FILTERS, "training": None, "spikes": set()})

    weeks, has_more, spikes = await _week_groups(db, user.id, page, sport)
    # Semaines and FC en footing: on the first page, for the sport chosen
    training = await training_top(db, user.id, now, sport, m) if page == 1 else None

    return templates.TemplateResponse(
        request, "activities.html",
        context={
            "user": user,
            "weeks": weeks,
            "has_more": has_more,
            "page": page,
            "sport": sport,
            "filters": FILTERS,
            "training_load": training_load,
            "training": training,
            "spikes": spikes,
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
    weeks, has_more, spikes = await _week_groups(db, user.id, page, sport)
    return templates.TemplateResponse(
        request, "partials/activity_weeks.html",
        context={"weeks": weeks, "has_more": has_more, "page": page, "sport": sport, "oob": True, "spikes": spikes},
    )


@router.post("/api/sync", response_class=HTMLResponse)
async def manual_sync(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a Strava activity sync."""
    new_count = 0  # sessions Strava brought, a watch's row taken over included
    if user.has_strava_linked and user.has_own_strava_app:
        new_count = await _sync_recent_activities(user, db)
        request.session["last_strava_sync"] = datetime.now().timestamp()

    if new_count > 0:
        # Show the result, then refresh so the new rows land in their week.
        return HTMLResponse(
            f'<span class="text-emerald-700">{new_count} nouvelle(s) activité(s) importée(s).</span>'
            "<script>setTimeout(function () { window.location.reload(); }, 700);</script>"
        )
    if not user.has_strava_linked:
        msg = "Strava non connecté : connecte-le dans les Réglages."
    elif not user.has_own_strava_app:
        msg = "Reconnecte Strava dans les Réglages pour activer la sync."
    else:
        msg = "Déjà à jour — aucune nouvelle activité."
    return HTMLResponse(f"<span>{msg}</span>")


# ── helpers ─────────────────────────────────────────────────────────────────

RECENT_MAX_DAYS = 31  # how far back Santé's link may open the list (its 7 days, a cycle's 8 at most)


async def _recent(db: AsyncSession, user_id: int, depuis: str) -> dict | None:
    """What Santé's Entraînement card counts, for its link (owner, 2026-10-09: « Ça ne filtre pas sur la
    semaine ? »): the sessions from `depuis` (the athlete's local day) to now, duplicates and false starts left out
    exactly as Santé reads them, newest first, their moving time summed alike (the card's figure); None for a day
    that is not a date of the last month (the whole list then)."""
    from app.services import sante_training as st

    try:
        since = date.fromisoformat(depuis)
    except ValueError:
        return None
    today = await st.athlete_today(db, user_id)
    if not today - timedelta(days=RECENT_MAX_DAYS) <= since <= today:
        return None
    sessions = sorted((s for s in await st.load_sessions(db, user_id, today) if s.day >= since),
                      key=lambda s: s.start, reverse=True)
    ids = [s.id for s in sessions]
    rows = {a.id: a for a in (await db.execute(select(Activity).where(Activity.id.in_(ids)))).scalars()} if ids else {}
    dplus = sum(s.dplus for s in sessions)
    return {"label": "7 derniers jours" if (today - since).days in (6, 7) else f"Depuis le {d_short(since)}",
            "activities": [_activity_to_summary(rows[s.id]) for s in sessions if s.id in rows],
            "hours": hm(sum(s.minutes for s in sessions)), "km": sum(s.km for s in sessions),
            "dplus_formatted": dplus_fmt(dplus) if round(dplus) >= 1 else None}


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
    """The house duration (viz.hm, rounded to the minute): Activités › Semaines prints the same week alike."""
    return hm(seconds / 60)


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
) -> tuple[list[dict], bool, set[int]]:
    """Activities of a WEEKS_PER_PAGE window, grouped by week (newest first), and the ids of the single-run
    spikes among them (their rows say « plus longue que d'habitude », training_view.spike_ids)."""
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
        g["dplus_formatted"] = dplus_fmt(g["dplus"]) if round(g["dplus"]) >= 1 else None
        g["label"] = _week_label(g["monday"], this_monday)
        weeks.append(g)

    older = _sport_filter(
        select(func.count(Activity.id)).where(
            Activity.user_id == user_id, Activity.start_date < window_start
        ),
        sport,
    )
    has_more = ((await db.execute(older)).scalar() or 0) > 0
    shown = {a.id for a in activities}
    spikes = (await spike_ids(db, user_id, window_start.date(), now)) & shown if shown else set()
    return weeks, has_more, spikes


async def _sync_recent_activities(user: User, db: AsyncSession) -> int:
    """Sync recent activities from Strava. Paginates until we find existing ones.
    Returns how many Strava sessions were saved."""
    total_synced = 0
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
                activity = await adopt_watch_twin(db, activity)  # a watch may have brought it first
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
    return total_synced
