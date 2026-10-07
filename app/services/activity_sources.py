"""One row per outing, whichever service brought it first.

Strava, Garmin and COROS can each send the same session. The first one in
creates the row; the others only attach their id to it:
- a watch (Garmin, COROS) finding the outing already there links its id to
  that row (`find_twin`);
- Strava finding a watch-only row of the outing takes it over: Strava's fields
  (splits, name, suffer score…) replace the watch's, the watch ids stay
  (`adopt_watch_twin`);
- rows that were saved twice before this rule existed are merged
  (`merge_twins`).
The outing test is activity_dedupe's: same sport family, starts within 3 min,
distances within 15 % (or no distance on either and durations within 15 %).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.services.activity_dedupe import _DUP_DISTANCE_TOL, _DUP_WINDOW_S, _same_family

WATCH_IDS = ("garmin_activity_id", "coros_activity_id")
KEEP = {"id", "created_at", *WATCH_IDS}  # what Strava never overwrites on a watch row


def same_outing(a_sport: str, a_dist: float | None, a_time: float | None,
                b_sport: str, b_dist: float | None, b_time: float | None) -> bool:
    """Starts are checked by the caller (the 3 min window of the query)."""
    if not _same_family(a_sport, b_sport):
        return False
    da, db_ = a_dist or 0, b_dist or 0
    big = max(da, db_)
    if big > 0:
        return abs(da - db_) / big <= _DUP_DISTANCE_TOL
    ta, tb = a_time or 0, b_time or 0  # no distance (strength, yoga…): the durations agree
    return max(ta, tb) > 0 and abs(ta - tb) / max(ta, tb) <= _DUP_DISTANCE_TOL


async def find_twin(db: AsyncSession, user_id: int, sport: str, start: datetime, distance: float | None,
                    moving: float | None, *where) -> Activity | None:
    """The athlete's row of the same outing among those matching `where`, the closest start first."""
    window = timedelta(seconds=_DUP_WINDOW_S)
    rows = (await db.execute(select(Activity).where(
        Activity.user_id == user_id, Activity.start_date >= start - window,
        Activity.start_date <= start + window, *where))).scalars().all()
    rows = [r for r in rows if same_outing(r.sport_type, r.distance, r.moving_time, sport, distance, moving)]
    return min(rows, key=lambda r: abs((_aware(r.start_date) - _aware(start)).total_seconds()), default=None)


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def adopt_watch_twin(db: AsyncSession, activity: Activity) -> Activity:
    """A new Strava session (not added yet): the watch-only row of the same
    outing takes Strava's id and fields and is returned; else `activity` is
    added and returned."""
    twin = None
    if activity.start_date is not None:
        twin = await find_twin(db, activity.user_id, activity.sport_type, activity.start_date, activity.distance,
                               activity.moving_time, Activity.strava_activity_id.is_(None))
    if twin is None:
        db.add(activity)
        return activity
    for col in Activity.__table__.columns.keys():
        value = getattr(activity, col)
        if col not in KEEP and value is not None:
            setattr(twin, col, value)
    return twin


async def merge_twins(db: AsyncSession, user_id: int, since: datetime) -> int:
    """Watch-only rows whose outing Strava saved apart (the watch came first,
    before Strava rows took them over): their ids move to Strava's row and the
    watch row goes. Returns how many were merged."""
    watch_rows = (await db.execute(select(Activity).where(
        Activity.user_id == user_id, Activity.strava_activity_id.is_(None),
        Activity.start_date >= since))).scalars().all()
    merged = 0
    for w in watch_rows:
        ids = {k: getattr(w, k) for k in WATCH_IDS if getattr(w, k) is not None}
        if not ids:
            continue
        free = [getattr(Activity, k).is_(None) for k in ids]
        s = await find_twin(db, user_id, w.sport_type, w.start_date, w.distance, w.moving_time,
                            Activity.strava_activity_id.is_not(None), *free)
        if s is None:
            continue
        await db.delete(w)  # its ids are unique: free them first
        await db.flush()
        for k, v in ids.items():
            setattr(s, k, v)
        if not s.total_elevation_gain and w.total_elevation_gain:
            s.total_elevation_gain = w.total_elevation_gain
        merged += 1
    if merged:
        await db.flush()
    return merged
