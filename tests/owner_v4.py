"""The owner on 2026-10-08, Santé v4's fixture: his real COROS nights 29/09 →
08/10 as the COROS sync writes them (the 07 → 08 night read from COROS that
morning: 22:42 → 07:30, 8h36, no nap; PaceForge's own VFC from the raw series,
100 ms; « Sleep HR » 35 bpm) and the Transjeju 100M as a plain activity
(02/10 21:00 in Korea, 16h53 stops included, marked as a race on Strava:
just an activity for Santé). No Route is read by Santé."""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.health import HealthMetric
from app.models.user import User
from app.services import coros
from tests import owner_coros as oc
from tests.test_nights import owner_rows

D8 = date(2026, 10, 8)
TRANSJEJU_ELAPSED = oc.TRANSJEJU_END - oc.TRANSJEJU_START  # 16:53:27


def owner_rows_v4() -> dict:
    """{metric: {day: (value, details, source)}}: owner_rows (29/09 → 07/10) and the 08/10 night."""
    rows = owner_rows()
    ov = coros.parse_sleep_overview(oc.OVERVIEW_2026_10_08)
    naps = coros.parse_naps(oc.OVERVIEW_2026_10_08)
    for r in coros.sleep_dailies(ov, {d: 36 for d in ov}):
        rows["sleep"][r.day] = (r.value, r.details, "COROS")
    points = coros.parse_hrv_points(oc.HRV_2026_10_08)
    for r in coros.hrv_dailies(points, ov, {D8, D8 - timedelta(days=1)}):
        rows["hrv"][r.day] = (r.value, r.details, "COROS")
    for r in coros.hr_night_dailies(coros.parse_daily_sleep(oc.DAILY_2026_10_08), ov, naps):
        rows["hr_night"][r.day] = (r.value, r.details, "COROS")
    return rows


def transjeju(user_id: int, sid: int = 8200) -> Activity:
    """The Transjeju 100M as Strava wrote it: 02/10 12:00:28 UTC (21:00 in Korea), 16:53:27 elapsed, a race."""
    return Activity(user_id=user_id, strava_activity_id=sid, sport_type="TrailRun", name="Transjeju 100M",
                    start_date=datetime.fromtimestamp(oc.TRANSJEJU_START, timezone.utc), distance=148150,
                    moving_time=56100, elapsed_time=TRANSJEJU_ELAPSED, total_elevation_gain=6100,
                    average_heartrate=126, max_heartrate=171,
                    raw_data={"utc_offset": 32400, "workout_type": 1, "elev_high": 1100})


async def seed_owner_v4(db: AsyncSession, user: User) -> Activity:
    """The owner's rows and the Transjeju activity (no Route: Santé never reads one)."""
    for metric, per_day in owner_rows_v4().items():
        for d, (v, det, src) in per_day.items():
            db.add(HealthMetric(user_id=user.id, date=d, metric=metric, value=v, source=src, details=det,
                                n_samples=1))
    act = transjeju(user.id)
    db.add(act)
    await db.flush()
    return act
