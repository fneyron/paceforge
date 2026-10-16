"""Browser-side hardening: assets served from here, security headers, cross-site requests refused, sessions that
end, limits that hold without Redis, tokens stored hashed, a webhook that tells nothing."""
import base64
import json
import time
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.build import BUILD_ID
from app.dependencies import get_current_user
from app.models.user import User
from tests.test_race_plan_services import CPS, _course

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


async def _route(client: AsyncClient) -> int:
    r = await client.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS), "name": "Jeju",
        "target_time_s": 5 * 3600, "race_date": "2026-10-02", "start_hour": 21, "start_minute": 0,
        "sport_type": "trail",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ── MapLibre vendored, with the credits its sources ask for ─────────────────

async def test_the_map_library_is_served_from_here_with_its_credits(as_user: AsyncClient):
    """Base code: maplibre-gl came from unpkg.com at runtime, without integrity check (a compromised CDN release
    ran in every signed-in session), and the map hid every source's attribution."""
    page = (await as_user.get(f"/simulator/routes/{await _route(as_user)}")).text
    assert "unpkg.com" not in page
    assert "'/static/vendor/maplibre-gl/maplibre-gl'" in page and f"ASSET_V = '{BUILD_ID}'" in page
    assert "maplibregl.AttributionControl({ compact: true" in page and "'bottom-right'" in page
    # the styles carry OpenStreetMap's, CARTO's and OpenFreeMap's credit; the relief source gets its own
    assert "attribution: DEM_CREDIT" in page and "Terrain Tiles" in page
    for name in ("maplibre-gl.js", "maplibre-gl.css"):
        r = await as_user.get(f"/static/vendor/maplibre-gl/{name}?v={BUILD_ID}")
        assert r.status_code == 200 and r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert "MapLibre GL JS" in (ROOT / "app/static/vendor/maplibre-gl/maplibre-gl.js").read_text(encoding="utf-8")[:200]
    assert (ROOT / "app/static/vendor/licenses/maplibre-gl-4.7.1.txt").exists()
    bike = (ROOT / "app/templates/simulator_route_bike.html").read_text(encoding="utf-8")
    assert "unpkg.com" not in bike and "/static/vendor/maplibre-gl/maplibre-gl.js?v={{ request.app.state.build_id }}" in bike


# ── security headers, and signed-in pages never stored ──────────────────────

async def _member(db_session, email="me@x.fr", password="pw-12345678") -> User:
    from app.services.auth import hash_password

    user = User(email=email, password_hash=hash_password(password), email_verified=True)
    db_session.add(user)
    await db_session.flush()
    return user


async def _login(c: AsyncClient, email: str, password: str = "pw-12345678"):
    return await c.post("/auth/login", data={"email": email, "password": password})


@pytest.fixture
def no_commit(db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)


async def test_every_answer_carries_the_security_headers(client: AsyncClient, monkeypatch):
    """Base code: no HSTS, nosniff, frame, referrer or permissions header, no content security policy."""
    from app.config import settings
    from app.middleware.security import MAP_HOSTS

    r = await client.get("/auth/login")
    assert r.status_code == 200
    assert r.headers["strict-transport-security"] == "max-age=31536000"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert r.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    csp = r.headers["content-security-policy"]
    directives = {d.split(" ")[0]: d for d in csp.split("; ")}
    assert directives["default-src"] == "default-src 'self'"
    assert directives["script-src"] == "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
    assert directives["object-src"] == "object-src 'none'" and directives["base-uri"] == "base-uri 'self'"
    assert directives["form-action"] == "form-action 'self' https://www.strava.com"
    assert directives["frame-ancestors"] == "frame-ancestors 'none'"
    assert directives["worker-src"] == "worker-src 'self' blob:"
    assert directives["font-src"] == "font-src 'self'"
    assert all(host in directives["connect-src"] for host in MAP_HOSTS) and "unpkg" not in csp
    assert "cache-control" not in r.headers  # a public page
    # the same on a static file, a 404 and the health check, whose own caching stands
    r = await client.get(f"/static/js/app.js?v={BUILD_ID}")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "private, max-age=31536000, immutable"
    r = await client.get("/nothing-here")
    assert r.status_code == 404 and r.headers["content-security-policy"] == csp
    r = await client.get("/health")
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    # a local http:// server must stay reachable once the browser has seen the production site
    monkeypatch.setattr(settings, "DEBUG", True)
    r = await client.get("/auth/login")
    assert "strict-transport-security" not in r.headers and r.headers["x-frame-options"] == "DENY"


async def test_signed_in_pages_are_never_stored(client: AsyncClient, db_session, no_commit):
    """Base code: /sante, /settings… had no Cache-Control (the next person at the browser, the back button after
    logout, a shared cache)."""
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert "cache-control" not in (await c.get("/auth/login")).headers
        assert (await _login(c, user.email)).status_code == 302
        for path in ("/settings", "/sante", "/simulator", "/activities"):
            r = await c.get(path)
            assert r.status_code == 200 and r.headers["cache-control"] == "private, no-store", path
        r = await c.get("/mobile/privacy")  # the phone's pages keep their own headers
        assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
        r = await c.get(f"/static/css/theme.css?v={BUILD_ID}")
        assert r.headers["cache-control"] == "private, max-age=31536000, immutable"


# ── cross-site requests refused (CSRF) ──────────────────────────────────────

REFUSED = [{"Origin": "https://evil.example"}, {"Origin": "null"},
           {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "cross-site"},
           {"Sec-Fetch-Site": "same-site"}]
ALLOWED = [{}, {"Origin": "https://test", "Sec-Fetch-Site": "same-origin"}, {"Origin": "http://localhost:8000"},
           {"Sec-Fetch-Site": "none"}, {"Sec-Fetch-Site": "same-origin"}]


@pytest.mark.parametrize("headers", REFUSED)
async def test_a_form_posted_from_another_site_is_refused(client: AsyncClient, db_session, no_commit, headers):
    """Base code: SameSite=Lax was the only layer; a cross-site POST /settings (delete-account, strava/disconnect,
    setup/credentials…) with the cookie went through."""
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert (await _login(c, user.email)).status_code == 302
        r = await c.post("/settings", data={"weight_kg": "66"}, headers=headers)
        assert r.status_code == 403 and user.weight_kg is None
        r = await c.post("/settings/delete-account", data={"confirmation": "SUPPRIMER"}, headers=headers)
        assert r.status_code == 403
        assert (await c.post("/settings", data={"weight_kg": "66"})).status_code == 200  # the session itself is fine
        assert user.weight_kg == 66


@pytest.mark.parametrize("headers", ALLOWED)
async def test_a_form_from_this_site_or_a_plain_client_goes_through(client: AsyncClient, db_session, no_commit,
                                                                   headers):
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert (await _login(c, user.email)).status_code == 302
        assert (await c.post("/settings", data={"weight_kg": "66"}, headers=headers)).status_code == 200
        assert user.weight_kg == 66


async def test_htmx_partials_still_post(as_user: AsyncClient):
    route_id = await _route(as_user)
    r = await as_user.post(f"/api/simulator/routes/{route_id}/params", data={"hr_cap_climb": 150},
                           headers={"Origin": "http://test", "Sec-Fetch-Site": "same-origin", "HX-Request": "true"})
    assert r.status_code in (200, 204)


async def test_the_webhook_and_the_bearer_api_are_exempt(client: AsyncClient):
    """Strava posts from its servers; the phone sends its token (a browser cannot add that header cross-site)."""
    evil = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    r = await client.post("/webhook/strava", json={"object_type": "activity", "object_id": 1, "aspect_type": "create",
                                                   "owner_id": 1, "subscription_id": 1, "event_time": 1}, headers=evil)
    assert r.status_code == 200
    r = await client.post("/api/mobile/exchange", json={"code": "c" * 43, "verifier": "v" * 43}, headers=evil)
    assert r.status_code == 401  # looked at, not refused for its origin
    bearer = {**evil, "Authorization": "Bearer " + "t" * 64}
    assert (await client.post("/api/mobile/daily", json={"days": []}, headers=bearer)).status_code == 401
    assert (await client.delete("/api/mobile/device", headers=bearer)).status_code == 401
    assert (await client.post("/api/mobile/daily", json={"days": []}, headers=evil)).status_code == 403


# ── sessions that end: logout revokes, a lifetime, sessions without an id signed out ──

def _cookie(user_id: int, **fields) -> str:
    from itsdangerous import TimestampSigner

    from app.config import settings

    value = base64.b64encode(json.dumps({"user_id": user_id, **fields}).encode())
    return TimestampSigner(settings.SECRET_KEY).sign(value).decode()


async def test_logout_ends_the_session_for_good(client: AsyncClient, db_session, no_commit):
    """Base code: logout cleared the cookie and nothing else; the cookie as it was before (copied, kept by a
    shared browser or an extension) still opened the account, for 30 more days at each use."""
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert (await _login(c, user.email)).status_code == 302
        before = c.cookies.get("paceforge_session")
        assert (await c.get("/settings")).status_code == 200
        r = await c.post("/auth/logout")
        # the POST signs out, then sends the browser through GET /auth/logout: the navigation the phone app keys
        # its own sign-out on (Android reports no POST to it); a no-op on the dead session, then home
        assert r.status_code == 303 and r.headers["location"] == "/auth/logout"
        assert (await c.get("/settings")).headers["location"] == "/auth/login"  # dead after the first hop
        r = await c.get("/auth/logout", headers={"Sec-Fetch-Site": "same-origin"})
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert (await c.get("/settings")).headers["location"] == "/auth/login"
    assert user.session_version == 0  # the shared store took the id: no need to sign the account out everywhere
    async with AsyncClient(transport=client._transport, base_url="https://test",
                           cookies={"paceforge_session": before}) as old:
        assert (await old.get("/settings")).headers["location"] == "/auth/login"
        assert (await old.get("/")).status_code == 200  # the public home: nobody is signed in


async def test_logout_without_redis_signs_the_account_out_everywhere(client: AsyncClient, db_session, no_commit,
                                                                     monkeypatch):
    """A revocation only this worker knows would not hold on the others: the account's session version is bumped
    instead, durable, at the price of its other sessions."""
    from app.config import settings
    from app.services import sessions

    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens there
    monkeypatch.setattr(sessions, "_store", sessions.Revocations())
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as here, \
            AsyncClient(transport=client._transport, base_url="https://test") as other:
        for c in (here, other):
            assert (await _login(c, user.email)).status_code == 302
        assert (await here.post("/auth/logout")).status_code == 303
        assert user.session_version == 1
        assert (await here.get("/settings")).headers["location"] == "/auth/login"
        assert (await other.get("/settings")).headers["location"] == "/auth/login"  # signed out too
        assert (await _login(other, user.email)).status_code == 302
        assert (await other.get("/settings")).status_code == 200


async def test_a_dead_session_is_dropped_on_public_pages_too(client: AsyncClient, db_session, no_commit):
    """get_optional_user: a session without an id (made before this change) is cleared, so the browser's public
    pages are not marked no-store for a session that no longer counts."""
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test",
                           cookies={"paceforge_session": _cookie(user.id)}) as c:
        r = await c.get("/")
        assert r.status_code == 200 and "cache-control" not in r.headers
        assert "paceforge_session=null" in r.headers.get("set-cookie", "")  # the cookie is cleared


async def test_an_old_logout_link_acts_only_on_the_browsers_own_navigation(client: AsyncClient, db_session, no_commit):
    """GET /auth/logout stays for old links and bookmarks: a link on another site (logout CSRF) does nothing."""
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert (await _login(c, user.email)).status_code == 302
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {}):
            r = await c.get("/auth/logout", headers=headers)
            assert r.status_code == 302 and r.headers["location"] == "/"
            assert (await c.get("/settings")).status_code == 200, headers  # still signed in
        r = await c.get("/auth/logout", headers={"Sec-Fetch-Site": "same-origin"})
        assert r.status_code == 302 and (await c.get("/settings")).headers["location"] == "/auth/login"
        assert (await _login(c, user.email)).status_code == 302
        await c.get("/auth/logout", headers={"Sec-Fetch-Site": "none"})  # a typed address
        assert (await c.get("/settings")).headers["location"] == "/auth/login"


async def test_a_session_ends_after_its_lifetime_and_one_without_an_id_is_signed_out(client: AsyncClient, db_session,
                                                                                    no_commit):
    """Base code: a 30-day cookie renewed at every answer, so a session used now and then never ended. A session
    made before this change has no id logout could revoke: it is signed out once."""
    user = await _member(db_session)
    now = int(time.time())
    cases = (({"sid": "s" * 22, "iat": now - 89 * 86400}, 200), ({"sid": "o" * 22, "iat": now - 91 * 86400}, 303),
             ({}, 303), ({"sid": "x" * 22}, 303), ({"sid": "f" * 22, "iat": now + 3600}, 303))
    for fields, expected in cases:
        async with AsyncClient(transport=client._transport, base_url="https://test",
                               cookies={"paceforge_session": _cookie(user.id, **fields)}) as c:
            assert (await c.get("/settings")).status_code == expected, fields


async def test_the_logout_buttons_post(client: AsyncClient, db_session, no_commit):
    user = await _member(db_session)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert (await _login(c, user.email)).status_code == 302
        for path in ("/sante", "/settings"):
            page = (await c.get(path)).text
            assert 'href="/auth/logout"' not in page, path
            assert '<form method="post" action="/auth/logout" class="pf-logout-form"><button type="submit"' in page, path
        page = (await c.get("/sante")).text
        assert 'class="pf-ibtn pf-logout" title="Se déconnecter" aria-label="Se déconnecter"' in page
        page = (await c.get("/mobile/connect?challenge=" + "a" * 64 + "&platform=ios")).text
        assert '<form method="post" action="/auth/logout"><p><button type="submit" class="link">Changer de compte</button>' in page


async def test_revocations_hold_in_this_process_when_redis_is_down(monkeypatch):
    from app.config import settings
    from app.services import sessions

    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens there
    store = sessions.Revocations()
    assert await store.revoke("gone", 60) is False  # this process only
    assert await store.is_revoked("gone") and not await store.is_revoked("other")
    assert store.down_until > time.monotonic() and store.redis is None  # Redis left alone for a minute

    class Dropped:  # a client Redis stopped answering through: closed when dropped
        closed = False

        async def revoke(self, sid, ttl):
            raise ConnectionError

        async def aclose(self):
            self.closed = True
    store.down_until, store.redis = 0.0, Dropped()
    dropped = store.redis
    assert await store.revoke("x", 60) is False and dropped.closed and store.redis is None
    assert 89 * 86400 - 2 <= sessions.remaining_s(time.time() - 86400) <= 89 * 86400
    assert sessions.remaining_s(None) == 90 * 86400 and sessions.alive(time.time() - 10) and not sessions.alive("x")


# ── quotas on the costly or sensitive requests ──────────────────────────────

def _gpx(n: int = 60) -> bytes:
    points = "".join(f'<trkpt lat="{45 + i * 0.0005:.6f}" lon="{6 + i * 0.0005:.6f}"><ele>{1000 + i}</ele></trkpt>'
                     for i in range(n))
    return (f'<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">'
            f'<trk><name>T</name><trkseg>{points}</trkseg></trk></gpx>').encode()


async def test_gpx_imports_and_exports_have_a_quota_per_account(as_user: AsyncClient, monkeypatch):
    from app.services import auth as auth_service

    monkeypatch.setitem(auth_service.QUOTAS, "gpx", (3600, {"user": 2}))
    monkeypatch.setitem(auth_service.QUOTAS, "export", (3600, {"user": 2}))
    for _ in range(2):
        r = await as_user.post("/partials/simulator/gpx-upload", files={"gpx_file": ("t.gpx", _gpx(), "application/gpx+xml")})
        assert r.status_code == 204 and r.headers["hx-redirect"].startswith("/simulator/routes/")
    r = await as_user.post("/partials/simulator/gpx-upload", files={"gpx_file": ("t.gpx", _gpx(), "application/gpx+xml")})
    assert r.status_code == 429 and "Trop d’imports" in r.text and r.headers["retry-after"] == "3600"
    route_id = int(r.headers.get("hx-redirect", "/x/0").rsplit("/", 1)[1]) or await _route(as_user)
    r = await as_user.post(f"/api/simulator/routes/{route_id}/reimport", files={"gpx_file": ("t.gpx", _gpx(), "application/gpx+xml")})
    assert r.status_code == 429  # the same quota, whichever way a GPX comes in
    assert (await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=csv")).status_code == 200
    assert (await as_user.get(f"/api/simulator/routes/{route_id}/gpx")).status_code == 200
    r = await as_user.get(f"/api/simulator/routes/{route_id}/offline")
    assert r.status_code == 429 and r.json()["error"].startswith("Trop d’exports")
    assert (await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=gpx")).status_code == 429


async def test_the_weather_proxy_has_a_quota_per_account_and_address(as_user: AsyncClient, monkeypatch):
    from app.services import auth as auth_service
    from app.services import weather

    async def forecast(*a, **k):
        return {"source": "prevision", "heat_factor": 1.0, "hourly": None, "date": "2026-10-02"}
    monkeypatch.setattr(weather, "cached_forecast", forecast)
    monkeypatch.setitem(auth_service.QUOTAS, "weather", (3600, {"user": 4, "ip": 2}))
    form = {"lat": "45.0", "lon": "6.0", "date": "2026-10-02"}
    for _ in range(2):
        assert (await as_user.post("/api/simulator/weather", data=form)).status_code == 200
    r = await as_user.post("/api/simulator/weather", data=form)  # the address's quota first (the third request)
    assert r.status_code == 429 and r.json() == {"error": "trop de demandes"}
    other = {"X-Forwarded-For": "203.0.113.8"}  # another address: the account's quota (the fifth request)
    assert (await as_user.post("/api/simulator/weather", data=form, headers=other)).status_code == 200
    assert (await as_user.post("/api/simulator/weather", data=form, headers=other)).status_code == 429


async def test_pairing_exchanges_and_webhook_events_have_a_quota_per_address(client: AsyncClient, monkeypatch):
    from app.services import auth as auth_service

    monkeypatch.setitem(auth_service.QUOTAS, "exchange", (3600, {"ip": 2}))
    monkeypatch.setitem(auth_service.QUOTAS, "webhook", (600, {"ip": 2}))
    body = {"code": "c" * 43, "verifier": "v" * 43}
    for _ in range(2):
        assert (await client.post("/api/mobile/exchange", json=body)).status_code == 401
    r = await client.post("/api/mobile/exchange", json=body)
    assert r.status_code == 429 and r.headers["retry-after"] == "3600"
    assert (await client.post("/api/mobile/exchange", json=body, headers={"X-Forwarded-For": "203.0.113.8"})).status_code == 401
    event = {"object_type": "activity", "object_id": 1, "aspect_type": "create", "owner_id": 1, "subscription_id": 1,
             "event_time": 1}
    for i in range(2):
        assert (await client.post("/webhook/strava", json={**event, "object_id": i})).status_code == 200
    assert (await client.post("/webhook/strava", json={**event, "object_id": 9})).status_code == 429


# ── the emails' tokens stored hashed ────────────────────────────────────────

async def test_email_tokens_are_stored_hashed(client: AsyncClient, db_session, no_commit, monkeypatch):
    """Base code: the verification and reset tokens sat in clear in the users table (a read of it gave a working
    link into any account). The emails are unchanged; a stored value is no link."""
    from sqlalchemy import select

    from app.services.auth import hash_token

    sent = []
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *a: sent.append(a) or True)
    monkeypatch.setattr("app.routers.auth.send_password_reset_email", lambda *a: sent.append(a))
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert (await c.post("/auth/register", data={"email": "new@x.fr", "password": "pw-12345678"})).status_code == 302
        user = (await db_session.execute(select(User).where(User.email == "new@x.fr"))).scalar_one()
        token = sent[0][1]
        assert user.email_verify_token == hash_token(token) and len(user.email_verify_token) == 64
        assert "Lien invalide" in (await c.get("/auth/verify-email", params={"token": user.email_verify_token})).text
        assert "Email vérifié" in (await c.get("/auth/verify-email", params={"token": token})).text
        assert user.email_verified and user.email_verify_token is None
        assert (await c.post("/auth/forgot-password", data={"email": "new@x.fr"})).status_code == 200
        reset = sent[1][1]
        assert user.password_reset_token == hash_token(reset) and reset != user.password_reset_token
        r = await c.post("/auth/reset-password", data={"token": user.password_reset_token, "password": "pw-87654321"})
        assert "Lien expiré" in r.text
        r = await c.post("/auth/reset-password", data={"token": reset, "password": "pw-87654321"})
        assert r.status_code == 302 and user.password_reset_token is None


# ── the webhook: one reply for every event, an event queued once, a default token warned about ──

async def test_the_webhook_answers_alike_and_queues_an_event_once(client: AsyncClient, db_session, test_user, monkeypatch):
    """Base code: « ok » for a known (athlete, subscription) pair, « ignored » otherwise (an oracle), and Strava's
    retries or a replay queued the same event again (a Strava read on the athlete's token each time)."""
    from app.tasks import webhook_sync

    calls = []
    monkeypatch.setattr(webhook_sync.sync_activity_from_webhook, "delay", lambda *a, **k: calls.append(a))
    test_user.strava_client_id, test_user.strava_webhook_subscription_id = "123", 55  # the athlete's own app
    await db_session.flush()
    known = {"object_type": "activity", "object_id": 7, "aspect_type": "create", "owner_id": test_user.strava_athlete_id,
             "subscription_id": 55, "event_time": 1_760_000_000, "updates": {}}
    bodies = (known, {**known, "owner_id": 999}, {**known, "subscription_id": 56}, {"bad": 1})
    answers = [await client.post("/webhook/strava", json=body) for body in bodies]
    assert all(r.status_code == 200 and r.json() == {"status": "ok"} for r in answers)
    assert calls == [(7, test_user.strava_athlete_id)]
    r = await client.post("/webhook/strava", json=known)  # the same event again: acknowledged, not queued twice
    assert r.status_code == 200 and r.json() == {"status": "ok"} and len(calls) == 1
    assert (await client.post("/webhook/strava", json={**known, "object_id": 8})).status_code == 200
    assert (await client.post("/webhook/strava", json={**known, "aspect_type": "update"})).status_code == 200
    assert calls == [(7, test_user.strava_athlete_id), (8, test_user.strava_athlete_id),
                     (7, test_user.strava_athlete_id, "update")]
    # not queued (a broker hiccup): the event is forgotten, so Strava's retry, or the next copy, goes through
    calls.clear()

    def broken(*a, **k):
        raise ConnectionError("broker down")
    monkeypatch.setattr(webhook_sync.sync_activity_from_webhook, "delay", broken)
    assert (await client.post("/webhook/strava", json={**known, "object_id": 9})).status_code == 200
    monkeypatch.setattr(webhook_sync.sync_activity_from_webhook, "delay", lambda *a, **k: calls.append(a))
    assert (await client.post("/webhook/strava", json={**known, "object_id": 9})).status_code == 200
    assert calls == [(9, test_user.strava_athlete_id)]


async def test_a_counter_client_redis_dropped_is_closed(monkeypatch):
    from app.services import auth as auth_service

    class Dropped:
        class r:  # noqa: N801 — the client's connection, as _RedisCounter keeps it
            closed = False

            @classmethod
            async def aclose(cls):
                cls.closed = True

        async def incr(self, keys, window):
            raise ConnectionError
    counter = auth_service.FallbackCounter()
    counter.shared = Dropped()
    assert await counter.incr(["k"], 60) == [1]  # this process counted
    assert Dropped.r.closed and counter.shared is None and counter.down_until > time.monotonic()


def test_the_default_webhook_token_is_warned_about_outside_debug(monkeypatch, caplog):
    import logging

    from app.config import settings
    from app.main import check_secrets

    monkeypatch.setattr(settings, "SECRET_KEY", "a-long-random-secret-for-this-test-0123456789")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", "")
    monkeypatch.setattr(settings, "DEBUG", False)
    caplog.set_level(logging.WARNING)
    check_secrets()
    assert "STRAVA_WEBHOOK_VERIFY_TOKEN is the default" in caplog.text
    caplog.clear()
    monkeypatch.setattr(settings, "STRAVA_WEBHOOK_VERIFY_TOKEN", "something-random-and-long")
    check_secrets()
    assert "STRAVA_WEBHOOK_VERIFY_TOKEN" not in caplog.text


# ── the API schema off outside DEBUG, the fonts versioned, immutable and preloaded ──

async def test_the_api_schema_and_docs_are_for_development_only(client: AsyncClient, monkeypatch):
    """Base code: /openapi.json listed every route (84 paths) to anyone, in production."""
    from httpx import ASGITransport

    from app.config import settings
    from app.main import create_app

    assert (await client.get("/openapi.json")).status_code == 404
    assert (await client.get("/docs")).status_code == 404
    monkeypatch.setattr(settings, "DEBUG", True)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as dev:
        assert (await dev.get("/openapi.json")).status_code == 200
        assert (await dev.get("/docs")).status_code == 200


async def test_the_fonts_are_versioned_immutable_and_preloaded(client: AsyncClient):
    """Base code: the woff2 files were served no-cache, unversioned and not preloaded (ux audit)."""
    page = (await client.get("/auth/login")).text
    for family in ("sora", "figtree"):
        url = f"/static/fonts/{family}-latin-wght-normal.woff2?v={BUILD_ID}"
        assert f'<link rel="preload" as="font" type="font/woff2" crossorigin href="{url}">' in page
        assert f'src: url("{url}") format("woff2")' in page  # the same URL: the preload is the one used
        r = await client.get(url)
        assert r.status_code == 200 and r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert page.count("@font-face") == 4
    assert "@font-face {" not in (ROOT / "app/static/css/theme.css").read_text(encoding="utf-8")  # declared once
    print_page = (ROOT / "app/templates/simulator_print.html").read_text(encoding="utf-8")
    assert '{% include "partials/_fonts.html" %}' in print_page
