"""Security, operations and performance corrections of the 2026-10-31 re-audit."""
import re
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.dependencies import get_current_user, get_db
from app.main import create_app
from app.models.lactate import LactateTest
from app.models.route import Route
from app.models.user import User
from app.services import coros
from app.services.auth import hash_password, hash_token, verification_token
from app.services.strava import StravaService
from tests.test_coros import D30, FakeCoros
from tests.test_lactate import fields
from tests.test_mobile import login
from tests.test_simulator_routes import _create_route


# ── a write the next request reads is committed before the answer ────────────
# get_db commits only once the response has been sent (FastAPI runs a request-scoped dependency's exit after it):
# a redirect or an answer the page acts on at once must not wait for it.

@pytest.fixture
def commits(db_session, monkeypatch) -> AsyncMock:
    """Every commit a route asks for (the `client` fixture's get_db never commits): flushed, counted."""
    spy = AsyncMock(wraps=db_session.flush)
    monkeypatch.setattr(db_session, "commit", spy)
    return spy


@pytest.fixture
async def as_user(client, test_user):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    return client


async def test_an_account_deletion_is_committed_before_its_redirect(committing_session, monkeypatch):
    """A get_db that commits nothing (as if the redirect had already been followed): the account is gone all the
    same, for good."""
    from datetime import datetime, timedelta, timezone

    from app.crypto import encrypt_secret
    from app.models import CorosConnection

    db = committing_session
    user = User(email="leaving@x.fr", email_verified=True)
    db.add(user)
    await db.flush()
    db.add(CorosConnection(user_id=user.id, issuer=FakeCoros.META["issuer"], client_id="cid-1",
                           access_token_encrypted=encrypt_secret("at-1"), refresh_token_encrypted=encrypt_secret("rt-1"),
                           expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                           resource_url=FakeCoros.PRM["resource"], token_endpoint=FakeCoros.META["token_endpoint"],
                           revocation_endpoint=FakeCoros.META["revocation_endpoint"]))
    await db.commit()
    fake = FakeCoros(shift_days=(datetime.now(timezone.utc).date() - D30).days)
    monkeypatch.setattr(coros, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(StravaService, "deauthorize", AsyncMock())
    app = create_app()

    async def never_commits():
        yield db
    app.dependency_overrides[get_db] = never_commits
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as c:
        r = await c.post("/settings/delete-account", data={"confirmation": "SUPPRIMER"})
    assert r.status_code == 302 and r.headers["location"] == "/?account_deleted=1"
    await db.rollback()  # only what the route committed itself counts
    assert (await db.execute(select(func.count()).select_from(User).where(User.id == user.id))).scalar() == 0


async def test_a_lactate_test_is_committed_before_the_page_it_redirects_to(client, db_session, test_user, commits):
    login(client, test_user)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', (await client.get("/sante/lactate")).text)[1]
    saved = await client.post("/sante/lactate", data={**fields(), "csrf_token": csrf})
    assert saved.status_code == 303 and commits.await_count == 1
    row = await db_session.scalar(select(LactateTest))
    assert (await client.post(f"/sante/lactate/{row.id}/supprimer", data={"csrf_token": csrf})).status_code == 303
    assert commits.await_count == 2


async def test_the_plan_writes_the_page_reads_at_once_are_committed_first(as_user, db_session, commits):
    """The settings forms recalculate as soon as they answer, a rename reloads the list, a deletion drops the card."""
    rid = await _create_route(as_user)
    n = commits.await_count
    assert (await as_user.post(f"/api/simulator/routes/{rid}/scenarios", data={"scenario_fast_pct": "5"})).status_code == 204
    assert commits.await_count == n + 1
    assert (await as_user.post(f"/api/simulator/routes/{rid}/params", data={"hr_cap_climb": "150"})).status_code == 204
    assert commits.await_count == n + 2
    assert (await as_user.patch(f"/api/simulator/routes/{rid}", data={"name": "Autre nom"})).json()["name"] == "Autre nom"
    assert commits.await_count == n + 3
    assert (await as_user.delete(f"/api/simulator/routes/{rid}")).json() == {"ok": True}
    assert commits.await_count == n + 4 and await db_session.get(Route, rid) is None


async def test_a_new_verification_link_is_committed_before_the_email_goes(client, db_session, monkeypatch):
    user = User(email="new@x.fr", password_hash=hash_password("pw-12345678"), email_verified=False)
    db_session.add(user)
    await db_session.flush()
    order = []
    monkeypatch.setattr(db_session, "commit", AsyncMock(side_effect=lambda: order.append("commit")))
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *a: order.append("email") or True)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        await c.post("/auth/login", data={"email": user.email, "password": "pw-12345678"})  # pending_user_id
        assert (await c.post("/auth/resend-verification")).status_code == 303
    assert order == ["commit", "email"]


async def test_a_verified_email_is_committed_before_the_answer(client, db_session, commits):
    token = verification_token()
    user = User(email="v@x.fr", email_verified=False, email_verify_token=hash_token(token))
    db_session.add(user)
    await db_session.flush()
    assert (await client.get(f"/auth/verify-email?token={token}")).status_code == 200
    assert user.email_verified and commits.await_count == 1
