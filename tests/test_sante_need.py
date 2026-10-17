"""The sleep need, never asked (owner, 2026-10-09: « Ne demande pas, ce doit être auto comme WHOOP ») and 8 h for
everyone, the sleep owed over 7 nights said apart, never added to it (owner, 2026-10-14: scientific basis — the
need was a learnt base plus an effort's 30 min plus a quarter of the debt); and the breathing rate (« utilise la
respiration aussi si tu l'as »; B3): its usual line, its flag (an orange reason of the state, with its sentence)
and the alert's sentence."""
import math
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services import nights as nt
from app.services import sante
from app.services import sante_score as sc
from app.services import sante_sleep as sl
from app.services import sante_today as td
from app.services import sante_training as st
from app.services.sante_training import Effort
from tests import test_coros
from tests.test_sante import _garmin_rows, _seed_rows
from tests.test_sante_score import _day, _nights, night_rows

as_user, no_commit = test_coros.as_user, test_coros.no_commit
D = date(2026, 10, 8)
NB = " "


def _asleep(rows: dict, minutes: dict) -> dict:
    """The rows with other minutes asleep on some days (offsets before D)."""
    for k, m in minutes.items():
        v, det, src = rows["sleep"][D - timedelta(days=k)]
        rows["sleep"][D - timedelta(days=k)] = (m, det, src)
    return rows


# ── the need: 8 h, never asked; the sleep owed said apart ───────────────────

def _owed(deficit: dict) -> dict:
    """What a deficit says (its raw value and noise floor apart)."""
    return {k: deficit[k] for k in ("minutes", "n", "raced", "days", "text")}


def test_the_need_is_8_h_and_a_short_week_is_said_apart():
    """7h20 every night: 40 min short each of the 7 nights, the sleep owed is 4h40, said in hours, never added to
    the need (it was a 9-h need: 8 h + a quarter of it, 1 h at most); 7h20 is 92 % of 8 h, « suffisant » (from
    7 h: it read « un peu court » against the 9 h). 8 h
    every night: nothing owed."""
    rows = night_rows(range(0, 20))
    assert _owed(sl.deficit(_nights(rows), D)) == {"minutes": 280, "n": 7, "raced": 0, "days": 7,
                                                   "text": f"écart estimé au repère sur 7 jours{NB}: 4h40"}
    day = _day(rows)
    assert day["tst24"] == 440 and "need" not in day
    dial = sante.sleep_dial(day["tst24"], sl.NEED_DEFAULT, "#sommeil")
    assert (dial["value"], dial["sub"], dial["tone"]) == ("92", "suffisant", "sleep")
    rested = sl.deficit(_nights(night_rows(range(0, 20), asleep=480)), D)
    assert (rested["minutes"], rested["text"]) == (0, "écart faible au repère sur 7 jours")


def test_a_long_night_repays_the_short_ones_and_a_night_not_measured_is_never_0_h():
    """7h20 this morning, 3 nights of 9 h and 3 of 7 h: 40 − 3 × 60 + 3 × 60 = 40 min owed, under its floor: every
    night measured, the week's debt is known, so the floor is 1 h (it was 1,28 · σ_T · 7/√7 = 93 min for a σ_T of
    27 min, a test of the athlete's mean against 8 h, not of the week's debt: the second statistics review,
    2026-10-14); a night not recorded is never 0 h: over the 4 nights measured, the 7 nights' estimate is 7/4 of
    their sum (they were skipped, so the debt halved when the watch was worn one night in two), over its floor of
    1,28 · σ_T · √(3 · 7/4) (the 3 nights it stands in for: 79 min); under 4 nights of the 7, not estimable."""
    rows = _asleep(night_rows(range(0, 20)), {1: 540, 2: 540, 3: 540, 4: 420, 5: 420, 6: 420, 7: 420})
    repaid = sl.deficit(_nights(rows), D)
    assert (repaid["raw"], round(repaid["floor"]), repaid["minutes"]) == (40, 60, 0)  # floor was 93
    for k in (1, 2, 3):
        for metric in rows:
            rows[metric].pop(D - timedelta(days=k), None)
    four = sl.deficit(_nights(rows), D)
    assert four["minutes"] == 390  # 7/4 × (40 + 3 × 60) = 385, to 10 min
    assert round(four["floor"]) == round(1.28 * sl.tst_spread(_nights(rows), D) * math.sqrt(3 * 7 / 4))
    for metric in rows:  # this morning and 5, 6 days ago left: 3 of the 7
        rows[metric].pop(D - timedelta(days=4), None)
    thin = sl.deficit(_nights(rows), D)
    assert (thin["minutes"], thin["n"], thin["text"]) == (None, 3, "écart non estimable (3/7 nuits)")


def test_a_big_effort_no_longer_adds_to_the_need():
    """The night after a big effort (tagged long, big or ultra) asked 30 min more: no study gave that dose (owner,
    2026-10-14: scientific basis); the need stays 8 h, the 24 h still say how the night went."""
    nights = _nights(night_rows(range(0, 20), asleep=480))
    nights[D].tags.add("ultra")
    assert sl.deficit(nights, D)["minutes"] == 0 and sl.NEED_DEFAULT == 480
    for name in ("NEED_EFFORT", "NEED_DEBT_SHARE", "NEED_DEBT_MAX", "sleep_need", "sleep_baseline", "need_line"):
        assert not hasattr(sl, name), name


def test_a_night_spent_racing_counts_as_its_naps_only():
    """A race through the night (01:00 → 05:00 covered) leaves no main night: its day is not skipped but counted as
    its naps (Kishi 2024), 8 h owed, never scaled up by the other nights' 7/n (owner, 2026-10-14: scientific
    basis — 3 nights of 8 h and a raced one read 14h00, and became estimable with 3 measured nights); the wake
    days a 25-h race ran through, from its effort."""
    rows = night_rows(range(0, 20), asleep=480)
    for metric in rows:
        rows[metric].pop(D - timedelta(days=2), None)
    nights = _nights(rows)
    assert sl.deficit(nights, D)["minutes"] == 0  # not recorded: 6 nights, none short
    raced = frozenset({D - timedelta(days=2)})
    assert _owed(sl.deficit(nights, D, raced)) == {"minutes": 480, "n": 6, "raced": 1, "days": 7,
                                                   "text": f"écart estimé au repère sur 7 jours{NB}: 8h00"}
    for k in (1, 3, 4):  # 3 nights of 8 h measured and the raced one: not estimable (4 measured nights needed)
        for metric in rows:
            rows[metric].pop(D - timedelta(days=k), None)
    assert sl.deficit(_nights(rows), D, raced)["text"] == "écart non estimable (3/7 nuits)"
    start = datetime(2026, 10, 2, 21, 0)  # Friday 21:00 → Sunday 0:00 local: the nights of Saturday and Sunday
    race = Effort(session_id=1, kind="ultra", minutes=27 * 60, end=start + timedelta(hours=27), day=date(2026, 10, 3))
    assert st.raced_nights([race]) == {date(2026, 10, 3)}  # Sunday's 01:00 → 05:00 not covered: its night slept
    assert st.raced_nights([replace(race, minutes=33 * 60, end=start + timedelta(hours=33))]) == {
        date(2026, 10, 3), date(2026, 10, 4)}


def test_a_rendormi_nap_counts_once_with_its_own_night():
    """A nap ≤ 3 h after the wake belongs to that night (nights.day_naps never gives it to the next morning): the
    sleep owed reads it there, never lost; an afternoon nap is the next morning's, never twice."""
    rows = night_rows(range(0, 20), asleep=480)
    y = D - timedelta(days=1)
    rows["sleep"][y] = (300, rows["sleep"][y][1], "Garmin")  # 5 h, woke 06:40
    rows["nap"][y] = (120, {"windows": [[f"{y}T07:00", f"{y}T09:00"]]}, "Garmin")  # back to sleep 2 h
    nights = _nights(rows)
    assert nt.slept_before_wake(nights, y) == 420 and nt.day_tst24(nights, D) == 480  # never the next morning's
    assert sl.deficit(nights, D)["raw"] == 60  # 1 h short, once (under its noise floor: « none »)
    rows["nap"][y] = (120, {"windows": [[f"{y}T15:00", f"{y}T17:00"]]}, "Garmin")  # an afternoon nap
    nights = _nights(rows)
    assert nt.slept_before_wake(nights, y) == 300 and nt.slept_before_wake(nights, D) == 600
    assert sl.deficit(nights, D)["raw"] == 60  # 3 h short yesterday, 2 h over this morning: 1 h


# ── the sleep owed on the Sommeil card ──────────────────────────────────────

def test_the_deficit_says_the_sleep_owed_in_hours():
    """The Sommeil card's data (its wording on the page: a GUI proposal): « écart estimé au repère sur 7 jours : 3h00 »,
    « écart faible au repère sur 7 jours » (1 h at most, its floor with every night measured: the second statistics
    review, 2026-10-14 — « pas de manque » was false for a 45-min debt), « écart non estimable (3/7 nuits) »; never
    a « besoin » raised by it."""
    short = sl.deficit(_nights(_asleep(night_rows(range(0, 20), asleep=480), {0: 300})), D)
    assert short["text"] == f"écart estimé au repère sur 7 jours{NB}: 3h00"
    once = sl.deficit(_nights(_asleep(night_rows(range(0, 20), asleep=480), {0: 420})), D)
    assert (once["raw"], once["floor"], once["text"]) == (60, 60, "écart faible au repère sur 7 jours")
    assert sl.deficit(_nights(night_rows(range(0, 20), asleep=500)), D)["text"] == "écart faible au repère sur 7 jours"
    assert sl.deficit(_nights(night_rows([0, 2, 5], asleep=420)), D)["text"] == "écart non estimable (3/7 nuits)"


def test_the_floor_is_what_the_estimate_cannot_tell_and_one_hour_at_least():
    """The floor is the extrapolation's error only, max(1 h, 1,28 · σ_T · √(u · (7 − r)/n_m)), u the nights not
    measured (the second statistics review, 2026-10-14: 1,28 · σ_T · 7/√n_m tested the athlete's mean against 8 h,
    not the week's debt, and left the raced nights out): with every night measured the week's debt is known."""
    def week(asleep, measured=range(0, 7)):
        rows = night_rows(range(7, 60), asleep=480)  # a steady 8-h history: σ_T the prior's share (45 min)
        rows = {m: {**rows[m], **night_rows(measured, asleep=asleep)[m]} for m in rows}
        return _nights(rows)
    nights = week(460)  # 7h40 × 7: 2h20 owed, said (with a noisier history, σ_T 73 min, the old floor was 2h32)
    assert (sl.deficit(nights, D)["floor"], sl.deficit(nights, D)["text"]) == (60, f"écart estimé au repère sur 7 jours{NB}: 2h20")
    nights = week(450)  # 7h30 × 7: 3h30 owed
    assert sl.deficit(nights, D)["text"] == f"écart estimé au repère sur 7 jours{NB}: 3h30"
    nights = week(480, measured=(0, 2, 4, 5))  # 4 nights of 8 h measured, one raced through, 2 not measured
    raced = sl.deficit(nights, D, frozenset({D - timedelta(days=1)}))
    sigma = sl.tst_spread(nights, D)
    assert (raced["n"], raced["raced"], raced["minutes"]) == (4, 1, 480)
    assert raced["floor"] == pytest.approx(max(60, 1.28 * sigma * math.sqrt(2 * 6 / 4)))
    nights = week(480, measured=(0, 2, 4))  # 3 measured and a raced one: not estimable
    assert sl.deficit(nights, D, frozenset({D - timedelta(days=1)}))["text"] == "écart non estimable (3/7 nuits)"


async def test_the_card_never_asks_the_need(as_user: AsyncClient, db_session: AsyncSession, test_user: User,
                                            monkeypatch):
    """No question, no form, no « Changer »: the card says the need computed for that morning, once; the old
    answer's address is gone and ?besoin, from an old page, changes nothing."""
    async def today(*a, **k):
        return D
    monkeypatch.setattr(sante, "athlete_today", today)
    await _seed_rows(db_session, test_user, _garmin_rows(D))
    for path in ("/sante", "/sante?besoin=1"):
        card = (await as_user.get(path)).text.split('<section id="sommeil"')[1].split("</section>")[0]
        assert 'name="need"' not in card and "Combien d" not in card and "Changer" not in card and "pf-need-chip" not in card
        assert "Base personnelle estimée" not in card
        assert "Comprendre le sommeil" not in card
        assert "repère 8h00" not in card  # 8 h for everyone (it was a learnt 7h50)
    assert (await as_user.post("/sante/besoin", data={"need": "450"})).status_code in (404, 405)
    assert not hasattr(User, "sleep_need_min")


# ── the breathing rate ──────────────────────────────────────────────────────

def _resp(rows: dict, value=lambda k: 14.0 + (k % 3) * 0.2, days=range(0, 20), source="Garmin") -> dict:
    rows["resp_night"] = {D - timedelta(days=k): (value(k), {"method": "points"}, source) for k in days}
    return rows


def test_the_breathing_rate_line_is_1_per_min_or_2_sd_over_its_median():
    """Its night-to-night SD is small (0.5 /min: Miller 2020): the line is the median + the larger of 1 /min and
    2 SD, the SD shrunk towards 0.5 (H); one-sided: no lower line; Garmin's own night average never in our band."""
    nights = _nights(_resp(night_rows(range(0, 20))))
    b = nt.band(nights, "resp", D)
    assert b["center"] == 14.2 and b["lo"] is None and b["hi"] is None
    assert b["up"] == b["center"] + max(1.0, 2 * b["sd"]) and 1.0 <= b["up"] - b["center"] < 2.5
    wide = _nights(_resp(night_rows(range(0, 20)), value=lambda k: 13.0 + (k % 4)))  # 13 to 16 /min
    w = nt.band(wide, "resp", D)
    assert w["up"] - w["center"] == pytest.approx(2 * w["sd"]) and 2 * w["sd"] > 1
    rows = _resp(night_rows(range(0, 20)))
    for d, (v, det, src) in list(rows["resp_night"].items()):
        rows["resp_night"][d] = (v, {"method": "garmin_summary"}, src)
    summary = nt.build_nights(rows, D)
    assert summary[D].resp_source == "Garmin (résumé)" and nt.band(summary, "resp", D)["source"] == "Garmin (résumé)"


def test_two_nights_over_the_line_are_an_orange_reason_with_its_sentence():
    """The breathing flag (owner, 2026-10-14: scientific basis — it capped the score at 69 while the page showed it
    as « Au quotidien » context): the mean of last night and the night before ≥ the median + max(1 /min, 2 σ̂) of
    its full baseline (days −67 → −8), on one series, neither night context-tagged: an orange reason, its sentence
    in the reasons, the indice held to 69; listed in « Mesures utilisées ». One night far up can carry the mean; a
    night after an effort explains it: no flag."""
    up = _resp(night_rows(range(0, 30)), value=lambda k: 17.0 if k < 2 else 14.0 + (k % 3) * 0.2, days=range(0, 30))
    day = _day(up)
    assert day["resp"] == {"days": [D - timedelta(days=1), D], "values": [17.0, 17.0], "mean": 17.0,
                           "line": pytest.approx(15.2)}
    assert day["score"]["value"] == 69 and day["score"]["reason"] == "resp"
    reason = day["state"]["reasons"][0]
    assert (reason["key"], reason["tone"], reason["label"]) == ("resp", "warn", "Respiration plus rapide depuis 2 nuits")
    assert reason["text"] == sc.TEXTS["resp"]
    assert {r["name"]: r["state"] for r in sc.quality(day)["rows"]}["Respiration"] == "Utilisée"
    one = _resp(night_rows(range(0, 30)), value=lambda k: 17.0 if k < 1 else 14.0 + (k % 3) * 0.2, days=range(0, 30))
    assert _day(one)["resp"]["mean"] == pytest.approx(15.6)  # 17 and 14,2: over the 15,2 line
    calm = _resp(night_rows(range(0, 30)), value=lambda k: 16.0 if k < 1 else 14.0 + (k % 3) * 0.2, days=range(0, 30))
    assert _day(calm)["resp"] is None and _day(calm)["state"]["key"] == "ok"  # 16 and 14,2: 15,1
    nights = _nights(up)
    nights[D].tags.add("long")
    assert nt.resp_up_nights(nights, D) is None


def test_no_breathing_rate_no_row_and_its_usual_values_built_first():
    """COROS sends none: no Respiration row in « Mesures utilisées »; a new watch: its baseline builds (14 nights
    from day −67 to −8)."""
    assert "Respiration" not in {r["name"] for r in sc.quality(_day(night_rows(range(0, 20))))["rows"]}
    rows = _resp(night_rows(range(0, 20)), days=range(0, 4))
    rows_by = {r["name"]: r["state"] for r in sc.quality(_day(rows))["rows"]}
    assert rows_by["Respiration"] == "Repères en construction (0/14)"


def test_the_alert_says_the_breathing_rate_too_when_it_is_up():
    score = {"value": 39, "tone": "danger", "reason": "ill"}
    alone = td.state(score, {"alert": {"resp_up": False}})
    both = td.state(score, {"alert": {"resp_up": True}})
    assert alone["text"] == td.ILL and both["text"] == f"{td.ILL} {td.ILL_RESP}"
    assert both["aria"].endswith("Ta respiration aussi est plus rapide que d'habitude.")
