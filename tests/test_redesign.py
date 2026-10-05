"""The A redesign: identity tokens in one place, the app shell, and the race plan
reorganised in three blocks (objective, profile, passages) with everything else
in « Outils »."""

import json
import re
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.route import Route
from app.models.user import User
from tests.test_race_plan_services import CPS, _course

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


async def _route(client: AsyncClient, race_date: str = "2099-10-02", cps: list[dict] = CPS, name: str = "Jeju test") -> int:
    r = await client.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(cps),
        "name": name, "target_time_s": 5 * 3600, "race_date": race_date,
        "start_hour": 21, "start_minute": 0, "sport_type": "trail", "stop_minutes": 3,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ── identity tokens ─────────────────────────────────────────────────────────

def test_brand_tokens_live_in_one_file_and_drive_tailwind():
    theme = (ROOT / "app/static/css/theme.css").read_text(encoding="utf-8")
    for token in ("--pf-accent", "--pf-accent-ink", "--pf-accent-soft", "--pf-ok", "--pf-warn", "--pf-danger",
                  "--pf-font-display", "--pf-font-text"):
        assert re.search(re.escape(token) + r"\s*:", theme), token
    assert ".dark {" in theme  # dark mode derived from the same names
    config = (ROOT / "tailwind.config.js").read_text(encoding="utf-8")
    assert "rgb(var(--pf-${name}) / <alpha-value>)" in config and "accent:" in config
    built = (ROOT / "app/static/css/tailwind.css").read_text(encoding="utf-8")
    assert "var(--pf-accent)" in built and "var(--pf-gray-900)" in built
    # no orange accent left in the app chrome or the race plan (the map line keeps its slope colours: data, not brand)
    for tpl in ("base.html", "simulator.html", "simulator_route.html", "partials/gpx_result.html", "partials/passage_times.html", "sante.html"):
        text = (ROOT / "app/templates" / tpl).read_text(encoding="utf-8")
        text = re.sub(r"var SLOPE_CLASSES = \[.*?\];", "", text)
        assert "#f97316" not in text and "#f59e0b" not in text and "bg-orange" not in text, tpl


@pytest.mark.asyncio
async def test_app_shell_top_bar_and_tab_bar(as_user: AsyncClient):
    page = (await as_user.get("/simulator")).text
    assert '/static/css/theme.css' in page and "pf-sidebar" not in page
    assert 'class="pf-top"' in page and 'class="pf-tabbar"' in page
    tabbar = page.split('class="pf-tabbar"')[1].split("</nav>")[0]
    assert [label for label in ("Courses", "Activités", "Santé", "Réglages") if label in tabbar] == ["Courses", "Activités", "Santé", "Réglages"]
    assert "Vélo" not in tabbar and "Triathlon" not in tabbar


# ── race plan ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_race_plan_has_one_primary_action_a_plan_selector_and_the_tools(as_user: AsyncClient):
    rid = await _route(as_user)
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    # hero: objective, arrival, verdict
    assert 'id="hero-time"' in html and 'id="hero-arrival"' in html and 'id="hero-verdict"' in html
    # ONE primary action, and it leads to the existing exports
    assert html.count("data-primary-export") == 1 and "Envoyer à la montre" in html
    for call in ("exportPace('gpx')", "exportPace('tcx')", "exportPace('csv')", "exportGpx()"):
        assert call in html, call
    # Optimiste / Cible / Sécurité as a segmented control
    seg = html.split('id="scn-seg"')[1].split("</div>")[0]
    assert [b for b in re.findall(r'data-scn-btn="(\w+)"', seg)] == ["fast", "target", "safe"]
    # everything else in « Outils »
    tools = html.split('aria-label="Outils"')[1]
    for label in ("Partager le plan", "Bande imprimable", "Sacs et drop bags", "Nutrition", "Pilotage", "Carte du parcours",
                  "Finisher de référence", "Exporter", "Réglages du plan", "Remplacer la trace GPX"):
        assert label in tools, label
    for hook in ('id="rtab-nutrition"', 'id="rtab-pacing"', 'id="map-toggle"', 'id="advanced"', 'id="stop-min"',
                 'name="scenario_fast_pct"', "/reimport", 'id="rpanel-nutrition"', 'id="rpanel-pacing"', 'id="rpanel-reference"'):
        assert hook in html, hook
    # the old tab strip is gone, the debrief waits for race day
    assert "rtab-plan" not in html and 'id="rtab-realise"' not in html


@pytest.mark.asyncio
async def test_debrief_joins_the_tools_once_the_race_is_run(as_user: AsyncClient):
    rid = await _route(as_user, race_date="2020-10-02")
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    assert 'id="rtab-realise"' in html and "Débrief" in html.split('aria-label="Outils"')[1]


@pytest.mark.asyncio
async def test_passages_are_a_list_whose_rows_open_with_the_point_details(as_user: AsyncClient):
    rid = await _route(as_user)
    cps = [dict(c) for c in CPS]
    cps[1]["kind"] = "base"
    r = await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(cps), "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0,
        "route_id": rid, "stop_minutes": 3,
    })
    assert r.status_code == 200, r.text
    t = r.text
    assert "<table" not in t and 'role="list"' in t
    assert t.count("data-row ") == len(CPS) + 1  # one per point + the finish
    # each row: a toggle and a detail block (hidden until opened), one per row
    assert t.count('class="pf-prow-head"') == t.count("data-detail=") == len(CPS) + 1
    assert t.count('class="pf-det hidden"') == len(CPS) + 1
    # the detail: heart-rate ceiling, carbs, water, the point's chip and its actions
    assert "FC max" in t and "glucides" in t and ">eau<" in t
    assert 'data-poste="1"' in t and "Modifier le poste" in t and "Fixer l'heure" in t and "Supprimer" in t
    # base vie badge, day separator, the three plans' finish for the selector
    assert '<span class="pf-tag">Base vie</span>' in t and 'data-day="1"' in t
    pill = re.search(r'data-scn-pill hidden data-end-fast="([^"]+)" data-end-target="([^"]+)" data-end-safe="([^"]+)"', t)
    assert pill and all(pill.groups())
    # one clock per plan in each row, CSS shows the one picked
    assert t.count("data-safe") >= len(CPS) + 1 and t.count("data-fast") >= len(CPS) + 1


# ── courses ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_courses_list_next_race_upcoming_and_done_with_real_vs_plan(as_user: AsyncClient, db_session: AsyncSession):
    nxt = await _route(as_user, race_date="2099-10-02", name="Prochaine")
    later = await _route(as_user, race_date="2099-12-02", name="Plus tard")
    done = await _route(as_user, race_date="2020-10-02", name="Finie")
    route = (await db_session.execute(select(Route).where(Route.id == done))).scalar_one()
    route.result_json = {"total_actual_s": 5 * 3600 - 12 * 60, "actual": []}
    await db_session.flush()
    html = (await as_user.get("/simulator")).text
    assert "Importer un GPX" in html and 'hx-post="/partials/simulator/gpx-upload"' in html
    assert html.index("Prochaine course") < html.index(f'id="route-next-{nxt}"') < html.index("À venir") < html.index(f'id="route-card-{later}"')
    finished = html.split("Terminées")[1]
    assert f'id="route-card-{done}"' in finished and "4h48" in finished and "−12 min vs plan" in finished
