"""Santé v4.4, the VFC and FC de nuit cards (owner, 2026-10-08 evening: « La VFC et la FC de nuit, on ne les utilise
toujours pas parce qu'il n'y a pas assez de données ? Comment matérialiser que c'est en cours de construction dans le
graphique directement ? … Comment ça va s'afficher quand tu auras assez de données ? »): a filled dot for a night
that counts toward the athlete's usual values, a hollow one for a night that does not; while there are none, how
many nights until they come; a provisional band (7 to 13 nights, H) dashed and lighter, a full one solid; the
legend names only what is drawn."""
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


def test_without_usual_values_the_card_says_when_they_come():
    """No band yet (6 nights before the 7-night window): the status line says how many nights until the usual
    values exist (nights_to_normal, every coming night counted) and which nights do not count; no band drawn, no
    band in the legend."""
    c = _card(night_rows(range(0, 13)))  # tomorrow's band reads 7 nights (offsets 6 → 12)
    assert c["status"] == {"key": "none", "value": None, "word": "en construction", "tone": None,
                           "meaning": sante.COUNTS, "text": "En construction : tes valeurs habituelles seront prêtes "
                                                            "après ta prochaine nuit, si tu portes ta montre."}
    assert c["band"] == [] == c["band_prov"] and not c["edge_lo"] and not c["edge_prov_lo"]
    assert c["legend"] == [("is-dot", "nuit"), sante.LEGEND_MEAN]  # every night counts: one kind of dot
    c = _card(night_rows(range(0, 5)))
    assert c["status"]["text"] == ("En construction : tes valeurs habituelles seront prêtes dans 9 nuits, si tu "
                                   "portes ta montre.")
    html = render("{{ v.viz_night_card(c, 'VFC · 14 nuits') }}", c=c)
    assert "pf-viz-band" not in html and "pf-viz-edge" not in html


def test_a_provisional_band_is_dashed_and_a_full_one_solid():
    """7 to 13 nights before the window: the band is provisional — dashed edges, a lighter fill, « valeurs
    habituelles (provisoires) »; from 14 nights it is solid, « tes valeurs habituelles »."""
    prov = _card(night_rows(range(0, 16)))  # 10 nights before the week: provisional (its first days: none)
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


def test_a_night_that_does_not_count_is_a_hollow_dot():
    """A night out of the usual values (here the night after a 3h20 outing, « après une sortie longue ») is a
    hollow dot, a little larger so its ring shows; its readout says « ne compte pas », and so does its spoken
    sentence; the legend names both dots only when both are drawn."""
    long = _session(D - timedelta(days=3), 200, sid=9)
    c = _card(night_rows(range(0, 40)), _runs() + [long])
    out = [d["i"] for d in c["dots"] if d["out"]]
    assert out == [27]  # 06/10, the night after the outing of 05/10
    assert c["legend"][:2] == [("is-dot", "compte"), ("is-out", "ne compte pas (voyage, gros effort, altitude)")]
    data = c["data"]
    assert '"ne compte pas"' in data and "cette nuit ne compte pas" in data
    html = render("{{ v.viz_night_card(c, 'VFC · 30 nuits') }}", c=c)
    assert len(re.findall(r'<circle [^>]*r="3.2" class="pf-viz-dot is-out"', html)) == 1
    assert len(re.findall(r'class="pf-viz-dot(?: is-sel)?" data-i', html)) == len(c["dots"]) - 1
    css = (ROOT / "app/static/css/interface.css").read_text(encoding="utf-8")
    assert ".pf-viz-card .pf-viz-dot.is-out { fill: rgb(var(--pf-soft)); stroke: rgb(var(--pf-viz-mark));" in css
    assert ".pf-lg.is-out {" in css and ".pf-lg.is-band-prov {" in css
