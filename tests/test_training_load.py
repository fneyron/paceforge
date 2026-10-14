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
    # the headline km are every sport's, as Activités says them (« 28 j : 45 km »: a cyclist's are a ride's; review,
    # 2026-10-14); each sport's km apart, the running's alone, the hours of every sport
    assert result.volume_28d_km == 45.0  # 15 + 30km
    assert result.run_km_28d == 15.0 and result.run_km_7d == 15.0
    assert result.sport_breakdown_28d == {"Run": 15.0, "Ride": 30.0}
    assert result.volume_28d_hours == 2.25 and result.volume_7d_hours == 1.25
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


@pytest.mark.asyncio
async def test_a_cyclist_s_kilometres_are_counted(db_session: AsyncSession, test_user: User):
    """Activités' « 7 j : X km · 28 j : Y km » and the analyses' training_load_*_km are every sport's kilometres: a
    cyclist read 0 km once they were the running's alone (review, 2026-10-14). The running's stay apart."""
    now = datetime.now(timezone.utc)
    for i in range(2):
        db_session.add(Activity(strava_activity_id=400000 + i, user_id=test_user.id, sport_type="Ride",
                                name=f"Ride {i}", start_date=now - timedelta(days=2 * i + 1), distance=42000.0,
                                moving_time=5400, elapsed_time=5600, total_elevation_gain=300.0,
                                raw_data={"id": 400000 + i}))
    await db_session.flush()
    result = await calculate_training_load(db_session, test_user.id, now)
    assert result.volume_7d_km == 84.0 and result.volume_28d_km == 84.0
    assert result.run_km_7d == 0.0 and result.sport_breakdown_7d == {"Ride": 84.0}
