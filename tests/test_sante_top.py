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


def test_a_night_row_repeats_its_cards_word_never_its_explanation():
    """VFC (7 nuits) / FC de nuit (7 nuits): the card's word in its colour; grey without a comparison (« pas
    encore de normale », « trop peu de nuits »); no row without a card; the FC de nuit row under the alert."""
    def card(key, text, tone):
        return {"status": {"key": key, "text": text, "tone": tone, "meaning": "x"}}
    plain = {"day": D, "window": None, "alert": None}
    rows = [sante.night_fact(card("in", sante.STATUS["in"], "ok"), "hrv", plain),
            sante.night_fact(card("below", sante.STATUS["below"], "danger"), "hrv", plain),
            sante.night_fact(card("above", sante.STATUS["above"], "accent"), "hrv", plain),
            sante.night_fact(card("none", "Tes valeurs habituelles seront prêtes dans 3 nuits …", None), "hr", plain),
            sante.night_fact(card("few", sante.NO_MEAN, None), "hr", plain)]
    assert [(r["name"], r["qual"], r["word"], r["tone"], r["href"]) for r in rows] == [
        ("VFC", "7 nuits", "dans tes valeurs habituelles", "ok", "#vfc"),
        ("VFC", "7 nuits", "plus basse que d'habitude", "danger", "#vfc"),
        ("VFC", "7 nuits", "plus haute que d'habitude", "accent", "#vfc"),
        ("FC de nuit", "7 nuits", "pas encore de valeurs habituelles", "none", "#fc"),
        ("FC de nuit", "7 nuits", "trop peu de nuits pour comparer", "none", "#fc")]
    assert all(r["value"] is None for r in rows)  # a word, no number: the card prints last night's value
    assert sante.night_fact(None, "hrv", plain) is None
    ill = {**plain, "alert": {"values": [53.0, 53.0]}}
    assert sante.night_fact(card("above", sante.STATUS["above"], "danger"), "hr", ill)["word"] == (
        "nettement plus haute depuis 2 nuits")
    assert sante.night_fact(card("in", sante.STATUS["in"], "ok"), "hrv", ill)["word"] == "dans tes valeurs habituelles"
