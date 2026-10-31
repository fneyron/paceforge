"""Regressions of the app bugs and accessibility defects of the late-October 2026 site audit: Santé's help bubbles,
words and legends, the number formats, the activity page's way back, the logged-in 404, Réglages, the race plan's
screen-reader names, the checkin's focus and announcement, the tab navigation and the refresh toast."""
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import pytest

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.services import race_prep as rp
from app.services import sante, sante_score, sante_sleep, viz
from tests.test_race_prep import S, data, route, steady
from tests.test_sante_score import D, _nights, _rich, _session
from tests.test_settings_links import _logged_in
from tests.test_viz import _env, render

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/interface.css").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_commit(db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
async def as_user(client, test_user):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    return client


def _page(**kw) -> tuple[dict, str]:
    sessions = [_session(D, 40, sid=2, name="Sortie récente")]
    p = sante._compute(_nights(_rich(**kw), sessions), sessions, D, D, None, {}, True, 190, {})
    return p, _env().get_template("partials/sante_page.html").render(page=p)


# ── Santé ───────────────────────────────────────────────────────────────────

def test_the_recovery_ring_word_agrees_with_its_name():
    assert sante_score.DIAL_WORDS == {"ok": "habituelle", "warn": "à surveiller", "danger": "inhabituelle"}
    assert sante_score.index_label(100) == "100/100"  # « 100/100 en moyenne », no space before the denominator


def test_help_bubbles_sit_right_with_human_names_and_clear_the_tab_bar():
    _, html = _page()
    labels = re.findall(r'class="pf-help-button" aria-label="([^"]+)"', html)
    assert "À propos du sommeil" in labels and "À propos de la récupération" in labels
    assert "À propos de la VFC" in labels and "À propos de la FC de nuit" in labels
    assert "À propos de la charge cumulée" in labels and not any("-" in label for label in labels)
    # one place: last in its header (after the value or the status), on the right
    head = html.split('<section id="sommeil"')[1].split("</header>")[0]
    assert head.index("pf-panel-value") < head.index("pf-help")
    trends = _env().get_template("partials/sante_trends.html").render(page={"trends": [
        {"key": "weight", "title": "Poids", "delta": "−0,3 kg en 4 sem.", "value": "65 kg", "latest": ""}]})
    head = trends.split('class="pf-measurement-head"')[1].split("</div>")[0]
    assert head.index("pf-trend-delta") < head.index("pf-help") and 'aria-label="À propos du poids"' in head
    assert ":is(.pf-health-panel-heading, .pf-measurement-head, .pf-card-top) > :is(.pf-help, .pf-head-end) { margin-left: auto;" in CSS
    assert head.count("pf-head-end") == 1  # the delta and the (i) wrap together, never the (i) alone on the left
    phone = CSS.split("@media (max-width: 480px) {\n    .pf-help-tip")[1].split("}\n}")[0]
    assert "z-index: 50" in phone and "bottom: calc(63px + 1.0625rem + env(safe-area-inset-bottom" in phone
    assert ".pf-native .pf-help-tip { bottom: calc(20px + env(safe-area-inset-bottom" in phone


def test_escape_dismisses_a_tip_and_keeps_the_focus():
    js = (ROOT / "app/static/js/pf-viz.js").read_text(encoding="utf-8")
    assert ".blur()" not in js and 'el.classList.add("is-tip-dismissed")' in js


def test_a_phone_opens_the_help_sheet_on_a_click():
    """Re-audit 2026-10-31: an (i) in the lower third of a phone never opened (the sheet shown at the press landed
    under the finger and swallowed the click). On a phone a click opens and closes it (aria-expanded), its × and
    Escape close it (the focus back on the (i)); the text it describes the button with leaves the × out."""
    js = (ROOT / "app/static/js/pf-viz.js").read_text(encoding="utf-8")
    assert 'matchMedia("(max-width: 480px)")' in js and 'button.setAttribute("aria-expanded", "true")' in js
    assert 'if (openHelp) { closeHelp(true); return; }' in js and 'button.focus({ preventScroll: true })' in js
    assert 'e.target.closest(".pf-help-close")' in js and 'classList.add("pf-help-js")' in js
    phone = CSS.split("@media (max-width: 480px) {\n    .pf-help-tip")[1].split("}\n}")[0]
    assert ".pf-help-js .pf-help:is(:hover, :focus-within) .pf-help-tip { display: none; }" in phone
    assert ".pf-help-js .pf-help.is-open .pf-help-tip { display: block;" in phone
    html = render("{{ v.help('vfc', 'Texte.', 'de la VFC') }}")
    assert 'aria-describedby="help-vfc"' in html and '<span role="tooltip" id="help-vfc">Texte.</span>' in html
    assert '<button type="button" class="pf-help-close" aria-label="Fermer l’aide">' in html


def test_the_charge_card_has_one_name():
    _, html = _page()
    assert "Charge accumulée" not in html and "Charge cumulée : choisis un jour" in html
    assert "Charge accumulée" not in (ROOT / "app/templates/sante_help.html").read_text(encoding="utf-8")
    # the range's word takes its article: « une nuit », « un jour »
    assert not re.search(r"choisis une jour\b", html) and "choisis une nuit" in html


def test_the_sleep_legend_names_only_what_is_drawn():
    nights = _nights(_rich(days=20))
    c = sante_sleep.bars(nights, D, "14")
    assert c["legend"] == [("is-night", "nuit")]  # no phases, no nap: no « sieste », no « nuit sans phases »
    nights[D].stages = {"awake": 10, "light": 240, "deep": 100, "rem": 80}
    c = sante_sleep.bars(nights, D, "14")
    assert c["legend"] == [("is-light", "léger"), ("is-deep", "profond"), ("is-rem", "paradoxal"),
                           ("is-night", "nuit sans phases")]


def test_the_night_cards_legend_wears_the_series_colours():
    assert ".pf-sante4 .pf-health-nights .pf-viz-legend .pf-lg.is-dot { background: rgb(var(--pf-accent-strong)); }" in CSS
    assert ".dark .pf-sante4 .pf-health-nights .pf-viz-legend .pf-lg.is-mean { border-top-color: rgb(var(--pf-accent)); }" in CSS


def test_the_recovery_axis_says_100():
    p, html = _page()
    assert [line["label"] for line in p["recup"]["lines"]] == ["100", "70", "40", "0"]
    assert ">100</text>" in html.split('id="recuperation-detail"')[1]


async def test_one_tap_keeps_its_chip_ids_and_says_the_answer(client, db_session, test_user, monkeypatch):
    from unittest.mock import AsyncMock

    from app.services import sante as sante_service

    monkeypatch.setattr(sante_service, "athlete_today", AsyncMock(return_value=D))
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    headers = {"HX-Request": "true"}
    r = await client.post("/sante/ressenti", data={"day": D.isoformat(), "feel": "good"}, headers=headers)
    assert 'id="feel-good"' in r.text and 'id="feel-ok"' in r.text and 'id="feel-bad"' in r.text
    assert '<p id="ressenti-live" hx-swap-oob="innerHTML">Ressenti enregistré : Bien</p>' in r.text
    # the group is a div inside the form (a form keeps its own role)
    assert '<form id="ressenti" class="pf-feel-form"' in r.text and 'role="group" aria-labelledby="ressenti-label"' in r.text
    assert not re.search(r'<form[^>]*role="group"', r.text)
    r = await client.post("/sante/ressenti", data={"day": D.isoformat(), "action": "clear"}, headers=headers)
    assert '<p id="ressenti-live" hx-swap-oob="innerHTML">Ressenti effacé</p>' in r.text
    _, html = _page()
    assert '<p id="ressenti-live" class="sr-only" aria-live="polite"></p>' in html


def test_connect_apple_health_is_one_run_of_text():
    page = (ROOT / "app/templates/sante.html").read_text(encoding="utf-8")
    assert '<span>Connecter <span data-phone-health>Apple Santé</span></span>' in page


# ── numbers ─────────────────────────────────────────────────────────────────

def test_a_single_taper_day_is_singular():
    rd = date(2026, 10, 20)  # a Tuesday: S0 holds one day, the Monday
    today, now = rd - timedelta(days=1), datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    d = data(rp.taper(steady(rd - timedelta(days=14), days=60) + [S(rd - timedelta(days=k), minutes=40, i=5)
                                                                   for k in range(1, 15)], route(rd), rd, today, now))
    assert d["r"][6][2].endswith(" · 1 jour d'affûtage"), d["r"][6]
    assert any("dont 1 jour d'affûtage" in a for a in d["a"])
    assert not any("1 jours" in "".join(r) for r in d["r"])


def test_fr_int_groups_like_the_week_headings():
    assert viz.fr_int(2100) == "2 100" and viz.dplus(2140) == "+2 140 m" and viz.fr_int(None) == "0"


async def test_activity_page_numbers_and_way_back(as_user, db_session, test_user):
    strength = Activity(strava_activity_id=9901, user_id=test_user.id, sport_type="WeightTraining", name="Renfo",
                        start_date=datetime.now(timezone.utc) - timedelta(days=1), distance=0, moving_time=1800,
                        elapsed_time=1800, total_elevation_gain=0, raw_data={})
    trail = Activity(strava_activity_id=9902, user_id=test_user.id, sport_type="TrailRun", name="Crêtes",
                     start_date=datetime.now(timezone.utc) - timedelta(days=2), distance=21000, moving_time=9000,
                     elapsed_time=9200, total_elevation_gain=2100, average_speed=21000 / 9000, raw_data={},
                     splits_metric=[{"distance": 1000, "moving_time": 400, "elevation_difference": d} for d in (3, -12, 0)])
    ride = Activity(strava_activity_id=9903, user_id=test_user.id, sport_type="Ride", name="Vélo",
                    start_date=datetime.now(timezone.utc) - timedelta(days=3), distance=40000, moving_time=5400,
                    elapsed_time=5400, average_speed=40000 / 5400, raw_data={},
                    laps=[{"distance": 10000, "moving_time": 1200, "average_watts": 250} for _ in range(4)])
    db_session.add_all([strength, trail, ride])
    await db_session.flush()
    page = (await as_user.get(f"/activity/{strength.id}")).text
    assert ">Distance<" not in page  # no « 0,0 km » tile for a session without a distance
    page = (await as_user.get(f"/activity/{trail.id}")).text
    tile = page.split(">D+<")[1].split("</div>")[0]
    assert "2 100" in tile and "text-red" not in tile  # grouped, in ink (red is an alert elsewhere)
    assert "Par kilomètre" in page and "Splits par" not in page
    splits = page.split("Par kilomètre")[1]
    assert "+3 m" in splits and "−12 m" in splits and "text-red" not in splits and "text-blue" not in splits
    page = (await as_user.get(f"/activity/{ride.id}")).text
    assert "26,7 km/h" in page and "250 W" in page and "text-red" not in page.split("Intervalles")[1]
    rows = (await as_user.get("/activities")).text
    assert ">+2 100<small" in rows and "+2 100 m" in rows  # desktop and phone rows, as the week heading
    # the way back: the list it was opened from (its filter kept, the app's marker dropped), else Activités
    back = (await as_user.get(f"/activity/{trail.id}", headers={"referer": "http://test/sante?app_nav=1"})).text
    assert 'href="/sante" data-back' in back and "<span data-back-label>Santé</span>" in back
    back = (await as_user.get(f"/activity/{trail.id}", headers={"referer": "http://test/activities?sport=trail&m=dplus"})).text
    assert 'href="/activities?sport=trail&amp;m=dplus" data-back' in back
    back = (await as_user.get(f"/activity/{trail.id}", headers={"referer": "https://elsewhere.example/sante"})).text
    assert 'href="/activities" data-back' in back and "<span data-back-label>Activités</span>" in back
    assert "history.back()" in back and "document.referrer" in back


async def test_a_leg_without_descent_prints_none(as_user):
    from tests.test_redesign import CPS, _route, _rows

    rid = await _route(as_user)
    t = await _rows(as_user, rid, CPS)
    assert "−0 m" not in t and "+0 m" not in t


# ── the 404 page, signed in ─────────────────────────────────────────────────

async def test_a_signed_in_404_keeps_the_app_layout_in_french(client, db_session):
    async with _logged_in(client, db_session) as (c, _user):
        for path, words in (("/activity/99999", "Cette activité n'existe plus."),
                            ("/simulator/routes/99999", "Cette course n'existe plus."),
                            ("/nulle-part", "Cette page n'existe pas, ou plus.")):
            r = await c.get(path)
            assert r.status_code == 404, path
            text = unescape(r.text)
            assert words in text and 'class="pf-tabbar"' in r.text and "Se connecter" not in text, path
            assert "not found" not in text.lower()
    r = await client.get("/nulle-part")  # a visitor: the public layout
    assert r.status_code == 404 and 'class="pf-tabbar"' not in r.text


# ── Réglages ────────────────────────────────────────────────────────────────

async def test_settings_source_label_weight_and_status_dot(as_user):
    page = (await as_user.get("/settings")).text
    assert '<option value="auto" selected>Automatique</option>' in page and "Automatique · COROS" not in page
    assert "Automatique : COROS, puis Garmin, puis Apple Santé." in page
    assert 'placeholder="72"' not in page
    desk = CSS.split("/* @desk-pages */", 1)[1]
    assert ".pf-settings .pf-section.pf-set-appearance { row-gap: 0; }" in desk
    assert ".pf-settings .pf-set-appearance > button { justify-self: start; }" in desk
    assert ".pf-pill-soft .pf-dot { background: rgb(var(--pf-gray-400)); }" in CSS


# ── Activités ───────────────────────────────────────────────────────────────

async def test_older_weeks_keep_the_focus_and_say_how_many(as_user, db_session, test_user):
    for k in (1, 50):
        db_session.add(Activity(strava_activity_id=9800 + k, user_id=test_user.id, sport_type="Run", name="Footing",
                                start_date=datetime.now(timezone.utc) - timedelta(days=k), distance=8000,
                                moving_time=2400, elapsed_time=2400, raw_data={}))
    await db_session.flush()
    page = (await as_user.get("/activities")).text
    button = page.split("data-load-more")[1].split(">")[0]
    assert 'hx-sync="this:drop"' in button and "hx-disabled-elt" not in page
    assert '<p id="load-more-live" class="sr-only" aria-live="polite"></p>' in page
    assert '<h2 class="pf-h3" tabindex="-1">' in page
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert 'setAttribute("aria-disabled", "true")' in js and "semaines ajoutées" in js and "heading.focus(" in js
    assert '@media (min-width: 900px) { .pf-activities [id^="week-"] { scroll-margin-top: 64px; } }' in CSS


def test_a_measure_switch_reserves_every_panels_readout():
    js = (ROOT / "app/static/js/pf-viz.js").read_text(encoding="utf-8")
    assert "function siblings()" in js and "(D.r || []).concat(siblings())" in js


def test_desktop_chart_text_keeps_its_size():
    js = (ROOT / "app/static/js/pf-viz.js").read_text(encoding="utf-8")
    assert '"--pf-viz-k"' in js and 'querySelectorAll("svg.pf-viz-svg").forEach(scaled)' in js
    assert ".pf-viz-svg { font-size: calc(12px / var(--pf-viz-k, 1.0909)); }" in CSS
    # from 600 px (re-audit 2026-10-31: a 768 px tablet printed its axis at 19 px, over the card titles); a phone keeps
    # its 11 units
    assert "@media (min-width: 600px) {\n    .pf-viz-svg { font-size: calc(12px / var(--pf-viz-k, 1.0909)); }" in CSS
    assert "@media (min-width: 600px) { .pf-viz-svg text.pf-feel-row { font-size: .75em; } }" in CSS
    assert ".pf-activities .pf-activity-chart { max-width: calc(560px + 34px); }" in CSS


# ── accessibility ───────────────────────────────────────────────────────────

async def test_the_gpx_field_is_named_by_its_visible_label(as_user):
    """Its two visible labels show its focus; one is always display:none (« Nouvelle course » below 1024 px: axe's
    label-title-only, re-audit 2026-10-31), so its own name holds the words of both. Never required: an empty
    required field read « invalide » before anything was done."""
    page = (await as_user.get("/simulator")).text
    field = page.split('id="gpx-file"')[1].split(">")[0]
    assert 'aria-label="Importer un GPX, nouvelle course"' in field and "required" not in field
    assert 'class="pf-btn pf-btn-primary pf-btn-sm pf-only-d cursor-pointer">' in page and "Nouvelle course</label>" in page
    assert '.pf-simulator:has(#gpx-file:focus-visible) label[for="gpx-file"] { outline: 2px solid' in CSS


async def test_passage_rows_name_their_alternative_times(as_user):
    from tests.test_redesign import CPS, _route, _rows

    rid = await _route(as_user)
    t = await _rows(as_user, rid, CPS)
    head = t.split('class="pf-prow-head"')[1].split("</svg>")[0]
    assert '<span class="sr-only">optimiste </span><span data-fast>' in head
    assert '<span class="sr-only">sécurité </span><span data-safe' in head


async def test_plan_sheets_menu_profile_and_objective(as_user):
    from tests.test_redesign import _route

    rid = await _route(as_user)
    html = (await as_user.get(f"/simulator/routes/{rid}")).text
    menu = html.split('id="plan-more"')[1].split("</details>")[0]
    assert "role=" not in menu.split("</summary>")[1]
    assert "button, [href], input, select, textarea, summary, [tabindex]" in html  # the sheet's trap knows « Autres formats »
    assert "function openerOf(el)" in html  # back to « … » when its menu item is gone
    assert 'aria-label="Modifier l&#39;objectif : 5h00"' in html or "aria-label=\"Modifier l'objectif : 5h00\"" in html
    assert "'Modifier l\\'objectif : ' + fmtHM(total)" in html
    assert "role: 'img'" in html and "Profil d’altitude : " in html


def test_inputs_tabs_and_segments_carry_more_than_hue():
    assert ".pf-input { width: 100%; min-height: 48px; padding: 0 16px; border-radius: 14px; border: 1px solid rgb(var(--pf-gray-400));" in CSS
    assert ".pf-input:focus, .pf-input:focus-visible { outline: 2px solid rgb(var(--pf-accent));" in CSS
    assert ".pf-hero-in:focus { outline: none; }" not in CSS
    # the objective's fields: the ring after Tab, not after a tap (re-audit 2026-10-31; a text field matches
    # :focus-visible on a tap too, app.js marks the pointer)
    assert ".pf-hero-in:focus-visible { outline: 2px solid rgb(var(--pf-accent));" in CSS
    assert "[data-pf-pointer] .pf-hero-in:focus-visible { outline: none; }" in CSS
    app_js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert 'setAttribute("data-pf-pointer", "")' in app_js and 'event.key === "Tab"' in app_js
    assert '.pf-tabbar a[aria-current="page"] { color: rgb(var(--pf-accent)); font-weight: 700; }' in CSS
    assert '.pf-seg > [aria-pressed="true"], .pf-seg > [aria-current] { background: rgb(var(--pf-surface)); color: rgb(var(--pf-ink)); box-shadow: inset 0 0 0 2px rgb(var(--pf-ink)); }' in CSS


def test_current_places_and_large_text_hold_more_than_hue():
    """Re-audit 2026-10-31: the desktop tab is bold (its pill alone is 1.09:1); at 200 % text the sport filter no longer
    clips the ring of « Tout », an activity's tiles go to one column rather than letting « 5:24/km » spill out."""
    assert '.pf-nav a[aria-current="page"] { color: rgb(var(--pf-ink)); background: rgb(var(--pf-soft)); font-weight: 700; }' in CSS
    assert ".pf-large-text .pf-filters { overflow: visible; border-radius: 24px; }" in CSS
    assert ".pf-activity-metrics { grid-template-columns: repeat(auto-fit, minmax(max(8em, calc((100% - 0.75rem) / 2 - 1px)), 1fr)); }" in CSS
    assert ".pf-activity-metrics p { overflow-wrap: anywhere; }" in CSS
    detail = (ROOT / "app/templates/activity_detail.html").read_text(encoding="utf-8")
    assert '<div class="pf-activity-metrics grid grid-cols-2 md:grid-cols-4 gap-3">' in detail


def test_the_strava_setup_says_its_step():
    """Re-audit 2026-10-31: /setup in a <main>, the shown step aria-current="step" and said « Étape N sur 3 », a step not
    reached yet at 5.3:1 (gray-500 on gray-100, was gray-400: 4.3:1)."""
    setup = (ROOT / "app/templates/setup.html").read_text(encoding="utf-8")
    assert '<main id="main-content" tabindex="-1" class="min-h-screen bg-white" x-data="{ step: 1 }">' in setup
    assert setup.count("<main") == 1 and "</main>\n{% endblock content_public %}" in setup
    assert ''':aria-current="step === s ? 'step' : null"''' in setup and '''x-text="'Étape ' + s + ' sur 3'"''' in setup
    assert "'bg-gray-100 text-gray-500'" in setup and "text-gray-400'" not in setup


def test_danger_buttons_keep_their_contrast_in_the_dark():
    """Re-audit 2026-10-31: white on the dark theme's light red (#f88074) was 2.52:1; the page's dark ink is 7.6:1."""
    assert ".dark .bg-danger.text-white { color: rgb(var(--pf-bg)); }" in CSS
    theme = (ROOT / "app/static/css/theme.css").read_text(encoding="utf-8")
    assert "--pf-danger: 248 128 116;" in theme and "--pf-bg: 14 14 16;" in theme


def test_tab_changes_move_the_focus_and_say_loading():
    js = (ROOT / "app/static/js/tab-navigation.js").read_text(encoding="utf-8")
    assert "next.focus({ preventScroll: true })" in js and "'Chargement…'" in js and "aria-label', 'Chargement'" not in js
    assert "#main-content:focus { outline: none; }" in CSS


def test_a_replaced_page_keeps_the_focus_where_it_was():
    """Re-audit 2026-10-31: a pull refresh or a tab's quiet revalidation replaced #main-content and dropped the focus to
    <body>: it comes back on the same id in the new page (a feel chip), else on its main."""
    js = (ROOT / "app/static/js/refresh.js").read_text(encoding="utf-8")
    assert "hadFocus = !!active && old.contains(active), focusId = hadFocus ? active.id : ''" in js
    assert "if (!target || !next.contains(target)) target = next;" in js
    assert js.index("target.focus({ preventScroll: true })") > js.index("document.body.insertBefore(next, nav || null)")


def test_the_refresh_region_exists_before_it_speaks():
    js = (ROOT / "app/static/js/refresh.js").read_text(encoding="utf-8")
    assert "function liveRegion()" in js and "\n  liveRegion();" in js and ", 6000);" in js


def test_ranges_macro_keeps_the_form_free_of_roles():
    form = render("{{ v.viz_ranges([('a', 'A'), ('b', 'B')], 'a', action='/x', param='m', hidden={'sport': 'trail'}) }}")
    assert form.startswith('<form class="pf-viz-ranges-form" method="get" action="/x"><input type="hidden" name="sport" value="trail">')
    assert ".pf-viz-ranges-form { display: contents; }" in CSS
