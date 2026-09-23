"""Real-data shapes that used to 500 or hang: odd race dates, string caps, race day without an objective,
incomplete nutrition targets, catalogue on a course-less route, the privacy page."""
import json

import pytest
from httpx import AsyncClient

from app.models.user import User
from tests.test_simulator_routes import CPS, _create_route


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    from app.dependencies import get_current_user

    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
async def test_odd_race_date_is_dropped_and_the_list_still_renders(as_user: AsyncClient):
    route_id = await _create_route(as_user)
    for bad in ("2026-10", "02/10/2026", "2026-13-45", ""):
        r = await as_user.post("/api/simulator/routes", data={"route_id": route_id, "checkpoints_json": json.dumps(CPS), "race_date": bad, "name": "Odd"})
        assert r.status_code == 200, r.text
        r = await as_user.get("/simulator")
        assert r.status_code == 200
    r = await as_user.post("/api/simulator/routes", data={"route_id": route_id, "checkpoints_json": json.dumps(CPS), "race_date": "2026-10-02", "name": "Odd"})
    assert r.status_code == 200
    r = await as_user.get("/simulator")
    assert r.status_code == 200 and "2 oct. 2026" in r.text


@pytest.mark.asyncio
async def test_string_hr_caps_do_not_break_the_passage_table(as_user: AsyncClient):
    route_id = await _create_route(as_user)
    r = await as_user.post(f"/api/simulator/routes/{route_id}/params", data={"hr_cap_climb": "150.5", "hr_cap_flat": "140", "hr_release_descent": "", "walk_grade": "18"})
    assert r.status_code in (200, 303)
    r = await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(CPS), "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0, "route_id": route_id,
    })
    assert r.status_code == 200 and "Sécurité" in r.text


@pytest.mark.asyncio
async def test_passage_table_failure_is_a_500_not_a_swapped_error_div(as_user: AsyncClient):
    r = await as_user.post("/partials/simulator/passage-times", data={"course_json": "{not json", "checkpoints_json": "[]", "start_hour": 6, "start_minute": 0})
    assert r.status_code == 500 and r.text == ""


@pytest.mark.asyncio
async def test_print_on_race_day_without_objective(as_user: AsyncClient):
    route_id = await _create_route(as_user)
    r = await as_user.post("/api/simulator/routes", data={"route_id": route_id, "checkpoints_json": json.dumps(CPS), "name": "Sans objectif", "start_hour": 21, "start_minute": 0})
    assert r.status_code == 200
    r = await as_user.post(f"/api/simulator/routes/{route_id}/live", data={"anchor_km": CPS[1]["distance_km"], "anchor_clock": "23:30", "anchor_name": CPS[1]["name"]})
    assert r.status_code in (200, 204), r.text
    r = await as_user.get(f"/simulator/routes/{route_id}/print")
    assert r.status_code == 200 and "Estimé" in r.text


@pytest.mark.asyncio
async def test_nutrition_card_with_incomplete_saved_targets(as_user: AsyncClient, db_session):
    from sqlalchemy import select

    from app.models.route import Route

    route_id = await _create_route(as_user)
    route = (await db_session.execute(select(Route).where(Route.id == route_id))).scalar_one()
    route.nutrition_json = {"targets": {"carbs_g_per_h": 60}, "items": []}
    await db_session.flush()
    r = await as_user.get(f"/partials/simulator/nutrition/{route_id}")
    assert r.status_code == 200 and "Ce que tu prends" in r.text
    assert 'name="fluid_ml_per_h" type="number" min="0" step="any"' in r.text  # the form must stay submittable


@pytest.mark.asyncio
async def test_catalogue_on_a_route_without_course_is_404(as_user: AsyncClient, db_session):
    from sqlalchemy import select

    from app.models.route import Route

    route_id = await _create_route(as_user)
    route = (await db_session.execute(select(Route).where(Route.id == route_id))).scalar_one()
    route.course_json = None
    await db_session.flush()
    r = await as_user.post(f"/partials/simulator/nutrition/{route_id}/catalog/maurten-gel-100")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_privacy_page(as_user: AsyncClient):
    r = await as_user.get("/privacy")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_debrief_refuses_an_activity_of_another_distance(as_user: AsyncClient, db_session, test_user):
    from datetime import datetime, timezone

    from app.models.activity import Activity

    route_id = await _create_route(as_user)
    act = Activity(user_id=test_user.id, strava_activity_id=987654322, name="Footing", sport_type="Run", distance=25000, moving_time=7200, elapsed_time=7300,
                   start_date=datetime(2026, 10, 3, tzinfo=timezone.utc), total_elevation_gain=100, raw_data={})
    db_session.add(act)
    await db_session.flush()
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result", data={"activity_id": act.id})
    assert r.status_code == 200 and "pas le même parcours" in r.text
