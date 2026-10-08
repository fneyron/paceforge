"""The chart kit: French formats (the house duration everywhere), the geometry
builders (the race page's and Activités' figures, Santé v4's rings, day bars,
night cards and timeline), the Jinja macros' accessible structure, and the
CSS/JS rules (contrast of marks and selected states in both themes, motion
≤ 300 ms under no-preference only, forced colours, no colour or number
formatting in JS)."""
import json
import math
import pathlib
import re
from datetime import date, datetime, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services import sante_today, viz

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


def test_ring_arc_is_the_fill_and_the_value_is_printed_by_the_caller():
    """V1: an arc from the top, clockwise, up to the fill (capped at one turn); none at 0 or without a value."""
    r = viz.ring("recup", 0.59, "59", "Récupération", "en cours", tone="warn", href="#contributeurs",
                 aria="Récupération : 59 sur 100, récupération en cours")
    assert r["c"] == round(2 * math.pi * 42, 2) and r["dash"] == round(0.59 * r["c"], 2) and not r["long"]
    assert viz.ring("sommeil", 1.4, "8h36", "Sommeil")["dash"] == r["c"]  # more than the full mark: one turn
    assert viz.ring("charge", None, "—", "Charge")["dash"] == 0 and viz.ring("x", -1, "0", "x")["dash"] == 0
    assert viz.ring("charge", .5, "16h53", "Charge")["long"]  # 5 characters: a smaller size in the dial


def test_day_bars_stack_the_naps_mark_today_and_leave_a_gap():
    """V2: one bar per day; a lighter part on top (the naps); a day without data is a dot, never a zero;
    the reference line; the day of the month under 16 slots or fewer, the months beyond."""
    days = [D - timedelta(days=13 - i) for i in range(14)]
    vals = [440.0] * 14
    vals[3] = None
    naps = [None] * 13 + [140.0]
    c = viz.day_bars("sommeil-14", days, vals, readouts=[["a", "b", "c"]] * 14, arias=["a"] * 14, stack=naps,
                     reference=(420, "7 h"), today=13, sel=13)
    b = c["bars"]
    assert b[3]["miss"] and "h" not in b[3] and not any(x.get("miss") for i, x in enumerate(b) if i != 3)
    assert b[13]["today"] and b[13]["top"]["y"] < b[13]["y"] and b[13]["top"]["y"] + b[13]["top"]["h"] == b[13]["y"]
    assert c["ref"]["label"] == "7 h" and b[0]["y"] < c["ref"]["y"] < c["base"]  # 7h20 reaches above the 7 h line
    assert [t["label"] for t in c["xt"]] == [str(d.day) for d in days] and c["xt"][13]["today"]
    score = viz.day_bars("recuperation", days[:2], [50, 100], readouts=[["a", "b", "c"]] * 2, arias=["a"] * 2,
                         y_max=100)
    assert score["bars"][1]["y"] == 8 and abs(2 * score["bars"][0]["h"] - score["bars"][1]["h"]) < 0.2
    many = [D - timedelta(days=89 - i) for i in range(90)]
    m = viz.day_bars("sommeil-90", many, [400.0] * 90, readouts=[["a", "b", "c"]] * 90, arias=["a"] * 90)
    assert [t["label"] for t in m["xt"]] == ["août", "sept.", "oct."] and m["bars"][0]["w"] >= 1.4


def test_night_card_prints_the_night_and_its_normal_never_a_verdict():
    """V3: a dot per measured night, the latest selected; the readout says the night's value and the normal
    (« provisoire » from 7 to 13 nights), never above or below on one night (Buchheit 2014)."""
    days = [D - timedelta(days=29 - i) for i in range(30)]
    vals = [60.0 + (i % 5) for i in range(30)]
    vals[10] = None
    band = [None] * 7 + [(55.0, 66.0)] * 23
    prov = [False] * 7 + [True] * 7 + [False] * 16
    mean = [viz.rolling(vals, i, log=True) for i in range(30)]
    c = viz.night_card("vfc", days, vals, band=band, prov=prov, mean=mean, unit="ms", unit_long="millisecondes",
                       name="VFC", min_span=20)
    d = json.loads(c["data"])
    assert d["sel"] == 29 and c["read"] == [viz.night_label(D), f"64{NN}ms", "normale 55–66"]
    assert d["r"][10] == [viz.night_label(days[10]), "—", "pas de mesure cette nuit"]
    assert d["a"][10] == f"{viz.night_label(days[10])} : pas de mesure"  # spoken as it reads
    assert d["r"][8][2] == "normale provisoire 55–66" and d["r"][2][2] == ""
    printed = json.dumps(d["r"], ensure_ascii=False)
    assert not any(w in printed for w in ("au-dessus", "en dessous", "▲", "▼"))
    assert len(c["dots"]) == 29 and c["summary"] == "VFC, 30 nuits : 29 mesurées" and c["mean"].startswith("M")
    assert d["a"][-1] == "nuit du mar. 6 au mer. 7 : VFC 64 millisecondes, ta normale de 55 à 66"
    for m in mean:  # the 7-night mean is drawn, never printed
        if m is not None and m != int(m):
            assert viz.num(m, 1) not in printed


def test_the_timeline_draws_the_night_and_its_nap_on_a_clock_axis():
    """V4: one bar from bedtime to wake, the nap on its own lane, the hours every 2 h; the times
    themselves are printed once, above it, by the page."""
    t = viz.timeline(datetime(2026, 10, 6, 23, 35), datetime(2026, 10, 7, 5, 38),
                     naps=[(datetime(2026, 10, 7, 6, 42), datetime(2026, 10, 7, 9, 7))])
    assert [nm for nm, _, _ in t["lanes"]] == ["nuit", "sieste"] and len(t["naps"]) == 1 and not t["out"]
    assert t["main"]["x"] < t["naps"][0]["x"] and t["main"]["x"] + t["main"]["w"] < t["naps"][0]["x"]
    assert [k["label"] for k in t["ticks"]] == ["00:00", "02:00", "04:00", "06:00", "08:00", "10:00"]
    assert t["X0"] <= t["main"]["x"] and t["naps"][0]["x"] + t["naps"][0]["w"] <= t["X1"]


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
    sleep = viz.day_bars("sommeil-14", days, [None] + [440.0] * 13, readouts=[["n", "Nuit 7h20", "c"]] * 14,
                         arias=["a"] * 14, stack=[None] * 13 + [60.0], reference=(420, "7 h"), today=13)
    card = viz.night_card("vfc", days, [60.0] * 14, band=[(55.0, 65.0)] * 14, prov=[False] * 14, mean=[None] * 14,
                          unit="ms", unit_long="millisecondes", name="VFC")
    figs = {
        "day_bars": ("{{ v.viz_day_bars(c, 'Sommeil sur 24 heures', 'nuit') }}", sleep),
        "night_card": ("{{ v.viz_night_card(c, 'VFC · 30 nuits') }}", card),
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
    html = render(figs["day_bars"][0], c=sleep)
    assert 'class="pf-viz pf-viz-card"' in html and "choisis une nuit" in html
    assert html.count('class="pf-bar-top"') == 1 and html.count('class="pf-viz-gap"') == 1
    assert 'class="pf-viz-col is-today"' in html and ">7 h</text>" in html
    html = render(figs["night_card"][0], c=card)
    assert html.count('class="pf-viz-dot"') == 14 and 'class="pf-viz-band"' in html and "choisis une nuit" in html


def test_json_cannot_close_the_script_tag():
    days = [D]
    c = viz.band_chart("x", days, [{"name": "</script><b>", "unit": "ms", "unit_long": "ms", "values": [60.0],
                                    "band": [None]}])
    html = render("{{ v.viz_band(c, 't') }}", c=c)
    assert html.count("</script>") == 1 and data_of(html)["r"][0][1].startswith("</script><b>")


def test_ring_and_ranges_macros():
    """A ring is one link to the section it sums up; its name starts with the visible label (WCAG 2.5.3);
    the dial is drawn, the value, label and word printed once."""
    r = viz.ring("recup", .59, "59", "Récupération", "en cours", tone="warn", href="#contributeurs",
                 aria="Récupération : 59 sur 100, récupération en cours")
    html = render("{{ v.viz_ring(r) }}", r=r)
    assert html.count("<a ") == 1 and 'href="#contributeurs"' in html
    assert 'aria-label="Récupération : 59 sur 100, récupération en cours"' in html
    assert 'class="pf-ring pf-ring-recup is-warn"' in html and f'stroke-dasharray="{r["dash"]} {r["c"]}"' in html
    assert 'aria-hidden="true" focusable="false"' in html and 'transform="rotate(-90 50 50)"' in html
    assert re.sub(r"<[^>]+>", " ", html).split() == ["59", "Récupération", "en", "cours"]
    empty = render("{{ v.viz_ring(r) }}", r=viz.ring("charge", 0, "0 min", "Charge", "7 jours"))
    assert "pf-ring-arc" not in empty and "pf-ring-track" in empty  # the track alone
    assert "is-long" in render("{{ v.viz_ring(r) }}", r=viz.ring("charge", .4, "16h53", "Charge"))
    html = render("{{ v.viz_ranges([('14', '14 nuits'), ('90', '3 mois')], '14') }}")
    assert 'data-viz-ranges role="group"' in html and html.count('aria-pressed="true"') == 1


def test_the_timeline_is_one_image_with_hours_only():
    t = viz.timeline(datetime(2026, 10, 6, 23, 35), datetime(2026, 10, 7, 5, 38),
                     naps=[(datetime(2026, 10, 7, 6, 42), datetime(2026, 10, 7, 9, 7))])
    html = render("{{ v.viz_timeline(t, 'Nuit de 23 h 35 à 5 h 40, sieste de 6 h 40 à 9 h 05') }}", t=t)
    assert html.count("<svg") == 1 and 'role="img" aria-label="Nuit de 23 h 35' in html
    assert html.count('class="pf-tl-night"') == 1 and html.count('class="pf-tl-nap"') == 1
    texts = re.findall(r">([^<]+)</text>", html)
    assert {"nuit", "sieste"} <= set(texts) and set(texts) - {"nuit", "sieste"} <= {f"{h:02d}:00" for h in range(24)}
    stages = viz.timeline(datetime(2026, 10, 6, 23, 0), datetime(2026, 10, 7, 6, 30), stages=[
        ("core", datetime(2026, 10, 6, 23, 0), datetime(2026, 10, 7, 1, 0)),
        ("deep", datetime(2026, 10, 7, 1, 0), datetime(2026, 10, 7, 6, 30))])
    html = render("{{ v.viz_timeline(t, 'x') }}", t=stages)
    assert html.count('class="pf-tl-seg"') == 2 and html.count('class="pf-tl-step"') == 1 and "pf-tl-night" not in html


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


def test_santes_marks_reach_3_to_1_on_the_page_and_on_the_cards():
    """The rings' arcs and the cards' bars (accent, ok, warn, danger) on the page, on the soft cards and
    against the ring's track; a nap's lighter part keeps an accent edge."""
    theme = (ROOT / "app/static/css/theme.css").read_text()
    light, dark = _tokens(theme, ":root"), {**_tokens(theme, ":root"), **_tokens(theme, ".dark")}
    for name, t in (("light", light), ("dark", dark)):
        for tone in ("pf-accent", "pf-ok", "pf-warn", "pf-danger"):
            for ground in ("pf-bg", "pf-soft", "pf-line"):
                assert contrast(t[tone], t[ground]) >= 3, (name, tone, ground)
        assert contrast(t["pf-gray-400"], t["pf-soft"]) >= 3, name  # a missing day's dot, the band's edges on a card
    css = (ROOT / "app/static/css/interface.css").read_text()
    assert ".pf-bar-top { fill: rgb(var(--pf-accent) / .38); stroke: rgb(var(--pf-accent)); stroke-width: 1; }" in css
    assert ".pf-tl-nap { fill: rgb(var(--pf-accent) / .4); stroke: rgb(var(--pf-accent)); stroke-width: 1; }" in css


def test_santes_motion_is_short_and_only_under_no_preference():
    css = (ROOT / "app/static/css/interface.css").read_text()
    section = css[css.index("/* ── Santé v4"):css.index("/* ── Réglages")]
    for dur in re.findall(r"(\d*\.?\d+)(ms|s)\b", section):
        v = float(dur[0]) / (1000 if dur[1] == "ms" else 1)
        assert v <= 0.3, dur
    outside = re.sub(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\n\}", "", section, flags=re.S)
    assert "transition" not in outside and "animation:" not in outside
    assert ".pf-ring-arc { animation: pf-ring-in .3s ease-out both; }" in section


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


def test_the_readout_reserves_its_height():
    """UX2: pf-viz.js sets the readout's min-height to its tallest line; a card's readout reserves two
    lines; the timeline is drawn by the server at its own height (nothing to swap)."""
    js = (ROOT / "app/static/js/pf-viz.js").read_text()
    assert "function reserve()" in js and "readBox.style.minHeight" in js and "ResizeObserver" in js
    css = (ROOT / "app/static/css/interface.css").read_text()
    assert ".pf-viz-card .pf-viz-read { min-height: 60px; }" in css
    assert ".pf-tl { display: block; width: 100%; max-width: 560px; height: auto;" in css


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
    kit = css[css.index("/* ── viz: the chart kit"):]  # the race page's nights figure
    normal = ".pf-viz-col.is-sel .pf-viz-night:not(.is-short)"
    fc = kit[kit.index("@media (forced-colors: active)"):]
    rule = next(ln for ln in fc.splitlines() if "fill: Highlight" in ln and ".pf-viz-col.is-sel" in ln)
    sel = next(s for s in _split_top(rule.split("{")[0]) if "pf-viz-col.is-sel" in s)
    assert _specificity(sel) >= _specificity(normal) == (0, 4, 0), (sel, _specificity(sel))
    assert ".pf-viz-col.is-sel .pf-viz-night.is-short { fill: rgb(var(--pf-ink)); }" in css


def test_the_rings_bars_and_cards_show_in_forced_colours():
    """A thin track and a thick arc (the ring's value without colour); the cards outlined; a selected bar in
    Highlight whatever its tone; the contributors' bars keep their length."""
    css = (ROOT / "app/static/css/interface.css").read_text()
    sante = css[css.index("/* ── Santé v4"):css.index("/* ── Réglages")]
    fc = sante[sante.index("@media (forced-colors: active)"):]
    assert ".pf-ring-track { stroke: CanvasText; stroke-width: 2; }" in fc
    assert ".pf-ring.is-danger .pf-ring-arc { stroke: CanvasText; }" in fc
    assert ".pf-card, .pf-habits > div { border: 1px solid CanvasText; }" in fc  # the cards keep an edge
    rule = next(ln for ln in fc.splitlines() if "fill: Highlight" in ln)
    sel = _split_top(rule.split("{")[0].strip())[0]
    assert _specificity(sel) >= _specificity(".pf-viz-col.is-sel .pf-bar") == (0, 3, 0), sel
    assert _specificity(sel) > _specificity(".pf-bar.is-danger")  # a tone's forced fill never hides the selection
    assert "forced-color-adjust: none" in fc.split(".pf-contrib-bar > i")[1].split("}")[0]


def test_every_ring_and_card_anchor_lands_under_the_top_bar():
    css = (ROOT / "app/static/css/interface.css").read_text()
    assert ".pf-sante4 [id] { scroll-margin-top: 80px; }" in css
    page = (ROOT / "app/templates/partials/sante_page.html").read_text()
    for anchor in ("contributeurs", "recuperation", "sommeil", "charge"):
        assert f'id="{anchor}"' in page, anchor
    assert '("vfc", "VFC · 30 nuits", p.vfc), ("fc", "FC de nuit · 30 nuits", p.fc)' in page
    assert 'id="{{ key }}"' in page


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


def test_band_chart_says_a_provisional_normal():
    """A band from 7 to 13 nights (H): « normale provisoire » once when every panel's is, else per panel; a judged
    value's word gets « (provisoire) »; the spoken sentence says it."""
    days = [D - timedelta(days=1), D]
    panels = lambda prov_hrv, prov_hr: [  # noqa: E731
        {"name": "VFC", "unit": "ms", "unit_long": "millisecondes", "values": [60, 50], "band": [(55, 65)] * 2,
         "prov": [prov_hrv] * 2, "judge": [True, True], "min_span": 20},
        {"name": "FC", "unit": "bpm", "unit_long": "battements par minute", "values": [45, 46], "band": [(43, 49)] * 2,
         "prov": [prov_hr] * 2, "min_span": 8}]
    c = viz.band_chart("coeur", days, panels(True, True), title="Cœur la nuit")
    assert c["read"][1] == f"VFC 50{NN}ms en dessous (provisoire) · FC 46{NN}bpm"
    assert c["read"][2] == "normale provisoire VFC 55–65, FC 43–49"
    assert "ta normale provisoire 55 à 65" in json.loads(c["data"])["a"][-1]
    mixed = viz.band_chart("coeur", days, panels(True, False), title="Cœur la nuit")
    assert mixed["read"][2] == "normale VFC 55–65 (provisoire), FC 43–49"
    full = viz.band_chart("coeur", days, panels(False, False), title="Cœur la nuit")
    assert full["read"][2] == "normale VFC 55–65, FC 43–49" and "provisoire" not in full["read"][1]


