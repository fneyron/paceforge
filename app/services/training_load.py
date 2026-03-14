import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.schemas.analysis import TrainingLoadResult

logger = logging.getLogger(__name__)


async def calculate_training_load(
    db: AsyncSession,
    user_id: int,
    before_date: datetime,
) -> TrainingLoadResult:
    """Calculate 7-day and 28-day training load for a user."""
    date_7d = before_date - timedelta(days=7)
    date_28d = before_date - timedelta(days=28)

    # 7-day aggregation
    result_7d = await db.execute(
        select(
            func.coalesce(func.sum(Activity.distance), 0).label("total_distance"),
            func.coalesce(func.sum(Activity.moving_time), 0).label("total_time"),
            func.count(Activity.id).label("count"),
        )
        .where(
            Activity.user_id == user_id,
            Activity.start_date >= date_7d,
            Activity.start_date < before_date,
        )
    )
    row_7d = result_7d.one()

    # 28-day aggregation
    result_28d = await db.execute(
        select(
            func.coalesce(func.sum(Activity.distance), 0).label("total_distance"),
            func.coalesce(func.sum(Activity.moving_time), 0).label("total_time"),
            func.count(Activity.id).label("count"),
        )
        .where(
            Activity.user_id == user_id,
            Activity.start_date >= date_28d,
            Activity.start_date < before_date,
        )
    )
    row_28d = result_28d.one()

    # Sport breakdown for 7 days
    sport_result = await db.execute(
        select(
            Activity.sport_type,
            func.sum(Activity.distance).label("total_distance"),
        )
        .where(
            Activity.user_id == user_id,
            Activity.start_date >= date_7d,
            Activity.start_date < before_date,
        )
        .group_by(Activity.sport_type)
    )
    sport_breakdown = {
        row.sport_type: round(row.total_distance / 1000, 2)
        for row in sport_result.all()
    }

    return TrainingLoadResult(
        volume_7d_km=round(row_7d.total_distance / 1000, 2),
        volume_7d_hours=round(row_7d.total_time / 3600, 2),
        count_7d=row_7d.count,
        volume_28d_km=round(row_28d.total_distance / 1000, 2),
        volume_28d_hours=round(row_28d.total_time / 3600, 2),
        count_28d=row_28d.count,
        sport_breakdown_7d=sport_breakdown,
    )
