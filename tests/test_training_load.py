from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.services.training_load import calculate_training_load


@pytest.mark.asyncio
async def test_calculate_training_load_empty(db_session: AsyncSession, test_user: User):
    now = datetime.now(timezone.utc)
    result = await calculate_training_load(db_session, test_user.id, now)

    assert result.volume_7d_km == 0.0
    assert result.volume_7d_hours == 0.0
    assert result.count_7d == 0
    assert result.volume_28d_km == 0.0
    assert result.count_28d == 0


@pytest.mark.asyncio
async def test_calculate_training_load_with_activities(
    db_session: AsyncSession, test_user: User
):
    now = datetime.now(timezone.utc)

    # Add activities in the last 7 days
    for i in range(3):
        activity = Activity(
            strava_activity_id=100000 + i,
            user_id=test_user.id,
            sport_type="Run",
            name=f"Run {i}",
            start_date=now - timedelta(days=i + 1),
            distance=5000.0,  # 5km
            moving_time=1500,  # 25min
            elapsed_time=1600,
            total_elevation_gain=20.0,
            raw_data={"id": 100000 + i},
        )
        db_session.add(activity)

    # Add an activity 15 days ago (in 28d but not 7d)
    old_activity = Activity(
        strava_activity_id=200000,
        user_id=test_user.id,
        sport_type="Ride",
        name="Old Ride",
        start_date=now - timedelta(days=15),
        distance=30000.0,  # 30km
        moving_time=3600,  # 1h
        elapsed_time=3700,
        total_elevation_gain=100.0,
        raw_data={"id": 200000},
    )
    db_session.add(old_activity)
    await db_session.flush()

    result = await calculate_training_load(db_session, test_user.id, now)

    assert result.volume_7d_km == 15.0  # 3 * 5km
    assert result.count_7d == 3
    assert result.volume_28d_km == 45.0  # 15 + 30km
    assert result.count_28d == 4
    assert "Run" in result.sport_breakdown_7d
    assert result.sport_breakdown_7d["Run"] == 15.0


@pytest.mark.asyncio
async def test_training_load_excludes_future_activities(
    db_session: AsyncSession, test_user: User
):
    now = datetime.now(timezone.utc)
    before = now - timedelta(days=3)

    activity = Activity(
        strava_activity_id=300000,
        user_id=test_user.id,
        sport_type="Run",
        name="Recent Run",
        start_date=now - timedelta(days=1),
        distance=10000.0,
        moving_time=3000,
        elapsed_time=3100,
        total_elevation_gain=0,
        raw_data={"id": 300000},
    )
    db_session.add(activity)
    await db_session.flush()

    # Calculate load as of 3 days ago — should not include the recent activity
    result = await calculate_training_load(db_session, test_user.id, before)
    assert result.count_7d == 0
