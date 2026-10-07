"""The chart kit: French formats (the house duration everywhere), geometry
builders on the owner's real nights, the Jinja macros' accessible structure,
and the CSS/JS rules (contrast of marks and selected states in both themes,
motion ≤ 300 ms under no-preference only, no colour or number formatting in JS)."""
import json
import pathlib
import re
from datetime import date, datetime, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services import nights as nt
from app.services import sante_today, viz
from tests.test_nights import RACE, owner_rows

ROOT = pathlib.Path(__file__).resolve().parents[1]
D = date(2026, 10, 7)
NN = " "


def _env():
    return Environment(loader=FileSystemLoader(str(ROOT / "app" / "templates")), autoescape=select_autoescape())


def render(src: str, **ctx) -> str:
    return _env().from_string('{% import "partials/_viz.html" as v %}' + src).render(**ctx)


def data_of(html: str) -> dict:
    m = re.search(r'<script type="application/json" class="pf-viz-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


# ── formats ─────────────────────────────────────────────────────────────────

def test_one_duration_format_everywhere():
    assert (viz.hm(350), viz.hm(140), viz.hm(490), viz.hm(45), viz.hm(60)) == ("5h50", "2h20", "8h10", "45 min",
                                                                                  "1h00")
    assert sante_today.hm is viz.hm  # Activités and the race page print durations through the same function
    owner = viz.sleep_readout(350, 140)
    assert owner == f"Nuit 5h50 · sieste 2h20 · 8h10 sur 24{NN}h"
    assert owner.replace(NN, " ") == "Nuit 5h50 · sieste 2h20 · 8h10 sur 24 h"  # the 7 Oct readout
    assert viz.sleep_readout(440, 0) == f"Nuit 7h20 sur 24{NN}h"
    assert viz.sleep_readout(None, 82) == "Sieste 1h22 · nuit incomplète ?"
    assert viz.sleep_spoken(350, 140) == "nuit 5 heures 50, sieste 2 heures 20, 8 heures 10 sur 24 heures"
    assert (viz.hm_long(65), viz.hm_long(1), viz.hm_long(45)) == ("1 heure 05", "1 minute", "45 minutes")


def test_numbers_dates_clocks():
    assert viz.num(-3, unit="bpm") == f"−3{NN}bpm" and viz.num(-0.2) == "0"
    assert viz.num(58.44, 1, "ms") == f"58,4{NN}ms" and viz.signed(3, unit="bpm") == f"+3{NN}bpm"
    assert viz.signed(-3) == "−3" and viz.signed(0) == "0"
    assert viz.d_short(date(2026, 10, 6)) == "mar. 6 oct." and viz.d_long(date(2026, 10, 6)) == "mardi 6 octobre"
    assert viz.night_label(D) == "nuit du mar. 6 au mer. 7"
    assert viz.clock(datetime(2026, 10, 7, 5, 38)) == "05:40" and viz.clock(datetime(2026, 10, 6, 23, 35)) == "23:35"
    assert viz.clock(datetime(2026, 10, 7, 5, 38), five=False) == "05:38"


def test_helpers():
    assert viz.paths([0, 10, 20, 30], [5, None, 7, 8]) == "M0.0 5.0 M20.0 7.0 L30.0 8.0"  # broken at the gap
    assert viz.band_polys([0, 10, 20], [9, 9, None], [1, 1, None]) == ["0.0,1.0 10.0,1.0 10.0,9.0 0.0,9.0"]
    lo, hi = viz.span_of([50, 51], 8)
    assert hi - lo >= 8  # 1 bpm must not look like a cliff
    assert viz.nice_ticks(41.2, 55.3) == [45, 50, 55] and viz.slot_x(4) == [36.0, 108.0, 180.0, 252.0]
    assert viz.rolling([60, None, 64, 66], 3) == (60 + 64 + 66) / 3 and viz.rolling([60, None, 64], 2) is None


# ── builders ────────────────────────────────────────────────────────────────

def test_tile_end_dot_is_the_printed_value():
    means = [None, None, 46, 46.5, 47, None, 48]
    t = viz.tile("FC de nuit · 7 nuits", "48", "bpm", means, (43, 47), word="au-dessus", glyph="▲", tone="warn",
                 href="/sante?vue=sommeil#coeur", aria="FC de nuit sur 7 nuits : 48 battements par minute")
    assert t["path"].startswith("M") and t["end"]["x"] == 106 and t["band"]["h"] > 0
    ys = [float(p.split()[1]) for p in t["path"].split("M")[1:] for p in [p]]
    assert t["end"]["y"] == min(float(seg.split()[-1]) for seg in t["path"].replace("L", "M").split("M")[1:])
    sparse = viz.tile("VFC · 7 nuits", None, "ms", [None] * 14, None, gap="2 nuits sur 7 · il en faut 3")
    assert sparse["path"] is None and sparse["gap"] == "2 nuits sur 7 · il en faut 3"
    assert ys  # the line exists


def test_band_chart_prints_per_night_values_and_normal_only():
    days = [D - timedelta(days=13 - i) for i in range(14)]
    hrv = [None if i in (3, 9) else 60 + i % 3 for i in range(14)]
    hrv[-1] = 50.0
    hr = [None if i in (3, 9) else 45 + i % 2 for i in range(14)]
    mean = [viz.rolling(hrv, i, log=True) for i in range(14)]
    c = viz.band_chart("coeur", days, [
        {"name": "VFC", "unit": "ms", "unit_long": "millisecondes", "values": hrv, "band": [(55, 65)] * 14,
         "mean": mean, "min_span": 20},
        {"name": "FC", "unit": "bpm", "unit_long": "battements par minute", "values": hr, "band": [(43, 49)] * 14,
         "min_span": 8}], races=[(days[5], "Trail des Glaciers")], tags={days[-1]: ["alcool"]},
        title="Cœur la nuit")
    data = json.loads(c["data"])
    assert len(data["x"]) == 14 and len(data["y"]) == 2 and data["d"][-1] == D.isoformat() and data["sel"] == 13
    assert c["read"] == ["nuit du mar. 6 au mer. 7", f"VFC 50{NN}ms en dessous · FC 46{NN}bpm",
                         "normale VFC 55–65, FC 43–49 · ◇ alcool"]
    assert data["r"][3][1] == "—" and "pas de montre cette nuit" in data["r"][3][2]
    assert "⚑ Trail des Glaciers" in data["r"][5][2] and c["races"][0]["name"] == "Trail des Glaciers"
    assert data["a"][-1].startswith("mercredi 7 octobre : VFC 50 millisecondes en dessous, ta normale 55 à 65")
    printed = json.dumps(data["r"])
    for m in mean:  # the 7-night mean is drawn, never printed
        if m is not None and m != int(m):
            assert viz.num(m, 1) not in printed
    out = [d for d in c["panels"][0]["dots"] if d["out"]]
    assert [d["i"] for d in out] == [13] and out[0]["out"] == "down"
    assert c["summary"] == "Cœur la nuit, 14 nuits : 12 mesurées"


def test_nights_chart_on_the_owner():
    nights = nt.build_nights(owner_rows(), D)
    nt.tag_nights(nights, (), [RACE], {})
    days = [D - timedelta(days=13 - i) for i in range(14)]
    tags = {d: [nt.TAG_WORDS[t] for t in sorted(n.tags)] for d, n in nights.items() if n.tags}
    c = viz.nights_chart(days, nights, races=[(RACE[0], RACE[1])], tags=tags, link="#hyp")
    data = json.loads(c["data"])
    col = c["cols"][-1]
    assert col["night"] and col["nap"] and not col["short"]  # 5h50 solid + 2h20 hatched on one bar
    assert col["nap"]["y"] < col["night"]["y"]  # stacked on top of the night
    assert len(col["nap_segs"]) == 1 and not col["nap_edges"]  # the 06:42 → 09:07 nap sits in the axis
    seg, win = col["nap_segs"][0], col["win"]
    assert seg["y"] > win["y"] + win["h"]  # after the wake, with its 64-min gap
    assert c["read"] == ["nuit du mar. 6 au mer. 7", f"Nuit 5h50 · sieste 2h20 · 8h10 sur 24{NN}h",
                         "23:35 → 05:40 · sieste 06:40 → 09:05 · ◇ autour de la course"]  # times to 5 min
    assert data["link"] == "#hyp"
    short = c["cols"][days.index(date(2026, 10, 6))]
    assert short["short"]  # 5h33 with no nap: under 6 h, drawn as an outlined bar
    lone = c["cols"][days.index(date(2026, 9, 25))]
    assert "night" not in lone and lone["nap"] and data["r"][days.index(date(2026, 9, 25))][1] == \
        "Sieste 1h22 · nuit incomplète ?"
    assert c["cols"][days.index(date(2026, 10, 3))].get("miss")  # the race night: no watch, a gap, never zero
    hours = [h["label"] for h in c["hours"]]
    assert hours == ["00:00", "02:00", "04:00", "06:00", "08:00", "10:00"]  # 23:00 → 11:00 fits windows and naps
    assert seg["y"] + seg["h"] <= c["timing"][1]
    assert "moins de 6 heures sur 24 heures" in data["a"][days.index(date(2026, 10, 6))]


def test_hypnogram_only_from_real_intervals():
    start, end = datetime(2026, 10, 4, 23, 0), datetime(2026, 10, 5, 6, 30)
    assert viz.hypnogram(None, start, end) is None and viz.hypnogram([], start, end) is None
    h = viz.hypnogram([("core", start, start + timedelta(minutes=90)),
                       ("deep", start + timedelta(minutes=90), start + timedelta(minutes=180)),
                       ("awake", start + timedelta(minutes=180), end)], start, end)
    assert [n for n, _ in h["lanes"]] == ["Éveil", "REM", "Léger", "Profond"] and len(h["steps"]) == 2
    assert "bed" not in h and "wake" not in h  # no end labels: the times are in the readout


def test_bars_lines_dots():
    weeks = [D - timedelta(weeks=11 - i) for i in range(12)]
    hours = [6.5, 7.0, None, 8.2, 7.5, 6.8, 9.1, 7.7, 8.0, 3.0, 2.5, 1.0]
    c = viz.bars(weeks, hours, readouts=[["sem.", viz.hm(h * 60) if h else "—", ""] for h in hours],
                 arias=["x"] * 12, links=[{"href": f"#week-{w}", "label": "Voir la semaine ›"} for w in weeks],
                 secondary=[400, 0, None, 900] + [300] * 8, planned=[None] * 9 + [5, 4, 3], target=(2.8, 4.2))
    assert c["bars"][2].get("h") is None and c["bars"][9]["plan"]["h"] > 0 and c["target"]["h"] > 0
    assert len(c["secondary"]) == 10 and json.loads(c["data"])["h"][0]["href"] == f"#week-{weeks[0]}"
    days = [D - timedelta(days=89 - i) for i in range(90)]
    ln = viz.lines(days, [{"name": "fond", "values": [40 + i / 10 for i in range(90)]},
                          {"name": "fatigue", "values": [35 + (i % 9) for i in range(90)], "cls": "is-soft"}])
    assert json.loads(ln["data"])["r"][-1] == [viz.d_short(D), "", ""]  # the date only: no ratio, no %
    assert ln["series"][0]["end"]["x"] == viz.slot_x(90)[-1]
    dt = viz.dots(days, [{"day": days[10], "value": 141, "href": "/activity/1"},
                         {"day": days[40], "value": 146, "hot": True, "href": "/activity/2"}])
    d = json.loads(dt["data"])
    assert d["h"] == ["/activity/1", "/activity/2"] and d["r"][1][2] == " · journée chaude"
    assert [x["hot"] for x in dt["dots"]] == [False, True]


# ── macros ──────────────────────────────────────────────────────────────────

def test_every_scrubbable_figure_has_steps_range_live_and_readout():
    days = [D - timedelta(days=13 - i) for i in range(14)]
    nights = nt.build_nights(owner_rows(), D)
    figs = {
        "nights": ("{{ v.viz_nights(c, 'Tes nuits', 'nuits', 'sommeil') }}", viz.nights_chart(days, nights)),
        "band": ("{{ v.viz_band(c, 'Cœur la nuit', 'sommeil') }}", viz.band_chart("coeur", days, [
            {"name": "VFC", "unit": "ms", "unit_long": "millisecondes", "values": [60.0] * 14, "band": [None] * 14}])),
        "bars": ("{{ v.viz_bars(c, 'Semaines', 'semaines') }}", viz.bars(
            days, [1.0] * 14, readouts=[["a", "b", "c"]] * 14, arias=["a"] * 14)),
        "lines": ("{{ v.viz_lines(c, 'Fond et fatigue', 'fond') }}", viz.lines(days, [{"name": "fond",
                                                                                    "values": [1.0] * 14}])),
        "dots": ("{{ v.viz_dots(c, 'FC en footing', 'fc') }}", viz.dots(days, [{"day": days[3], "value": 140}])),
    }
    for name, (src, c) in figs.items():
        html = render(src, c=c)
        assert html.count('class="pf-viz-step"') == 2 and 'data-step="-1"' in html and 'data-step="1"' in html, name
        assert html.count('class="pf-viz-range sr-only"') == 1 and "aria-valuetext=" in html, name
        assert 'aria-live="polite" data-live' in html and '<svg class="pf-viz-svg"' in html, name
        assert 'aria-hidden="true" focusable="false"' in html, name
        assert f"<b data-r>{c['read'][1]}</b>".replace("'", "&#39;") in html or c["read"][1] in html, name
        assert data_of(html)["sel"] == c["sel"], name
        assert "<a " not in html.split('<svg class="pf-viz-svg"')[1].split("</svg>")[0], name  # links via the readout
    html = render("{{ v.viz_nights(c, 'Tes nuits', 'nuits', 'sommeil') }}", c=figs["nights"][1])
    assert 'data-viz-group="sommeil"' in html and 'fill="url(#pf-hatch-nuits)"' in html
    assert "Nuit 5h50 · sieste 2h20 · 8h10 sur 24" in html


def test_json_cannot_close_the_script_tag():
    days = [D]
    c = viz.band_chart("x", days, [{"name": "</script><b>", "unit": "ms", "unit_long": "ms", "values": [60.0],
                                    "band": [None]}])
    html = render("{{ v.viz_band(c, 't') }}", c=c)
    assert html.count("</script>") == 1 and data_of(html)["r"][0][1].startswith("</script><b>")


def test_tile_and_ranges_macros():
    t = viz.tile("VFC · 7 nuits", "58", "ms", [60, 59, 58], (55, 65), word="dans ta normale", glyph="●",
                 href="/sante?vue=sommeil#coeur", aria="VFC sur 7 nuits : 58 millisecondes, dans ta normale", driver=True)
    html = render("{{ v.viz_tile(t) }}", t=t)
    assert 'class="pf-tile is-driver"' in html and "● dans ta normale" in html and 'aria-hidden="true"' in html
    assert "55" not in re.sub(r"<svg.*</svg>", "", html, flags=re.S).replace("aria-label", "")  # no band edge printed
    html = render("{{ v.viz_ranges([('14', '14 nuits'), ('90', '3 mois')], '14') }}")
    assert 'data-viz-ranges role="group"' in html and html.count('aria-pressed="true"') == 1


# ── CSS and JS rules ────────────────────────────────────────────────────────

def _tokens(css: str, block: str) -> dict[str, tuple]:
    body = re.search(re.escape(block) + r"\s*\{(.*?)\}", css, re.S).group(1)
    return {k: tuple(int(x) for x in v.split()) for k, v in re.findall(r"--(pf-[\w-]+):\s*(\d+ \d+ \d+)", body)}


def _lum(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_marks_and_selected_states_reach_3_to_1_in_both_themes():
    theme = (ROOT / "app/static/css/theme.css").read_text()
    iface = (ROOT / "app/static/css/interface.css").read_text()
    light, dark = _tokens(theme, ":root"), {**_tokens(theme, ":root"), **_tokens(theme, ".dark")}
    dark_mark = tuple(int(x) for x in re.search(r"\.dark \{ --pf-viz-mark: (\d+ \d+ \d+); \}", iface).group(1).split())
    for name, t, mark in (("light", light, light["pf-gray-400"]), ("dark", dark, dark_mark)):
        assert contrast(mark, t["pf-bg"]) >= 3, name  # every mark on the page
        assert contrast(t["pf-ink"], mark) >= 3, name  # the selected mark against the others
        assert contrast(t["pf-ink"], t["pf-bg"]) >= 3, name  # the selected mark, the crosshair
        assert contrast(t["pf-gray-400"], t["pf-bg"]) >= 3, name  # the band's edges
        assert contrast(t["pf-ink"], t["pf-soft"]) >= 3, name  # the pressed range button's ring on its track
        assert contrast(t["pf-gray-500"], t["pf-bg"]) >= 4.5, name  # axis and readout text


def test_motion_is_short_and_only_under_no_preference():
    css = (ROOT / "app/static/css/interface.css").read_text()
    section = css[css.index("/* ── viz: the chart kit"):]
    for dur in re.findall(r"(\d*\.?\d+)(ms|s)\b", section):
        v = float(dur[0]) / (1000 if dur[1] == "ms" else 1)
        assert v <= 0.3, dur
    outside = re.sub(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\n\}", "", section, flags=re.S)
    assert "transition" not in outside and "animation:" not in outside
    assert "@media (forced-colors: active)" in section and "Highlight" in section and "CanvasText" in section


def test_js_moves_the_selection_only():
    js = (ROOT / "app/static/js/pf-viz.js").read_text()
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", js)  # no colour
    assert "Intl" not in js and "toLocale" not in js and "toFixed" not in js  # no number formatting
    for needed in ('"pfviz:sel"', "[data-step]", "[data-r-href]", "aria-valuetext", "PageUp", "Escape",
                   "toggleAttribute", "htmx:afterSettle", "replaceState", "prefers-reduced-motion"):
        assert needed in js, needed


# ── review fixes (v3): nothing moves under the finger, focus never lost ─────

def _bars_with_links(sel_link: bool):
    days = [D - timedelta(days=7 * (3 - i)) for i in range(4)]
    links = [{"href": f"#w{i}", "label": "Voir la semaine ›"} if i % 2 == 0 else None for i in range(4)]
    return viz.bars(days, [60.0] * 4, readouts=[["a", "b", "c"]] * 4, arias=["a"] * 4, links=links,
                    sel=2 if sel_link else 1)


def test_the_link_row_is_reserved_never_inserted():
    """UX1: a figure with links keeps the link row whatever is selected
    (visibility, not display); the default slot's link is printed by the server."""
    html = render("{{ v.viz_bars(c, 'Semaines', 'semaines') }}", c=_bars_with_links(False))
    a = re.search(r"<a [^>]*data-r-href[^>]*>[^<]*</a>", html).group(0)
    assert "is-off" in a and 'aria-hidden="true"' in a and 'tabindex="-1"' in a and " hidden" not in a
    html = render("{{ v.viz_bars(c, 'Semaines', 'semaines') }}", c=_bars_with_links(True))
    a = re.search(r"<a [^>]*data-r-href[^>]*>[^<]*</a>", html).group(0)
    assert 'href="#w2"' in a and "Voir la semaine ›" in a and "is-off" not in a and "aria-hidden" not in a
    days = [D]
    plain = viz.bars(days, [1.0], readouts=[["a", "b", "c"]], arias=["a"])
    assert "data-r-href" not in render("{{ v.viz_bars(c, 'x', 'x') }}", c=plain)  # no link anywhere: no row
    css = (ROOT / "app/static/css/interface.css").read_text()
    assert ".pf-viz-link.is-off { visibility: hidden; }" in css
    js = (ROOT / "app/static/js/pf-viz.js").read_text()
    assert 'classList.toggle("is-off"' in js and 'hrefEl.toggleAttribute("hidden"' not in js


def test_the_readout_and_the_hypnogram_reserve_their_height():
    """UX2: pf-viz.js sets the readout's min-height to its tallest line; the
    hypnogram sits in a box of its own height that also holds « Pas de forme »."""
    js = (ROOT / "app/static/js/pf-viz.js").read_text()
    assert "function reserve()" in js and "readBox.style.minHeight" in js and "ResizeObserver" in js
    css = (ROOT / "app/static/css/interface.css").read_text()
    assert ".pf-hyp-box { max-width: 560px; aspect-ratio: 320 / 72;" in css
    tpl = (ROOT / "app/templates/partials/sante_sleep_range.html").read_text()
    box = tpl[tpl.index('<div class="pf-hyp-box">'):]
    box = box[:box.index("</div>")]
    assert "viz_hyp" in box and 'data-night="none"' in box
    assert viz.hypnogram([("core", datetime(2026, 10, 7, 0), datetime(2026, 10, 7, 1))], datetime(2026, 10, 7),
                         datetime(2026, 10, 7, 6))["H"] == 72 and viz.W == 320


def test_a_touch_selects_on_a_tap_or_a_sideways_drag_only():
    """UX3: a touch selects nothing on pointerdown; a vertical swipe (the
    browser's pan-y, then pointercancel) leaves the selection alone."""
    js = (ROOT / "app/static/js/pf-viz.js").read_text()
    down = js[js.index('svg.addEventListener("pointerdown"'):js.index('svg.addEventListener("pointermove"')]
    assert 'e.pointerType === "touch"' in down and "return;" in down.split('"touch"')[1].split("\n")[0]
    move = js[js.index('svg.addEventListener("pointermove"'):js.index('svg.addEventListener("pointerup"')]
    assert "dx <= SLOP || dx <= dy" in move and "var SLOP = 8" in js
    cancel = js[js.index('svg.addEventListener("pointercancel"'):]
    assert "select(" not in cancel.split("\n")[0]


def test_steps_at_either_end_keep_their_focus():
    """UX5: aria-disabled, never the native disabled that drops focus to <body>."""
    days = [D - timedelta(days=13 - i) for i in range(14)]
    html = render("{{ v.viz_bars(c, 'Semaines', 'semaines') }}", c=viz.bars(
        days, [1.0] * 14, readouts=[["a", "b", "c"]] * 14, arias=["a"] * 14))
    assert " disabled" not in html.replace('aria-disabled', '') and html.count('aria-disabled="true"') == 1
    js = (ROOT / "app/static/js/pf-viz.js").read_text()
    assert ".disabled =" not in js and 'setAttribute("aria-disabled"' in js
    assert 'getAttribute("aria-disabled") === "true") return' in js
    css = (ROOT / "app/static/css/interface.css").read_text()
    assert '.pf-viz-step[aria-disabled="true"]' in css


def _split_top(text: str, sep: str = ",") -> list[str]:
    """Split on `sep` outside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == sep and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return out + [cur.strip()]


def _specificity(sel: str) -> tuple[int, int, int]:
    """CSS specificity (ids, classes/attributes/pseudo-classes, types); :is()
    and :not() count their most specific argument."""
    total, i, base = [0, 0, 0], 0, ""
    while i < len(sel):
        m = re.match(r":(is|not)\(", sel[i:])
        if m:
            depth, j = 1, i + len(m.group(0))
            while depth:
                depth += sel[j] == "("
                depth -= sel[j] == ")"
                j += 1
            best = max(_specificity(a) for a in _split_top(sel[i + len(m.group(0)):j - 1]))
            total = [a + b for a, b in zip(total, best)]
            i = j
        else:
            base += sel[i]
            i += 1
    total[0] += base.count("#")
    total[1] += len(re.findall(r"\.[\w-]+|\[[^\]]+\]|:[\w-]+", base))
    total[2] += len(re.findall(r"(?:^|[\s>+~])([a-z][\w-]*)", base))
    return tuple(total)


def test_the_selection_shows_in_forced_colours_and_on_a_short_night():
    """UX6: the forced-colors selected rule is at least as specific as the
    normal-mode one it must beat; a selected < 6 h night is filled."""
    css = (ROOT / "app/static/css/interface.css").read_text()
    normal = ".pf-viz-col.is-sel .pf-viz-night:not(.is-short)"
    fc = css[css.index("@media (forced-colors: active)"):]
    rule = next(ln for ln in fc.splitlines() if "fill: Highlight" in ln and ".pf-viz-col.is-sel" in ln)
    sel = next(s for s in _split_top(rule.split("{")[0]) if "pf-viz-col.is-sel" in s)
    assert _specificity(sel) >= _specificity(normal) == (0, 4, 0), (sel, _specificity(sel))
    assert ".pf-viz-col.is-sel .pf-viz-night.is-short { fill: rgb(var(--pf-ink)); }" in css


def test_the_selected_tab_and_the_pressed_answers_show_in_forced_colours():
    """UX7."""
    css = (ROOT / "app/static/css/interface.css").read_text()
    fc = css[css.index("@media (forced-colors: active)"):]
    assert '.pf-stab[aria-selected="true"] { border-bottom-color: Highlight; }' in fc
    assert ".pf-stab { border-bottom-color: Canvas; }" in fc
    assert '.pf-chip[aria-pressed="true"] { forced-color-adjust: none; background: Highlight;' in fc


def test_a_race_name_never_runs_past_the_plot():
    """UX8: a race in the right part of a chart has its name end at its flag;
    every label's extent (≈ 6.2 px a character at 11 px) stays inside the viewBox."""
    days = [D - timedelta(days=89 - i) for i in range(90)]
    for k in (2, 5, 30, 80):
        rd = D - timedelta(days=k)
        c = viz.band_chart("coeur", days, [{"name": "FC", "unit": "bpm", "unit_long": "bpm", "values": [45.0] * 90,
                                            "band": [None] * 90}], races=[(rd, "10 km de la Saint-Michel")])
        html = render("{{ v.viz_band(c, 'Cœur la nuit') }}", c=c)
        m = re.search(r'<text (x="([\d.]+)"(?: text-anchor="end")?|x="([\d.]+)") y="[\d.]+" class="pf-viz-strong">([^<]+)'
                      r'</text>', html)
        label = m.group(4)
        x = float(re.search(r'x="([\d.]+)"', m.group(1)).group(1))
        width = len(label) * 6.2
        lo, hi = (x - width, x) if "text-anchor" in m.group(1) else (x, x + width)
        assert lo >= 0 and hi <= viz.W, (k, lo, hi)


def test_bars_never_collapse_after_being_seen_whole():
    """UX14: under no-preference, bars wait drawn small while pf-viz.js has set
    up their figure off screen; a figure already on screen stays as painted."""
    css = (ROOT / "app/static/css/interface.css").read_text()
    motion = css[css.index("@media (prefers-reduced-motion: no-preference)"):]
    assert ".pf-viz-on:not(.pf-viz-in) :is(.pf-viz-night, .pf-viz-bar) { transform: scaleY(.3); opacity: .3;" in motion
    assert ".pf-viz-in:not(.pf-viz-still) :is(.pf-viz-night, .pf-viz-bar) { animation:" in motion
    js = (ROOT / "app/static/js/pf-viz.js").read_text()
    assert 'fig.classList.add("pf-viz-in", "pf-viz-still")' in js and "threshold: 0 }" in js


def test_the_small_targets_reach_44_px():
    """UX15: « Synchroniser maintenant » and the method fold's references."""
    css = (ROOT / "app/static/css/interface.css").read_text()
    assert ".pf-sante-sync > button { min-height: 44px; }" in css
    refs = re.search(r"\.pf-refs a \{ display: inline-block;[^}]*\}", css).group(0)
    assert "min-height: 44px" in refs and "white-space: nowrap" in refs


def test_focus_follows_the_in_place_switches():
    """UX4 / UX16: the check-in fold's summary takes the focus after a swap that
    folds it; a tile or driver opening Sommeil focuses the figure's heading."""
    page = (ROOT / "app/templates/sante.html").read_text()
    assert '(el || document.getElementById("tab-sommeil")).focus({ preventScroll: true })' in page
    assert 'document.getElementById("feel-summary")' in page and "htmx:afterSettle" in page
    today = (ROOT / "app/templates/partials/sante_today.html").read_text()
    assert '<summary id="feel-summary">' in today
    sleep = (ROOT / "app/templates/partials/sante_sleep_range.html").read_text()
    assert '<h2 id="nuits" class="pf-sante-h" tabindex="-1">' in sleep and \
        '<h2 id="coeur" class="pf-sante-h" tabindex="-1">' in sleep
