"""Bike and triathlon planning are hidden for now (settings.CYCLING_ENABLED off):
no sport toggle, no bike/tri route in the list, their pages and endpoints 404,
no FTP in the settings. With the flag on, everything comes back."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_current_user
from app.models.route import Route
from app.models.user import User
from tests.test_race_plan_services import CPS, _course


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


async def _route(db: AsyncSession, user: User, sport: str, name: str) -> Route:
    course = _course()
    route = Route(
        user_id=user.id, name=name, sport_type=sport,
        total_distance_km=course.total_distance_km,
        total_elevation_gain=course.total_elevation_gain,
        total_elevation_loss=course.total_elevation_loss,
        course_json=None if sport == "triathlon" else json.loads(course.model_dump_json()),
        params_json={"swim_distance_m": 1500} if sport == "triathlon" else None,
    )
    db.add(route)
    await db.flush()
    return route


def test_flag_is_off_by_default():
    assert settings.CYCLING_ENABLED is False


@pytest.mark.asyncio
async def test_courses_page_has_no_sport_toggle_and_no_bike_routes(as_user: AsyncClient, db_session, test_user):
    bike = await _route(db_session, test_user, "bike", "Gran fondo caché")
    await _route(db_session, test_user, "triathlon", "Triathlon caché")
    trail = await _route(db_session, test_user, "trail", "Trail visible")

    page = await as_user.get("/simulator")
    assert page.status_code == 200
    html = page.text
    assert "sport-bike" not in html and "bike-gpx-upload" not in html and "setSport" not in html and "Vélo" not in html
    assert 'hx-post="/partials/simulator/gpx-upload"' in html  # the upload goes straight to the run flow
    assert "Trail visible" in html and f"/simulator/routes/{trail.id}" in html
    assert "Gran fondo caché" not in html and f'/simulator/routes/{bike.id}"' not in html
    assert "Triathlon caché" not in html


@pytest.mark.asyncio
async def test_bike_and_tri_routes_and_endpoints_404(as_user: AsyncClient, db_session, test_user):
    bike = await _route(db_session, test_user, "bike", "Gran fondo")
    tri = await _route(db_session, test_user, "triathlon", "Tri")

    for rid in (bike.id, tri.id):
        page = await as_user.get(f"/simulator/routes/{rid}")
        assert page.status_code == 404 and "Page non trouvée" in page.text  # the app's 404 page
        assert (await as_user.get(f"/simulator/routes/{rid}/print")).status_code == 404
        assert (await as_user.get(f"/api/simulator/routes/{rid}/pace-export?format=gpx")).status_code == 404
        assert (await as_user.get(f"/api/simulator/routes/{rid}/gpx")).status_code == 404
        assert (await as_user.get(f"/api/simulator/routes/{rid}")).status_code == 404

    gpx = b'<?xml version="1.0"?><gpx version="1.1"><trk><trkseg></trkseg></trk></gpx>'
    r = await as_user.post("/partials/simulator/bike-gpx-upload", files={"gpx_file": ("a.gpx", gpx, "application/gpx+xml")})
    assert r.status_code == 404
    assert (await as_user.post(f"/api/simulator/routes/{bike.id}/bike", data={"target_power_watts": 200})).status_code == 404
    assert (await as_user.post(f"/api/simulator/routes/{tri.id}/tri", data={})).status_code == 404
    assert (await as_user.post("/api/simulator/triathlon", data={"name": "x"})).status_code == 404
    assert (await as_user.post(f"/api/simulator/routes/{bike.id}/checkpoints", data={"name": "S", "distance_km": 3})).status_code == 404
    assert (await as_user.post(f"/api/simulator/routes/{bike.id}/checkpoints/1", data={"kind": "full"})).status_code == 404
    assert (await as_user.post(f"/api/simulator/routes/{bike.id}/checkpoints/1/delete")).status_code == 404

    # the data is kept: deleting through the API doesn't touch a hidden route
    await as_user.delete(f"/api/simulator/routes/{bike.id}")
    assert await db_session.get(Route, bike.id) is not None


@pytest.mark.asyncio
async def test_landing_page_does_not_sell_the_bike(client: AsyncClient):
    page = await client.get("/")
    assert page.status_code == 200
    assert "CdA" not in page.text and "Vélo" not in page.text and "vélo" not in page.text


@pytest.mark.asyncio
async def test_saving_a_bike_route_stores_a_trail_route(as_user: AsyncClient, db_session):
    r = await as_user.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS[:2]),
        "name": "Sortie", "sport_type": "bike",
    })
    assert r.status_code == 200, r.text
    assert (await db_session.get(Route, r.json()["id"])).sport_type == "trail"


@pytest.mark.asyncio
async def test_settings_have_no_ftp_and_keep_the_saved_one(as_user: AsyncClient, db_session, test_user):
    test_user.ftp_watts = 250
    await db_session.flush()
    page = await as_user.get("/settings")
    assert page.status_code == 200
    assert "FTP" not in page.text and "ftp_watts" not in page.text and "(vélo)" not in page.text
    assert "Sert à ta" not in page.text and "caféine" not in page.text and 'name="weight_kg"' in page.text  # the weight, no helper

    r = await as_user.post("/settings", data={"weight_kg": "68"})
    assert r.status_code == 200
    await db_session.refresh(test_user)
    assert test_user.weight_kg == 68 and test_user.ftp_watts == 250


@pytest.mark.asyncio
async def test_flag_on_brings_everything_back(as_user: AsyncClient, db_session, test_user, cycling_on):
    bike = await _route(db_session, test_user, "bike", "Gran fondo visible")
    page = await as_user.get("/simulator")
    assert "sport-bike" in page.text and "bike-gpx-upload" in page.text and "Gran fondo visible" in page.text
    assert (await as_user.get(f"/simulator/routes/{bike.id}")).status_code == 200
    settings_page = await as_user.get("/settings")
    assert "FTP" in settings_page.text and "Ton poids sert à ta puissance à vélo." in settings_page.text
    # the landing page no longer markets the bike planner, flag or not
    assert (await as_user.get("/landing")).status_code == 200
