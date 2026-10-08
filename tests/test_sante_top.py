"""Santé v4.4, the top (CLEAR_BRIEF.md; owner, 2026-10-08 evening: « C'est toujours pas compréhensible : Sommeil
100, Charge récente 20 avec les donuts au-dessus, on comprend rien »): one ring, the state, then plain facts. What
the page-level tests (test_sante, test_sante_v41 → v43, test_health) do not pin: the « Effort récent » countdown
and its colour, the grey words of a night card without a comparison, the rows' order and the empty cases."""
from datetime import date, datetime, time, timedelta

from app.services import sante
from app.services import sante_training as st

D = date(2026, 10, 8)


def _effort(day: date, caps: tuple, load: int = 20, kind: str = "ultra", sid: int = 1) -> st.Effort:
    return st.Effort(session_id=sid, kind=kind, minutes=700, end=datetime.combine(day, time(12)), day=day,
                     start_day=day, caps=caps, load=load)


def _day(efforts: list, d: date = D) -> dict:
    return {"day": d, "window": st.effort_window(efforts, d), "alert": None}


def test_effort_recent_counts_the_days_left_and_wears_the_cap():
    """« encore N jours », N the days from today to the window's last day (« dernier jour » on it), never the
    activity, its date or its hours; red while the window caps the score at 35 or 45, orange at 65."""
    ultra = _effort(D - timedelta(days=2), ((3, 35), (10, 65)))  # D+2: capped at 35, its window to D+10 (16/10)
    f = sante.effort_fact([ultra], _day([ultra]))
    assert (f["name"], f["value"], f["word"], f["tone"], f["href"]) == ("Effort récent", None, "encore 8 jours",
                                                                        "danger", None)
    later = _day([ultra], D + timedelta(days=2))  # D+4: 65
    assert (sante.effort_fact([ultra], later)["word"], sante.effort_fact([ultra], later)["tone"]) == (
        "encore 6 jours", "warn")
    assert sante.effort_fact([ultra], _day([ultra], date(2026, 10, 15)))["word"] == "encore 1 jour"
    assert sante.effort_fact([ultra], _day([ultra], date(2026, 10, 16)))["word"] == "dernier jour"
    assert sante.effort_fact([ultra], _day([ultra], date(2026, 10, 17))) is None  # the window is over: no row
    very_long = _effort(D - timedelta(days=1), ((2, 45), (5, 65)), load=30, kind="very_long")
    assert sante.effort_fact([very_long], _day([very_long]))["tone"] == "danger"  # 45: red
    assert sante.effort_fact([], _day([])) is None  # no window: no row


def test_two_windows_the_row_lasts_until_the_last_one_ends():
    """Two windows open: the row says how long until the last one ends (it counts down to the day it goes), in
    the colour of the cap that binds today (effort_window: the lowest)."""
    old = _effort(D - timedelta(days=9), ((3, 35), (13, 65)), sid=1)  # 65 until D+4
    new = _effort(D - timedelta(days=1), ((2, 45), (5, 65)), load=30, kind="very_long", sid=2)  # 45 today, to D+4
    longer = _effort(D - timedelta(days=1), ((3, 35), (10, 65)), sid=3)  # 35 today, to D+9
    f = sante.effort_fact([old, new], _day([old, new]))
    assert (f["word"], f["tone"]) == ("encore 4 jours", "danger")
    f = sante.effort_fact([old, longer], _day([old, longer]))
    assert (f["word"], f["tone"]) == ("encore 9 jours", "danger")


def test_a_night_row_repeats_its_cards_status_never_its_explanation():
    """VFC (7 nuits) / FC de nuit (7 nuits): its card's percentage and word (one source of truth: card_status) in
    its colour; « comme d'habitude » at 0 %; grey and no percentage without a comparison (« en construction »,
    « trop peu de nuits pour comparer »); no row without a card."""
    def card(key, value, word, tone):
        return {"status": {"key": key, "value": value, "word": word, "text": "x", "tone": tone, "meaning": "x"}}
    plain = {"day": D, "window": None, "alert": None}
    rows = [sante.night_fact(card("in", "+2\u00a0%", sante.STATUS["in"], "ok"), "hrv", plain),
            sante.night_fact(card("in", None, sante.SAME, "ok"), "hrv", plain),
            sante.night_fact(card("below", "\u221212\u00a0%", sante.STATUS["below"], "danger"), "hrv", plain),
            sante.night_fact(card("above", "+9\u00a0%", sante.STATUS["above"], "accent"), "hrv", plain),
            sante.night_fact(card("none", None, sante.BUILDING, None), "hr", plain),
            sante.night_fact(card("few", None, sante.FEW, None), "hr", plain)]
    assert [(r["name"], r["qual"], r["value"], r["word"], r["tone"], r["href"]) for r in rows] == [
        ("VFC", "7 nuits", "+2\u00a0%", "dans tes valeurs habituelles", "ok", "#vfc"),
        ("VFC", "7 nuits", None, "comme d'habitude", "ok", "#vfc"),
        ("VFC", "7 nuits", "\u221212\u00a0%", "plus basse que d'habitude", "danger", "#vfc"),
        ("VFC", "7 nuits", "+9\u00a0%", "plus haute que d'habitude", "accent", "#vfc"),
        ("FC de nuit", "7 nuits", None, "en construction", "none", "#fc"),
        ("FC de nuit", "7 nuits", None, "trop peu de nuits pour comparer", "none", "#fc")]
    assert sante.night_fact(None, "hrv", plain) is None


# ── v4.4 (owner: « Mets des pourcentages plutôt que des valeurs (comme WHOOP / Oura) ») ──────────────────────────

def test_the_night_rows_in_percent_from_real_nights():
    """Once the usual values exist, the 7-night mean against the usual value (the band's centre) in whole
    percent, half up, signed: VFC above them is « plus haute que d'habitude », neutral (never praised); « 0 % »
    reads « comme d'habitude »; a week with fewer than 3 nights that count: no percentage; the card's status line
    and the row are one (card_status)."""
    from app.services import nights as nt
    from tests.test_sante_score import _day, _nights, _rich, _runs

    def card(rows, metric="hrv"):
        day = _day(rows, _runs())
        nights = _nights(rows, _runs())
        with nt.memo():
            nt.freeze(nights)
            return sante._night_card(nights, metric, D, day), day
    up, day = card(_rich(hrv_last=85.0))
    centre = day["stats"]["hrv"]["normal"]["center"]
    p = sante.signed_pct(85.0, centre)
    assert p > 0 and up["status"] == {"key": "above", "value": f"+{p} %", "word": "plus haute que d'habitude",
                                      "text": f"+{p} % plus haute que d'habitude", "tone": "accent",
                                      "meaning": None}
    row = sante.night_fact(up, "hrv", day)
    assert (row["value"], row["word"], row["tone"]) == (up["status"]["value"], up["status"]["word"], "accent")
    rows = _rich()
    for metric in rows:  # only 2 nights in the last 7 (5 and 6 days ago)
        for k in range(5):
            rows[metric].pop(D - timedelta(days=k), None)
    few, _ = card(rows)
    assert few["status"] == {"key": "few", "value": None, "word": "trop peu de nuits pour comparer",
                             "text": sante.NO_MEAN, "tone": None, "meaning": None}
    assert [sante.signed_pct(v, 45.0) for v in (45.0, 45.2, 44.775, 44.7, 45.23, 48.6)] == [0, 0, 0, -1, 1, 8]


def test_the_ring_prints_the_score_as_a_percentage():
    """« 65 % »: the same number, the « % » smaller on its baseline; spoken « Récupération 65 % »; the 14-day card
    and the fold say percentages too (the score is one quantity)."""
    from app.services import sante_score as sc
    from app.services import sante_today as td
    from tests.test_viz import render

    score = {"value": 65, "tone": "warn", "estimated": False}
    r = sc.ring(score, td.state({**score, "reason": "effort"}), "#recuperation")
    assert (r["value"], r["unit"], r["aria"]) == ("65", "%", "Récupération 65 %. Récupération en cours.")
    html = render("{{ v.viz_ring(r) }}", r=r)
    assert '<span class="pf-ring-value" aria-hidden="true"><span>65<span class="pf-ring-unit">%</span></span></span>' in html
    assert sc.pct(100) == "100 %" and "70 % et plus" in sc.flat(sc.typo(sc.METHOD)).replace(" ", " ")
    assert "Les pourcentages comparent tes nuits à 8 h de sommeil et à tes valeurs habituelles." in sc.METHOD
