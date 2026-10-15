"""Strava → Activity: authoritative metrics, bounded resumable history reconciliation, and the athlete's zone."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.models.activity import Activity
from app.models.user import User
from app.services.activity_sources import adopt_watch_twin
from app.services import strava_state
from app.services.timezones import zone_from_strava

# what only the detail answer (GET /activities/{id}) carries: the first import reads it after the list
DETAIL_FIELDS = ("splits_metric", "best_efforts", "laps", "max_watts", "weighted_average_watts", "suffer_score",
                 "calories")
PER_PAGE = 30
DETAIL_BACKLOG = 5  # a poll completes this many activities saved from the list only with their detail
DETAIL_ASKED = "paceforge_detail_asked"  # in raw_data: its detail was asked once (got, or none shown): not again
HISTORY_DAYS = 24 * 30  # same history horizon as the first import; each sweep is bounded per poll
CORE_FIELDS = ("distance", "moving_time", "elapsed_time", "total_elevation_gain", "average_speed", "max_speed",
               "average_heartrate", "max_heartrate", "average_cadence", "average_watts")
STRUCTURAL_FIELDS = {"start_date", "sport_type", *CORE_FIELDS}
# the athlete's zone: held by at least ZONE_MAJORITY of their last ZONE_SAMPLE activities that carry one
ZONE_SAMPLE, ZONE_MAJORITY = 30, 20


def parse_date(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def activity_from_strava(data: dict, user_id: int) -> Activity:
    """The row of a Strava activity, from its list summary or its detail (what the summary lacks stays empty)."""
    return Activity(
        strava_activity_id=data["id"],
        user_id=user_id,
        sport_type=data.get("sport_type") or data.get("type") or "Unknown",
        name=data.get("name") or "Untitled",
        start_date=parse_date(data.get("start_date")),
        distance=data.get("distance") or 0,
        moving_time=data.get("moving_time") or 0,
        elapsed_time=data.get("elapsed_time") or 0,
        total_elevation_gain=data.get("total_elevation_gain") or 0,
        average_speed=data.get("average_speed"),
        max_speed=data.get("max_speed"),
        average_heartrate=data.get("average_heartrate"),
        max_heartrate=data.get("max_heartrate"),
        average_cadence=data.get("average_cadence"),
        average_watts=data.get("average_watts"),
        **{field: data.get(field) for field in DETAIL_FIELDS},
        raw_data=data,
    )


def apply_detail(activity: Activity, detail: dict, *, summary: bool = False) -> bool:
    """Apply fields Strava actually supplies, including corrections to duration, distance and heart rate.

    Missing/null optional measurements keep a watch's contribution. List summaries keep existing detail; a metric
    correction puts them back in the detail backlog. The revision invalidates Santé in every worker, including
    same-size metadata or lap edits that an aggregate of JSON lengths cannot detect.
    """
    values = {field: detail[field] for field in (*CORE_FIELDS, *DETAIL_FIELDS) if detail.get(field) is not None}
    if detail.get("name"):
        values["name"] = detail["name"]
    if detail.get("sport_type") or detail.get("type"):
        values["sport_type"] = detail.get("sport_type") or detail["type"]
    if detail.get("start_date"):
        values["start_date"] = parse_date(detail["start_date"])
    changed = set()
    for field, value in values.items():
        previous = getattr(activity, field)
        # SQLite returns naive datetimes; Strava's dates and PostgreSQL's are aware, all describing UTC instants.
        if field == "start_date" and previous is not None:
            previous = previous.replace(tzinfo=timezone.utc) if previous.tzinfo is None else previous
        if previous != value:
            changed.add(field)
            setattr(activity, field, value)
    old = activity.raw_data or {}
    # Never accept caller/provider changes to PaceForge's cache keys or revision.
    incoming = {k: v for k, v in detail.items() if not k.startswith("paceforge")}
    raw = {**old, **incoming}
    structural = bool(changed & STRUCTURAL_FIELDS)
    if summary and old.get("resource_state") == 3:
        raw["resource_state"] = 2 if structural else 3
    if structural:
        activity.streams_data = None
        activity.computed_metrics = None
        for key in ("paceforge_dplus", "paceforge_heat"):
            raw.pop(key, None)
        if summary:
            raw.pop(DETAIL_ASKED, None)
    if changed & {"laps", "splits_metric"}:
        activity.computed_metrics = None
    if "start_date" in changed or any(k in incoming and incoming[k] != old.get(k)
                                       for k in ("start_latlng", "end_latlng")):
        raw.pop("paceforge_env", None)
        raw.pop("paceforge_heat", None)
    if "average_temp" in incoming and incoming["average_temp"] != old.get("average_temp"):
        raw.pop("paceforge_heat", None)
    if not changed and raw == old:
        return False
    revision = old.get("paceforge_revision", 0)
    raw["paceforge_revision"] = (revision if type(revision) is int and revision >= 0 else 0) + 1
    activity.raw_data = raw
    return True


async def is_known(db: AsyncSession, strava_id: int) -> bool:
    return bool((await db.execute(
        select(func.count(Activity.id)).where(Activity.strava_activity_id == strava_id))).scalar())


async def save_new(db: AsyncSession, user: User, data: dict) -> Activity | None:
    """Save an unseen activity, or reconcile an existing one's summary without counting it as new."""
    if not data.get("id"):
        return None
    existing = (await db.execute(select(Activity).options(
        defer(Activity.streams_data), defer(Activity.computed_metrics)).where(
        Activity.strava_activity_id == data["id"]))).scalar_one_or_none()
    if existing is not None:
        if existing.user_id == user.id:
            apply_detail(existing, data, summary=True)
        return None
    return await adopt_watch_twin(db, activity_from_strava(data, user.id))


async def import_recent(db: AsyncSession, user: User, strava, max_pages: int, progress: dict | None = None) -> int:
    """Refresh page 1, then resume an older-history sweep, at most `max_pages` requests in total.

    A page of known activities is not the end of the history: late uploads and a previous bounded import can lie
    behind it. The cursor advances only with committed rows; at the end of the 24-month history it starts over.
    Each fresh poll still reads the newest page. `progress` reports saves even if a later request fails.
    """
    progress = {"saved": 0} if progress is None else progress
    progress.setdefault("saved", 0)
    older = strava_state.reconciliation_page(user.id, user.strava_athlete_id)
    since = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    for page in ([1] + list(range(older, older + max_pages - 1))) if max_pages > 0 else []:
        if strava.backing_off():
            break
        batch = await strava.get_recent_activities(user, per_page=PER_PAGE, page=page)
        for data in batch:
            if await save_new(db, user, data) is not None:
                progress["saved"] += 1
        await db.flush()
        dates = [parse_date(data.get("start_date")) for data in batch if data.get("start_date")]
        exhausted = len(batch) < PER_PAGE or bool(dates and min(dates) < since)
        if exhausted or page > 1:
            strava_state.reconciliation_after_commit(db, user.id, user.strava_athlete_id,
                                                      2 if exhausted else page + 1)
        if exhausted:
            break
    return progress["saved"]


async def complete_details(db: AsyncSession, user: User, strava, limit: int = DETAIL_BACKLOG) -> int:
    """Activities saved from Strava's list only (a first import cut short by the app's quota, the poll,
    « Synchroniser »): `limit` of them get their detail (splits, laps, best efforts, power, calories), the newest
    races and long runs first, as the races' D+ backfill; each asked once. Stops at the app's quota backoff, a 429
    or a Strava hiccup (the next poll carries on). Returns how many were completed."""
    from app.exceptions import StravaAPIError, StravaRateLimitError, StravaTokenError
    from app.services.race_calibration import LONG_EFFORT_KM

    race = Activity.raw_data["workout_type"].as_integer() == 1
    rows = (await db.execute(
        select(Activity).where(Activity.user_id == user.id, Activity.strava_activity_id.is_not(None),
                               Activity.raw_data["resource_state"].as_integer() == 2,  # a list summary
                               Activity.raw_data[DETAIL_ASKED].as_string().is_(None))
        .order_by(case((race, 0), (Activity.distance >= LONG_EFFORT_KM * 1000, 1), else_=2),
                  Activity.start_date.desc())
        .limit(limit))).scalars().all()
    done = 0
    for activity in rows:
        if strava.backing_off():
            break
        try:
            detail = await strava.get_activity(user, activity.strava_activity_id)
        except StravaTokenError:
            raise  # the poll's to handle (a refusal, or Strava's token endpoint failing)
        except StravaRateLimitError:
            break
        except StravaAPIError as exc:
            if exc.status_code not in (403, 404):
                break
            activity.raw_data = {**(activity.raw_data or {}), DETAIL_ASKED: True}  # deleted, private: none shown
            continue
        apply_detail(activity, detail)
        activity.raw_data = {**activity.raw_data, DETAIL_ASKED: True}
        done += 1
    if rows:
        await db.flush()
    return done


async def adopt_zone(db: AsyncSession, user: User) -> None:
    """users.timezone follows the athlete's Strava activities (« (GMT+01:00) Europe/Paris »), read again at each
    sync, but only on a clear majority: a zone held by at least ZONE_MAJORITY of their last ZONE_SAMPLE activities
    that carry one. A trip or a training camp never moves it, a move does (after 20 activities there); whatever set
    it before (none yet, the default, the phone) gives way to that majority only."""
    labels = (await db.execute(
        select(Activity.raw_data["timezone"].as_string())
        .where(Activity.user_id == user.id, Activity.strava_activity_id.is_not(None),
               Activity.raw_data["timezone"].as_string().is_not(None))
        .order_by(Activity.start_date.desc()).limit(2 * ZONE_SAMPLE))).scalars().all()
    zones = [z for z in map(zone_from_strava, labels) if z][:ZONE_SAMPLE]
    if not zones:
        return
    zone, held = Counter(zones).most_common(1)[0]
    if held >= ZONE_MAJORITY and zone != user.timezone:
        user.timezone = zone
