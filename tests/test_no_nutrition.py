"""Owner, 2026-10-09: « Supprime la nutrition et repars de zéro, je n'aime toujours pas. »
The Nutrition tool (« Ravitaillement » in older code) is gone from the product: nothing of it is
reachable or visible on the race page, the print page, the pace exports or the passage rows. The
aid-station vocabulary (a point typed « Ravito complet », « Drop bag ici ») is checkpoint metadata
and stays; the race-week carbohydrates of « Préparation » (race_prep.py) were not named and stay."""

import json
import re
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.dependencies import get_current_user
from app.models.route import Route
from app.models.user import User
from tests.test_race_plan_services import CPS, _course

ROOT = Path(__file__).resolve().parent.parent
# the tool's words: its name, its old name, what it counted (gels, caffeine), its blocks and buttons
GONE = re.compile(r"nutri|ravitaillement|\bgels?\b|caf[ée]ine|à chaque ravito|de secours|tes produits|plan type|prends \d", re.I)
# the privacy page's one line about the tool: its stored data (nutrition_products, routes.nutrition_json) stays until the
# new model replaces it, so the page declares it (RGPD) and says it is read by nothing
DISCLOSURE = ("Ce que tu avais saisi dans l'ancien outil nutrition (produits, plan) reste enregistré sans être utilisé, "
              "jusqu'à sa refonte.")


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


async def _route(client: AsyncClient) -> int:
    r = await client.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS),
        "name": "Jeju test", "target_time_s": 5 * 3600, "race_date": "2099-10-02",
        "start_hour": 21, "start_minute": 0, "sport_type": "trail", "stop_minutes": 3,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_the_race_page_print_exports_and_rows_carry_nothing_of_the_tool(as_user: AsyncClient, db_session):
    rid = await _route(as_user)
    # an old plan left in the database (the data goes with the new model) changes nothing on any surface
    route = (await db_session.execute(select(Route).where(Route.id == rid))).scalar_one()
    route.nutrition_json = {"v": 2, "rhythms": [{"product_id": -1, "every_min": 30, "from_min": 0, "to_min": None}],
                            "spare": True, "refills": [CPS[0]["distance_km"]]}
    await db_session.flush()

    page = await as_user.get(f"/simulator/routes/{rid}")
    assert page.status_code == 200 and not GONE.search(page.text), GONE.search(page.text)
    assert "Préparer" not in page.text and "pf-prep" not in page.text and "rpanel-tool" in page.text
    # the old deep links open the plan itself
    page = await as_user.get(f"/simulator/routes/{rid}?vue=nutrition&open=produits#nutrition")
    assert page.status_code == 200 and not GONE.search(page.text) and 'id="rpanel-plan" class="pf-col"' in page.text

    printed = await as_user.get(f"/simulator/routes/{rid}/print")
    assert printed.status_code == 200 and not GONE.search(printed.text), GONE.search(printed.text)
    assert "Consignes" in printed.text

    for fmt in ("gpx", "tcx", "csv"):
        r = await as_user.get(f"/api/simulator/routes/{rid}/pace-export?format={fmt}")
        assert r.status_code == 200 and not GONE.search(r.text), (fmt, GONE.search(r.text))

    rows = await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(CPS), "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0,
        "route_id": rid, "stop_minutes": 3,
    })
    assert rows.status_code == 200 and not GONE.search(rows.text), GONE.search(rows.text)
    # the checkpoint metadata stays: the kind in words, the drop bag flag
    assert "<span>Ravito</span>" in rows.text and "Drop bag ici" in rows.text
    # the aid stops come from the typed points only (eau 2′, ravito 5′), never from an old plan's refills
    from app.routers.simulator import _aid_stops

    assert _aid_stops(CPS) == {6.0: 120, 10.0: 300, 20.0: 300} and _aid_stops(CPS, 3) == {6.0: 180, 10.0: 180, 20.0: 180}


@pytest.mark.asyncio
async def test_the_tool_s_routes_pacing_words_and_settings_are_gone(as_user: AsyncClient):
    rid = await _route(as_user)
    assert (await as_user.get(f"/partials/simulator/nutrition/{rid}")).status_code == 404
    assert (await as_user.post(f"/partials/simulator/nutrition/{rid}/starter")).status_code == 404
    assert (await as_user.post(f"/partials/simulator/nutrition/{rid}/products", data={"name": "x"})).status_code == 404
    assert (await as_user.get("/nutrition", follow_redirects=False)).status_code == 404
    # the pacing words carry no eating cue any more
    from app.services.pacing_guide import effort_sentence

    assert effort_sentence("descent", 120) == "<b>Cardio vers 120</b> · descente : relâché, sans freiner."
    assert "manger" not in (await as_user.get(f"/partials/simulator/pacing/{rid}")).text
    # Réglages: the weight stays, without the nutrition helper or the caffeine cap
    settings = (await as_user.get("/settings")).text
    assert 'name="weight_kg"' in settings and not GONE.search(settings), GONE.search(settings)
    # the public pages: nothing of the tool, except the privacy page's disclosure of its stored data
    for path in ("/landing", "/methode", "/privacy", "/auth/login"):
        r = await as_user.get(path)
        text = r.text
        if path == "/privacy":
            assert DISCLOSURE in text
            text = text.replace(DISCLOSURE, "")
        assert r.status_code == 200 and not GONE.search(text), (path, GONE.search(text))


def test_nothing_of_the_tool_is_left_in_the_code():
    assert not (ROOT / "app/services/nutrition.py").exists() and not (ROOT / "app/services/nutrition_plan.py").exists()
    assert not (ROOT / "app/templates/partials/nutrition_card.html").exists()
    css = (ROOT / "app/static/css/interface.css").read_text(encoding="utf-8")
    assert not re.search(r"pf-nu\b|pf-nu-|pf-rv\b|pf-rv-|pf-prep|nutrition|ravitaillement", css, re.I)  # (.pf-num stays)
    for tpl in ("simulator_route.html", "simulator_route_bike.html", "simulator_print.html", "partials/gpx_result.html",
                "partials/passage_times.html", "partials/race_prep.html", "partials/tri_plan.html", "settings.html"):
        text = (ROOT / "app/templates" / tpl).read_text(encoding="utf-8")
        assert "nutrition" not in text.lower() and "/partials/simulator/nutrition" not in text, tpl
    router = (ROOT / "app/routers/simulator.py").read_text(encoding="utf-8")
    assert "nutrition" not in router.lower() and "NutritionProduct" not in router
    # the exports carry no per-point note (they were the plan's « Prends 2 gels »), the dead race-strategy prompt that asked
    # for a « plan nutrition » went with it, and an old deep link (#nutrition, #bags…) opens the plan and drops its hash
    assert not (ROOT / "app/prompts/race_strategy.py").exists()
    for py in ("app/services/pace_export.py", "app/services/claude.py", "app/schemas/simulator.py"):
        text = (ROOT / py).read_text(encoding="utf-8")
        assert "nutrition" not in text.lower() and "notes_by_km" not in text and "race_strategy" not in text, py
    route = (ROOT / "app/templates/simulator_route.html").read_text(encoding="utf-8")
    assert "function switchRouteTab(tab) {" in route and "else if (h && !document.getElementById(h)) switchRouteTab('plan');" in route
