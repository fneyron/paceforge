"""End-to-end through the HTTP layer: save a route with typed checkpoints, then
render every plan surface (passage times + scenarios, pacing guide, nutrition,
exports, reference finisher, debrief)."""

import json
import re

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


async def _create_route(client: AsyncClient, cps: list[dict] = CPS) -> int:
    course = _course()
    r = await client.post("/api/simulator/routes", data={
        "course_json": course.model_dump_json(), "checkpoints_json": json.dumps(cps),
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
    assert "Ravitaillement" in html and "Pilotage" not in html and "exportPace(" in html
    assert '"kind": "full"' in html and '"drop_bag": true' in html  # checkpoint metadata round-trips to the page

    course = _course()
    r = await as_user.post("/partials/simulator/passage-times", data={
        "course_json": course.model_dump_json(), "checkpoints_json": json.dumps(CPS),
        "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0, "route_id": route_id, "stop_minutes": 3,
    })
    assert r.status_code == 200, r.text
    t = r.text
    assert "Sécurité" in t  # one table: scenario columns live in the passage table
    assert "Sécurité" in t and "Optimiste" in t and "bascule" not in t  # scenario columns, no switch marker in the table
    assert "barrière 10:30</span>" in t  # cutoff shown read-only, in the opened row
    assert "<span>Ravito</span>" in t and "<span>assistance</span>" in t and "Drop bag ici" in t  # kind, crew and drop bag, in words
    # v4: day separator, no "+1j" suffix, no start row, switch row flagged, autonomy legs in the plan data
    assert 'data-day="1"' in t and "+1j" not in t and ">Départ<" not in t and "data-switch" not in t and '"autonomy"' in t

    # race day, passage after midnight at Village (km 20): the day separator still precedes the anchor row,
    # the header carries the delta, the cutoff margin is the one against the real clock
    r = await as_user.post("/partials/simulator/passage-times", data={
        "course_json": course.model_dump_json(), "checkpoints_json": json.dumps(CPS),
        "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0, "route_id": route_id, "stop_minutes": 3,
        "anchor_km": 20.0, "anchor_clock": "00:58",
    })
    assert r.status_code == 200, r.text
    t = r.text
    assert "Recalée" in t and "Sécurité" not in t
    assert t.index('data-day="1"') < t.index(' data-anchor')  # separator before the anchor row
    plan = json.loads(t.split('id="plan-data">')[1].split("</script>")[0])
    village = next(p for p in plan["points"] if p["name"] == "Village")
    assert village["clock_s"] == 86400 + 58 * 60
    assert village["cutoff_margin_s"] == 16 * 3600 - (3 * 3600 + 58 * 60)  # 13:00 next day − real elapsed


@pytest.mark.asyncio
async def test_pacing_guide_and_params(as_user: AsyncClient, db_session: AsyncSession):
    route_id = await _create_route(as_user)
    r = await as_user.get(f"/partials/simulator/pacing/{route_id}")
    assert r.status_code == 200 and "Raide ≥" in r.text and "Escaliers" in r.text
    r = await as_user.post(f"/api/simulator/routes/{route_id}/params", data={
        "hr_cap_climb": 140, "hr_cap_flat": 136, "hr_release_descent": 128, "walk_grade": 20,
        "scenario_fast_pct": 4, "scenario_safe_pct": 12, "switch_km": 10.0,
    })
    assert r.status_code == 200 and re.search(r"sous 1[23]\d battements", r.text)  # the climb cap (140 at the start) comes down with the race
    r = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=csv")
    assert r.status_code == 200 and "Village" in r.text
    # Réglages du plan › « Plafond cardio en montée » alone: 204, the rest of params_json kept, flat / descent derive again
    r = await as_user.post(f"/api/simulator/routes/{route_id}/params", data={"hr_cap_climb": 150})
    assert r.status_code == 204
    from app.models.route import Route

    route = await db_session.get(Route, route_id)
    await db_session.refresh(route)
    p = route.params_json
    assert p["hr_cap_climb"] == 150 and p["walk_grade"] == 20 and p["scenario_fast_pct"] == 4 and p["switch_km"] == 10.0
    assert "hr_cap_flat" not in p and "hr_release_descent" not in p


@pytest.mark.asyncio
async def test_nutrition_card_with_packing_and_caffeine(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _create_route(as_user)
    # « + Marque » › « Un produit à toi »: added to the pantry and picked at once
    r = await as_user.post(f"/partials/simulator/nutrition/{route_id}/products", data={"name": "Gel caf", "kind": "gel", "carbs_g": 25, "sodium_mg": 50, "caffeine_mg": 50})
    assert r.status_code == 200, r.text
    t = r.text
    assert 'aria-pressed="true" title="Gel caf"' in t
    assert re.search(r"<b>1 Gel caf</b> à \d\d:\d\d", t)  # « Ta règle »: the caffeine clock line
    assert "Sac au départ" in t and "Drop bag · Col" in t
    # an older client posting the previous form (use_ / qty_) still works; a typed quantity is « à la main »
    pid = int(re.search(r"/products/(\d+)/delete", t).group(1))
    r = await as_user.post(f"/partials/simulator/nutrition/{route_id}/plan", data={
        "carbs_g_per_h": 75, "fluid_ml_per_h": 500, "sodium_mg_per_h": 400, "flask_capacity_ml": 1000,
        "use_-1": 1, "qty_-1": "2.5", f"use_{pid}": 1, "caffeine_enabled": 1, "caffeine_from_h": 3, "caffeine_every_h": 2.5, "caffeine_dose_mg": 50,
    })
    assert r.status_code == 200, r.text
    assert "quantités à la main" in r.text and "<b>2,5</b>" in r.text and "à la main</span>" in r.text


@pytest.mark.asyncio
async def test_pace_exports(as_user: AsyncClient):
    route_id = await _create_route(as_user)
    gpx = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=gpx")
    assert gpx.status_code == 200 and gpx.text.count("<wpt") >= 4 and "<time>2026-10-02T21:00:00Z</time>" in gpx.text
    assert "ARR " in gpx.text and "CP2 " in gpx.text
    tcx = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=tcx")
    assert tcx.status_code == 200 and "<CoursePoint>" in tcx.text and tcx.headers["content-disposition"].endswith('plan.tcx"')


@pytest.mark.asyncio
async def test_reference_paste_and_debrief(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _create_route(as_user)
    r = await as_user.get(f"/api/simulator/routes/{route_id}/reference")
    # the panel's title is the page's: the card does not repeat it
    assert r.status_code == 200 and 'name="source"' in r.text and "Finisher de référence" not in r.text
    r = await as_user.post(f"/api/simulator/routes/{route_id}/reference", data={
        "label": "Mamba", "source": "Eau 1 km 6 0:40:00\nCol km 10 1:30:00\nVillage km 20 2:50:00\nArrivée km 30 4:10:00", "total_time": "4:10:00",
    })
    assert r.status_code == 200, r.text
    assert "Mamba" in r.text and "Où ta référence va plus vite" not in r.text  # the key gaps are marked in the table, not repeated beside it
    assert r.text.count("4h10") == 1  # his finish, once (the Arrivée row), not again beside his name

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
    assert "Tronçon par tronçon" in r.text and "Raison probable" in r.text
    assert "arrêts" in r.text  # the 15-min stop at the Col is called out
    assert "Simulé vs réalisé" not in r.text and "Prédit" not in r.text  # one comparison, against the plan
    assert "Où tu as perdu" not in r.text  # the biggest gaps are marked in the table, not listed again above it
    # the personal fatigue tilt is stored with the curve it was measured against
    from app.models.route import Route

    rj = (await db_session.get(Route, route_id)).result_json
    assert 0.05 <= rj["fatigue_tilt"] <= 0.40 and rj["fatigue_model"] == "hours"
    assert rj["fatigue_tilt_cps"] >= 1  # fitted on every matched checkpoint, not the first only


@pytest.mark.asyncio
async def test_result_matches_checkpoints_by_km_not_name(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """A loop course repeats a checkpoint name (Transjeju 2026: Healing Forest at km 7 and 136):
    the tilt and the comparison must pair each passage with its own km."""
    from app.models.route import Route

    # 9:00/km for 10 km, then 11:00/km: km 6 at 0h54, km 20 at 3h20
    splits = [{"distance": 1000, "moving_time": 540 if k < 10 else 660, "elapsed_time": 540 if k < 10 else 660} for k in range(30)]
    act = Activity(
        strava_activity_id=700, user_id=test_user.id, sport_type="TrailRun", name="Boucle",
        start_date=__import__("datetime").datetime(2026, 10, 2, 21, 0, tzinfo=__import__("datetime").timezone.utc),
        distance=30000.0, moving_time=18600, elapsed_time=18600, total_elevation_gain=800.0,
        raw_data={"id": 700}, splits_metric=splits,
    )
    db_session.add(act)
    await db_session.flush()
    tilts, pages = [], []
    for last in ("Eau 1", "Eau 2"):
        route_id = await _create_route(as_user, [CPS[0], CPS[1], {**CPS[2], "name": last}])
        r = await as_user.post(f"/api/simulator/routes/{route_id}/result", data={"activity_id": act.id})
        assert r.status_code == 200, r.text
        tilts.append((await db_session.get(Route, route_id)).result_json["fatigue_tilt"])
        pages.append(r.text)
    assert tilts[0] == tilts[1] and 0.05 < tilts[0] < 0.40  # not the clamp a km 6 vs km 20 mix-up gave
    # each "Eau 1" passage is stored at its own km…
    actual = {a["km"]: a["time_s"] for a in (await db_session.get(Route, route_id)).result_json["actual"]}
    assert actual[6.0] == 54 * 60 and actual[20.0] == 200 * 60
    # …and each "Eau 1" row of the débrief shows its own leg (0→6 km: 0h54, 10→20 km: 1h50)
    real = re.findall(r"<i>réel</i> (\d+h\d\d)<", pages[0])
    assert real[0] == "0h54" and real[2] == "1h50"


@pytest.mark.asyncio
async def test_debrief_shows_one_gap_against_the_plans_finish(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """One écart: real elapsed time (stops included) against the plan's finish (the objective,
    stops by point kind included) — the sum of the legs' gaps, whatever the stored moving total."""
    course = _course()
    r = await as_user.post("/api/simulator/routes", data={  # no stop_minutes: stops by kind (2′ + 5′ + 5′)
        "course_json": course.model_dump_json(), "checkpoints_json": json.dumps(CPS), "name": "Jeju sans arrêt réglé",
        "target_time_s": 5 * 3600, "race_date": "2026-10-02", "start_hour": 21, "start_minute": 0, "sport_type": "trail",
    })
    route_id = r.json()["id"]
    # 30 km at 10:00/km moving (5h00) + 15 min of stops = 5h15 elapsed
    splits = [{"distance": 1000, "moving_time": 600, "elapsed_time": 600 + (900 if k == 14 else 0)} for k in range(30)]
    act = Activity(strava_activity_id=801, user_id=test_user.id, sport_type="TrailRun", name="Jeju 2026",
                   start_date=__import__("datetime").datetime(2026, 10, 2, 21, 0, tzinfo=__import__("datetime").timezone.utc),
                   distance=30000.0, moving_time=18000, elapsed_time=18900, total_elevation_gain=800.0, raw_data={}, splits_metric=splits)
    db_session.add(act)
    await db_session.flush()
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result", data={"activity_id": act.id})
    assert r.status_code == 200, r.text
    t = r.text
    assert "Réalisé <b>5h15</b>" in t and "Plan <b>5h00</b>" in t
    assert '<p class="pf-db-num is-bad">+15<small' in t  # +15 min, not +27 (stops forgotten) nor −3 (moving vs estimate)
    assert "(12 prévues)" in t  # the plan's stops: eau 2′ + ravito 5′ + ravito 5′
    assert "<span>ven. 2 oct. 2026</span>" in t  # the activity's day in words, as the app writes dates
    from sqlalchemy import select

    from app.models.route import Route, RouteCheckpoint

    assert (await db_session.get(Route, route_id)).result_json["total_elapsed_s"] == 18900

    async def card_and_lead():
        """(real, gap) on « Mes courses » and in the débrief's lead."""
        page = (await as_user.get("/simulator")).text
        card = re.split(rf'id="route-(?:card|next)-{route_id}"', page)[1].split("</a>")[0]
        real, diff = re.search(r"<b>(\d+h\d\d)</b>.*?>([+−][^<]+) vs plan<", card, re.S).groups()
        d = (await as_user.get(f"/api/simulator/routes/{route_id}/result")).text
        hero = re.sub(r"<[^>]+>", "", re.search(r'class="pf-db-num[^"]*">(.*?)</p>', d).group(1))
        return (real, re.sub(r"\s+", "", diff)), (re.search(r"Réalisé <b>(\d+h\d\d)</b>", d).group(1), re.sub(r"\s+", "", hero))

    # « Mes courses » shows the débrief's numbers: the same real time, the same gap against the same plan…
    card, lead = await card_and_lead()
    assert card == lead == ("5h15", "+15min")
    # …also with a pinned point and no objective (the plan's finish is then where the pins lead)
    col = (await db_session.execute(select(RouteCheckpoint).where(RouteCheckpoint.route_id == route_id, RouteCheckpoint.name == "Col"))).scalar_one()
    col.target_s = 2 * 3600
    (await db_session.get(Route, route_id)).target_time_s = None
    await db_session.flush()
    card, lead = await card_and_lead()
    assert card == lead and card[1] != "+15min"
    col.target_s = None
    (await db_session.get(Route, route_id)).target_time_s = 5 * 3600
    await db_session.flush()

    # a refused activity comes back inside the card, the picker still there
    other = Activity(strava_activity_id=802, user_id=test_user.id, sport_type="Run", name="Footing", distance=8000.0, moving_time=2400, elapsed_time=2400,
                     start_date=__import__("datetime").datetime(2026, 10, 5, tzinfo=__import__("datetime").timezone.utc), raw_data={})
    db_session.add(other)
    await as_user.post(f"/api/simulator/routes/{route_id}/result/clear")
    await db_session.flush()
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result")  # the picker opens on the closest distance
    assert f'<option value="{act.id}" selected>' in r.text and f'<option value="{other.id}">' in r.text
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result", data={"activity_id": other.id})
    assert 'id="result-compare"' in r.text and "pas le même parcours" in r.text and 'name="activity_id"' in r.text


@pytest.mark.asyncio
async def test_reference_compares_race_times_with_the_plans_passages(as_user: AsyncClient):
    """The reference's times are race times (stops included): the plan in front of them is the plan's
    passage at each point (stops made before it), so the last leg does not swallow every stop."""
    route_id = await _create_route(as_user)  # 3 min at each of the 3 aid stations
    r = await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(CPS), "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0, "route_id": route_id, "stop_minutes": 3,
    })
    plan = json.loads(r.text.split('id="plan-data">')[1].split("</script>")[0])
    village_s = next(p for p in plan["points"] if p["name"] == "Village")["clock_s"] - 21 * 3600
    r = await as_user.post(f"/api/simulator/routes/{route_id}/reference", data={
        "label": "Mamba", "source": "Eau 1 km 6 0:40:00\nCol km 10 1:30:00\nVillage km 20 2:50:00", "total_time": "4:10:00",
    })
    rows = re.findall(r'<b>([^<]+)</b>.*?<i>plan</i> (\d+h\d\d)<', r.text, re.S)
    plan_at = dict(rows)
    m = (village_s + 30) // 60  # to the nearest minute, as the plan shows it
    assert plan_at["Village"] == f"{m // 60}h{m % 60:02d}" and plan_at["Arrivée"] == "5h00"
    # a pasted text with no time comes back as typed, with the reason
    r = await as_user.post(f"/api/simulator/routes/{route_id}/reference/clear")
    r = await as_user.post(f"/api/simulator/routes/{route_id}/reference", data={"source": "Col sans temps", "label": "X"})
    assert "Aucune ligne avec un temps" in r.text and ">Col sans temps</textarea>" in r.text and 'value="X"' in r.text


async def _create_bike_route(client: AsyncClient) -> int:
    course = _course()
    r = await client.post("/api/simulator/routes", data={
        "course_json": course.model_dump_json(), "checkpoints_json": json.dumps(CPS[:2]),
        "name": "Gran fondo test", "race_date": "2026-10-02", "start_hour": 8, "start_minute": 0, "sport_type": "bike",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_bike_plan_page_objective_checkpoints_and_exports(as_user: AsyncClient, cycling_on):
    route_id = await _create_bike_route(as_user)
    page = await as_user.get(f"/simulator/routes/{route_id}")
    assert page.status_code == 200
    html = page.text
    assert ("Plan de passage" in html or "Feuille de route" in html) and "Nutrition" in html and "Débrief" in html and "Exporter" in html
    assert "Calculateur mono-segment" not in html
    # objective → required power in the hero
    r = await as_user.post(f"/api/simulator/routes/{route_id}/bike", data={
        "target_power_watts": 200, "rider_weight_kg": 70, "bike_weight_kg": 8, "cda": 0.32, "crr": 0.005,
        "race_date": "2026-10-02", "start_time": "08:30", "wind_mode": "none", "target_h": 1, "target_m": 20, "stop_minutes": 2,
    })
    assert r.status_code == 200, r.text
    assert "Objectif" in r.text and "il faut tenir" in r.text and "08:30" in r.text
    # checkpoints: add, type, delete
    r = await as_user.post(f"/api/simulator/routes/{route_id}/checkpoints", data={"name": "Sommet", "distance_km": 12.5})
    assert r.status_code == 200 and "Sommet" in r.text
    cp_id = r.text.split("/checkpoints/")[1].split('"')[0]
    r = await as_user.post(f"/api/simulator/routes/{route_id}/checkpoints/{cp_id}", data={"kind": "full", "cutoff_clock": "12:00"})
    assert r.status_code == 200 and 'value="full" selected' in r.text and "barrière 12:00" in r.text
    r = await as_user.post(f"/api/simulator/routes/{route_id}/checkpoints/{cp_id}/delete")
    assert r.status_code == 200 and "barrière 12:00" not in r.text
    # nutrition, exports and print work on the bike plan too
    r = await as_user.get(f"/partials/simulator/nutrition/{route_id}")
    assert r.status_code == 200 and "Ta règle pour toute la course" in r.text
    r = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=tcx")
    assert r.status_code == 200 and "<CoursePoint>" in r.text
    r = await as_user.get(f"/simulator/routes/{route_id}/print")
    assert r.status_code == 200 and "Objectif 1h20" in r.text and "Pilotage" not in r.text


@pytest.mark.asyncio
async def test_trail_export_carries_pacing_points_and_courses_page_has_no_triathlon(as_user: AsyncClient):
    route_id = await _create_route(as_user)
    gpx = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=gpx")
    # one instruction per leg, attached to the point where the leg starts — no extra waypoints
    assert gpx.text.count("<wpt") == 5  # DEP + 3 CPs + ARR
    assert "DEP 21:00 | PLAT" in gpx.text and "| ESCAL marche" in gpx.text and "LIBRE" not in gpx.text
    print_page = await as_user.get(f"/simulator/routes/{route_id}/print")
    # « Consignes »: the rows' words, the cell's pace only (no second pace that disagrees with it)
    assert "Consignes" in print_page.text and "Pilotage" not in print_page.text and "mains sur les cuisses" in print_page.text
    assert "régulier, manger, boire" not in print_page.text and "garde-fous" not in print_page.text
    courses = await as_user.get("/simulator")
    assert courses.status_code == 200 and "tab-tri" not in courses.text and "mono-segment" not in courses.text
