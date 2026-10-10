"""Réglages: Strava shown like COROS and Garmin (state, one action, no app keys
on the page), the way back to Réglages after connecting, disconnect, a
refused refresh asking to reconnect; the caffeine cap shown with its value."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_secret
from app.dependencies import get_current_user
from app.exceptions import StravaTokenError
from app.models.user import User
from app.services.auth import hash_password
from app.services.strava import StravaService


@pytest.fixture
def no_commit(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, no_commit):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        yield c


@asynccontextmanager
async def _logged_in(client: AsyncClient, db: AsyncSession, **fields):
    """A real session login (the /auth/strava routes read the session)."""
    user = User(email="me@x.fr", password_hash=hash_password("pw-12345678"), email_verified=True, **fields)
    db.add(user)
    await db.flush()
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        r = await c.post("/auth/login", data={"email": "me@x.fr", "password": "pw-12345678"})
        assert r.status_code == 302
        yield c, user


async def _callback(client, **params):
    # Simulate the provider returning the nonce from the authorization redirect.
    import base64
    import json
    session = json.loads(base64.b64decode(client.cookies.get("paceforge_session").split(".")[0]))
    return await client.get("/auth/strava/callback", params={"state": session["strava_state"], **params})


# ── Strava row ──────────────────────────────────────────────────────────────

def _row(page: str) -> str:
    return page.split('id="strava"')[1].split('id="coros"')[0]


async def _own_app(db: AsyncSession, user: User, client_id: str = "4242", sub: int | None = None) -> None:
    user.strava_client_id = client_id
    user.strava_client_secret_encrypted = encrypt_secret("s3cret")
    user.strava_webhook_subscription_id = sub
    await db.flush()


async def test_strava_row_is_like_the_others(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    # linked through the old shared app (no own keys): never synced, so it asks to reconnect
    strava = _row((await as_user.get("/settings")).text)
    assert "À reconnecter" in strava and "Connecté</span>" not in strava
    await _own_app(db_session, test_user)
    page = (await as_user.get("/settings")).text
    strava = _row(page)
    assert "Connecté" in strava and "Déconnecter" in strava and "Première synchro en cours" in strava
    # the app keys are never on the page
    for word in ("Client ID", "Client Secret", "Identifiants", "strava-credentials", "Gérer ton app"):
        assert word not in page, word

    test_user.strava_credentials_valid = False
    await db_session.flush()
    strava = _row((await as_user.get("/settings")).text)
    assert "À reconnecter" in strava and 'href="/auth/strava?next=settings"' in strava
    assert 'href="/setup?next=settings"' in strava  # new app keys are typed there

    test_user.strava_access_token = None
    await db_session.flush()
    strava = _row((await as_user.get("/settings")).text)
    assert "Non connecté" in strava and "Connecter Strava" in strava and "Déconnecter" not in strava


async def test_connect_without_app_keys_goes_to_the_setup_wizard(client: AsyncClient, db_session: AsyncSession,
                                                                no_commit, monkeypatch):
    monkeypatch.setattr(StravaService, "exchange_token", AsyncMock(return_value={
        "access_token": "at", "refresh_token": "rt", "expires_at": 9999999999,
        "athlete": {"id": 779, "firstname": "A", "lastname": "B", "profile": None}}))
    monkeypatch.setattr(StravaService, "create_webhook_subscription", AsyncMock(return_value=None))
    started = []
    monkeypatch.setattr("app.tasks.initial_sync.initial_sync.delay", lambda uid: started.append(uid))
    async with _logged_in(client, db_session) as (c, user):
        r = await c.get("/auth/strava?next=settings")
        assert r.status_code == 302 and r.headers["location"] == "/setup?next=settings"
        assert "Client ID" in (await c.get(r.headers["location"])).text  # the only place the keys are typed
        await c.post("/setup/credentials", data={"client_id": "7777", "client_secret": "s"})
        r = await _callback(c, code="abc")
        assert r.headers["location"] == "/settings#strava"  # the wizard keeps the way back
        assert user.strava_client_id == "7777" and user.strava_access_token == "at" and started == [user.id]


async def test_connect_from_settings_comes_back_to_settings(client: AsyncClient, db_session: AsyncSession,
                                                            no_commit, monkeypatch):
    monkeypatch.setattr(StravaService, "exchange_token", AsyncMock(return_value={
        "access_token": "at", "refresh_token": "rt", "expires_at": 9999999999,
        "athlete": {"id": 777, "firstname": "A", "lastname": "B", "profile": None}}))
    monkeypatch.setattr(StravaService, "create_webhook_subscription", AsyncMock(return_value=None))
    async with _logged_in(client, db_session, strava_client_id="4242",
                          strava_client_secret_encrypted=encrypt_secret("s3cret"),
                          initial_sync_done=True) as (c, user):
        # keys left by an abandoned wizard don't win over the stored ones
        await c.post("/setup/credentials", data={"client_id": "WRONG", "client_secret": "x"})
        r = await c.get("/auth/strava?next=settings")
        assert r.status_code == 302
        q = parse_qs(urlsplit(r.headers["location"]).query)
        assert q["client_id"] == ["4242"]
        r = await _callback(c, code="abc")
        assert r.status_code == 302 and r.headers["location"] == "/settings#strava"
        assert user.strava_credentials_valid and user.strava_access_token == "at"
        client._transport.app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[attr-defined]
        page = (await c.get("/settings")).text
        assert "Strava connecté. Tes activités arrivent." in page


async def test_disconnect_revokes_and_forgets_the_tokens(as_user: AsyncClient, db_session: AsyncSession,
                                                         test_user: User, monkeypatch):
    await _own_app(db_session, test_user, sub=55)
    test_user.initial_sync_done = True
    test_user.last_activity_poll_at = datetime.now(timezone.utc)
    await db_session.flush()
    deauth = AsyncMock()
    unsubscribe = AsyncMock(return_value=True)
    monkeypatch.setattr(StravaService, "deauthorize", deauth)
    monkeypatch.setattr(StravaService, "delete_webhook_subscription", unsubscribe)
    r = await as_user.post("/settings/strava/disconnect")
    assert r.status_code == 303 and r.headers["location"] == "/settings#strava"
    assert deauth.await_count == 1 and unsubscribe.await_args.args[-1] == 55
    assert test_user.strava_access_token is None and test_user.strava_refresh_token is None
    assert test_user.strava_webhook_subscription_id is None
    assert test_user.strava_athlete_id and test_user.strava_client_id == "4242"  # one click to reconnect
    # reconnecting fetches what was recorded meanwhile; Réglages say so
    assert test_user.initial_sync_done is False and test_user.last_activity_poll_at is None
    page = (await as_user.get("/settings")).text
    assert "Strava déconnecté. Les activités déjà reçues restent." in page and "Connecter Strava" in page


async def test_a_disconnected_link_is_left_alone_by_the_syncs(db_session: AsyncSession, test_user: User,
                                                              monkeypatch):
    import app.database
    from app.tasks.poll_activities import _run_poll

    await _own_app(db_session, test_user, sub=55)
    test_user.initial_sync_done = True
    await db_session.flush()
    monkeypatch.setattr(StravaService, "deauthorize", AsyncMock())
    monkeypatch.setattr(StravaService, "delete_webhook_subscription", AsyncMock(return_value=False))
    from app.services.strava import disconnect

    await disconnect(db_session, test_user)
    assert test_user.strava_webhook_subscription_id == 55  # Strava kept it: retried next time
    test_user.initial_sync_done = True  # even so, no tokens means no poll
    await db_session.flush()
    with pytest.raises(StravaTokenError):  # not a TypeError on the missing expiry
        await StravaService.for_user(db_session, test_user).refresh_token_if_needed(test_user)

    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(app.database, "get_task_session", session)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(StravaService, "get_recent_activities", fetch)
    result = await _run_poll()
    assert result["users_checked"] == 0 and fetch.await_count == 0


async def test_deauthorize_keeps_the_token_out_of_the_url(db_session: AsyncSession, test_user: User):
    import logging

    import app.main  # noqa: F401  (sets the httpx logger level)

    assert logging.getLogger("httpx").level == logging.WARNING
    resp = httpx.Response(200, json={}, request=httpx.Request("POST", "https://www.strava.com/oauth/deauthorize"))
    post = AsyncMock(return_value=resp)
    with patch("httpx.AsyncClient.post", post):
        await StravaService(db_session).deauthorize(test_user)
    kwargs = post.await_args.kwargs
    assert kwargs["data"] == {"access_token": "test_access_token"} and "params" not in kwargs


async def test_a_refresh_lost_to_another_worker_is_not_a_revocation(db_session: AsyncSession, test_user: User):
    test_user.strava_token_expires_at = 0
    await db_session.flush()
    # another worker refreshed meanwhile and stored the new pair
    await db_session.execute(update(User).where(User.id == test_user.id).values(
        strava_refresh_token="newer", strava_access_token="fresh", strava_token_expires_at=9999999999)
        .execution_options(synchronize_session=False))
    resp = httpx.Response(400, json={"message": "Bad Request"},
                          request=httpx.Request("POST", "https://www.strava.com/oauth/token"))
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=resp)):
        user = await StravaService(db_session).refresh_token_if_needed(test_user)
    assert user.strava_access_token == "fresh" and user.strava_credentials_valid is True


async def test_a_pair_refreshed_by_another_worker_is_used_as_is(db_session: AsyncSession, test_user: User):
    test_user.strava_token_expires_at = 0
    await db_session.flush()
    await db_session.execute(update(User).where(User.id == test_user.id).values(
        strava_refresh_token="newer", strava_access_token="fresh", strava_token_expires_at=9999999999)
        .execution_options(synchronize_session=False))
    post = AsyncMock()
    with patch("httpx.AsyncClient.post", post):
        user = await StravaService(db_session).refresh_token_if_needed(test_user)
    assert user.strava_access_token == "fresh" and post.await_count == 0  # no second refresh


async def test_refused_keys_and_a_revoked_access_are_told_apart(db_session: AsyncSession, test_user: User):
    def refused(status):
        resp = httpx.Response(status, json={"message": "Bad Request"},
                              request=httpx.Request("POST", "https://www.strava.com/oauth/token"))
        return patch("httpx.AsyncClient.post", AsyncMock(return_value=resp))

    test_user.strava_token_expires_at = 0
    await db_session.flush()
    with refused(401), pytest.raises(StravaTokenError):  # the app's keys: new ones needed
        await StravaService(db_session).refresh_token_if_needed(test_user)
    assert test_user.strava_credentials_valid is False and test_user.strava_refresh_token

    test_user.strava_credentials_valid = True
    await db_session.flush()
    with refused(400), pytest.raises(StravaTokenError):  # PaceForge revoked on Strava: the link is gone
        await StravaService(db_session).refresh_token_if_needed(test_user)
    assert test_user.strava_credentials_valid is True and test_user.strava_refresh_token is None
    assert test_user.initial_sync_done is False  # reconnecting fetches the gap


async def test_cancel_or_refusal_from_settings_comes_back_to_settings(client: AsyncClient, db_session: AsyncSession,
                                                                       no_commit, monkeypatch):
    from app.exceptions import StravaAPIError

    refuse = AsyncMock(side_effect=StravaAPIError("Token exchange failed: 401", status_code=401))
    monkeypatch.setattr(StravaService, "exchange_token", refuse)
    async with _logged_in(client, db_session, strava_client_id="4242",
                          strava_client_secret_encrypted=encrypt_secret("s3cret")) as (c, user):
        client._transport.app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[attr-defined]
        await c.get("/auth/strava?next=settings")
        r = await _callback(c, error="access_denied")
        assert r.headers["location"] == "/settings#strava"
        assert "Connexion à Strava annulée." in (await c.get("/settings")).text
        await c.get("/auth/strava?next=settings")
        r = await _callback(c, code="abc")
        assert r.headers["location"] == "/settings#strava"
        page = (await c.get("/settings")).text
        assert "Strava refuse les clés de ton app : change-les." in page
        # the stored keys are known bad now: connecting goes through the wizard
        assert user.strava_credentials_valid is False and 'href="/setup?next=settings"' in _row(page)
        assert (await c.get("/auth/strava?next=settings")).headers["location"] == "/setup?next=settings"
        user.strava_credentials_valid = True
        await db_session.flush()
        # a reused code (a reload of the callback) or a Strava hiccup is not a refusal
        refuse.side_effect = StravaAPIError("Token exchange failed: 400", status_code=400)
        await c.get("/auth/strava?next=settings")
        await _callback(c, code="abc")
        assert user.strava_credentials_valid is True
        refuse.side_effect = StravaAPIError("Token exchange failed: 503", status_code=503)
        await c.get("/auth/strava?next=settings")
        await _callback(c, code="abc")
        assert user.strava_credentials_valid is True
        assert "La connexion à Strava a échoué. Réessaie." in (await c.get("/settings")).text
        # without the way back (onboarding), the wizard as before, and no stale way back
        await c.get("/auth/strava")
        r = await _callback(c, error="access_denied")
        assert r.headers["location"] == "/setup?error=auth_failed"


async def test_new_app_keys_from_settings_replace_the_old_app(client: AsyncClient, db_session: AsyncSession,
                                                              no_commit, monkeypatch):
    monkeypatch.setattr(StravaService, "exchange_token", AsyncMock(return_value={
        "access_token": "at2", "refresh_token": "rt2", "expires_at": 9999999999,
        "athlete": {"id": 778, "firstname": "A", "lastname": "B", "profile": None}}))
    unsubscribe = AsyncMock(return_value=True)
    monkeypatch.setattr(StravaService, "delete_webhook_subscription", unsubscribe)
    monkeypatch.setattr(StravaService, "create_webhook_subscription", AsyncMock(return_value=99))
    async with _logged_in(client, db_session, strava_client_id="4242", initial_sync_done=True,
                          strava_client_secret_encrypted=encrypt_secret("old"), strava_credentials_valid=False,
                          strava_webhook_subscription_id=55) as (c, user):
        assert "Client ID" in (await c.get("/setup?next=settings")).text
        r = await c.post("/setup/credentials", data={"client_id": "5555", "client_secret": "new"})
        assert parse_qs(urlsplit(r.headers["location"]).query)["client_id"] == ["5555"]
        r = await _callback(c, code="abc")
        assert r.headers["location"] == "/settings#strava"
        assert unsubscribe.await_args.args[-1] == 55  # the old app's subscription, with the old keys
        assert user.strava_client_id == "5555" and user.strava_webhook_subscription_id == 99
        assert user.strava_credentials_valid is True


# ── profile ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("weight", [None, 50.0, 80.0])
async def test_the_weight_has_no_helper_and_no_caffeine_line(as_user: AsyncClient, db_session: AsyncSession,
                                                             test_user: User, weight):
    """v4.3 (owner: « Ta caféine en course est plafonnée à 400 mg par 24 h — ça n'a rien à faire dans Réglages,
    non ? »), then no Nutrition tool at all (owner, 2026-10-09): the weight field alone (« Poids », « kg »),
    whatever the weight, no helper under it and no caffeine line."""
    test_user.weight_kg = weight
    await db_session.flush()
    page = (await as_user.get("/settings")).text
    profile = page.split('<h2 class="pf-h2">Profil</h2>')[1].split("</form>")[0]
    assert 'name="weight_kg"' in profile and "Sert à ta" not in profile and "nutrition" not in page.lower()
    assert "caféine" not in page and "400 mg" not in page and "Plafond de caféine" not in page
