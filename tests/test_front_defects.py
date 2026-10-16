"""Front-end defects fixed after the October 2026 UX audit. The browser suite
(tests/browser/iphone-ux.cjs) measures them live; these pin the files."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app/templates"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_the_phone_input_rule_spares_the_objective_fields_and_the_number_is_bounded_by_the_screen():
    css = _read("app/static/css/interface.css")
    assert ".pf-input, .sim-num:not(.pf-hero-in), select, textarea { font-size: max(16px, 1rem); min-height: 44px; }" in css
    assert ".pf-hero-num { font-size: clamp(2.5rem, 25vw, 5rem); }" in css


def test_passage_rows_measure_their_columns_in_rem_and_stack_the_clock_under_large_text():
    css = _read("app/static/css/interface.css")
    assert "68px minmax" not in css and "84px minmax" not in css
    assert ".pf-prow-head { display: grid; grid-template-columns: 4.25rem minmax(0, 1fr) 1.125rem;" in css
    assert ".pf-pass.has-alt .pf-prow-head { grid-template-columns: 4.25rem minmax(0, 1fr) auto 1.125rem; }" in css
    assert 'grid-template-areas: "clk alt chev" "main main chev"' in css


def test_failed_requests_say_so_in_the_toast_and_the_plan_page_error_is_plain():
    js = _read("app/static/js/app.js")
    assert "htmx:responseError" in js and "htmx:sendError" in js and "Impossible de charger. Réessaie." in js
    assert "window.pfConfirm = pfConfirm" in js and "window.pfToast = pfToast" in js
    assert r"/\bevery\b/" in js and "xhr.status === 404" in js and "lastFailedToast" in js  # not for polls, stop-polling 404s or repeats
    assert re.search(r"\.pf-toast \{[^}]*pointer-events: none;", _read("app/static/css/interface.css"))  # a status: taps go through it
    plan = _read("app/templates/partials/gpx_result.html")
    assert "function toast(msg) { if (window.pfToast) window.pfToast(msg); }" in plan  # one element, one timer
    assert "Un problème est survenu sur cette page. Recharge-la." in plan and "Erreur dans la page" not in plan
    for tpl in ("settings.html", "partials/strava_settings.html", "partials/coros_settings.html", "partials/garmin_settings.html"):
        assert re.search(r"pfConfirm\(msg, function \(\) \{ f\.submit\(\); \}, '[^']+'\)", _read(f"app/templates/{tpl}")), tpl


def test_check_email_actions_render_as_buttons():
    html = _read("app/templates/auth/check_email.html")
    assert html.count("pf-btn-secondary") == 2 and 'class="pf-btn"' not in html and 'class="pf-btn mt-4"' not in html


def test_phone_keyboards_match_the_fields():
    assert 'id="weight_kg" name="weight_kg" inputmode="decimal"' in _read("app/templates/settings.html")
    plan = _read("app/templates/partials/gpx_result.html")
    for field in ('id="target-h" inputmode="numeric"', 'id="target-m" inputmode="numeric"', 'id="stop-min" inputmode="numeric"'):
        assert field in plan, field
    assert plan.count('inputmode="numeric" aria-label="Optimiste, % plus rapide"') == 1
    assert plan.count('inputmode="numeric" aria-label="Sécurité, % plus lent"') == 1


# charts and profiles are drawn to their container's width: they are not icons
DRAWN_TO_THE_CONTAINER = ("pf-viz-svg", "pf-tl", "pf-next-pf", "w-full", 'viewBox="0 0 100 100"', 'viewBox="0 0 332 205"')


def test_decorative_icons_are_hidden_from_readers_and_inline_icons_carry_a_size():
    unhidden, unsized = [], []
    for tpl in TEMPLATES.rglob("*.html"):
        rel = tpl.relative_to(TEMPLATES).as_posix()
        text = tpl.read_text(encoding="utf-8")
        unhidden += [f"{rel}: {tag}" for tag in re.findall(r"<i data-lucide=[^>]*>", text) if "aria-hidden" not in tag]
        if rel.startswith("partials/garmin_"):
            continue
        for tag in re.findall(r"<svg\b[^>]*>", text):
            if not re.search(r"\swidth=", tag) and not any(k in tag for k in DRAWN_TO_THE_CONTAINER):
                unsized.append(f"{rel}: {tag[:80]}")
    assert not unhidden, unhidden
    assert not unsized, unsized


def test_the_service_worker_precaches_nothing_stale():
    sw = _read("app/static/sw.js")
    assert "interface.css?v=2" not in sw and "addAll" not in sw and "STATIC_ASSETS" not in sw
    assert "navigator.serviceWorker.register('/static/sw.js').then((r) => r.update())" in _read("app/templates/base.html")
