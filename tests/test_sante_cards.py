"""Santé v4.4, the VFC and FC de nuit cards (owner, 2026-10-08 evening: « La VFC et la FC de nuit, on ne les utilise
toujours pas parce qu'il n'y a pas assez de données ? Comment matérialiser que c'est en cours de construction dans le
graphique directement ? … Comment ça va s'afficher quand tu auras assez de données ? »), then « Tous les relevés VFC
doivent compter en fait, pareil pour la FC »: a filled dot for every measured night (each one counts toward the
athlete's usual values, last night included), nothing for a night without one; while there are no usual values,
how many nights until they come; a provisional band (7 to 13 nights, H) dashed and lighter, a full one solid; the
legend names only what is drawn."""
import json
import re
from datetime import timedelta
from pathlib import Path

from app.services import nights as nt
from app.services import sante, viz
from tests.test_sante_score import D, _day, _nights, _runs, _session, night_rows
from tests.test_viz import render

ROOT = Path(__file__).resolve().parent.parent

def _card(rows, sessions=(), metric="hrv"):
    sessions = list(sessions)
    day = _day(rows, sessions)
    nights = _nights(rows, sessions)
    with nt.memo():
        nt.freeze(nights)
        return sante._night_card(nights, metric, D, day)


def test_weekly_trend_and_selected_night_are_distinct_with_sparse_provisional_data():
    rows = night_rows(range(8), hr=lambda k: 42 if k == 0 else 35)
    rows['hr_night'].pop(D - timedelta(days=1))
    card = _card(rows, metric='hr')
    assert card['status']['key'] == 'in'  # the week is within the band, the latest night is not
    assert card['trend']['value'] == '36\u202fbpm'
    assert card['trend']['n'] == 6 and card['trend']['provisional']
    assert '7 nuits' in card['trend']['reference']
    assert sante.night_row(card, 'hr')['detail'] == 'comparaison provisoire'
    reads = json.loads(card['data'])['r']
    assert reads[-1][:2] == ['42\u00a0bpm', 'dernière nuit mesurée']
    assert reads[-2][0] == '—' and 'pas de mesure' in reads[-2][2]
    assert reads[-3][1] == 'nuit sélectionnée'


def test_alert_summary_uses_the_same_two_nights_as_its_status():
    card = _card(night_rows(range(40), hr=lambda k: 55 if k < 2 else 45), metric='hr')
    assert card['trend']['label'] == 'Tendance sur 2 nuits'
    assert card['trend']['value'] == '55\u202fbpm' and card['trend']['n'] == 2
    assert card['status']['tone'] == 'danger'


def test_without_usual_values_the_card_says_when_they_come():
    """No band yet (under 7 measured nights in the 60 days up to last night): the row says how many nights until the
    usual values exist (nights_to_normal: every measured night counts, last night included, and every coming night
    is counted), the chart card says in words that they build, nothing about nights that would not count; no band
    drawn, no band in the legend."""
    c = _card(night_rows(range(0, 6)))  # 6 nights: the 7th makes the band
    assert c["status"] == {"key": "none", "value": None, "word": "en construction", "tone": None, "meaning": None,
                           "detail": "prête après ta prochaine nuit",
                           "text": "Tes valeurs habituelles s'afficheront ici dès 7 nuits mesurées."}
    assert c["band"] == [] == c["band_prov"] and not c["edge_lo"] and not c["edge_prov_lo"]
    assert c["legend"] == [("is-dot", "nuit"), sante.LEGEND_MEAN]  # every night counts: one kind of dot
    c = _card(night_rows(range(0, 5)))
    assert (c["status"]["detail"], c["status"]["text"]) == ("prête dans 2\u00a0nuits", sante.BUILDS)
    html = render("{{ v.viz_night_card(c, 'VFC · 14 nuits') }}", c=c)
    assert "pf-viz-band" not in html and "pf-viz-edge" not in html
    # the nights older than 60 days leave the band as the coming ones enter it: 3 nights 56 to 58 days old and 2
    # recent ones make 7 in 5 nights (each coming night replaces one that leaves, until the old ones are gone)
    old = _nights(night_rows([0, 1, 56, 57, 58]))
    assert sante.nights_to_normal(old, "hrv", D) == 5 and sante.nights_to_normal(old, "hr", D) == 5
    assert sante.nights_to_normal(_nights(night_rows([0])), "hrv", D) == 6


def test_a_provisional_band_is_dashed_and_a_full_one_solid():
    """7 to 13 nights before the window: the band is provisional — dashed edges, a lighter fill, « valeurs
    habituelles (provisoires) »; from 14 nights it is solid, « tes valeurs habituelles »."""
    prov = _card(night_rows(range(0, 12)))  # 12 nights: provisional from the 7th (its first days: none)
    assert prov["band_prov"] and not prov["band"] and prov["edge_prov_lo"] and not prov["edge_lo"]
    assert sante.LEGEND_PROV in prov["legend"] and sante.LEGEND_BAND not in prov["legend"]
    assert prov["read"][2].endswith(" (provisoire)")
    html = render("{{ v.viz_night_card(c, 'VFC · 30 nuits') }}", c=prov)
    assert html.count('class="pf-viz-band is-prov"') == 1 and html.count('class="pf-viz-edge is-prov"') == 2
    assert 'class="pf-viz-band"' not in html and 'class="pf-viz-edge"' not in html
    full = _card(night_rows(range(0, 60)))
    assert full["band"] and not full["band_prov"] and not full["edge_prov_lo"]
    assert sante.LEGEND_BAND in full["legend"] and sante.LEGEND_PROV not in full["legend"]
    html = render("{{ v.viz_night_card(c, 'VFC · 30 nuits') }}", c=full)
    assert "is-prov" not in html and html.count('class="pf-viz-edge"') == 2
    css = (ROOT / "app/static/css/interface.css").read_text(encoding="utf-8")
    assert ".pf-viz-edge.is-prov { stroke-dasharray: 3 3; }" in css
    assert ".pf-card .pf-viz-band.is-prov { fill: rgb(var(--pf-accent) / .08); }" in css  # lighter than .16


def test_the_provisional_band_meets_the_full_one():
    """A band provisional, then full: the dashed run reaches the first full day, so the two meet with no gap; both
    named in the legend."""
    days = [D - timedelta(days=29 - i) for i in range(30)]
    band = [None] * 5 + [(55.0, 66.0)] * 25
    prov = [False] * 5 + [True] * 10 + [False] * 15
    c = viz.night_card("vfc", days, [60.0] * 30, band=band, prov=prov, mean=[None] * 30, unit="ms",
                       unit_long="millisecondes", name="VFC", min_span=20)
    xs = viz.slot_x(30)
    dashed = [float(p.split(",")[0]) for p in c["band_prov"][0].split()]
    solid = [float(p.split(",")[0]) for p in c["band"][0].split()]
    assert (min(dashed), max(dashed)) == (xs[5], xs[15]) and (min(solid), max(solid)) == (xs[15], xs[29])


def test_a_band_known_on_one_night_only_still_shows():
    """The 7th measured night after a gap (the owner's 14 nights: 29, 30, 1, then 6 to 9): the first « provisoire »
    band exists on that night alone; it spans the night's slot, edges included, instead of vanishing."""
    days = [D - timedelta(days=13 - i) for i in range(14)]
    values = [None, None, None, 88.0, 82.0, 79.0, None, None, None, None, 83.0, 95.0, 100.0, 92.0]
    band = [None] * 13 + [(83.0, 92.0)]
    prov = [False] * 13 + [True]
    c = viz.night_card("vfc", days, values, band=band, prov=prov, mean=[None] * 14, unit="ms",
                       unit_long="millisecondes", name="VFC", min_span=20)
    half = viz.X1 / 14 / 2
    x = viz.slot_x(14)[13]
    assert len(c["band_prov"]) == 1 and not c["band"]
    xs = sorted({float(pt.split(",")[0]) for pt in c["band_prov"][0].split()})
    assert xs == [round(x - half, 1), round(x + half, 1)]
    assert c["edge_prov_lo"].startswith(f"M{x - half:.1f} ") and f"L{x + half:.1f} " in c["edge_prov_lo"]


def test_every_measured_night_is_a_filled_dot():
    """Every measured night counts toward the usual values (owner, 2026-10-08: « Tous les relevés VFC doivent
    compter en fait »): the night after a 3h20 outing (« après une sortie longue ») is a filled dot like the
    others, its readout and its spoken sentence say nothing more, the legend names one dot; no hollow dot, no « ne
    compte pas » anywhere (nor its CSS)."""
    long = _session(D - timedelta(days=3), 200, sid=9)
    rows = night_rows(range(0, 40))
    c = _card(rows, _runs() + [long])
    assert "long" in _nights(rows, _runs() + [long])[D - timedelta(days=2)].tags  # 06/10, after the outing
    assert all(set(d) == {"i", "x", "y"} for d in c["dots"]) and len(c["dots"]) == 30
    assert c["legend"][0] == ("is-dot", "nuit") and not any(w.startswith("ne compte") for _, w in c["legend"])
    assert "compte" not in c["data"]
    html = render("{{ v.viz_night_card(c, 'VFC · 30 nuits') }}", c=c)
    assert "is-out" not in html and len(re.findall(r'class="pf-viz-dot(?: is-sel)?" data-i', html)) == 30
    assert len(set(re.findall(r'<circle cx="[\d.]+" cy="[\d.]+" r="([\d.]+)" class="pf-viz-dot', html))) == 1
    css = (ROOT / "app/static/css/interface.css").read_text(encoding="utf-8")
    assert ".pf-viz-card .pf-viz-dot.is-out" not in css and ".pf-lg.is-out" not in css and ".pf-lg.is-band-prov {" in css
    for f in ("app/services/sante.py", "app/services/viz.py", "app/services/sante_sleep.py",
              "app/templates/partials/sante_page.html", "app/templates/partials/_viz.html"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert "ne compte pas" not in text and "ne comptent pas" not in text, f
