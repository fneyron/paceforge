"""A Garmin link of an account without the consent to health data (one from before it was asked): its hourly sync
still brings the activities, and stores no health value (the storage's gate: Garmin's own files unchanged)."""
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, HealthMetric, HealthSample, User
from app.services import garmin, health_consent
from tests.test_garmin import _link, fake  # noqa: F401 (a fixture)


@pytest.fixture
def no_commit(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)


async def _count(db, model, user_id) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)) or 0


async def test_garmin_without_the_consent_brings_the_activities_only(db_session: AsyncSession, fake,  # noqa: F811
                                                                     no_commit):
    user = User(email="old@x.fr", email_verified=True, firstname="Camille")
    db_session.add(user)
    await db_session.flush()
    conn = await _link(db_session, user)
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    assert await _count(db_session, Activity, user.id) > 0
    assert await _count(db_session, HealthMetric, user.id) == 0
    assert await _count(db_session, HealthSample, user.id) == 0
    health_consent.record(user)
    conn.last_sync_at = None
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome["ok"] and await _count(db_session, HealthMetric, user.id) > 0
