"""The A redesign: identity tokens in one place, the app shell, and the race plan
simplified: one number, one arrival line, one button, the passages (clock, name, km),
two « Préparer » rows; everything else one tap away (the … menu, the sheets, an opened row)."""

import json
import re
from html import unescape
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

def _visible(html: str) -> str:
    """The page as text a reader can see: no scripts, no JSON, no tags, no attributes."""
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style\b.*?</style>", " ", html, flags=re.S)
    html = re.sub(r"<input type=\"hidden\"[^>]*>", " ", html)
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))


def _menu_items(html: str) -> list[str]:
    menu = html.split('id="plan-more"')[1].split("</details>")[0]
    items = re.findall(r'role="menuitem"[^>]*>(.*?)</(?:button|label)>', menu, flags=re.S)
    return [unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", i)).strip()) for i in items]


def _surface(html: str) -> str:
    """The plan as it opens: everything before the overlays (map, sheets)."""
    return html.split('id="cp-sheet"')[0]


@pytest.mark.asyncio
async def test_race_plan_is_one_column_with_one_primary_action_and_no_tools_column(as_user: AsyncClient):
    rid = await _route(as_user)
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    text = _visible(html)
    # hero: objective, arrival, verdict; the number is the button (pencil inside), no « Modifier » link
    assert 'id="hero-time"' in html and 'id="hero-arrival"' in html and 'id="hero-verdict"' in html
    assert 'class="pf-edit"' not in html and "hero-arrival-sub" not in html
    # ONE primary action on the surface, no print icon beside it; it leads to the four exports
    surface = _surface(html)
    assert surface.count("pf-btn-primary") == 1 and html.count("data-primary-export") == 1 and "Envoyer à la montre" in html
    assert "pf-btn-icon" not in surface
    for call in ("exportPace('gpx')", "exportPace('tcx')", "exportPace('csv')", "exportGpx()"):
        assert call in html, call
    # Optimiste / Cible / Sécurité: « Plan affiché » inside the objective editor, not on the surface
    seg = html.split('id="scn-seg"')[1].split("</div>")[0]
    assert re.findall(r'data-scn-btn="(\w+)"', seg) == ["fast", "target", "safe"]
    panel = html.split('id="obj-panel"')[1].split('class="pf-arr"')[0]
    assert 'id="scn-seg"' in panel and "Plan affiché" in panel and "Ton estimation" in panel and "pf-objok" in panel
    assert 'id="scn-chip"' in html  # « Plan Sécurité affiché · revenir à Cible », shown by the script when it applies
    # no « Outils » column (the desktop side pane holds only the profile and Préparer), no legend, no weather chip in the meta line
    assert 'aria-label="Outils"' not in html and "pf-plan-tools" not in html and " Outils " not in text
    assert "pf-legend" not in html and 'id="weather-result"' not in html and 'id="pass-count"' not in html
    # Préparer: one row, « Ravitaillement », no subtitle (Pilotage lives in the rows now)
    prep = html.split('class="pf-prep"')[1].split("</section>")[0]
    assert re.findall(r'class="pf-tool"[^>]*>.*?<span>(?:<svg.*?</svg>)<span>([^<]+)<', prep, flags=re.S) == ["Ravitaillement"]
    assert "<small" not in prep and 'id="nutri-sub"' not in prep
    # nothing explains the obvious, nothing duplicated
    for gone in ("Exporter", "Carte du parcours", "Masquer la carte", "Agrandir la carte", "Verdict", "postes", "Double-clic sur le profil",
                 "Renommer", "Fixer l'heure", "Changer l'heure", "Glisse ou tape", "de jour", "de nuit", "Ton rythme", "Sacs et drop bags",
                 "Pilotage", "Nutrition et sacs"):
        assert gone not in text, gone
    # the hooks the tools and scripts rely on are all still there
    for hook in ('id="rtab-nutrition"', 'id="rtab-reference"', 'name="hr_cap_climb"', 'id="advanced"', 'id="stop-min"', 'id="settings-sheet"',
                 'id="race-sheet"', 'id="save-name"', 'id="race-date"', 'id="start-time"', 'name="scenario_fast_pct"', "/reimport",
                 'id="rpanel-nutrition"', 'id="rpanel-reference"', 'class="pf-carte"', 'id="wx-block"'):
        assert hook in html, hook
    for gone in ('id="rtab-pacing"', 'id="rpanel-pacing"', 'id="rtab-bags"'):
        assert gone not in html, gone
    # the map is an overlay, closed on load, never restored from a remembered state
    assert re.search(r'id="map-wrap" class="hidden pf-mapov"', html)
    assert "getItem('pf.map')" not in html and "pf.map3d" in html
    # the old tab strip is gone, the debrief waits for race day
    assert "rtab-plan" not in html and 'id="rtab-realise"' not in html


@pytest.mark.asyncio
async def test_plan_is_one_column_on_the_phone_and_two_panes_on_a_desktop(as_user: AsyncClient):
    css = (ROOT / "app/static/css/interface.css").read_text(encoding="utf-8")
    # phone and tablet: one centred column in reading order, nothing moved by CSS (focus and screen readers follow the screen)
    assert re.search(r"\.pf-col \{ max-width: 720px; margin-inline: auto; \}", css)
    assert not re.search(r"\.pf-side\b", css) and not re.search(r"\.pf-prep \{[^}]*\border:", css)
    # desktop: the profile and Préparer sticky in column 2 from the hero's row (3) beside the passages, no tools column
    assert re.search(r"#simulator-root > :is\(\.pf-profile, \.pf-prep\) \{[^}]*grid-row: 3 / span \d+;[^}]*position: sticky;", css)
    assert "pf-plan-tools" not in css and "pf-plan-grid" not in css
    route = (ROOT / "app/templates/simulator_route.html").read_text(encoding="utf-8")
    assert 'id="rpanel-plan" class="pf-col"' in route
    # the page in reading order: the title, the re-import error, the hero (grid row 3, where the side pane starts),
    # then the profile, the passages, Préparer
    rid = await _route(as_user)
    root = (await as_user.get(f"/simulator/routes/{rid}")).text.split('id="simulator-root"')[1]
    assert re.match(r'[^>]*>\s*<div class="pf-plan-title">', root)
    assert re.search(r'</div>\s*<div id="reimport-error"></div>\s*<section class="pf-hero"', root)
    marks = ('class="pf-hero"', 'class="pf-profile"', 'id="scn-chip"', 'id="passage-times-result"', 'id="add-pt"', 'class="pf-prep"')
    at = [root.index(m) for m in marks]
    assert at == sorted(at)


@pytest.mark.asyncio
async def test_title_and_meta_are_plain_french_text(as_user: AsyncClient):
    from app.routers.simulator import fr_date_label

    rid = await _route(as_user, race_date="2099-10-17")
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    head = html.split('class="pf-plan-title"')[1].split('id="plan-more"')[0]
    assert "<input" not in head and "<select" not in head  # the fields live in « Nom, date et départ »
    assert '<h1 id="race-name"' in head and ">Jeju test</h1>" in head
    assert '<span id="meta-date">sam. 17 oct. 2099</span>' in head and '<span id="meta-start">21:00</span>' in head
    assert 'id="date-pill" class="pf-date-pill hidden"' in head
    # the start time is typed as hh:mm (no AM/PM picker), the date as a date
    sheet = html.split('id="race-sheet"')[1].split('id="settings-sheet"')[0]
    assert 'type="text" id="start-time" inputmode="numeric"' in sheet and 'maxlength="5"' in sheet and 'value="21:00"' in sheet
    assert 'type="date" id="race-date"' in sheet and "Ta course" in sheet and "Enregistrer" in sheet
    # the server writes the same French text the script does, whatever the browser's language
    from datetime import date
    today = date(2026, 10, 5)
    assert fr_date_label("2026-10-17", today=today) == "sam. 17 oct."
    assert fr_date_label("2027-02-01", today=today) == "lun. 1 févr. 2027"
    assert fr_date_label("2026-08-15", today=today) == "sam. 15 août"
    assert fr_date_label(None) == "" and fr_date_label("pas une date") == ""


@pytest.mark.asyncio
async def test_meta_line_wraps_without_a_hanging_dot(as_user: AsyncClient):
    rid = await _route(as_user, race_date="2099-10-17")
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    head = html.split('class="pf-plan-title"')[1].split('id="plan-more"')[0]
    # no separator elements: each item draws its own « · » before it, and the one that starts a line is clipped
    assert "pf-sep" not in head and "·" not in _visible(head)
    meta = head.split('class="pf-meta"')[1].split("</div>")[0]
    items = [i.strip() for i in re.findall(r">([^<>]+)</span>", meta)]
    assert items[:2] == ["sam. 17 oct. 2099", "21:00"] and len(items) == 4
    assert items[2].endswith(" km") and items[3].endswith(" m D+")
    css = (ROOT / "app/static/css/interface.css").read_text(encoding="utf-8")
    assert re.search(r"\.pf-meta \{ overflow: hidden;", css) and re.search(r"\.pf-meta-in \{[^}]*margin-left: -16px;", css)
    assert re.search(r'::before \{ content: "·"; display: inline-block; width: 16px;', css)


@pytest.mark.asyncio
async def test_without_objective_one_estimate_and_the_arrival_follows_it(as_user: AsyncClient):
    r = await as_user.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS), "name": "Sans objectif",
        "race_date": "", "start_hour": 21, "start_minute": 0, "sport_type": "trail", "stop_minutes": 3,
    })
    rid = r.json()["id"]
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    hero = re.search(r'id="hero-num"[^>]*>(\d+)h(\d\d)<', html).groups()
    # the editor says the same estimate, its inputs hold it (minutes on two digits)
    assert re.search(r'Ton estimation : <b[^>]*>(\d+)h(\d\d)</b>', html).groups() == hero
    th = re.search(r'id="target-h"[^>]*value="(\d+)"', html).group(1)
    tm = re.search(r'id="target-m"[^>]*value="(\d+)"', html).group(1)
    assert (th, tm) == hero and len(tm) == 2
    # the first table already runs on that estimate, stops included: start + number = arrival
    # (the page's own recalculations send the same value, so nothing jumps once it loads)
    plan = json.loads(html.split('id="plan-data">')[1].split("</script>")[0])
    assert plan["plan_total_s"] == int(hero[0]) * 3600 + int(hero[1]) * 60
    # every time on the page is rounded the way the script's fmtHM rounds (no 20h06 beside 20h07)
    gpx = (ROOT / "app/templates/partials/gpx_result.html").read_text(encoding="utf-8")
    assert "(course.predicted_total_time_s + 30) // 60 * 60" in gpx and "rc.total_s + 30" in gpx


def test_phone_anti_zoom_rule_spares_the_objective_inputs():
    route = (ROOT / "app/templates/simulator_route.html").read_text(encoding="utf-8")
    assert "#simulator-root input:not(.pf-hero-in), #simulator-root select { font-size: 16px; }" in route


@pytest.mark.asyncio
async def test_missing_date_shows_the_pill_instead_of_date_and_start(as_user: AsyncClient):
    rid = await _route(as_user, race_date="")
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    head = html.split('class="pf-plan-title"')[1].split('id="plan-more"')[0]
    assert 'id="meta-when" class="pf-meta-when hidden"' in head
    assert 'id="date-pill" class="pf-date-pill"' in head and "Ajoute la date et l'heure de départ" in head
    assert 'id="wx-block" class="pt-4 border-t border-line hidden"' in html  # no date, no weather block


@pytest.mark.asyncio
async def test_menu_lists_share_print_reference_then_race_settings_and_gpx(as_user: AsyncClient):
    rid = await _route(as_user)
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    assert _menu_items(html) == ["Partager le plan", "Bande imprimable", "Finisher de référence",
                                 "Nom, date et départ", "Réglages du plan", "Remplacer la trace GPX"]
    menu = html.split('id="plan-more"')[1].split("</details>")[0]
    assert menu.count('class="pf-menu-sep"') == 1 and "<small" not in menu
    assert "sharePlan()" in menu and "printPlan()" in menu and "switchRouteTab('reference')" in menu
    assert "openRaceSheet()" in menu and "openTool('advanced')" in menu and 'type="file" name="gpx_file"' in menu


def test_debrief_mode_rules():
    from datetime import date

    from app.routers.simulator import debrief_mode

    today = date(2026, 10, 5)
    assert debrief_mode("2026-10-17", False, today=today) is None  # before a dated race: nowhere
    assert debrief_mode(None, False, today=today) == "menu"  # no date: in the menu
    assert debrief_mode("2026-10-05", False, today=today) == "menu"  # race day: in the menu
    assert debrief_mode("2026-10-04", False, today=today) == "primary"  # after: the main button
    assert debrief_mode("2026-10-17", True, today=today) == "primary"  # a result is linked


@pytest.mark.asyncio
async def test_debrief_appears_only_when_it_applies(as_user: AsyncClient, db_session: AsyncSession):
    from datetime import date

    # a future dated race: no debrief anywhere
    html = (await as_user.get(f"/simulator/routes/{await _route(as_user)}")).text
    assert "Débrief" not in _visible(html) and "débrief" not in _visible(html) and "data-primary-debrief" not in html
    # no date, or race day: in the menu, after « Finisher de référence »; the main button stays « Envoyer à la montre »
    for rd in ("", date.today().isoformat()):
        html = (await as_user.get(f"/simulator/routes/{await _route(as_user, race_date=rd)}")).text
        assert _menu_items(html) == ["Partager le plan", "Bande imprimable", "Finisher de référence", "Débrief",
                                     "Nom, date et départ", "Réglages du plan", "Remplacer la trace GPX"], rd
        assert html.count("data-primary-export") == 1 and 'id="rpanel-realise"' in html and "/result?" in html
    # after race day: « Voir le débrief » is the main button, « Envoyer à la montre » heads the menu (the 4 formats stay)
    html = (await as_user.get(f"/simulator/routes/{await _route(as_user, race_date='2020-10-02')}")).text
    assert "data-primary-export" not in html and html.count("data-primary-debrief") == 1 and "Voir le débrief" in html
    assert _surface(html).count("pf-btn-primary") == 1
    assert 'id="heat-line"' not in html  # the forecast heat is for before the race
    assert _menu_items(html) == ["Envoyer à la montre", "Partager le plan", "Bande imprimable", "Finisher de référence",
                                 "Nom, date et départ", "Réglages du plan", "Remplacer la trace GPX"]
    assert 'id="rtab-realise"' in html and "/result?" in html
    for call in ("exportPace('gpx')", "exportPace('tcx')", "exportPace('csv')", "exportGpx()"):
        assert call in html, call
    # a linked result counts as a run race, even with a date ahead
    rid = await _route(as_user, race_date="2099-10-02")
    route = (await db_session.execute(select(Route).where(Route.id == rid))).scalar_one()
    route.result_json = {"total_actual_s": 5 * 3600, "actual": []}
    await db_session.flush()
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    assert "data-primary-debrief" in html and _menu_items(html)[0] == "Envoyer à la montre"


async def _rows(client: AsyncClient, rid: int, cps: list[dict]) -> str:
    r = await client.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(cps), "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0,
        "route_id": rid, "stop_minutes": 3,
    })
    assert r.status_code == 200, r.text
    return r.text


def _heads(t: str) -> list[str]:
    return re.findall(r'(<div class="pf-prow-head".*?)</div>\s*<div data-detail', t, flags=re.S)


@pytest.mark.asyncio
async def test_closed_rows_show_clock_name_and_km_only(as_user: AsyncClient):
    rid = await _route(as_user)
    cps = [dict(c) for c in CPS]
    cps[1]["kind"] = "base"
    t = await _rows(as_user, rid, cps)
    assert "<table" not in t and 'role="list"' in t
    assert t.count("data-row ") == len(CPS) + 1  # one per point + the finish
    heads = _heads(t)
    assert len(heads) == len(CPS) + 1 and t.count('class="pf-det hidden"') == len(CPS) + 1
    for h in heads:
        assert h.count("<svg") == 1  # the chevron only: no poste glyph, no bag, no weather icon
        text = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip())
        # the clock (plus the hidden Optimiste / Sécurité clocks the plan selector swaps in), the name, the km
        assert re.fullmatch(r"(\d\d:\d\d ){1,3}\S.* km [\d,]+", text), text
        assert "FC" not in text and "+" not in text and "°" not in text and " min" not in text and "Base vie" not in text
    assert "pf-tag" not in t and "pf-kind" not in t and "pf-prow-col" not in t
    # the day separator and the three plans' finish for « Plan affiché »
    assert 'data-day="1"' in t
    pill = re.search(r'data-scn-pill hidden data-end-fast="([^"]+)" data-end-target="([^"]+)" data-end-safe="([^"]+)"', t)
    assert pill and all(pill.groups())
    assert t.count("data-safe") >= len(CPS) + 1 and t.count("data-fast") >= len(CPS) + 1


@pytest.mark.asyncio
async def test_an_opened_row_has_the_leg_how_to_run_it_one_line_of_facts_and_two_actions(as_user: AsyncClient):
    rid = await _route(as_user)
    t = await _rows(as_user, rid, CPS)
    det = t.split('data-detail="1"')[1].split('data-row role="listitem"')[0]  # Col: ravito, crew, drop bag, cutoff
    text = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", det)))
    assert re.search(r"\d+ ?(h\d\d|min) · 4 km · \+[\d ]+ m −[\d ]+ m", text), text  # the previous row is right above: no « depuis »
    # how to run the leg (cardio + terrain) and what was eaten on it, in sentences: no tiles
    # (flat legs: the ceiling only, running steady goes without saying)
    assert "pf-tiles" not in t and re.search(r"Cardio (sous|vers) \d+|(Montée|montée|Très raide|très raide|Descente|descente) :", text), text
    assert "roulant" not in text.lower(), text
    assert "À prendre en route :" in text and "depuis" not in text.lower(), text
    # 3 min at every point is a setting (« Même durée partout »), not a fact of each row: not repeated
    assert re.search(r"Ravito · assistance · barrière 10:30 · marge [+−]\d+h\d\d", text) and "arrêt" not in text, text
    assert re.search(r"Selon ta forme : entre \d\d:\d\d et \d\d:\d\d", text), text
    assert "Drop bag ici" in det and "rien de prévu" not in t  # nothing planned in it: just « Drop bag ici. »
    actions = re.findall(r"<button[^>]*>([^<]+)</button>", det.split('class="pf-det-actions"')[1])
    assert actions == ["Modifier ce point", "Voir sur la carte"]
    assert "openSheet(1)" in det and "flyToCp(1)" in det
    first = t.split('data-detail="0"')[1].split('data-row role="listitem"')[0]
    assert "depuis" not in first and "Point d'eau" in unescape(first)  # the previous row is right above
    # gone from the row: the poste chip, the plans line, pinning and deleting (all in the point dialog)
    for gone in ("data-poste", "Modifier le poste", "Fixer l'heure", "Changer l'heure", ">Supprimer<", "Plans</dt>", "<dl", "aucune"):
        assert gone not in unescape(t), gone
    # the finish row: the leg and how to run it, no actions
    fin = t.split('data-detail="-1"')[1]
    assert 'class="pf-legline' in fin and "pf-det-actions" not in fin


@pytest.mark.asyncio
async def test_a_close_or_missed_cutoff_shows_on_the_closed_row(as_user: AsyncClient):
    rid = await _route(as_user)
    cps = [dict(c) for c in CPS]
    t = await _rows(as_user, rid, cps)
    plan = json.loads(t.split('id="plan-data">')[1].split("</script>")[0])
    clk = next(p for p in plan["points"] if p["cp_index"] == 0)["clock_s"] // 60 * 60

    def hhmm(s: int) -> str:
        s %= 86400
        return "%02d:%02d" % (s // 3600, s % 3600 // 60)

    # 30 min ahead: amber, on the closed row (on the phone on its own line under the km: never cut by the ellipsis)
    cps[0]["cutoff_clock"] = hhmm(clk + 30 * 60)
    head = _heads(await _rows(as_user, rid, cps))[0]
    assert f"barrière {hhmm(clk + 30 * 60)} · marge 30 min" in head and "text-warn" in head and "text-danger" not in head
    assert re.search(r'class="pf-bar-m text-warn">barrière', head)  # no leading « · » before it
    css = (ROOT / "app/static/css/interface.css").read_text(encoding="utf-8")
    assert ".pf-bar-m { display: block;" in css and ".pf-prow.is-open :is(.pf-bar-m, .pf-bar-d) { display: none; }" in css
    # missed by 15 min: red, and the clock too
    cps[0]["cutoff_clock"] = hhmm(clk - 15 * 60)
    head = _heads(await _rows(as_user, rid, cps))[0]
    assert f"barrière {hhmm(clk - 15 * 60)} · marge −0h15" in head and 'data-cible-text class="text-danger"' in head
    # two hours ahead: nothing on the closed row (it is in the opened one)
    cps[0]["cutoff_clock"] = hhmm(clk + 2 * 3600)
    t = await _rows(as_user, rid, cps)
    assert "barrière" not in _heads(t)[0] and f"barrière {hhmm(clk + 2 * 3600)}" in t


@pytest.mark.asyncio
async def test_no_checkpoint_shows_the_empty_state(as_user: AsyncClient):
    rid = await _route(as_user, cps=[])
    t = await _rows(as_user, rid, [])
    assert "data-empty-state" in t and "Ajoute tes ravitos pour avoir ton heure de passage à chacun." in t
    assert "+ Ajouter un ravito" in t and "openAddSheet()" in t
    assert "data-empty-state" not in await _rows(as_user, rid, CPS)


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
