"""Activités' 7 / 28 days (owner, 2026-10-14: scientific basis — they summed every stored activity, its duplicate
recordings included, over UTC days counted back from « now », and added a ride's kilometres to a run's).

The same sessions and the same daily load as Santé (sante_load: one pipeline): the deduplicated sessions
(sante_training.load_sessions: copies and false starts left out, as Activités' list), each on the local day it
ended, the 7 / 28 local days up to the athlete's today (timezones.athlete_zone) and nothing that had not ended by
`before_date`; kilometres per sport (the headline kilometres are the running's: Run, TrailRun, VirtualRun), hours
of every sport, and the summed daily load with the day's heart-rate bounds.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.analysis import TrainingLoadResult
from app.services import nights as nt
from app.services import sante_load, timezones
from app.services import sante_training as st

logger = logging.getLogger(__name__)


class TrainingLoad(TrainingLoadResult):
    """TrainingLoadResult with what the one pipeline adds: the hours and loads of both windows, the 28 days' km per
    sport. `volume_*_km` are the running's kilometres (never a ride's added to them)."""
    sport_breakdown_28d: dict[str, float] = {}
    load_7d: float = 0.0
    load_28d: float = 0.0


async def calculate_training_load(
    db: AsyncSession,
    user_id: int,
    before_date: datetime,
) -> TrainingLoad:
    """The 7 and 28 local days up to the athlete's day of `before_date` (sessions ended by then only)."""
    before = before_date if before_date.tzinfo else before_date.replace(tzinfo=timezone.utc)
    user = await db.get(User, user_id)
    today = timezones.local_date(before, timezones.athlete_zone(user))
    sessions = [s for s in await st.load_sessions(db, user_id, today)
                if st._utc(s.start) + timedelta(minutes=st.effort_minutes(s)) <= before and s.end_day <= today]
    if not sessions:
        return TrainingLoad()
    rows = await nt.read_rows(db, user_id, today - timedelta(days=nt.BAND_DAYS), today, ("sleep", "hr_night"))
    rest = nt.rest_hr(nt.build_nights(rows, today), today)
    peak = st.hr_max(sessions, today)
    await st.load_hr_segments(db, user_id, sessions, today - timedelta(days=st.RATE_DAYS))
    load = st.loads(sessions, today, rest, peak)
    week, month = (sante_load.totals(sessions, load, today, n) for n in (7, 28))
    return TrainingLoad(
        volume_7d_km=round(week["run_km"], 2),
        volume_7d_hours=round(week["hours"], 2),
        count_7d=week["count"],
        volume_28d_km=round(month["run_km"], 2),
        volume_28d_hours=round(month["hours"], 2),
        count_28d=month["count"],
        sport_breakdown_7d={k: round(v, 2) for k, v in week["km_by_sport"].items()},
        sport_breakdown_28d={k: round(v, 2) for k, v in month["km_by_sport"].items()},
        load_7d=round(week["load"], 1),
        load_28d=round(month["load"], 1),
    )
