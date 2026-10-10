"""Regressions reproduced during the October 2026 audit."""
import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import bcrypt
import pytest
from httpx import AsyncClient
from sqlalchemy import event, select

from app.crypto import encrypt_secret
from app.dependencies import get_current_user
from app.models.health import HealthMetric
from app.models.nutrition import NutritionProduct
from app.models.oauth_attempt import OAuthAttempt
from app.models.route import Route, RouteCheckpoint
from app.models.user import User
from app.services.auth import hash_password, verification_token, verify_password
from app.services.strava import StravaService
from tests.test_race_plan_services import _course
from tests.test_settings_links import _logged_in
from tests.test_simulator_routes import CPS, _create_route


@pytest.fixture(autouse=True)
def no_commit(db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
async def as_user(client, test_user):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    return client


async def test_unauthenticated_post_redirects_to_a_get_login_page(client):
    response = await client.post("/settings", data={"weight_kg": "70"}, follow_redirects=True)
    assert response.status_code == 200
    assert response.url.path == "/auth/login" and response.request.method == "GET"


@pytest.mark.parametrize("password", ["a" * 73, "é" * 80, "long-" * 180])
def test_passwords_use_the_entire_value_beyond_72_bytes(password):
    hashed = hash_password(password)
    assert verify_password(password, hashed)
    assert not verify_password(password + "different", hashed)


def test_legacy_bcrypt_passwords_still_work():
    hashed = bcrypt.hashpw(b"old-password", bcrypt.gensalt()).decode()
    assert verify_password("old-password", hashed)
    assert not verify_password("wrong-password", hashed)


async def test_invalid_email_never_creates_an_account(client, db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *args: sent.append(args))
    for address in ("not-an-email", "me@@example.com", "me@-example.com", ".me@example.com"):
        response = await client.post("/auth/register", data={"email": address, "password": "password-123"})
        assert response.status_code == 200 and "Vérifie ton adresse" in response.text
    assert (await db_session.execute(select(User))).scalars().all() == []
    assert sent == []


async def test_unverified_registration_and_login_cannot_access_private_pages(client, db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *args: sent.append(args) or True)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        response = await c.post("/auth/register", data={"email": "new@example.com", "password": "é" * 80})
        assert response.headers["location"] == "/auth/check-email"
        assert (await c.get("/settings")).status_code == 303
        response = await c.post("/auth/login", data={"email": "new@example.com", "password": "é" * 80})
        assert response.headers["location"] == "/auth/check-email"
        # Logging in again cannot bypass the server-side resend cooldown.
        await c.post("/auth/resend-verification")
        assert len(sent) == 1
        response = await c.get("/auth/verify-email", params={"token": sent[0][1]})
        assert response.headers["location"] == "/sante"
        assert (await c.get("/settings")).status_code == 200
        assert "Lien invalide" in (await c.get("/auth/verify-email", params={"token": sent[0][1]})).text


async def test_expired_verification_can_be_resent_after_login(client, db_session, monkeypatch):
    with patch("itsdangerous.TimestampSigner.get_timestamp", return_value=1):
        expired = verification_token()
    user = User(email="waiting@example.com", password_hash=hash_password("password-123"), email_verify_token=expired)
    db_session.add(user)
    await db_session.flush()
    sent = []
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *args: sent.append(args) or True)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert "Lien invalide" in (await c.get("/auth/verify-email", params={"token": expired})).text
        await c.post("/auth/login", data={"email": user.email, "password": "password-123"})
        await c.post("/auth/resend-verification")
        assert len(sent) == 1 and sent[0][1] != expired
        assert (await c.get("/auth/verify-email", params={"token": sent[0][1]})).headers["location"] == "/sante"


def _session(client):
    return json.loads(base64.b64decode(client.cookies.get("paceforge_session").split(".")[0]))


async def test_oauth_credentials_are_server_side_and_state_is_single_use(client, db_session, monkeypatch):
    exchange = AsyncMock(return_value={"access_token": "at", "refresh_token": "rt", "expires_at": 9999999999,
                                     "athlete": {"id": 741}})
    monkeypatch.setattr(StravaService, "exchange_token", exchange)
    monkeypatch.setattr(StravaService, "create_webhook_subscription", AsyncMock(return_value=None))
    async with _logged_in(client, db_session, initial_sync_done=True) as (c, user):
        response = await c.post("/setup/credentials", data={"client_id": "4242", "client_secret": "super-secret"})
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        assert _session(c)["strava_state"] == state
        assert "super-secret" not in json.dumps(_session(c))
        attempt = (await db_session.execute(select(OAuthAttempt))).scalar_one()
        assert b"super-secret" not in attempt.credentials
        for bad in (None, "forged-state", "état-invalide"):
            await c.get("/auth/strava/callback", params={"code": "abc", **({"state": bad} if bad else {})})
        assert exchange.await_count == 0
        await c.get("/auth/strava/callback", params={"code": "abc", "state": state})
        assert user.strava_athlete_id == 741 and user.strava_client_secret == "super-secret"
        await c.get("/auth/strava/callback", params={"code": "abc", "state": state})
        assert exchange.await_count == 1
        assert (await db_session.execute(select(OAuthAttempt))).first() is None


async def test_expired_oauth_state_never_exchanges_a_token(client, db_session, monkeypatch):
    exchange = AsyncMock()
    monkeypatch.setattr(StravaService, "exchange_token", exchange)
    async with _logged_in(client, db_session, strava_client_id="42",
                          strava_client_secret_encrypted=encrypt_secret("secret")) as (c, user):
        response = await c.get("/auth/strava")
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        attempt = (await db_session.execute(select(OAuthAttempt))).scalar_one()
        attempt.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db_session.flush()
        await c.get("/auth/strava/callback", params={"code": "abc", "state": state})
        assert exchange.await_count == 0


async def test_strava_collision_preserves_the_other_account_and_health(client, db_session, monkeypatch):
    old = User(strava_athlete_id=741)
    db_session.add(old)
    await db_session.flush()
    health = HealthMetric(user_id=old.id, date=datetime.now().date(), metric="sleep", source="Garmin", value=480)
    db_session.add(health)
    await db_session.flush()
    exchange = AsyncMock(return_value={"access_token": "at", "refresh_token": "rt", "expires_at": 9999999999,
                                     "athlete": {"id": 741}})
    monkeypatch.setattr(StravaService, "exchange_token", exchange)
    async with _logged_in(client, db_session, strava_client_id="42",
                          strava_client_secret_encrypted=encrypt_secret("secret")) as (c, user):
        await c.get("/auth/strava")
        response = await c.get("/auth/strava/callback", params={"code": "abc", "state": _session(c)["strava_state"]})
        assert response.headers["location"] == "/setup?error=already_linked"
        assert (await db_session.get(User, old.id)).strava_athlete_id == 741
        assert (await db_session.get(HealthMetric, health.id)).user_id == old.id
        assert user.strava_athlete_id is None


@pytest.mark.parametrize("payload", ["[null]", "{}", '[{"distance_km": NaN}]', '[{"distance_km": 999}]'])
async def test_invalid_checkpoints_cannot_partially_save_a_route(as_user, db_session, payload):
    route_id = await _create_route(as_user)
    response = await as_user.post("/api/simulator/routes", data={"route_id": route_id, "name": "Changed",
                                                               "checkpoints_json": payload})
    assert response.status_code == 422
    route = await db_session.get(Route, route_id)
    assert route.name == "Jeju test" and route.target_time_s == 18000
    assert len((await db_session.execute(select(RouteCheckpoint).where(RouteCheckpoint.route_id == route_id))).scalars().all()) == len(CPS)


async def test_failed_checkpoint_insert_rolls_back_the_whole_save(as_user, db_session):
    route_id = await _create_route(as_user)

    def fail(*args):
        raise RuntimeError("Injected write failure")

    event.listen(RouteCheckpoint, "before_insert", fail)
    try:
        response = await as_user.post("/api/simulator/routes", data={"route_id": route_id, "name": "Changed",
                                                                   "checkpoints_json": json.dumps(CPS)})
    finally:
        event.remove(RouteCheckpoint, "before_insert", fail)
    assert response.status_code == 500
    route = await db_session.get(Route, route_id)
    await db_session.refresh(route)
    assert route.name == "Jeju test" and route.target_time_s == 18000
    assert len((await db_session.execute(select(RouteCheckpoint).where(RouteCheckpoint.route_id == route_id))).scalars().all()) == len(CPS)


async def test_empty_date_clears_but_an_omitted_date_preserves_it(as_user, db_session):
    route_id = await _create_route(as_user)
    await as_user.post("/api/simulator/routes", data={"route_id": route_id, "name": "Renamed"})
    assert (await db_session.get(Route, route_id)).race_date == "2026-10-02"
    await as_user.post("/api/simulator/routes", data={"route_id": route_id, "race_date": ""})
    assert (await db_session.get(Route, route_id)).race_date is None


async def test_a_successful_save_has_already_committed(as_user, db_session, monkeypatch):
    committed = AsyncMock(wraps=db_session.flush)
    monkeypatch.setattr(db_session, "commit", committed)
    await _create_route(as_user)
    committed.assert_awaited_once()


async def test_commit_failure_is_an_error_and_rolls_back(as_user, db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", AsyncMock(side_effect=RuntimeError("Commit failed")))
    response = await as_user.post("/api/simulator/routes", data={"course_json": _course().model_dump_json()})
    assert response.status_code == 500
    assert (await db_session.execute(select(Route))).first() is None


async def test_malformed_course_and_unknown_route_are_rejected(as_user, db_session):
    for course in ('{}', '[]', '{"total_distance_km": -20}'):
        assert (await as_user.post("/api/simulator/routes", data={"course_json": course})).status_code == 422
    assert (await as_user.post("/api/simulator/routes", data={"route_id": 9876})).status_code == 404
    assert (await db_session.execute(select(Route))).first() is None


@pytest.mark.parametrize("weight", ["-42", "NaN", "inf", "999", "not-a-number"])
async def test_invalid_weight_preserves_the_saved_value(as_user, test_user, weight):
    test_user.weight_kg = 70
    response = await as_user.post("/settings", data={"weight_kg": weight})
    assert response.status_code == 422
    assert test_user.weight_kg == 70


async def test_account_deletion_removes_legacy_nutrition_only_for_that_user(as_user, db_session, test_user):
    other = User()
    db_session.add(other)
    await db_session.flush()
    db_session.add_all([NutritionProduct(user_id=test_user.id, name="Owned"),
                        NutritionProduct(user_id=other.id, name="Other")])
    await db_session.flush()
    response = await as_user.post("/settings/delete-account", data={"confirmation": "SUPPRIMER"})
    assert response.status_code == 302
    assert (await db_session.execute(select(User).where(User.id == test_user.id))).first() is None
    assert [p.name for p in (await db_session.execute(select(NutritionProduct))).scalars()] == ["Other"]


def test_oauth_migration_round_trip_and_foreign_key():
    import sqlalchemy as sa
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from tests.test_sleep_need_migrations import ROOT, _load, _run

    migration = _load("b2e3f4a5b6c7_oauth_attempts")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == [migration.revision]
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO users VALUES (1), (2)"))
        _run(connection, migration.upgrade)
        connection.execute(sa.text("INSERT INTO oauth_attempts (state_hash, user_id, expires_at) VALUES ('state', 1, '2026-10-10')"))
        connection.execute(sa.text("DELETE FROM users WHERE id=1"))
        assert connection.execute(sa.text("SELECT count(*) FROM oauth_attempts")).scalar() == 0
        _run(connection, migration.downgrade)
        assert "oauth_attempts" not in sa.inspect(connection).get_table_names()
        assert connection.execute(sa.text("SELECT id FROM users")).scalar() == 2
    engine.dispose()
