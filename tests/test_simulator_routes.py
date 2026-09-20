"""End-to-end through the HTTP layer: save a route with typed checkpoints, then
render every plan surface (passage times + scenarios, pacing guide, nutrition,
exports, reference finisher, debrief)."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.user import User
from tests.test_race_plan_services import CPS, _course


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


async def _create_route(client: AsyncClient) -> int:
    course = _course()
    r = await client.post("/api/simulator/routes", data={
        "course_json": course.model_dump_json(), "checkpoints_json": json.dumps(CPS),
        "name": "Jeju test", "target_time_s": 5 * 3600, "race_date": "2026-10-02",
        "start_hour": 21, "start_minute": 0, "sport_type": "trail", "stop_minutes": 3,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_route_page_and_passage_times_with_metadata(as_user: AsyncClient):
    route_id = await _create_route(as_user)
    page = await as_user.get(f"/simulator/routes/{route_id}")
    assert page.status_code == 200
    html = page.text
    assert "Pilotage" in html and "exportPace(" in html
    assert '"kind": "full"' in html and '"drop_bag": true' in html  # checkpoint metadata round-trips to the page

    course = _course()
    r = await as_user.post("/partials/simulator/passage-times", data={
        "course_json": course.model_dump_json(), "checkpoints_json": json.dumps(CPS),
        "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0, "route_id": route_id, "stop_minutes": 3,
    })
    assert r.status_code == 200, r.text
    t = r.text
    assert "Autonomie" in t and "sans ravito complet" in t and "→ Col" in t
    assert "Trois scénarios" in t and "Règle de bascule" in t
    assert "barrière 10:30" in t  # cutoff shown under the planned clock
    assert 'value="full" selected' in t and "Drop bag" in t


@pytest.mark.asyncio
async def test_pacing_guide_and_params(as_user: AsyncClient):
    route_id = await _create_route(as_user)
    r = await as_user.get(f"/partials/simulator/pacing/{route_id}")
    assert r.status_code == 200 and "Alertes de raideur" in r.text and "Escaliers" in r.text
    r = await as_user.post(f"/api/simulator/routes/{route_id}/params", data={
        "hr_cap_climb": 140, "hr_cap_flat": 136, "hr_release_descent": 128, "walk_grade": 20,
        "scenario_fast_pct": 4, "scenario_safe_pct": 12, "switch_km": 10.0,
    })
    assert r.status_code == 200 and "≤ 135" in r.text  # early-phase climb cap = 140 − 5
    r = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=csv")
    assert r.status_code == 200 and "Village" in r.text


@pytest.mark.asyncio
async def test_nutrition_card_with_packing_and_caffeine(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _create_route(as_user)
    r = await as_user.post("/api/nutrition/products", data={"name": "Gel caf", "kind": "gel", "carbs_g": 25, "sodium_mg": 50, "caffeine_mg": 50})
    assert r.status_code == 200
    r = await as_user.get(f"/partials/simulator/nutrition/{route_id}")
    assert r.status_code == 200 and "Ce que tu prends" in r.text and "Coche les produits" in r.text
    pid = r.text.split('name="use_')[1].split('"')[0]
    # ticking a product with no quantity → it gets its share of the target (75 g/h / 25 g = 3/h)
    r = await as_user.post(f"/partials/simulator/nutrition/{route_id}/plan", data={
        "carbs_g_per_h": 75, "fluid_ml_per_h": 500, "sodium_mg_per_h": 400, "flask_capacity_ml": 1000,
        f"use_{pid}": 1, "caffeine_enabled": 1, "caffeine_from_h": 1, "caffeine_every_h": 2, "caffeine_dose_mg": 50, "caffeine_boost_dawn": 1,
    })
    assert r.status_code == 200, r.text
    t = r.text
    assert f'name="qty_{pid}"' in t and 'value="3"' in t and "75 g/h" in t
    assert "Par tronçon" in t and "Sac au départ" in t and "Drop bag · Col" in t
    assert "Caféine" in t and "aube" not in t.split("Caféine <span")[0]  # the caffeine card renders its doses
    # a typed quantity is kept as is
    r = await as_user.post(f"/partials/simulator/nutrition/{route_id}/plan", data={
        "carbs_g_per_h": 75, "fluid_ml_per_h": 500, "sodium_mg_per_h": 400, "flask_capacity_ml": 1000, f"use_{pid}": 1, f"qty_{pid}": "2.5",
    })
    assert 'value="2.5"' in r.text


@pytest.mark.asyncio
async def test_pace_exports(as_user: AsyncClient):
    route_id = await _create_route(as_user)
    gpx = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=gpx")
    assert gpx.status_code == 200 and gpx.text.count("<wpt") == 4 and "<time>2026-10-02T21:00:00Z</time>" in gpx.text
    tcx = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=tcx")
    assert tcx.status_code == 200 and "<CoursePoint>" in tcx.text and tcx.headers["content-disposition"].endswith('plan.tcx"')


@pytest.mark.asyncio
async def test_reference_paste_and_debrief(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _create_route(as_user)
    r = await as_user.get(f"/api/simulator/routes/{route_id}/reference")
    assert r.status_code == 200 and "Finisher de référence" in r.text
    r = await as_user.post(f"/api/simulator/routes/{route_id}/reference", data={
        "label": "Mamba", "source": "Eau 1 km 6 0:40:00\nCol km 10 1:30:00\nVillage km 20 2:50:00\nArrivée km 30 4:10:00", "total_time": "4:10:00",
    })
    assert r.status_code == 200, r.text
    assert "Mamba" in r.text and "Où il gagne du temps" in r.text or "Où ton plan est plus rapide" in r.text
    assert "4h10" in r.text

    # debrief: a matched activity with per-km splits (moving + elapsed + HR)
    splits = [{"distance": 1000, "moving_time": 600, "elapsed_time": 600 + (900 if k == 9 else 0), "average_heartrate": 150 if k < 8 else 130} for k in range(30)]
    act = Activity(
        strava_activity_id=555, user_id=test_user.id, sport_type="TrailRun", name="Jeju réel",
        start_date=__import__("datetime").datetime(2026, 10, 2, 21, 0, tzinfo=__import__("datetime").timezone.utc),
        distance=30000.0, moving_time=18000, elapsed_time=18900, total_elevation_gain=800.0,
        raw_data={"id": 555, "workout_type": 1}, splits_metric=splits,
    )
    db_session.add(act)
    await db_session.flush()
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result", data={"activity_id": act.id})
    assert r.status_code == 200, r.text
    assert "Débrief tronçon par tronçon" in r.text and "Raison probable" in r.text
    assert "arrêts" in r.text  # the 15-min stop at the Col is called out
