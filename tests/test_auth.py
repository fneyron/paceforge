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


async def test_a_password_reset_signs_out_every_other_session(client, db_session, no_commit):
    from datetime import datetime, timedelta, timezone

    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as other, \
            AsyncClient(transport=client._transport, base_url="https://test") as here:
        for c in (other, here):
            assert (await _login(c, user.email, "pw-12345678")).status_code == 302
            assert (await c.get("/settings")).status_code == 200
        user.password_reset_token = "reset-token"
        user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        await db_session.flush()
        r = await here.post("/auth/reset-password", data={"token": "reset-token", "password": "pw-87654321"})
        assert r.headers["location"] == "/sante"
        assert (await here.get("/settings")).status_code == 200  # the browser that reset it stays signed in
        assert (await other.get("/settings")).headers["location"] == "/auth/login"  # every other one is out
        assert (await other.get("/connections/strava")).headers["location"] == "/auth/login?next=strava"
        assert (await _login(other, user.email, "pw-87654321")).status_code == 302  # the new password works
        assert (await other.get("/settings")).status_code == 200


async def test_api_404s_are_json_pages_stay_html(client, test_user):
    from app.dependencies import get_current_user

    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    r = await client.get("/api/simulator/routes/987654/offline")  # the phone app's offline plan, gone
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    r = await client.get("/api/nothing-here")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "Not Found"}
    r = await client.get("/api/nothing-here", headers={"Accept": "text/html,application/xhtml+xml"})
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")  # opened as a page
    r = await client.get("/nothing-here")
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")


async def test_sending_the_emails_never_logs_the_address(client, db_session, no_commit, caplog):
    import logging

    caplog.set_level(logging.INFO)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:  # SMTP unset in tests
        await c.post("/auth/register", data={"email": "private@x.fr", "password": "password-123"})
        await c.post("/auth/forgot-password", data={"email": "private@x.fr"})
    assert "SMTP" in caplog.text and "private@x.fr" not in caplog.text


# ── parallel attempts: counted before any work, so none slips past the limit ──

async def _burst(engine, monkeypatch, path: str, forms: list[dict]):
    """The same form posted in parallel from one address, each request with its own database session as in
    production. Returns the status codes and the password checks made."""
    import asyncio

    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.dependencies import get_db
    from app.main import create_app

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = create_app()

    async def per_request_db():
        async with factory() as s:
            yield s
            await s.commit()
    app.dependency_overrides[get_db] = per_request_db
    checks = []
    real = auth_service.verify_password

    def spy(password, password_hash):
        checks.append(password_hash)
        return real(password, password_hash)
    monkeypatch.setattr(auth_service, "verify_password", spy)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as c:
        responses = await asyncio.gather(*[c.post(path, data=form) for form in forms])
    return [r.status_code for r in responses], checks


@pytest.fixture
async def committed(engine):
    """Rows committed to the shared test database for a parallel test, deleted afterwards."""
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    emails = []

    async def add(email, password="pw-12345678"):
        async with factory() as s:
            s.add(User(email=email, password_hash=hash_password(password), email_verified=True))
            await s.commit()
        emails.append(email)
    yield add, emails
    async with factory() as s:
        await s.execute(delete(User).where(User.email.in_(emails) | User.email.like("burst-%@x.fr")))
        await s.commit()


async def test_parallel_failed_logins_never_pass_the_limit(engine, committed, monkeypatch):
    add, _ = committed
    await add("burst@x.fr")
    monkeypatch.setitem(auth_service.LIMITS, "login", (3, 50, 900))
    codes, checks = await _burst(engine, monkeypatch, "/auth/login",
                                 [{"email": "burst@x.fr", "password": f"guess-{i}"} for i in range(30)])
    assert len(checks) == 3 and codes.count(200) == 3 and codes.count(429) == 27


async def test_parallel_reset_requests_never_pass_the_limit(engine, committed, monkeypatch):
    add, _ = committed
    await add("burst-reset@x.fr")
    sent = []
    monkeypatch.setattr("app.routers.auth.send_password_reset_email", lambda to, token: sent.append(to))
    codes, _ = await _burst(engine, monkeypatch, "/auth/forgot-password", [{"email": "burst-reset@x.fr"}] * 20)
    assert len(sent) == 5 and codes.count(429) == 15


async def test_parallel_sign_ups_never_pass_the_limit(engine, committed, monkeypatch):
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *args: True)
    monkeypatch.setitem(auth_service.LIMITS, "register", (5, 3, 3600))
    codes, _ = await _burst(engine, monkeypatch, "/auth/register",
                            [{"email": f"burst-{i}@x.fr", "password": "pw-12345678"} for i in range(12)])
    assert codes.count(302) == 3 and codes.count(429) == 9


async def test_a_login_that_works_is_not_charged_to_the_address(client, db_session, no_commit, monkeypatch):
    monkeypatch.setitem(auth_service.LIMITS, "login", (10, 3, 900))
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        for _ in range(5):  # five good logins from the address: none counts against it
            assert (await _login(c, user.email, "pw-12345678")).status_code == 302
            await c.get("/auth/logout")
        for i in range(3):
            assert (await _login(c, f"x{i}@x.fr")).status_code == 200
        assert (await _login(c, "x9@x.fr")).status_code == 429


async def test_the_reset_token_is_committed_before_the_email_goes(committing_session, committing_client, monkeypatch):
    """The email must never carry a link whose token is not saved yet, nor hold the row's lock during SMTP."""
    db = committing_session
    user = User(email="reset-order@x.fr", password_hash=hash_password("pw-12345678"), email_verified=True)
    db.add(user)
    await db.commit()
    order = []
    real_commit = db.commit

    async def commit():
        order.append("commit")
        await real_commit()
    monkeypatch.setattr(db, "commit", commit)
    monkeypatch.setattr("app.routers.auth.send_password_reset_email", lambda to, token: order.append("email"))
    assert (await committing_client.post("/auth/forgot-password", data={"email": user.email})).status_code == 200
    assert "email" in order and order.index("commit") < order.index("email")
