"""Deleting an account: PaceForge's access is revoked at each provider first (best effort, time-bounded), then
every row of the athlete goes, whether or not the database cascades; the logs carry the account id, never the
email."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.crypto import encrypt_secret
from app.database import Base
from app.dependencies import get_current_user
from app.models import (
    Activity,
    CorosConnection,
    GarminConnection,
    HealthMetric,
    HealthSample,
    LactateTest,
    MobileDaily,
    MobileDevice,
    NutritionProduct,
    OAuthAttempt,
    Route,
    RouteCheckpoint,
    Simulation,
    User,
)
from app.routers import settings as settings_router
from app.services import coros
from app.services.strava import StravaService
from tests.test_coros import D30, FakeCoros

NOW = datetime.now(timezone.utc)
SHARED = {"oauth_clients"}  # the app's own client registrations, nobody's data


def _rows(user: User) -> list:
    """One row of every kind an athlete has."""
    uid = user.id
    activity = Activity(user_id=uid, strava_activity_id=uid * 1000, name="Sortie", sport_type="Run",
                        start_date=NOW, distance=10000, moving_time=3600, elapsed_time=3600,
                        total_elevation_gain=100, raw_data={})
    route = Route(user_id=uid, name="Course", total_distance_km=42)
    return [
        activity, route,
        HealthSample(user_id=uid, metric="hrv", kind="", start_at=NOW.replace(tzinfo=None),
                     end_at=NOW.replace(tzinfo=None), value=60, source="COROS"),
        HealthMetric(user_id=uid, date=NOW.date(), metric="sleep", value=480, source="COROS"),
        NutritionProduct(user_id=uid, name="Gel"),
        OAuthAttempt(state_hash=f"{uid:064d}", user_id=uid, expires_at=NOW + timedelta(minutes=10)),
        MobileDevice(user_id=uid, platform="ios", expires_at=NOW + timedelta(days=30)),
        MobileDaily(user_id=uid, platform="ios", date=NOW.date(), metric="weight", value=70, sources=["x"]),
        LactateTest(user_id=uid, tested_on=NOW.date(), mode="pace", device="d", protocol="p", stages=[]),
        CorosConnection(user_id=uid, issuer=FakeCoros.META["issuer"], client_id="cid-1",
                        access_token_encrypted=encrypt_secret("at-1"), refresh_token_encrypted=encrypt_secret("rt-1"),
                        expires_at=NOW + timedelta(days=30), resource_url=FakeCoros.PRM["resource"],
                        token_endpoint=FakeCoros.META["token_endpoint"],
                        revocation_endpoint=FakeCoros.META["revocation_endpoint"]),
        GarminConnection(user_id=uid, di_client_id="GARMIN", access_token_encrypted=encrypt_secret("g-1")),
    ]


async def _seed(db, user: User) -> None:
    rows = _rows(user)
    db.add_all(rows)
    await db.flush()
    route = rows[1]
    db.add_all([
        RouteCheckpoint(route_id=route.id, name="Ravito", distance_km=10),
        Simulation(route_id=route.id, user_id=user.id),
    ])
    await db.flush()


async def _counts(db) -> dict[str, int]:
    out = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in SHARED:
            out[table.name] = (await db.execute(select(func.count()).select_from(table))).scalar()
    return out


@pytest.fixture
def fake_coros(monkeypatch) -> FakeCoros:
    f = FakeCoros(shift_days=(NOW.date() - D30).days)
    monkeypatch.setattr(coros, "_transport", httpx.MockTransport(f.handler))
    return f


@pytest.fixture
def strava(monkeypatch):
    calls = {"deauthorize": AsyncMock(), "delete_webhook_subscription": AsyncMock(return_value=True)}
    for name, mock in calls.items():
        monkeypatch.setattr(StravaService, name, mock)
    return calls


async def _athlete(db, email: str) -> User:
    user = User(email=email, email_verified=True, strava_athlete_id=hash(email) % 10**8,
                strava_access_token="a" * 40, strava_refresh_token="b" * 40, strava_token_expires_at=2147483000,
                strava_client_id="4242", strava_client_secret_encrypted=encrypt_secret("s3cret"),
                strava_webhook_subscription_id=77)
    db.add(user)
    await db.flush()
    await _seed(db, user)
    return user


async def _delete(client: AsyncClient, user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[attr-defined]
    return await client.post("/settings/delete-account", data={"confirmation": "SUPPRIMER"})


async def test_deletion_revokes_every_provider_then_empties_every_table(committing_session, committing_client,
                                                                        fake_coros, strava, caplog):
    """Foreign keys enforced (as on PostgreSQL), commits for real: nothing of the athlete is left, the other
    athlete keeps everything."""
    db = committing_session
    other = await _athlete(db, "other@x.fr")
    user = await _athlete(db, "leaving@x.fr")
    await db.commit()
    before = await _counts(db)
    caplog.set_level(logging.INFO)
    response = await _delete(committing_client, user)
    assert response.status_code == 302 and response.headers["location"] == "/?account_deleted=1"
    strava["deauthorize"].assert_awaited_once()  # POST /oauth/deauthorize with its access token
    strava["delete_webhook_subscription"].assert_awaited_once_with(77)  # its own app stops pushing to us
    assert [r["token_type_hint"] for r in fake_coros.revoked] == ["refresh_token", "access_token"]
    await db.rollback()  # only what was committed counts
    after = await _counts(db)
    assert after == {table: n // 2 for table, n in before.items()}, after  # the other athlete's half stays
    assert (await db.get(User, other.id)) is not None and (await db.get(User, user.id)) is None
    assert "leaving@x.fr" not in caplog.text and str(user.id) in caplog.text


async def test_without_cascades_every_table_is_still_emptied(client, db_session, fake_coros, strava, monkeypatch):
    """SQLite's default (foreign keys off) cascades nothing: every table is deleted explicitly."""
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    other = await _athlete(db_session, "other@x.fr")
    user = await _athlete(db_session, "leaving@x.fr")
    before = await _counts(db_session)
    assert (await _delete(client, user)).status_code == 302
    assert await _counts(db_session) == {table: n // 2 for table, n in before.items()}
    assert (await db_session.get(User, other.id)) is not None


async def test_a_provider_that_hangs_or_fails_never_stops_the_deletion(committing_session, committing_client,
                                                                        fake_coros, strava, monkeypatch):
    db = committing_session
    user = await _athlete(db, "leaving@x.fr")
    await db.commit()

    async def hang(*args, **kwargs):
        await asyncio.sleep(3600)
    strava["deauthorize"].side_effect = hang
    monkeypatch.setattr(settings_router, "REVOKE_TIMEOUT_S", 0.05)
    monkeypatch.setattr("app.services.garmin.disconnect", AsyncMock(side_effect=RuntimeError("Garmin bug")))
    response = await _delete(committing_client, user)
    assert response.status_code == 302
    assert fake_coros.revoked  # the next provider was still asked
    await db.rollback()
    assert all(n == 0 for n in (await _counts(db)).values())
