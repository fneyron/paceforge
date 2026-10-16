import logging
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db, get_optional_user
from app.features import cycling_enabled
from app.models.activity import Activity
from app.models.user import User
from app.schemas.activity import ActivitySummary
from app.services import sante_training as st
from app.services import strava_state
from app.services.activity_dedupe import SPORT_GROUPS, find_duplicate_ids, is_false_start
from app.services.model_stats import load_model_stats
from app.services.strava import StravaService
from app.services.strava_activities import adopt_zone, import_recent
from app.services.timezones import athlete_zone, local_date, zone_of
from app.services.training_view import MEASURES, spike_ids, training_top
from app.services.viz import d_short, hm
from app.services.viz import dplus as dplus_fmt

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["cycling_enabled"] = cycling_enabled

router = APIRouter(tags=["dashboard"])

HOME = "/sante"  # Santé is the main dashboard

# The history is paginated by WEEKS (not by row count) so a week is never split
# across two "load more" chunks and weekly totals stay honest.
WEEKS_PER_PAGE = 6
SYNC_PAGES = 5  # « Synchroniser »: 150 activities at most
SYNC_COOLDOWN_S = 60  # « Synchroniser » asks Strava once a minute per athlete at most (a click more: nothing new)
SYNC_RUNNING_S = 120  # « Synchroniser » running (a request lost meanwhile frees it after this)
FILTERS = [(None, "Tout"), ("run", "Course"), ("trail", "Trail"), ("bike", "Vélo"), ("other", "Autre")]
_FILTER_KEYS = {"run", "trail", "bike", "other"}
_MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
# History rows need only these values. Loading Activity entities also decodes
# every GPS stream / source payload and eagerly fetches their analyses.
_SUMMARY_COLUMNS = (
    Activity.id, Activity.strava_activity_id, Activity.sport_type, Activity.name,
    Activity.start_date, Activity.distance, Activity.moving_time,
    Activity.average_speed, Activity.average_heartrate, Activity.total_elevation_gain,
)


@router.get("/", response_class=HTMLResponse)
async def landing(
    request: Request,
    error: str | None = None,
    user: User | None = Depends(get_optional_user),
):
    if user:  # signed in: the daily dashboard
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
    """Legacy home (an installed app opened from an old manifest lands here): Santé."""
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
    """Training log: « Semaines » visible on page 1 (one measure, `m`: duree, distance
    or dplus; the sport filter applies to it and to the list), then the activities grouped by week. `depuis` (a
    day of the last month, Santé's Entraînement card's link): only the activities from that day, as the card
    counts them."""
    sport = sport if sport in _FILTER_KEYS else None
    m = m if m in {k for k, _, _ in MEASURES} else None
    now, zone, today = await _clock(db, user)  # one day and clock for every part of this request
    recent = await _recent(db, user.id, depuis, zone) if depuis else None
    if recent is not None:
        return templates.TemplateResponse(request, "activities.html", context={
            "user": user, "recent": recent, "weeks": [], "has_more": False,
            "page": 1, "sport": None, "filters": FILTERS, "training": None, "spikes": set()})

    sessions = None
    if page == 1:
        try:
            sessions = await st.load_sessions(db, user.id, today, zone=zone)
        except Exception:  # the optional training context must never hide the history
            logger.exception("Activités: the training sessions failed for user %d", user.id)
    weeks, has_more, spikes = await _week_groups(db, user.id, page, sport, now, zone,
                                                today=today, sessions=sessions)
    # Semaines and FC en footing: on the first page, for the sport chosen
    training = await training_top(db, user.id, now, sport, m, zone=zone,
                                   today=today, sessions=sessions) if page == 1 else None

    return templates.TemplateResponse(
        request, "activities.html",
        context={
            "user": user,
            "weeks": weeks,
            "has_more": has_more,
            "page": page,
            "sport": sport,
            "filters": FILTERS,
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
    now, zone, today = await _clock(db, user)
    weeks, has_more, spikes = await _week_groups(db, user.id, page, sport, now, zone, today=today)
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
    if request.headers.get("accept") == "application/json":
        if request.headers.get("x-paceforge-refresh") != "1":
            raise HTTPException(403)
        received, errors = 0, []
        if user.has_strava_linked:
            if not user.has_strava_app or not user.strava_credentials_valid:
                errors.append("Reconnecte Strava dans Réglages.")
            else:
                try:
                    received = await _sync_recent_activities(user, db, strict=True)
                except SyncRunning:
                    pass  # another tab's sync brings them: nothing to report
                except Exception:
                    await db.rollback()
                    errors.append("Strava : synchronisation incomplète. Réessaie dans un instant.")
        return JSONResponse({"received": received, "errors": errors}, headers={"Cache-Control": "no-store"})
    new_count = 0  # sessions Strava brought, a watch's row taken over included
    running = failed = False
    if user.has_strava_linked and user.has_strava_app and user.strava_credentials_valid:
        progress = {"saved": 0}
        try:
            new_count = await _sync_recent_activities(user, db, strict=True, progress=progress)
        except SyncRunning:
            running = True
        except Exception:  # what it saved before failing stays (this request commits it): said as such
            new_count, failed = progress["saved"], True

    if new_count > 0:
        # Show the result, then refresh so the new rows land in their week.
        return HTMLResponse(
            f'<span class="text-emerald-700">{new_count} nouvelle(s) activité(s) importée(s).</span>'
            "<script>setTimeout(function () { window.location.reload(); }, 700);</script>"
        )
    if not user.has_strava_linked:
        msg = "Strava non connecté : connecte-le dans les Réglages."
    elif not user.has_strava_app or not user.strava_credentials_valid:
        msg = "Reconnecte Strava dans les Réglages pour activer la sync."
    elif running:
        msg = "Synchro déjà en cours."
    elif failed:
        msg = "Strava : synchronisation incomplète. Réessaie dans un instant."
    else:
        msg = "Déjà à jour — aucune nouvelle activité."
    return HTMLResponse(f"<span>{msg}</span>")


# ── helpers ─────────────────────────────────────────────────────────────────

RECENT_MAX_DAYS = 31  # how far back Santé's link may open the list (its 7 days, a cycle's 8 at most)


async def _clock(db: AsyncSession, user: User) -> tuple[datetime, tzinfo, date]:
    """Activités' « now » and the athlete's clock (sante_training.athlete_clock, Santé's: their weeks run Monday to
    Sunday there; read once, then passed to every part of the page). « now » comes from the source of « today »
    (st.athlete_today, the day Santé reads, and the one tests pin): the wall clock, unless that day is further from
    it than a time zone can be (then its noon) — so the weeks, « Semaines », the 7 / 28-day volume and the spikes
    all read one day."""
    now = datetime.now(timezone.utc)
    try:
        zone = await st.athlete_clock(db, user.id, user)
        today = await st.athlete_today(db, user.id, now, zone=zone)
    except Exception:
        logger.exception("Activités: the athlete's day failed for user %d", user.id)
        zone = athlete_zone(user)
        return now, zone, local_date(now, zone)
    if abs((today - local_date(now, zone)).days) > 1:
        now = datetime.combine(today, time(12), tzinfo=zone).astimezone(timezone.utc)
    return now, zone, today


async def _recent(db: AsyncSession, user_id: int, depuis: str, zone: tzinfo | None = None) -> dict | None:
    """What Santé's Entraînement card counts, for its link (owner, 2026-10-09: « Ça ne filtre pas sur la
    semaine ? »): the sessions from `depuis` (the athlete's local day) to now, duplicates and false starts left out
    exactly as Santé reads them, newest first, their moving time summed alike (the card's figure); None for a day
    that is not a date of the last month (the whole list then)."""
    try:
        since = date.fromisoformat(depuis)
    except ValueError:
        return None
    zone = zone if zone is not None else await st.athlete_clock(db, user_id)
    today = await st.athlete_today(db, user_id, zone=zone)
    if not today - timedelta(days=RECENT_MAX_DAYS) <= since <= today:
        return None
    # every session that ended in those days, as Santé counts them (Session.end_day: an overnight race too)
    sessions = sorted((s for s in await st.load_sessions(db, user_id, today, zone=zone) if s.end_day >= since),
                      key=lambda s: s.start, reverse=True)
    ids = [s.id for s in sessions]
    rows = {a.id: a for a in (await db.execute(select(*_SUMMARY_COLUMNS).where(
        Activity.user_id == user_id, Activity.id.in_(ids)
    ))).all()} if ids else {}
    dplus = sum(s.dplus for s in sessions)
    return {"label": "7 derniers jours" if (today - since).days in (6, 7) else f"Depuis le {d_short(since)}",
            "activities": [_activity_to_summary(rows[s.id], zone=zone) for s in sessions if s.id in rows],
            "hours": hm(sum(s.minutes for s in sessions)), "km": sum(s.km for s in sessions),
            "dplus_formatted": dplus_fmt(dplus) if round(dplus) >= 1 else None}


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _midnight(day: date, zone: tzinfo) -> datetime:
    """The day's local 00:00, in UTC (as activities are stored)."""
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)


def _local(moment: datetime, zone: tzinfo | None) -> datetime:
    """An activity's start on the athlete's clock (stored in UTC, naive ones too)."""
    if zone is None:
        return moment
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(zone)


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


def _week_label(monday: date, this_monday: date) -> str:
    weeks_ago = (this_monday - monday).days // 7
    if weeks_ago == 0:
        return "Cette semaine"
    if weeks_ago == 1:
        return "Semaine dernière"
    sunday = monday + timedelta(days=6)
    year = f" {monday.year}" if monday.year != this_monday.year else ""
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day} {_MONTHS_FR[monday.month - 1]}{year}"
    return f"{monday.day} {_MONTHS_FR[monday.month - 1]} – {sunday.day} {_MONTHS_FR[sunday.month - 1]}{year}"


def _activity_to_summary(activity: Activity | Row, duplicate_ids: frozenset | set = frozenset(),
                         zone: tzinfo | None = None) -> ActivitySummary:
    return ActivitySummary(
        id=activity.id,
        strava_activity_id=activity.strava_activity_id,
        sport_type=activity.sport_type,
        name=activity.name,
        start_date=_local(activity.start_date, zone),  # its row's day and date are the athlete's
        distance=activity.distance,
        moving_time=activity.moving_time,
        average_speed=activity.average_speed,
        average_heartrate=activity.average_heartrate,
        total_elevation_gain=activity.total_elevation_gain,
        is_duplicate=activity.id in duplicate_ids,
        is_false_start=is_false_start(activity),
    )


async def _week_groups(
    db: AsyncSession, user_id: int, page: int, sport: str | None, now: datetime | None = None,
    zone: tzinfo | None = None, *, today: date | None = None, sessions: list[st.Session] | None = None,
) -> tuple[list[dict], bool, set[int]]:
    """Activities of a WEEKS_PER_PAGE window, grouped by week (newest first), and the ids of the single-run
    spikes among them (their rows say « plus longue que d'habitude », training_view.spike_ids). The athlete's
    weeks: Monday to Sunday in their zone (a run at 00:30 on Monday is that week's), « now » from _clock."""
    now, zone = now or datetime.now(timezone.utc), zone or zone_of(None)
    this_monday = _monday(local_date(now, zone))
    end = this_monday + timedelta(weeks=1) - timedelta(weeks=(page - 1) * WEEKS_PER_PAGE)
    start = end - timedelta(weeks=WEEKS_PER_PAGE)
    window_start, window_end = _midnight(start, zone), _midnight(end, zone)

    # Keep splits' original truth value for duplicate ranking (including older
    # malformed values), but leave all unrelated bulk data in the database.
    query = select(*_SUMMARY_COLUMNS, Activity.splits_metric).where(
        Activity.user_id == user_id,
        Activity.start_date >= window_start,
        Activity.start_date < window_end,
    )
    query = _sport_filter(query, sport).order_by(Activity.start_date.desc())
    activities = (await db.execute(query)).all()
    duplicate_ids = find_duplicate_ids(activities)

    groups: dict[str, dict] = {}
    for a in activities:
        summary = _activity_to_summary(a, duplicate_ids, zone)
        monday = _monday(summary.start_date.date())
        key = monday.isoformat()
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
    spikes = (await spike_ids(db, user_id, start, now, zone=zone, today=today,
                               sessions=sessions)) & shown if shown else set()
    return weeks, has_more, spikes


class SyncRunning(Exception):
    """Another « Synchroniser » of this athlete is running (another tab, a double tap)."""


async def _sync_recent_activities(user: User, db: AsyncSession, *, strict: bool = False,
                                  progress: dict | None = None) -> int:
    """« Synchroniser »: the athlete's newest Strava activities until a page is all known (strava_activities).
    Once a minute per athlete at most, from the end of the last one: a click within it asks nothing and reads as
    nothing new; one while a sync of theirs runs raises SyncRunning; a failed one may be tried again at once.
    Returns how many Strava sessions were saved, those saved before a failure included (`progress["saved"]` also
    tells them when it raises)."""
    running = f"manual:{user.id}:running"
    if not strava_state.claim(running, SYNC_RUNNING_S):
        raise SyncRunning()
    try:
        if strava_state.read(f"manual:{user.id}") is not None:
            return 0
        progress = {"saved": 0} if progress is None else progress
        try:
            strava = StravaService.for_user(db, user)
            await import_recent(db, user, strava, SYNC_PAGES, progress)
            await db.flush()
            await adopt_zone(db, user)
            user.last_activity_poll_at = datetime.now(timezone.utc)  # Réglages' « Dernière synchro »
            strava_state.polled(user.id)  # as good as the round's poll
            strava_state.mark(f"manual:{user.id}", SYNC_COOLDOWN_S)
            if progress["saved"]:
                logger.info(
                    "Synced %d new activities from Strava for user %d",
                    progress["saved"], user.id,
                )
        except Exception:
            logger.exception("Failed to sync activities from Strava")
            if strict:
                raise
        return progress["saved"]
    finally:
        strava_state.release(running)
