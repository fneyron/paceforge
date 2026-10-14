from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_landing_page(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    assert "PaceForge" in response.text


@pytest.mark.asyncio
async def test_register_page(client: AsyncClient):
    response = await client.get("/auth/register")
    assert response.status_code == 200
    assert "Créer" in response.text or "compte" in response.text


@pytest.mark.asyncio
async def test_login_page(client: AsyncClient):
    response = await client.get("/auth/login")
    assert response.status_code == 200
    assert "Connexion" in response.text


@pytest.mark.asyncio
async def test_strava_link_requires_login(client: AsyncClient):
    """Without session, /auth/strava redirects to login."""
    response = await client.get("/auth/strava", follow_redirects=False)
    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_setup_page_loads(client: AsyncClient):
    response = await client.get("/setup")
    assert response.status_code == 200
    assert "Client ID" in response.text


@pytest.mark.asyncio
async def test_logout(client: AsyncClient):
    response = await client.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"


@pytest.mark.asyncio
async def test_dashboard_requires_auth(client: AsyncClient):
    response = await client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303


async def test_register_and_login_log_the_account_id_never_the_email(client, db_session, monkeypatch, caplog):
    import logging

    from sqlalchemy import select

    from app.models.user import User

    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *args: True)
    caplog.set_level(logging.INFO)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        await c.post("/auth/register", data={"email": "private@x.fr", "password": "password-123"})
        user = (await db_session.execute(select(User).where(User.email == "private@x.fr"))).scalar_one()
        user.email_verified = True
        await db_session.flush()
        assert (await c.post("/auth/login", data={"email": "private@x.fr", "password": "password-123"})).status_code == 302
    assert f"User {user.id} registered" in caplog.text and f"User {user.id} logged in" in caplog.text
    assert "private@x.fr" not in caplog.text


# ── passwords off the event loop, attempts limited, unknown emails as slow as known ones ──

import threading  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.models.user import User  # noqa: E402
from app.services import auth as auth_service  # noqa: E402
from app.services.auth import DUMMY_HASH, TOO_MANY, hash_password  # noqa: E402


@pytest.fixture
def no_commit(db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
def checks(monkeypatch):
    """Every password check: (hash checked, thread it ran on)."""
    seen = []
    real = auth_service.verify_password

    def spy(password, password_hash):
        seen.append((password_hash, threading.current_thread()))
        return real(password, password_hash)
    monkeypatch.setattr(auth_service, "verify_password", spy)
    return seen


async def _member(db_session, email="me@x.fr", password="pw-12345678"):
    user = User(email=email, password_hash=hash_password(password), email_verified=True)
    db_session.add(user)
    await db_session.flush()
    return user


async def _login(c, email, password="wrong-password"):
    return await c.post("/auth/login", data={"email": email, "password": password})


async def test_password_checks_run_off_the_event_loop(client, db_session, no_commit, checks):
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert (await _login(c, user.email, "pw-12345678")).status_code == 302
    assert checks and all(thread is not threading.main_thread() for _, thread in checks)


async def test_an_unknown_email_is_checked_against_a_dummy_hash(client, db_session, no_commit, checks):
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        r = await _login(c, "nobody@x.fr")
    assert "Email ou mot de passe incorrect." in r.text
    assert [h for h, _ in checks] == [DUMMY_HASH]  # as costly as a real check: the time tells nothing
    assert not auth_service.verify_password("anything", DUMMY_HASH)


async def test_failed_logins_are_limited_per_address_and_email(client, db_session, no_commit, checks, monkeypatch):
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        for _ in range(10):
            assert "Email ou mot de passe incorrect." in (await _login(c, user.email)).text
        n = len(checks)
        r = await _login(c, user.email, "pw-12345678")  # even the right password: wait
        assert r.status_code == 429 and TOO_MANY in r.text and len(checks) == n  # no bcrypt spent on it
        assert (await _login(c, "other@x.fr")).status_code == 200  # another email from here: still checked
    # a login that works clears that email's count
    monkeypatch.setattr(auth_service, "_counter", auth_service.MemoryCounter())
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        for _ in range(9):
            await _login(c, user.email)
        assert (await _login(c, user.email, "pw-12345678")).status_code == 302
        await c.get("/auth/logout")
        for _ in range(9):
            assert (await _login(c, user.email)).status_code == 200


async def test_one_address_trying_many_emails_is_limited(client, db_session, no_commit, monkeypatch):
    monkeypatch.setitem(auth_service.LIMITS, "login", (10, 3, 900))
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        for i in range(3):
            assert (await _login(c, f"n{i}@x.fr")).status_code == 200
        assert (await _login(c, "n9@x.fr")).status_code == 429


async def test_sign_ups_and_reset_emails_are_limited(client, db_session, no_commit, monkeypatch):
    sent = []
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *a: sent.append(a) or True)
    monkeypatch.setattr("app.routers.auth.send_password_reset_email", lambda *a: sent.append(a) or True)
    monkeypatch.setitem(auth_service.LIMITS, "register", (5, 2, 3600))
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        for i in range(2):
            assert (await c.post("/auth/register", data={"email": f"r{i}@x.fr", "password": "pw-12345678"})
                    ).status_code == 302
        r = await c.post("/auth/register", data={"email": "r9@x.fr", "password": "pw-12345678"})
        assert r.status_code == 429 and TOO_MANY in r.text
        assert len(sent) == 2 and (await db_session.execute(select(User).where(User.email == "r9@x.fr"))).first() is None
        user = await _member(db_session, "reset@x.fr")
        for _ in range(5):
            assert (await c.post("/auth/forgot-password", data={"email": user.email})).status_code == 200
        r = await c.post("/auth/forgot-password", data={"email": user.email})
        assert r.status_code == 429 and len(sent) == 2 + 5  # no sixth email, and the page says nothing more


async def test_a_reset_email_is_sent_after_the_answer(client, db_session, no_commit, monkeypatch):
    """The reset email (SMTP: seconds) goes after the response, so an email with an account is answered as fast
    as one without."""
    order = []
    monkeypatch.setattr("app.routers.auth.send_password_reset_email", lambda to, token: order.append("email"))
    user = await _member(db_session, "reset@x.fr")
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        r = await c.post("/auth/forgot-password", data={"email": user.email})
    assert r.status_code == 200 and order == ["email"] and user.password_reset_token


async def test_without_redis_nobody_is_held_back(client, db_session, no_commit, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens there
    monkeypatch.setattr(auth_service, "_counter", None)  # the production counters, unreachable
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        for _ in range(12):
            assert (await _login(c, user.email)).status_code == 200
        assert (await _login(c, user.email, "pw-12345678")).status_code == 302


async def test_a_verification_link_verifies_but_never_signs_the_browser_in(client, db_session, no_commit, monkeypatch):
    """Login CSRF: anyone can send someone the link of an account they registered; opening it must not put that
    browser into their account (whatever the victim then links or types would land there)."""
    sent = []
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *args: sent.append(args) or True)
    async with AsyncClient(transport=client._transport, base_url="https://test") as attacker:
        await attacker.post("/auth/register", data={"email": "attacker@x.fr", "password": "pw-12345678"})
    async with AsyncClient(transport=client._transport, base_url="https://test") as victim:
        r = await victim.get("/auth/verify-email", params={"token": sent[0][1]})
        assert r.status_code == 200 and "Email vérifié" in r.text
        assert (await victim.get("/settings")).headers["location"] == "/auth/login"  # not signed in
    user = (await db_session.execute(select(User).where(User.email == "attacker@x.fr"))).scalar_one()
    assert user.email_verified and user.email_verify_token is None
