"""Activités' 7 / 28 days (owner, 2026-10-14: scientific basis — they summed every stored activity, its duplicate
recordings included, over UTC days counted back from « now »).

The same sessions and the same daily load as Santé (sante_load: one pipeline): the deduplicated sessions
(sante_training.load_sessions: copies and false starts left out, as Activités' list), each on the local day it
ended, the 7 / 28 local days up to the athlete's today (sante_training.athlete_clock) and nothing that had not
ended by `before_date`. The headline kilometres are every sport's, as Activités says them (« 7 j : X km · 28 j :
Y km »: a cyclist's are a ride's); kilometres per sport too,
the running's alone apart (run_km_*: Run, TrailRun, VirtualRun), hours of every sport, and the summed daily load
with the day's heart-rate bounds.
"""
import logging
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import nights as nt
from app.services import sante_load, timezones
from app.services import sante_training as st

logger = logging.getLogger(__name__)


class TrainingLoad(BaseModel):
    """The 7 / 28 days: every sport's km, hours and count, the km per sport, the loads of both windows, and the
    running's km alone (`run_km_*`; `volume_*_km` are every sport's)."""
    volume_7d_km: float = 0.0
    volume_7d_hours: float = 0.0
    count_7d: int = 0
    volume_28d_km: float = 0.0
    volume_28d_hours: float = 0.0
    count_28d: int = 0
    sport_breakdown_7d: dict[str, float] = Field(default_factory=dict)
    sport_breakdown_28d: dict[str, float] = {}
    run_km_7d: float = 0.0
    run_km_28d: float = 0.0
    load_7d: float = 0.0
    load_28d: float = 0.0


async def calculate_training_load(
    db: AsyncSession,
    user_id: int,
    before_date: datetime,
    zone=None,
) -> TrainingLoad:
    """The 7 and 28 local days up to the athlete's day of `before_date` (sessions ended by then only). `zone`:
    sante_training.athlete_clock's, when the caller read it (Activités reads it once per page)."""
    before = before_date if before_date.tzinfo else before_date.replace(tzinfo=timezone.utc)
    if zone is None:
        zone = await st.athlete_clock(db, user_id)
    today = timezones.local_date(before, zone)
    sessions = [s for s in await st.load_sessions(db, user_id, today, zone=zone)
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
        volume_7d_km=round(week["km"], 2),
        volume_7d_hours=round(week["hours"], 2),
        count_7d=week["count"],
        volume_28d_km=round(month["km"], 2),
        volume_28d_hours=round(month["hours"], 2),
        count_28d=month["count"],
        sport_breakdown_7d={k: round(v, 2) for k, v in week["km_by_sport"].items()},
        sport_breakdown_28d={k: round(v, 2) for k, v in month["km_by_sport"].items()},
        run_km_7d=round(week["run_km"], 2),
        run_km_28d=round(month["run_km"], 2),
        load_7d=round(week["load"], 1),
        load_28d=round(month["load"], 1),
    )
