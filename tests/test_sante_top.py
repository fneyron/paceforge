"""Santé, the top (2026-10-08, the approved mockup, owner: « Fais comme WHOOP, ça doit rester simple »): three dials,
Sommeil · Récupération · Entraînement, each a percentage over its name and a plain word (never colour alone), a link
to its card below; and the Récupération card's rows. What the page-level tests (test_sante, test_sante_v41 → v43,
test_health) do not pin: each dial's values, words, colours and missing data, the usual week, the « Effort récent »
countdown and its colour, the grey words of a night row without a comparison."""
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.services import sante
from app.services import sante_score as sc
from app.services import sante_today as td
from app.services import sante_training as st
from tests.test_sante_score import _session
from tests.test_viz import _tokens, contrast, render

D = date(2026, 10, 8)  # a Thursday: this week's Monday is 05/10
ROOT = Path(__file__).resolve().parent.parent


def _effort(day: date, caps: tuple, kind: str = "ultra", sid: int = 1) -> st.Effort:
    return st.Effort(session_id=sid, kind=kind, minutes=700, end=datetime.combine(day, time(12)), day=day,
                     start_day=day, caps=caps)


def _day(efforts: list, d: date = D) -> dict:
    return {"day": d, "window": st.effort_window(efforts, d), "alert": None}


def _fill(dial: dict) -> float:
    return round(dial["dash"] / dial["c"], 3)


# ── the dials ───────────────────────────────────────────────────────────────

def test_the_sommeil_dial():
    """This morning's 24 h as a percentage of the day's need (8 h here), at most 100 % (H): its arc in the sleep
    colour, the warning colour under 6 h; its word from the hours (7 h « suffisant », 6 h « un peu court », under 6 h
    « court »); « — » and « pas de données » without a night; a link to the Sommeil card when there is one."""
    dials = {t: sante.sleep_dial(t, 480, "#sommeil") for t in (600, 480, 420, 419, 360, 359, 300)}
    assert {t: (d["value"], d["unit"], d["label"], d["sub"], d["tone"], _fill(d)) for t, d in dials.items()} == {
        600: ("100", "%", "Sommeil", "suffisant", "sleep", 1.0),
        480: ("100", "%", "Sommeil", "suffisant", "sleep", 1.0),
        420: ("88", "%", "Sommeil", "suffisant", "sleep", 0.875),
        419: ("87", "%", "Sommeil", "un peu court", "sleep", 0.873),
        360: ("75", "%", "Sommeil", "un peu court", "sleep", 0.75), 359: ("75", "%", "Sommeil", "court", "warn", 0.748),
        300: ("63", "%", "Sommeil", "court", "warn", 0.625)}
    assert dials[420]["href"] == "#sommeil"
    assert sante.sleep_dial(516, 480, "#sommeil")["aria"] == "Sommeil 100 % de ton besoin estimé de 8 heures, suffisant."
    none = sante.sleep_dial(None, 480, "#sommeil")
    assert (none["value"], none["sub"], none["tone"], none["dash"]) == ("—", "pas de données", "none", 0)
    assert none["aria"] == "Sommeil : aucune donnée de nuit reçue pour ce matin."
    assert sante.sleep_dial(420, 480, None)["href"] is None  # no Sommeil card: a plain dial


def test_the_sommeil_dial_reads_a_larger_need():
    """A 9-h need (2026-10-09: the athlete's own, or 8 h with sleep owed): 8h36 is 96 % and « suffisant » (⅞ of it,
    7h53, reached), 7h50 is 87 % and « un peu court » though over 7 h; 7 h stays the floor of « suffisant » for a
    smaller need (7 h: 6h30 is « un peu court »); 6 h and « court » never move."""
    d = sante.sleep_dial(516, 540, "#sommeil")
    assert (d["value"], d["sub"], d["tone"]) == ("96", "suffisant", "sleep")
    assert d["aria"] == "Sommeil 96 % de ton besoin estimé de 9 heures, suffisant."
    short = sante.sleep_dial(470, 540, None)
    assert (short["value"], short["sub"], short["tone"]) == ("87", "un peu court", "sleep")
    assert sante.sleep_word(390, 420) == "un peu court" and sante.sleep_word(420, 420) == "suffisant"
    assert sante.sleep_word(359, 420) == "court" and sante.sleep_word(359, 600) == "court"


def test_the_recuperation_dial():
    """The score as a percentage, its arc in its state's colour, the state's word under « Récupération »
    (« bonne », « en cours », « faible »); « — » and « pas de score » without one (no night measured: an empty
    dial, like WHOOP, owner 2026-10-09)."""
    def dial(value):
        score = {"value": value, "tone": sc.tone_of(value), "reason": "effort"}
        return sc.dial(score, td.state(score), "#recuperation")
    dials = (dial(85), dial(65), dial(35))
    assert [(d["value"], d["unit"], d["label"], d["sub"], d["tone"], _fill(d)) for d in dials] == [
        ("85", "%", "Récupération", "bonne", "ok", 0.85), ("65", "%", "Récupération", "en cours", "warn", 0.65),
        ("35", "%", "Récupération", "faible", "danger", 0.35)]
    assert dial(65)["aria"] == "Récupération 65 %, en cours."
    none = sc.dial({"value": None}, None, None)
    assert (none["value"], none["sub"], none["tone"], none["href"], none["dash"]) == ("—", "pas de score", "none",
                                                                                      None, 0)
    html = render("{{ v.viz_ring(r) }}", r=dial(65))
    assert '<a class="pf-ring pf-ring-recup is-warn" href="#recuperation" aria-label="Récupération 65 %, en cours.">' \
        in html
    assert '<span>65<span class="pf-ring-unit">%</span></span>' in html
    assert '<span class="pf-ring-label" aria-hidden="true">Récupération</span>' in html
    assert '<span class="pf-ring-sub" aria-hidden="true">en cours</span>' in html


def _weeks(minutes_per_week: list, this_week: float | None = None) -> list:
    """One activity on the Wednesday of each of the complete weeks before D (the latest first), and `this_week`
    minutes on Tuesday 06/10."""
    monday = D - timedelta(days=D.weekday())
    out = [_session(monday - timedelta(days=7 * k) + timedelta(days=2), m, sid=k)
           for k, m in enumerate(minutes_per_week, start=1)]
    if this_week:
        out.append(_session(D - timedelta(days=2), this_week, sid=99))
    return out


SINCE = D - timedelta(days=6)  # the 7 days' first day: the usual week is the 11 weeks before it


def test_the_entrainement_dial_against_the_usual_week():
    """The last 7 days' heart-rate load against the usual week's, the mean of the 11 weeks just before them (H;
    never the 7 days themselves: owner, 2026-10-09), as a percentage (100 % as usual), the arc full at
    twice it, in the accent colour; within ± 20 % « comme d'habitude » (H), else « plus » or « moins que
    d'habitude »; the card prints the 7 days' time and the usual week's (to 5 min), the dial the percentage: each
    once. Every session here runs at the same HR: every minute weighs the same, the dial is the hours' ratio (the
    weighting itself: test_sante_train_hr)."""
    usual = [300, 400] * 5 + [350] + [900]  # a 12th week back: out of the 11 (mean 350)
    assert sante.usual_week(_weeks(usual), SINCE) == 350
    cases = {350: ("100", "comme d'habitude", 0.5), 420: ("120", "comme d'habitude", 0.6),
             421: ("120", "plus que d'habitude", 0.601), 280: ("80", "comme d'habitude", 0.4),
             279: ("80", "moins que d'habitude", 0.399), 700: ("200", "plus que d'habitude", 1.0),
             1013: ("289", "plus que d'habitude", 1.0)}
    for minutes, (value, word, fill) in cases.items():
        t = sante.training(_weeks(usual, minutes), D)
        d = t["dial"]
        assert (d["value"], d["unit"], d["label"], d["sub"], d["tone"], d["href"], _fill(d)) == (
            value, "%", "Entraînement", word, "accent", "#entrainement", fill), minutes
        assert t["week"] == sante.viz.hm(minutes) and t["usual"] == "ta semaine habituelle : 5h50"
    assert sante.training(_weeks(usual, 350), D)["dial"]["aria"] == ("Entraînement 100 % de ta semaine "
                                                                     "habituelle, comme d'habitude.")
    none = sante.training(_weeks(usual), D)  # nothing in the last 7 days: 0 %, no « Intensité »
    assert (none["dial"]["value"], none["dial"]["sub"], none["week"], none["intensity"]) == (
        "0", "moins que d'habitude", "0 min", None)
    assert sante.training(_weeks(usual, 421), D)["intensity"] == "comme d'habitude"  # the same minutes' weight
    # the usual week to 5 min: 4 weeks of 47, 48, 52 and 53 min → 50 min
    assert sante.training(_weeks([47, 48, 52, 53], 50), D)["usual"] == "ta semaine habituelle : 50 min"


def test_the_7_days_never_count_in_the_usual_week():
    """Owner, 2026-10-09: his 148-km race counted in the 7 days AND in the usual week (its calendar week was one of
    the 11, so his race week read « comme d'habitude »). The usual week is the 11 weeks before the 7 days: a big
    Friday in them leaves it untouched (an « uncoupled » ratio)."""
    friday = D - timedelta(days=6)  # 02/10: the 7 days' first day, in the calendar week 28/09 → 04/10
    sessions = _weeks([300] * 11) + [_session(friday, 960, sid=50)]
    assert sante.usual_week(sessions, SINCE) == 300
    t = sante.training(sessions, D)
    assert (t["dial"]["value"], t["dial"]["sub"], t["usual"]) == ("320", "plus que d'habitude",
                                                                 "ta semaine habituelle\u00a0: 5h00")


def test_an_overnight_race_counts_on_the_day_it_ended():
    """The Transjeju started on 02/10 at 21:00 and ended on 03/10 (16h53, stops in): it counts in the 7 days until
    09/10 (03/10 → 09/10), as Récupération dates it, and then joins the usual weeks (owner, 2026-10-09: « demain le
    cadran tombe à 0 % »)."""
    race = _session(date(2026, 10, 2), 964, sid=60, hour=21, elapsed=1013)
    assert (race.day, race.end_day) == (date(2026, 10, 2), date(2026, 10, 3))
    assert _session(D, 50, hour=7).end_day == D  # a morning run: its own day
    sessions = _weeks([300] * 11) + [race]
    t9 = sante.training(sessions, date(2026, 10, 9))
    assert (t9["since"], t9["week"], t9["usual"]) == (date(2026, 10, 3), "16h04", "ta semaine habituelle\u00a0: 5h00")
    t10 = sante.training(sessions, date(2026, 10, 10))
    assert t10["week"] == "0 min" and t10["dial"]["sub"] == "moins que d'habitude"
    assert sante.usual_week(sessions, date(2026, 10, 4)) == (11 * 300 + 964) / 11  # in the week just before


def test_without_a_usual_week_the_dial_prints_the_time():
    """Fewer than 4 complete weeks holding an activity (H): no usual week, the dial prints the 7 days' time itself
    and « pas encore d'habitude », no arc; the card then prints no time (the dial does), only its words, and no
    « Intensité » (nothing to weigh the minutes against)."""
    t = sante.training(_weeks([300, 300, 300], 1013), D)
    d = t["dial"]
    assert (d["value"], d["unit"], d["sub"], d["tone"], d["dash"]) == ("16h53", None, "pas encore d'habitude",
                                                                      "accent", 0)
    assert (t["week"], t["usual"], t["word"], t["intensity"]) == (None, None, "pas encore de semaine habituelle",
                                                                  None)
    assert d["aria"] == "Entraînement : 16 heures 53 d'activité ces 7 derniers jours, pas encore d'habitude."
    assert sante.usual_week(_weeks([300, 300, 300, 300]), SINCE) == 300  # 4 weeks: there is one
    assert sante.usual_week([], SINCE) is None and sante.training([], D)["dial"]["value"] == "0 min"
    assert sante.usual_week(_weeks([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 30]), SINCE) is None  # a usual week of nothing
    html = render("{{ v.viz_ring(r) }}", r=d)
    assert '<span class="pf-ring-value is-long" aria-hidden="true">16h53</span>' in html


def test_the_dials_arcs_reach_3_to_1_in_both_themes():
    """Each arc is a large mark: ≥ 3:1 on the page and on its track in both themes (WCAG 1.4.11) — Sommeil's one
    hue (--pf-sleep, indigo), Entraînement's accent, Récupération's state colours (the brighter amber for marks);
    under forced colours every arc is CanvasText over a thin track (the words say the rest)."""
    theme = (ROOT / "app/static/css/theme.css").read_text()
    css = (ROOT / "app/static/css/interface.css").read_text()
    light, dark = _tokens(theme, ":root"), {**_tokens(theme, ":root"), **_tokens(theme, ".dark")}
    mark = tuple(int(x) for x in re.search(r":root \{ --pf-warn-mark: (\d+ \d+ \d+); \}", css).group(1).split())
    for name, t, warn in (("light", light, mark), ("dark", dark, dark["pf-warn"])):
        for key, rgb in (("sleep", t["pf-sleep"]), ("accent", t["pf-accent"]), ("ok", t["pf-ok"]), ("warn", warn),
                         ("danger", t["pf-danger"])):
            for ground in ("pf-bg", "pf-line"):
                assert contrast(rgb, t[ground]) >= 3, (name, key, ground)
    assert ".pf-ring.is-sleep .pf-ring-arc { stroke: rgb(var(--pf-sleep)); }" in css
    assert re.search(r"\.pf-ring-arc, [^{]*\.pf-ring\.is-sleep \.pf-ring-arc[^{]*\{ stroke: CanvasText; \}", css)


# ── the Récupération card's rows ────────────────────────────────────────────

def test_effort_recent_explains_progressive_prudence_without_a_recovery_deadline():
    """The effort stays visible, without promising recovery on a specific day."""
    ultra = _effort(D - timedelta(days=2), ((3, 35), (10, 65)))  # D+2: capped at 35, its window to D+10 (16/10)
    f = sante.effort_row([ultra], _day([ultra]))
    assert (f["name"], f["value"], f["word"], f["tone"]) == ("Effort récent", None, "prudence après l’effort", "danger")
    assert "diminue progressivement" in f["detail"] and "récupération musculaire" in f["detail"]
    later = _day([ultra], D + timedelta(days=2))  # D+4: 65
    assert (sante.effort_row([ultra], later)["value"], sante.effort_row([ultra], later)["tone"]) == (
        None, "warn")
    assert sante.effort_row([ultra], _day([ultra], date(2026, 10, 15)))["value"] is None
    assert sante.effort_row([ultra], _day([ultra], date(2026, 10, 16)))["value"] is None
    assert sante.effort_row([ultra], _day([ultra], date(2026, 10, 17))) is None  # the window is over: no row
    very_long = _effort(D - timedelta(days=1), ((2, 45), (5, 65)), kind="very_long")
    assert sante.effort_row([very_long], _day([very_long]))["tone"] == "danger"  # 45: red
    assert sante.effort_row([], _day([])) is None  # no window: no row


def test_two_windows_the_row_lasts_until_the_last_one_ends():
    """Two windows open: the row follows today's lowest cap without a countdown."""
    old = _effort(D - timedelta(days=9), ((3, 35), (13, 65)), sid=1)  # 65 until D+4
    new = _effort(D - timedelta(days=1), ((2, 45), (5, 65)), kind="very_long", sid=2)  # 45 today, to D+4
    longer = _effort(D - timedelta(days=1), ((3, 35), (10, 65)), sid=3)  # 35 today, to D+9
    f = sante.effort_row([old, new], _day([old, new]))
    assert (f["value"], f["tone"]) == (None, "danger")
    f = sante.effort_row([old, longer], _day([old, longer]))
    assert (f["value"], f["tone"]) == (None, "danger")


def test_the_effort_row_shows_whenever_a_window_is_open():
    """Owner, 2026-10-08: « Un effort récent, il faut le prendre en compte et afficher la fatigue quand même »:
    the row shows whenever a recovery window is open, its cap binding the score or not (here the illness alert's
    39 binds, under the window's 65); the window still caps the score."""
    from tests.test_sante_score import _day as day_of
    from tests.test_sante_score import _runs, night_rows

    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)  # the alert
    ultra = _session(D - timedelta(days=7), 610, sid=9)  # D+7 of an ultra: 65
    day = day_of(rows, _runs() + [ultra])
    assert day["state"]["key"] == "ill" and day["score"]["caps"][0] == "ill" and day["window"]["cap"] == 80
    f = sante.effort_row(st.efforts(_runs() + [ultra]), day)
    assert (f["value"], f["tone"]) == (None, "warn")


def test_a_night_row_repeats_its_cards_status_never_its_explanation():
    """VFC / FC de nuit (7 nuits): its card's percentage and word (one source of truth: card_status) in its
    colour; « comme d'habitude » at 0 %; grey and no percentage without a comparison (« en construction » over
    when its usual values will be ready, « trop peu de nuits pour comparer »); no row without a card."""
    def card(key, value, word, tone, detail=None):
        return {"period": "7 derniers jours", "status": {"key": key, "value": value, "word": word, "detail": detail, "text": "x", "tone": tone,
                           "meaning": "x"}}
    rows = [sante.night_row(card("in", "+2 %", sante.STATUS["in"], "ok"), "hrv"),
            sante.night_row(card("in", None, sante.SAME, "ok"), "hrv"),
            sante.night_row(card("below", "−12 %", sante.STATUS["below"], "danger"), "hrv"),
            sante.night_row(card("above", "+9 %", sante.STATUS["above"], "accent"), "hrv"),
            sante.night_row(card("none", None, sante.BUILDING, None, "prête dans 2 nuits"), "hr"),
            sante.night_row(card("few", None, sante.FEW, None), "hr")]
    assert [(r["name"], r["qual"], r["value"], r["word"], r["detail"], r["tone"]) for r in rows] == [
        ("VFC", "7 derniers jours", "+2 %", "dans tes valeurs habituelles", None, "ok"),
        ("VFC", "7 derniers jours", None, "comme d'habitude", None, "ok"),
        ("VFC", "7 derniers jours", "−12 %", "plus basse que d'habitude", None, "danger"),
        ("VFC", "7 derniers jours", "+9 %", "plus haute que d'habitude", None, "accent"),
        ("FC de nuit", "7 derniers jours", None, "en construction", "prête dans 2 nuits", "none"),
        ("FC de nuit", "7 derniers jours", None, "trop peu de nuits pour comparer", None, "none")]
    assert sante.night_row(None, "hrv") is None


# ── v4.4 (owner: « Mets des pourcentages plutôt que des valeurs (comme WHOOP / Oura) ») ──────────────────────────

def test_the_night_rows_in_percent_from_real_nights():
    """Once the usual values exist, the 7-night mean against the usual value (the band's centre) in whole
    percent, half up, signed: VFC above them is « plus haute que d'habitude », neutral (never praised); « 0 % »
    reads « comme d'habitude »; a week with fewer than 3 measured nights: no percentage; the card's status line
    and the row are one (card_status)."""
    from app.services import nights as nt
    from tests.test_sante_score import _day as day_of
    from tests.test_sante_score import _nights, _rich, _runs

    def card(rows, metric="hrv"):
        day = day_of(rows, _runs())
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
    row = sante.night_row(up, "hrv")
    assert (row["value"], row["word"], row["tone"]) == (up["status"]["value"], up["status"]["word"], "accent")
    rows = _rich()
    for metric in rows:  # only 2 nights in the last 7 (5 and 6 days ago)
        for k in range(5):
            rows[metric].pop(D - timedelta(days=k), None)
    few, _ = card(rows)
    assert few["status"] == {"key": "few", "value": None, "word": "trop peu de nuits pour comparer",
                             "text": sante.NO_MEAN, "tone": None, "meaning": None}
    assert [sante.signed_pct(v, 45.0) for v in (45.0, 45.2, 44.775, 44.7, 45.23, 48.6)] == [0, 0, 0, -1, 1, 8]


def test_the_percentages_are_said_in_the_fold():
    """« 65 % »: the same number on the dial and the 14-day card's taps; the fold says what each percentage
    compares (the 24 h to the need, the last 7 days to the usual week); the 70 / 40 bands are the chart's lines and
    the dial's word, no bullet any more (the audit, 2026-10-09: one fold, without what the page already says)."""
    text = sc.flat(sc.typo(sc.METHOD)).replace("\u00a0", " ")
    assert sc.pct(100) == "100\u00a0%" and "70 %" not in text
    assert "Sommeil compare tes 24 h, siestes comprises, à un besoin estimé." in text
    assert ("Entraînement compare tes 7 derniers jours à ta semaine habituelle. Une minute compte davantage quand "
            "ton pouls est haut.") in sc.METHOD
