"""Deleting an account: PaceForge's access is revoked at each provider first (best effort, time-bounded), then
every row of the athlete goes with the user, by the foreign keys' ON DELETE CASCADE (every table has one, checked
here), and by name in the legacy tables no model maps (some without a key to users); the logs carry the account id,
never the email."""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import func, select, text

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


def test_every_table_goes_with_the_athlete_by_cascade():
    """What delete_account relies on (one DELETE on users): every table but the shared ones reaches users through
    foreign keys ON DELETE CASCADE, directly or through activities and routes (a SET NULL link is not ownership)."""
    def goes_with_the_user(table) -> bool:
        return any(fk.ondelete == "CASCADE" and (fk.column.table.name == "users" or goes_with_the_user(fk.column.table))
                   for fk in table.foreign_keys)

    for table in Base.metadata.sorted_tables:
        if table.name not in SHARED | {"users"}:
            assert goes_with_the_user(table), table.name


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


# ── the legacy tables: unmapped, kept until a release drops them, some without a key to users ──

# as the migrations made them (their other columns left out): generated_plans.user_id without a foreign key,
# simulations.user_id neither (only route_id cascades), the others cascading from users or activities
LEGACY_DDL = (
    "CREATE TABLE generated_plans (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, plan_type VARCHAR(20))",
    "CREATE TABLE simulations (id INTEGER PRIMARY KEY, route_id INTEGER NOT NULL REFERENCES routes (id) "
    "ON DELETE CASCADE, user_id INTEGER NOT NULL)",
    "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users (id) "
    "ON DELETE CASCADE, content TEXT)",
    "CREATE TABLE weekly_digests (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users (id) "
    "ON DELETE CASCADE, summary TEXT)",
    "CREATE TABLE analyses (id INTEGER PRIMARY KEY, activity_id INTEGER NOT NULL REFERENCES activities (id) "
    "ON DELETE CASCADE, summary TEXT)",
)
LEGACY = ("generated_plans", "simulations", "chat_messages", "weekly_digests", "analyses")


async def _legacy_rows(db, user: User, base: int) -> None:
    route = (await db.execute(select(Route.id).where(Route.user_id == user.id))).scalar_one()
    activity = (await db.execute(select(Activity.id).where(Activity.user_id == user.id))).scalar_one()
    for sql in ("INSERT INTO generated_plans (id, user_id, plan_type) VALUES (:id, :uid, 'week')",
                "INSERT INTO simulations (id, route_id, user_id) VALUES (:id, :route, :uid)",
                "INSERT INTO chat_messages (id, user_id, content) VALUES (:id, :uid, 'Bonjour')",
                "INSERT INTO weekly_digests (id, user_id, summary) VALUES (:id, :uid, 'Semaine')",
                "INSERT INTO analyses (id, activity_id, summary) VALUES (:id, :activity, 'Analyse')"):
        await db.execute(text(sql), {"id": base, "uid": user.id, "route": route, "activity": activity})


async def _legacy_owners(db) -> dict[str, list[int]]:
    """Whose rows each legacy table holds (analyses: by its activity)."""
    out = {t: (await db.execute(text(f"SELECT user_id FROM {t} ORDER BY user_id"))).scalars().all()
           for t in LEGACY if t != "analyses"}
    out["analyses"] = (await db.execute(text("SELECT a.user_id FROM analyses x JOIN activities a "
                                             "ON a.id = x.activity_id"))).scalars().all()
    return out


@pytest.mark.skipif(bool(os.getenv("TEST_DATABASE_URL")),
                    reason="the legacy tables are made here as on an old database; alembic made the server's")
async def test_deletion_empties_the_legacy_tables_too(committing_session, committing_client, fake_coros, strava):
    """generated_plans has no foreign key to users: one DELETE on users left the athlete's plans there. Account
    deletion now deletes each legacy table's rows of the athlete by name; the other athlete keeps his."""
    db = committing_session
    for ddl in LEGACY_DDL:
        await db.execute(text(ddl))
    other = await _athlete(db, "other@x.fr")
    user = await _athlete(db, "leaving@x.fr")
    await _legacy_rows(db, other, 1)
    await _legacy_rows(db, user, 2)
    await db.commit()
    assert all(owners == sorted([other.id, user.id]) for owners in (await _legacy_owners(db)).values())
    response = await _delete(committing_client, user)
    assert response.status_code == 302
    await db.rollback()
    assert await _legacy_owners(db) == {t: [other.id] for t in LEGACY}


def test_the_legacy_orphans_migration_deletes_only_the_rows_of_owners_gone():
    """j0a1b2c3d4e5: in the chain (single head); the rows whose user, route or activity is gone go, the others
    stay; a table that does not exist is skipped; the downgrade changes nothing."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from tests.test_sleep_need_migrations import ROOT, _load, _run

    mig = _load("j0a1b2c3d4e5_legacy_orphans")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert mig.down_revision == "i9f0a1b2c3d4" and len(script.get_heads()) == 1
    assert mig.revision in {r.revision for r in script.walk_revisions()}
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        for ddl in ("CREATE TABLE users (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE activities (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)",
                    "CREATE TABLE routes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)",
                    *(ddl for ddl in LEGACY_DDL if "weekly_digests" not in ddl),  # one table absent: skipped
                    "INSERT INTO users (id) VALUES (1)",
                    "INSERT INTO activities (id, user_id) VALUES (10, 1)",
                    "INSERT INTO routes (id, user_id) VALUES (20, 1)",
                    "INSERT INTO generated_plans (id, user_id) VALUES (1, 1), (2, 7)",
                    "INSERT INTO simulations (id, route_id, user_id) VALUES (1, 20, 1), (2, 20, 7), (3, 99, 1)",
                    "INSERT INTO chat_messages (id, user_id) VALUES (1, 1), (2, 7)",
                    "INSERT INTO analyses (id, activity_id) VALUES (1, 10), (2, 98)"):
            c.execute(sa.text(ddl))
        _run(c, mig.upgrade)
        for table in ("generated_plans", "simulations", "chat_messages", "analyses"):
            assert c.execute(sa.text(f"SELECT id FROM {table}")).scalars().all() == [1], table
        _run(c, mig.downgrade)
        assert c.execute(sa.text("SELECT count(*) FROM generated_plans")).scalar() == 1
