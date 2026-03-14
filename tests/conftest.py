import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.dependencies import get_db
from app.main import create_app
from app.models.activity import Activity
from app.models.analysis import Analysis
from app.models.user import User

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(db_session: AsyncSession):
    app = create_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    user = User(
        strava_athlete_id=12345678,
        strava_access_token="test_access_token",
        strava_refresh_token="test_refresh_token",
        strava_token_expires_at=9999999999,
        firstname="Test",
        lastname="Runner",
        profile_picture_url="https://example.com/avatar.jpg",
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def test_activity(db_session: AsyncSession, test_user: User) -> Activity:
    activity = Activity(
        strava_activity_id=987654321,
        user_id=test_user.id,
        sport_type="Run",
        name="Morning Run",
        start_date=datetime(2024, 3, 10, 8, 0, 0, tzinfo=timezone.utc),
        distance=10000.0,
        moving_time=3000,
        elapsed_time=3100,
        total_elevation_gain=50.0,
        average_speed=3.33,
        max_speed=4.0,
        average_heartrate=150.0,
        max_heartrate=168.0,
        average_cadence=85.0,
        raw_data={"id": 987654321, "name": "Morning Run"},
        splits_metric=[
            {"distance": 1000, "moving_time": 300, "average_heartrate": 145},
            {"distance": 1000, "moving_time": 298, "average_heartrate": 148},
            {"distance": 1000, "moving_time": 302, "average_heartrate": 150},
            {"distance": 1000, "moving_time": 295, "average_heartrate": 152},
            {"distance": 1000, "moving_time": 305, "average_heartrate": 155},
        ],
    )
    db_session.add(activity)
    await db_session.flush()
    await db_session.refresh(activity)
    return activity


@pytest.fixture
async def test_analysis(db_session: AsyncSession, test_activity: Activity) -> Analysis:
    analysis = Analysis(
        activity_id=test_activity.id,
        summary="Bonne sortie d'endurance avec une allure régulière.",
        strengths=["Allure stable", "Cadence régulière"],
        improvements=["Légère dérive cardiaque après 4km"],
        next_workout_tip="Essaye une sortie similaire en partant 10s/km plus lent.",
        strava_comment="Analyse automatique 🧠\n\nBonne régularité.\n\n👉 Pars un peu plus lent.\n\nAnalyse générée par PaceForge",
        fatigue_note=None,
        training_load_7d_km=25.0,
        training_load_7d_hours=2.5,
        training_load_7d_count=3,
        training_load_28d_km=90.0,
        training_load_28d_hours=9.0,
        training_load_28d_count=12,
        model_used="claude-sonnet-4-20250514",
        raw_response='{"summary": "test"}',
    )
    db_session.add(analysis)
    await db_session.flush()
    await db_session.refresh(analysis)
    return analysis


@pytest.fixture
def authenticated_client(client: AsyncClient, test_user: User):
    """Client with session cookie set."""
    client.cookies.set("paceforge_session", "test_session")
    return client
