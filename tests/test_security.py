"""Browser-side hardening: assets served from here, security headers, cross-site requests refused, sessions that
end, limits that hold without Redis, tokens stored hashed, a webhook that tells nothing."""
import json
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
