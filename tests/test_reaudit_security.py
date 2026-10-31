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
from app.services import auth as auth_service
from app.services import coros
from app.services.auth import TOO_MANY, hash_password, hash_token, verification_token
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


# ── the account's login cap never locks its owner's own browsers out ─────────

async def _member(db, email="me@x.fr", password="pw-12345678") -> User:
    user = User(email=email, password_hash=hash_password(password), email_verified=True)
    db.add(user)
    await db.flush()
    return user


def _browser(client) -> AsyncClient:
    return AsyncClient(transport=client._transport, base_url="https://test")


async def _login_from(c, address, email, password="wrong-password"):
    return await c.post("/auth/login", data={"email": email, "password": password},
                        headers={"X-Forwarded-For": address})


@pytest.fixture
def alerts(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr("app.routers.auth.send_login_alert_email", lambda to: sent.append(to) or True)
    return sent


async def test_a_browser_that_signed_in_before_passes_the_account_cap(client, db_session, monkeypatch, alerts):
    """Five addresses × ten wrong passwords used to lock the owner out for an hour: the browser he signed in with
    before still gets in (the address limits still hold it); any other browser waits, as before."""
    monkeypatch.setitem(auth_service.ACCOUNT_LIMITS, "login", (4, 3600))
    user = await _member(db_session)
    async with _browser(client) as own:
        assert (await _login_from(own, "198.51.100.1", user.email, "pw-12345678")).status_code == 302
        await own.post("/auth/logout")
        for i in range(5):  # the campaign, each attempt from a browser and an address of its own
            async with _browser(client) as attacker:
                await _login_from(attacker, f"203.0.113.{i}", user.email)
        async with _browser(client) as stranger:
            r = await _login_from(stranger, "198.51.100.2", user.email, "pw-12345678")
        assert r.status_code == 429 and TOO_MANY in r.text
        assert (await _login_from(own, "198.51.100.3", user.email, "pw-12345678")).status_code == 302
        await own.post("/auth/logout")
        # the address limits still hold the known browser: ten failures from one address, then a wait
        for _ in range(10):
            assert (await _login_from(own, "198.51.100.4", user.email)).status_code == 200
        assert (await _login_from(own, "198.51.100.4", user.email, "pw-12345678")).status_code == 429


async def test_the_known_browser_cookie_is_signed_http_only_and_for_the_auth_routes(client, db_session):
    user = await _member(db_session)
    async with _browser(client) as c:
        r = await _login_from(c, "198.51.100.1", user.email, "pw-12345678")
    cookie = next(v for v in r.headers.get_list("set-cookie") if v.startswith(f"{auth_service.KNOWN_COOKIE}="))
    attributes = {part.strip().split("=")[0].lower() for part in cookie.split(";")[1:]}
    assert {"httponly", "secure", "samesite", "path", "max-age"} <= attributes
    assert "Path=/auth" in cookie and "SameSite=lax" in cookie and "Max-Age=31536000" in cookie
    value = cookie.split(";")[0].split("=", 1)[1]
    assert "me@x.fr" not in value and auth_service.known_version(value, user.email) == 0
    assert auth_service.known_version(value, "other@x.fr") is None  # names this account only
    assert auth_service.known_version(value[:-2] + "xx", user.email) is None  # forged
    # a browser remembers its last accounts, the newest first, never one twice
    value = auth_service.known_cookie(value, "other@x.fr", 3)
    value = auth_service.known_cookie(value, user.email, 1)
    assert auth_service.known_version(value, user.email) == 1 and auth_service.known_version(value, "other@x.fr") == 3


async def test_a_known_browser_from_before_a_password_reset_no_longer_passes(client, db_session, monkeypatch,
                                                                            alerts):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setitem(auth_service.ACCOUNT_LIMITS, "login", (2, 3600))
    user = await _member(db_session)
    async with _browser(client) as old, _browser(client) as resetter:
        assert (await _login_from(old, "198.51.100.1", user.email, "pw-12345678")).status_code == 302
        await old.post("/auth/logout")
        user.password_reset_token = hash_token("reset-token")
        user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        await db_session.flush()
        r = await resetter.post("/auth/reset-password", data={"token": "reset-token", "password": "pw-87654321"})
        assert r.status_code == 302 and auth_service.KNOWN_COOKIE in r.headers.get("set-cookie", "")
        await resetter.post("/auth/logout")
        for i in range(3):
            async with _browser(client) as attacker:
                await _login_from(attacker, f"203.0.113.{i}", user.email)
        assert (await _login_from(old, "198.51.100.2", user.email, "pw-87654321")).status_code == 429
        assert (await _login_from(resetter, "198.51.100.3", user.email, "pw-87654321")).status_code == 302


async def test_the_owner_is_told_once_when_the_cap_is_first_reached(client, db_session, monkeypatch, alerts):
    monkeypatch.setitem(auth_service.ACCOUNT_LIMITS, "login", (2, 3600))
    user = await _member(db_session)
    codes = []
    for i in range(5):
        async with _browser(client) as attacker:
            codes.append((await _login_from(attacker, f"203.0.113.{i}", user.email)).status_code)
            codes.append((await _login_from(attacker, f"203.0.113.{i}", "nobody@x.fr")).status_code)
    assert codes[::2] == [200, 200, 429, 429, 429] and codes[1::2] == [200, 200, 429, 429, 429]
    assert alerts == [user.email]  # one email, to the account's owner; none for an address without an account
    from app.services.email import send_login_alert_email

    sent = []
    monkeypatch.setattr("app.services.email.send_email", lambda to, subject, html: sent.append((to, subject, html)))
    send_login_alert_email(user.email)
    (to, subject, html), = sent
    assert to == user.email and subject == "Tentatives de connexion à ton compte PaceForge"
    assert "Plusieurs tentatives de connexion à ton compte ont échoué." in html
    assert "Si ce n'est pas toi, change ton mot de passe." in html and "/auth/forgot-password" in html


# ── an unreadable plan is refused in French, never a server error ────────────

@pytest.mark.parametrize("course", ["pas du json", "[]", '{"total_distance_km": "loin"}', '{"segments": 3}'])
async def test_an_unreadable_course_is_a_400_in_french(as_user, course):
    r = await as_user.post("/partials/simulator/passage-times", data={"course_json": course})
    assert r.status_code == 400 and "Plan illisible : recharge la page." in r.text


async def test_unreadable_points_or_weather_are_a_400_too(as_user):
    from tests.test_race_plan_services import _course

    course = _course().model_dump_json()
    for extra in ({"checkpoints_json": "{"}, {"checkpoints_json": "{}"}, {"checkpoints_json": "[3]"},
                  {"hourly_json": "[NaN"}):
        r = await as_user.post("/partials/simulator/passage-times", data={"course_json": course, **extra})
        assert r.status_code == 400 and "Plan illisible" in r.text, extra
    r = await as_user.post("/partials/simulator/passage-times", data={"course_json": course})
    assert r.status_code == 200 and 'id="plan-data"' in r.text  # a readable one is calculated as before


async def test_a_form_field_past_starlettes_cap_is_refused_in_french(as_user, test_user):
    login(as_user, test_user)  # a signed-in session: the plan routes' 2 MiB, past Starlette's 1 MiB a field
    big ='{"name": "' + "x" * (1024 * 1024) + '"}'
    r = await as_user.post("/partials/simulator/passage-times", data={"course_json": big})
    assert r.status_code == 400 and r.headers["content-type"].startswith("text/html")
    assert "Envoi trop volumineux : un champ dépasse 1 Mo." in r.text and "Field exceeded" not in r.text
    r = await as_user.post("/api/simulator/routes", data={"course_json": big})  # an API caller: JSON, in French
    assert r.status_code == 400 and r.json() == {"detail": "Envoi trop volumineux : un champ dépasse 1 Mo."}
    # every other 400 keeps FastAPI's own answer
    from app.exceptions import form_refusal

    assert form_refusal("Test invalide.") is None and form_refusal(None) is None
    assert form_refusal("Too many fields. Maximum number of fields is 1000.") == "Envoi refusé : trop de champs."


# ── isolation headers ────────────────────────────────────────────────────────

def test_no_page_relies_on_a_window_opener():
    """Cross-Origin-Opener-Policy: same-origin severs a cross-origin opener: nothing may read one (the printable
    band's own window.open stays on this origin and drops its opener itself)."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    files = [*(root / "templates").rglob("*.html"), *(root / "static" / "js").rglob("*.js")]
    readers = [f"{f.name}: {m}" for f in files
               for m in re.findall(r".{20}\.opener\b(?!\s*=\s*null).{0,20}", f.read_text(encoding="utf-8"))]
    assert not readers, readers


async def test_isolation_headers_on_pages_files_and_errors(client):
    for path in ("/", "/auth/login", "/static/js/app.js", "/nothing-here", "/health"):
        r = await client.get(path)
        assert r.headers["cross-origin-opener-policy"] == "same-origin", path
        assert r.headers["cross-origin-resource-policy"] == "same-site", path
        assert "payment=()" in r.headers["permissions-policy"] and "usb=()" in r.headers["permissions-policy"]
    assert "includeSubDomains" not in r.headers["strict-transport-security"]  # until www has its certificate
