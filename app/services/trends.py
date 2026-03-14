import logging
from datetime import date, datetime, timedelta

from sqlalchemy import Float, case, cast, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.schemas.trends import PaceTrend, PersonalRecord, WeeklyVolume

logger = logging.getLogger(__name__)

# Standard best effort distances from Strava
BEST_EFFORT_LABELS = [
    "400m", "1/2 mile", "1k", "1 mile", "2 mile",
    "5k", "10k", "15k", "20k", "Half-Marathon", "Marathon",
]


async def get_weekly_volume_trends(
    db: AsyncSession,
    user_id: int,
    weeks: int = 12,
) -> list[WeeklyVolume]:
    """Get weekly volume aggregated by ISO week."""
    cutoff = datetime.now().date() - timedelta(weeks=weeks)

    # Main aggregation per week
    result = await db.execute(
        select(
            func.date_trunc("week", Activity.start_date).label("week_start"),
            func.coalesce(func.sum(Activity.distance), 0).label("total_distance"),
            func.coalesce(func.sum(Activity.moving_time), 0).label("total_time"),
            func.count(Activity.id).label("count"),
        )
        .where(
            Activity.user_id == user_id,
            cast(Activity.start_date, Float) >= cast(func.cast(cutoff, Float), Float) if False else Activity.start_date >= cutoff,
        )
        .group_by(func.date_trunc("week", Activity.start_date))
        .order_by(func.date_trunc("week", Activity.start_date))
    )
    weeks_data = result.all()

    # Sport breakdown per week
    sport_result = await db.execute(
        select(
            func.date_trunc("week", Activity.start_date).label("week_start"),
            Activity.sport_type,
            func.sum(Activity.distance).label("total_distance"),
        )
        .where(
            Activity.user_id == user_id,
            Activity.start_date >= cutoff,
        )
        .group_by(
            func.date_trunc("week", Activity.start_date),
            Activity.sport_type,
        )
    )
    sport_rows = sport_result.all()

    # Build sport breakdown lookup
    sport_map: dict[str, dict[str, float]] = {}
    for row in sport_rows:
        week_key = row.week_start.strftime("%Y-%m-%d") if hasattr(row.week_start, "strftime") else str(row.week_start)
        if week_key not in sport_map:
            sport_map[week_key] = {}
        sport_map[week_key][row.sport_type] = round(row.total_distance / 1000, 2)

    volumes = []
    for row in weeks_data:
        week_key = row.week_start.strftime("%Y-%m-%d") if hasattr(row.week_start, "strftime") else str(row.week_start)
        ws = row.week_start
        if hasattr(ws, "date"):
            ws = ws.date()
        volumes.append(
            WeeklyVolume(
                week_start=ws,
                distance_km=round(row.total_distance / 1000, 2),
                duration_hours=round(row.total_time / 3600, 2),
                count=row.count,
                sport_breakdown=sport_map.get(week_key, {}),
            )
        )

    return volumes


async def get_pace_trends(
    db: AsyncSession,
    user_id: int,
    sport_type: str = "Run",
    weeks: int = 12,
) -> list[PaceTrend]:
    """Get average pace per week for a given sport."""
    cutoff = datetime.now().date() - timedelta(weeks=weeks)

    # Match sport types for running
    run_types = ["Run", "TrailRun", "VirtualRun"]
    ride_types = ["Ride", "VirtualRide", "EBikeRide", "GravelRide"]

    if sport_type in ("Run", "TrailRun"):
        sport_filter = Activity.sport_type.in_(run_types)
    elif sport_type in ("Ride",):
        sport_filter = Activity.sport_type.in_(ride_types)
    else:
        sport_filter = Activity.sport_type == sport_type

    result = await db.execute(
        select(
            func.date_trunc("week", Activity.start_date).label("week_start"),
            func.avg(
                case(
                    (Activity.average_speed > 0, Activity.average_speed),
                    else_=None,
                )
            ).label("avg_speed"),
        )
        .where(
            Activity.user_id == user_id,
            Activity.start_date >= cutoff,
            sport_filter,
            Activity.average_speed.is_not(None),
            Activity.average_speed > 0,
        )
        .group_by(func.date_trunc("week", Activity.start_date))
        .order_by(func.date_trunc("week", Activity.start_date))
    )

    paces = []
    for row in result.all():
        if row.avg_speed and row.avg_speed > 0:
            ws = row.week_start
            if hasattr(ws, "date"):
                ws = ws.date()
            if sport_type in ride_types:
                # For cycling, store speed in km/h instead of pace
                pace_value = round(row.avg_speed * 3.6, 2)
            else:
                # pace in min/km
                pace_value = round(1000 / row.avg_speed / 60, 2)
            paces.append(
                PaceTrend(
                    week_start=ws,
                    avg_pace_min_per_km=pace_value,
                    sport_type=sport_type,
                )
            )

    return paces


async def get_personal_records(
    db: AsyncSession,
    user_id: int,
) -> list[PersonalRecord]:
    """Extract personal records from best_efforts stored in activities."""
    result = await db.execute(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.best_efforts.is_not(None),
        )
        .order_by(Activity.start_date.desc())
    )
    activities = result.scalars().all()

    # Collect best times per distance
    best: dict[str, dict] = {}

    for activity in activities:
        efforts = activity.best_efforts
        if not efforts or not isinstance(efforts, list):
            continue
        for effort in efforts:
            name = effort.get("name", "")
            elapsed = effort.get("elapsed_time", 0)
            distance = effort.get("distance", 0)
            if not name or not elapsed or not distance:
                continue

            if name not in best or elapsed < best[name]["elapsed_time"]:
                # Calculate pace
                pace_sec = elapsed / (distance / 1000)
                p_min, p_sec = divmod(int(pace_sec), 60)
                best[name] = {
                    "distance_label": name,
                    "elapsed_time": elapsed,
                    "pace_formatted": f"{p_min}:{p_sec:02d}/km",
                    "activity_name": activity.name,
                    "activity_id": activity.id,
                    "date": activity.start_date,
                }

    # Order by standard distance order
    records = []
    for label in BEST_EFFORT_LABELS:
        if label in best:
            records.append(PersonalRecord(**best[label]))

    # Add any non-standard ones at the end
    for label, data in best.items():
        if label not in BEST_EFFORT_LABELS:
            records.append(PersonalRecord(**data))

    return records
