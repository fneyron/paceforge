"""Santé, the top (2026-10-08, the approved mockup, owner: « Fais comme WHOOP, ça doit rester simple »): three dials,
Sommeil · Récupération · Entraînement, each a percentage over its name and a plain word (never colour alone), a link
to its card below; and the Récupération card's rows. What the page-level tests (test_sante, test_sante_v41 → v43,
test_health) do not pin: each dial's values, words, colours and missing data, the usual week, the « Effort récent »
countdown and its colour, the grey words of a night row without a comparison."""
import re
import statistics
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from app.services import sante, sante_load
from app.services import sante_score as sc
from app.services import sante_today as td
from app.services import sante_training as st
from tests.test_sante_score import _session
from tests.test_viz import _tokens, contrast, render

D = date(2026, 10, 8)  # a Thursday: this week's Monday is 05/10
ROOT = Path(__file__).resolve().parent.parent


def _effort(day: date, last: int, kind: str = "ultra", sid: int = 1) -> st.Effort:
    return st.Effort(session_id=sid, kind=kind, minutes=700, end=datetime.combine(day, time(12)), day=day,
                     start_day=day, last=last)


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
    assert sante.sleep_dial(516, 480, "#sommeil")["aria"] == "Sommeil 100 % du repère de 8 heures, suffisant."
    none = sante.sleep_dial(None, 480, "#sommeil")
    assert (none["value"], none["sub"], none["tone"], none["dash"]) == ("—", "pas de données", "none", 0)
    assert none["aria"] == "Sommeil : aucune donnée de nuit reçue pour ce matin."
    assert sante.sleep_dial(420, 480, None)["href"] is None  # no Sommeil card: a plain dial


def test_the_sommeil_dial_reads_the_8_h_need():
    """The need is 8 h for everyone, never raised by the sleep owed or an effort (owner, 2026-10-14: scientific
    basis — it was 9 h after a short week): 8h36 is 100 % « suffisant », 7h50 98 %; « suffisant » from 7 h,
    « court » under 6 h, whatever need a caller passes."""
    d = sante.sleep_dial(516, 480, "#sommeil")
    assert (d["value"], d["sub"], d["tone"]) == ("100", "suffisant", "sleep")
    assert d["aria"] == "Sommeil 100\u00a0% du repère de 8 heures, suffisant."
    short = sante.sleep_dial(470, 480, None)
    assert (short["value"], short["sub"], short["tone"]) == ("98", "suffisant", "sleep")
    assert sante.sleep_word(419) == "un peu court" and sante.sleep_word(420) == "suffisant"
    assert sante.sleep_word(359) == "court" and sante.sleep_word(420, 600) == "suffisant"
    assert sante.sl.NEED_DEFAULT == 480 and not hasattr(sante.sl, "sleep_need")


def test_the_recuperation_dial():
    """The score as a percentage, its arc in its state's colour, the state's word under « Récupération »
    (« bonne », « en cours », « faible »); « — » and « pas de score » without one (no night measured: an empty
    dial, like WHOOP, owner 2026-10-09)."""
    def dial(value, tone):
        score = {"value": value, "tone": tone, "reason": "effort", "reasons": []}
        return sc.dial(score, td.state(score), "#recuperation")
    dials = (dial(85, "ok"), dial(65, "warn"), dial(35, "danger"))
    assert [(d["value"], d["unit"], d["label"], d["sub"], d["tone"], _fill(d)) for d in dials] == [
        ("85", "/100", "Récupération", "habituelle", "ok", 0.85), ("65", "/100", "Récupération", "à surveiller", "warn", 0.65),
        ("35", "/100", "Récupération", "inhabituelle", "danger", 0.35)]
    assert dial(65, "warn")["aria"] == "Récupération : indice nocturne 65 sur 100, à surveiller."
    none = sc.dial({"value": None}, None, None)
    assert (none["value"], none["sub"], none["tone"], none["href"], none["dash"]) == ("—", "pas de score", "none",
                                                                                      None, 0)
    html = render("{{ v.viz_ring(r) }}", r=dial(65, "warn"))
    assert '<a class="pf-ring pf-ring-recup is-warn pf-ring-index" href="#recuperation" aria-label="Récupération : indice nocturne 65 sur 100, à surveiller.">' \
        in html
    assert '<span>65<span class="pf-ring-unit">/100</span></span>' in html
    assert '<span class="pf-ring-label" aria-hidden="true">Récupération</span>' in html
    assert '<span class="pf-ring-sub" aria-hidden="true">à surveiller</span>' in html


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


def test_comme_d_habitude_is_the_range_of_the_active_usual_weeks():
    """« Comme d'habitude » from the lightest to the heaviest of the 11 usual weeks that hold an activity (owner,
    2026-10-14: scientific basis — the 25th–75th percentiles labelled 57 % of ordinary weeks « plus » or « moins »;
    the range of 11 weeks, about one in six): 450 min against weeks of 200 to 500 is as usual (it read « plus »
    over the 75th percentile, 400), a week without an activity is no lower bound."""
    usual = [200, 300, 400, 500] * 2 + [0, 300, 400]  # the 11 weeks, one of them empty
    for minutes, word in ((450, "comme d'habitude"), (500, "comme d'habitude"), (501, "plus que d'habitude"),
                          (200, "comme d'habitude"), (199, "moins que d'habitude")):
        assert sante.training(_weeks(usual, minutes), D)["dial"]["sub"] == word, minutes
    w = sante_load.week_ratio(_weeks(usual, 450), st.loads(_weeks(usual, 450), D, 50, 190), D)
    assert (w["lo"], w["hi"]) == pytest.approx((200 * w["usual"] / statistics.fmean(usual),
                                                500 * w["usual"] / statistics.fmean(usual)))


def test_the_word_needs_8_active_usual_weeks():
    """A week like the n_a active ones before it falls outside their range 2/(n_a + 1) of the time: the word (and
    the percentage) only from 8 active weeks of the 11 (H: 22 %; 40 % at 4; the second statistics review,
    2026-10-14); under them the dial says the 7 days' time, as without a usual week."""
    eight = [300] * 8 + [0] * 3
    t = sante.training(_weeks(eight, 300), D)
    assert (t["dial"]["value"], t["dial"]["sub"]) == ("100", "comme d'habitude")
    seven = [300] * 7 + [0] * 4
    t = sante.training(_weeks(seven, 300), D)
    assert (t["dial"]["value"], t["dial"]["sub"], t["usual"], t["word"], t["ratio"]) == (
        "5h00", "7 derniers jours", None, sante.NO_USUAL, None)
    assert sante.training_context({**t, "chart": None})["word"] == "5h00 sur 7 jours"  # no « Charge 7 j : x % »
    assert sante_load.USUAL_LABEL_WEEKS == 8


def test_the_entrainement_dial_against_the_usual_week():
    """The last 7 days' heart-rate load against the usual week's, the mean of the 11 weeks just before them (H;
    never the 7 days themselves: owner, 2026-10-09), as a percentage (100 % as usual), the arc full at
    twice it, in the accent colour; « comme d'habitude » while the 7 days lie between the lightest and the
    heaviest of those 11 weeks that hold an activity (owner, 2026-10-14: scientific basis — it was ± 20 %: 420 min
    read « comme d'habitude » though above every usual week; then the 25th–75th percentiles, which called half of
    the ordinary weeks « plus » or « moins »), else « plus » or « moins que d'habitude »; the card prints the 7
    days' time and the usual week's (to 5 min), the dial the percentage: each once. Every session here runs at the
    same HR: every minute weighs the same, the dial is the hours' ratio (the weighting itself:
    test_sante_train_hr)."""
    usual = [300, 400] * 5 + [350] + [900]  # a 12th week back: out of the 11 (mean 350, from 300 to 400)
    assert sante.usual_week(_weeks(usual), SINCE) == 350
    cases = {350: ("100", "comme d'habitude", 0.5), 400: ("114", "comme d'habitude", 0.571),
             401: ("115", "plus que d'habitude", 0.573), 300: ("86", "comme d'habitude", 0.429),
             299: ("85", "moins que d'habitude", 0.427), 700: ("200", "plus que d'habitude", 1.0),
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
    assert sante.training(_weeks(usual, 401), D)["intensity"] is None  # no confirmed or supported reference
    assert sante.training(_weeks(usual, 401), D, peak=190)["intensity"] == "comme d'habitude"
    # the usual week to 5 min: 8 weeks of 47, 48, 52 and 53 min → 50 min; with these 4 weeks alone, under the 8
    # active weeks the word needs, the dial says the 7 days' time, as without a usual week (it read « 50 min »: at 4
    # active weeks the word came with 40 % of ordinary weeks; the second statistics review, 2026-10-14)
    assert sante.training(_weeks([47, 48, 52, 53] * 2, 50), D)["usual"] == "ta semaine habituelle : 50 min"
    four = sante.training(_weeks([47, 48, 52, 53], 50), D)
    assert (four["usual"], four["word"], four["ratio"], four["dial"]["value"], four["dial"]["sub"]) == (
        None, sante.NO_USUAL, None, "50 min", "7 derniers jours")


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
    and « 7 derniers jours », no arc. The card retains the measured duration for its daily chart,
    without an invented usual reference or intensity comparison."""
    t = sante.training(_weeks([300, 300, 300], 1013), D)
    d = t["dial"]
    assert (d["value"], d["unit"], d["sub"], d["tone"], d["dash"]) == ("16h53", None, "7 derniers jours",
                                                                      "accent", 0)
    assert (t["week"], t["usual"], t["word"], t["intensity"]) == ("16h53", None, "pas encore de semaine habituelle",
                                                                  None)
    assert d["aria"] == "Entraînement : 16 heures 53 d'activité ces 7 derniers jours, pas encore d'habitude."
    assert sante.usual_week(_weeks([300, 300, 300, 300]), SINCE) == 300  # 4 weeks: there is one
    assert sante.usual_week([], SINCE) is None and sante.training([], D)["dial"]["value"] == "0 min"
    assert sante.usual_week(_weeks([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 30]), SINCE) is None  # a usual week of nothing
    html = render("{{ v.viz_ring(r) }}", r=d)
    assert '<span class="pf-ring-value is-long" aria-hidden="true">16h53</span>' in html


def test_cycle_pending_keeps_the_dial_chart_and_activity_link_on_the_same_seven_days():
    """Yesterday's sleep cycle must not extend the training window to eight days."""
    calendar_day = D + timedelta(days=1)
    first = calendar_day - timedelta(days=6)
    sessions = [
        _session(first - timedelta(days=1), 90, sid=1),
        _session(first, 20, sid=2),
        _session(calendar_day, 30, sid=3),
        _session(calendar_day + timedelta(days=1), 120, sid=4),
    ]
    t = sante.training(sessions, D, until=calendar_day)
    chart = st.training_days(sessions, D, until=calendar_day)
    assert t["since"] == chart["since"] == first
    assert t["week"] == chart["duration"] == t["dial"]["value"] == "50 min"
    assert chart["minutes"] == 50 and chart["days"] == 7
    assert t["usual"] is None and t["dial"]["unit"] is None


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


# ── the effort window: a reason of the state ────────────────────────────────

def test_effort_recent_explains_an_indicative_window_without_a_recovery_deadline():
    """The effort stays visible as a reason (owner, 2026-10-14: scientific basis — it was a cap that decayed day
    by day): red on D+0 and D+1 after an Ultra, orange to its window's last day, « fenêtre indicative jusqu'à
    J+n », never a countdown nor a promised recovery day."""
    ultra = _effort(D - timedelta(days=2), 5)  # D+2 of an Ultra: orange, its window to D+5
    w = _day([ultra])["window"]
    assert (w["tone"], w["days"], w["text"]) == ("warn", 2, "fenêtre indicative jusqu'à J+5")
    assert st.effort_window([ultra], D - timedelta(days=1))["tone"] == "danger"  # D+1: red
    assert st.effort_window([ultra], date(2026, 10, 11))["days"] == 5
    assert st.effort_window([ultra], date(2026, 10, 12)) is None  # the window is over: no reason
    very_long = _effort(D - timedelta(days=1), 3, kind="very_long")
    assert st.effort_window([very_long], D)["tone"] == "warn"  # never red but after an Ultra
    assert st.effort_window([], D) is None  # no window: no reason
    for name in ("effort_row", "night_row", "resp_row", "RESP_WORDS"):
        assert not hasattr(sante, name), name


def test_two_windows_the_strongest_names_the_reason():
    """Two windows open: the strongest holds (red first, then the bigger effort), until the last one ends."""
    old = _effort(D - timedelta(days=6), 7, sid=1)  # an Ultra of 24 h or more, D+6: orange
    new = _effort(D - timedelta(days=1), 3, kind="very_long", sid=2)  # D+1: orange
    assert st.effort_window([old, new], D)["effort"].session_id == 1  # the bigger effort
    longer = _effort(D - timedelta(days=1), 5, sid=3)  # an Ultra, D+1: red
    assert st.effort_window([old, longer], D)["effort"].session_id == 3
    assert st.effort_window([old, new], D + timedelta(days=1))["effort"].session_id == 1  # D+7 of the Ultra
    assert st.effort_window([old, new], D + timedelta(days=2))["effort"].session_id == 2  # the Ultra's over
    assert st.effort_window([old, new], D + timedelta(days=3)) is None  # D+9 and D+4: both over


def test_an_effort_window_does_not_add_a_signal_reason():
    """An open effort window cannot add a physiological reason or suppress an existing alert."""
    from tests.test_sante_score import _day as day_of
    from tests.test_sante_score import _runs, night_rows

    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)  # the alert
    ultra = _session(D - timedelta(days=7), 1500, sid=9)  # 25 h, ended on D-6: orange to D+7
    day = day_of(rows, _runs() + [ultra])
    assert day["state"]["key"] == "ill" and day["window"]["tone"] == "warn"
    assert [(r["key"], r["tone"]) for r in day["state"]["reasons"]] == [("ill", "danger")]


def test_a_card_status_says_its_place_in_words():
    """VFC / FC de nuit: the card's status line (card_status) — its percentage and its word, in its colour: « +2 %
    dans tes valeurs habituelles », « comme d'habitude » at 0 %, « plus basse / haute que d'habitude », grey and no
    percentage without a comparison (« en construction » with when its usual values will be ready, « trop peu de
    nuits pour comparer »)."""
    def status(value, key, metric="hrv"):
        day = {"day": D, "alert": None,
               "stats": {metric: {"value": value, "normal": {"center": 50.0, "provisional": False}, "status": key,
                                  "score_eligible": True}},
               "trends": {metric: {"last": True, "status": key}}}
        return sante.card_status({}, metric, day)
    assert [(c["value"], c["word"], c["tone"]) for c in (status(51.0, "in"), status(50.0, "in"),
                                                          status(44.0, "below"), status(54.5, "above"))] == [
        ("+2\u00a0%", "dans tes valeurs habituelles", "ok"), (None, "comme d'habitude", "ok"),
        ("\u221212\u00a0%", "plus basse que d'habitude", "warn"), ("+9\u00a0%", "plus haute que d'habitude", "accent")]
    assert status(None, None)["key"] == "few" and status(None, None)["word"] == "trop peu de nuits pour comparer"
    assert status(44.0, "below")["meaning"] == sante.MEANING["hrv"] and status(54.5, "above")["meaning"] is None


# ── v4.4 (owner: « Mets des pourcentages plutôt que des valeurs (comme WHOOP / Oura) ») ──────────────────────────

def test_the_night_rows_in_percent_from_real_nights():
    """Once the usual values exist, the 7-night mean against the usual value (the band's centre) in whole
    percent, half up, signed: VFC above them is « plus haute que d'habitude », neutral (never praised); « 0 % »
    reads « comme d'habitude »; a week with fewer than 3 measured nights: no percentage; the card's status line
    is one (card_status)."""
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
    assert "Sommeil compare tes 24 h, siestes comprises, au repère de 8 h." in text
    assert "Entraînement compare ta semaine à tes habitudes. L’intensité augmente le poids de chaque minute." in text
