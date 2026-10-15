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
