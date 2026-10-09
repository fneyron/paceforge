"""End-to-end through the HTTP layer: save a route with typed checkpoints, then
render every plan surface (passage times + scenarios, pacing guide, exports,
debrief)."""

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
    assert "Nutrition" not in html and "Pilotage" not in html and "exportPace(" in html
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
async def test_pace_exports(as_user: AsyncClient):
    route_id = await _create_route(as_user)
    gpx = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=gpx")
    assert gpx.status_code == 200 and gpx.text.count("<wpt") >= 4 and "<time>2026-10-02T21:00:00Z</time>" in gpx.text
    assert "ARR " in gpx.text and "CP2 " in gpx.text
    tcx = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=tcx")
    assert tcx.status_code == 200 and "<CoursePoint>" in tcx.text and tcx.headers["content-disposition"].endswith('plan.tcx"')


@pytest.mark.asyncio
async def test_no_reference_finisher_and_the_debrief(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _create_route(as_user)
    # the « Finisher de référence » tool is gone (owner, 2026-10-09: « je ne pense pas qu'on l'ait pour toutes les
    # courses »): its routes no longer exist
    assert (await as_user.get(f"/api/simulator/routes/{route_id}/reference")).status_code == 404
    assert (await as_user.post(f"/api/simulator/routes/{route_id}/reference", data={"source": "x"})).status_code in (404, 405)

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
    # a reason shared by consecutive legs is written once (the next rows say it to screen readers only)
    whys = re.findall(r'<span class="pf-cmp-why( is-same)?" role="cell">(.*?)</span>\s*</div>', t, re.S)
    shown = [re.sub(r"<[^>]+>", "", w) for same, w in whys]
    assert len(whys) == 4 and any(same for same, _ in whys)
    for k, (same, w) in enumerate(whys):
        assert bool(same) == (k > 0 and shown[k] == shown[k - 1])
        assert (w.startswith('<span class="sr-only">') if same else "<" not in w)
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

    # « Ce n'est pas la bonne activité »: set aside for good, nothing else fits race day — and no picker
    other = Activity(strava_activity_id=802, user_id=test_user.id, sport_type="Run", name="Footing", distance=8000.0, moving_time=2400, elapsed_time=2400,
                     start_date=__import__("datetime").datetime(2026, 10, 2, 20, tzinfo=__import__("datetime").timezone.utc), raw_data={})
    db_session.add(other)
    await db_session.flush()
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result/clear")
    assert "Aucune activité trouvée le ven. 2 oct. 2026 autour de 30 km (1 activité écartée)." in r.text
    assert (await db_session.get(Route, route_id)).result_json is None and (await db_session.get(Route, route_id)).params_json["result_excluded"] == [act.id]
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result")
    assert "1 activité écartée" in r.text and "db-activity" not in r.text and "<select" not in r.text
    # a refused activity comes back inside the card, still unlinked
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result", data={"activity_id": other.id})
    assert 'id="result-compare"' in r.text and "pas le même parcours" in r.text and "D'après" not in r.text and "db-activity" not in r.text


@pytest.mark.asyncio
async def test_mes_courses_builds_the_athlete_profile_once(as_user: AsyncClient, db_session: AsyncSession, monkeypatch):
    """Races run with a pinned point get their plan computed for « Mes courses »: on ONE athlete profile for the
    page, not one per race."""
    from sqlalchemy import select

    from app.models.route import Route, RouteCheckpoint
    from app.services import race_simulator

    ids = [await _create_route(as_user) for _ in range(3)]
    for rid in ids:
        (await db_session.get(Route, rid)).result_json = {"activity_id": None, "activity_name": "x", "activity_date": "",
                                                          "total_actual_s": 5 * 3600, "total_elapsed_s": 5 * 3600 + 600, "actual": []}
        cp = (await db_session.execute(select(RouteCheckpoint).where(RouteCheckpoint.route_id == rid).order_by(RouteCheckpoint.distance_km))).scalars().first()
        cp.target_s = 3600
    await db_session.flush()
    calls = []
    real = race_simulator.build_athlete_gradient_profile

    async def counting(*a, **kw):
        calls.append(1)
        return await real(*a, **kw)

    monkeypatch.setattr(race_simulator, "build_athlete_gradient_profile", counting)
    page = await as_user.get("/simulator")
    assert page.status_code == 200 and page.text.count(" vs plan<") == 3
    assert len(calls) == 1


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
    assert ("Plan de passage" in html or "Feuille de route" in html) and "Nutrition" not in html and "Débrief" in html and "Exporter" in html
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
    # exports and print work on the bike plan too
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
