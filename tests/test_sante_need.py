"""The sleep need, computed and never asked (owner, 2026-10-09: « Ne demande pas, ce doit être auto comme WHOOP »;
research_ind_sleep_resp.md A4) and the breathing rate (« utilise la respiration aussi si tu l'as »; B3): the need's
parts, the Sommeil dial and score read against it, its line on the Sommeil card; the breathing rate's usual line,
its row, its cap and the alert's sentence."""
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


# ── the need: computed, never asked (owner, 2026-10-09: « Ne demande pas, ce doit être auto comme WHOOP ») ──

def test_the_need_is_8_h_and_a_short_week_adds_at_most_1_h():
    """7h20 every night: 40 min short each of the 7 days before, a quarter of it is 70 min, 1 h at most: a 9-h need;
    7h20 is 81 % of it, « un peu court », its sub-score between ¾ and ⅞ of it. 8 h every night: 8 h, nothing to say."""
    rows = night_rows(range(0, 20))
    assert sl.sleep_need(_nights(rows), D) == {"total": 540, "base": 480, "effort": 0, "debt": 60}
    day = _day(rows, base=None)
    assert day["need"]["total"] == 540 and day["tst24"] == 440
    dial = sante.sleep_dial(day["tst24"], day["need"]["total"], "#sommeil")
    assert (dial["value"], dial["sub"], dial["tone"]) == ("81", "un peu court", "sleep")
    sleep = next(p for p in day["score"]["parts"] if p["key"] == "sleep")["sub"]
    assert sleep == sc.sleep_sub(440, 540) and 60 < sleep < 100
    rested = sl.sleep_need(_nights(night_rows(range(0, 20), asleep=480)), D)
    assert rested == {"total": 480, "base": 480, "effort": 0, "debt": 0} and sl.need_line(rested) is None


def test_a_long_night_repays_the_short_ones_and_a_day_without_a_night_is_skipped():
    """3 nights of 9 h and 4 of 7 h owe (4 − 3) × 60 = 60 min, a quarter: 15 → 8h20 (8h15 half up); a night not
    recorded is never 0 h (its day skipped): the 7 h nights alone then."""
    rows = _asleep(night_rows(range(0, 20)), {1: 540, 2: 540, 3: 540, 4: 420, 5: 420, 6: 420, 7: 420})
    assert sl.sleep_need(_nights(rows), D) == {"total": 500, "base": 480, "effort": 0, "debt": 20}
    for k in (1, 2, 3):
        for metric in rows:
            rows[metric].pop(D - timedelta(days=k), None)
    assert sl.sleep_need(_nights(rows), D)["debt"] == 60  # 4 × 60 owed, a quarter, 1 h at most


def test_a_big_effort_adds_30_min_to_its_night_and_to_the_nights_it_owes():
    """The night after a big effort (tagged long, big or ultra) asks 30 min more (H); a past one owes against its
    own larger need, never against today's (no compounding)."""
    nights = _nights(night_rows(range(0, 20), asleep=480))
    nights[D].tags.add("ultra")
    assert sl.sleep_need(nights, D) == {"total": 510, "base": 480, "effort": 30, "debt": 0}
    nights[D].tags.discard("ultra")
    nights[D - timedelta(days=1)].tags.add("long")  # 8 h slept against 8h30: 30 min owed, a quarter
    assert sl.sleep_need(nights, D) == {"total": 490, "base": 480, "effort": 0, "debt": 10}  # 7,5 half up


def test_a_night_spent_racing_counts_as_its_naps_only():
    """A race through the night (01:00 → 05:00 covered) leaves no main night: its day is not skipped but counted as
    its naps (Kishi 2024), 8 h owed, 1 h at most; the wake days a 25-h race ran through, from its effort."""
    rows = night_rows(range(0, 20), asleep=480)
    for metric in rows:
        rows[metric].pop(D - timedelta(days=2), None)
    nights = _nights(rows)
    assert sl.sleep_need(nights, D)["debt"] == 0  # not recorded: skipped
    raced = frozenset({D - timedelta(days=2)})
    assert sl.sleep_need(nights, D, raced) == {"total": 540, "base": 480, "effort": 0, "debt": 60}
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
    assert sl.sleep_need(nights, D)["debt"] == 20  # 60 min short, a quarter: 15 → half up with the base
    rows["nap"][y] = (120, {"windows": [[f"{y}T15:00", f"{y}T17:00"]]}, "Garmin")  # an afternoon nap
    nights = _nights(rows)
    assert nt.slept_before_wake(nights, y) == 300 and nt.slept_before_wake(nights, D) == 600
    assert sl.sleep_need(nights, D)["debt"] == 50  # its 3 h owed, a quarter: 45 → half up 50 (it is today's)


# ── the need on the Sommeil card ────────────────────────────────────────────

def test_the_need_line_says_what_adds_to_the_8_h():
    def need(effort=0, debt=0):
        return {"total": 480 + effort + debt, "base": 480, "effort": effort, "debt": debt}
    assert sl.need_line(need(debt=60)) == f"Besoin estimé{NB}: 8{NB}h, + 1{NB}h de sommeil en retard."
    assert sl.need_line(need(30, 20)) == (
        f"Besoin estimé{NB}: 8{NB}h, + 30{NB}min après un gros effort, + 20{NB}min de sommeil en retard.")
    assert sl.need_line(need()) is None  # a plain 8-h day: the night's row says « besoin estimé 8h00 »
    assert (sl.hm_words(30), sl.hm_words(60), sl.hm_words(545)) == (f"30{NB}min", f"1{NB}h", f"9{NB}h{NB}05")


async def test_the_card_never_asks_the_need(as_user: AsyncClient, db_session: AsyncSession, test_user: User,
                                            monkeypatch):
    """No question, no form, no « Changer »: the card says the need computed for that morning, once; the old
    answer's address is gone and ?besoin, from an old page, changes nothing."""
    async def today(*a, **k):
        return D
    monkeypatch.setattr(sante, "athlete_today", today)
    await _seed_rows(db_session, test_user, _garmin_rows(D))
    for path in ("/sante", "/sante?besoin=1"):
        card = (await as_user.get(path)).text.split('<details id="sommeil"')[1].split("</details>")[0]
        assert 'name="need"' not in card and "Combien d" not in card and "Changer" not in card and "pf-need-chip" not in card
        assert card.count(f"Besoin estimé{NB}: 7{NB}h{NB}30, + 20{NB}min de sommeil en retard.") == 1
        assert "Base personnelle estimée" in card
        assert "besoin estimé 7h50" in card
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


def test_two_nights_over_the_line_cap_the_score_at_69_and_the_row_says_it():
    """Two nights in a row over the line (full band, same watch, no context tag) cap the score at 69 (H, like
    WHOOP's one-sided adjustment), the row « plus rapide depuis 2 nuits » in orange; one night: neutral, no cap;
    under the line: « dans tes valeurs habituelles », green; a night after an effort explains it: no cap."""
    up = _resp(night_rows(range(0, 30)), value=lambda k: 17.0 if k < 2 else 14.0 + (k % 3) * 0.2, days=range(0, 30))
    day = _day(up)
    assert day["resp"]["days"] == [D - timedelta(days=1), D] and "resp" in day["score"]["caps"]
    assert day["score"]["value"] == sc.CAP_RESP == 69 and day["score"]["reason"] == "resp"
    with nt.memo():
        nights = _nights(up)
        nt.freeze(nights)
        row = sante.resp_row(nights, D, day)
    assert (row["name"], row["qual"], row["word"], row["tone"]) == (
        "Respiration", "cette nuit", "plus rapide depuis 2 nuits", "warn")
    one = _resp(night_rows(range(0, 30)), value=lambda k: 17.0 if k < 1 else 14.0 + (k % 3) * 0.2, days=range(0, 30))
    d1 = _day(one)
    assert d1["resp"] is None and "resp" not in d1["score"]["caps"]
    with nt.memo():
        nights = _nights(one)
        nt.freeze(nights)
        r1 = sante.resp_row(nights, D, d1)
    assert (r1["word"], r1["tone"]) == ("plus rapide que d'habitude", "accent")
    calm = _resp(night_rows(range(0, 30)), days=range(0, 30))
    with nt.memo():
        nights = _nights(calm)
        nt.freeze(nights)
        r0 = sante.resp_row(nights, D, _day(calm))
    assert r0["tone"] == "ok" and r0["word"] in ("comme d'habitude", "dans tes valeurs habituelles")
    nights = _nights(up)
    nights[D].tags.add("long")
    assert nt.resp_up_nights(nights, D) is None


def test_no_breathing_rate_no_row_and_its_usual_values_built_first():
    """COROS sends none: no row at all; a new watch: « en construction » until 7 nights, ready when."""
    with nt.memo():
        nights = _nights(night_rows(range(0, 20)))
        nt.freeze(nights)
        assert sante.resp_row(nights, D, {}) is None
    rows = _resp(night_rows(range(0, 20)), days=range(0, 4))
    with nt.memo():
        nights = _nights(rows)
        nt.freeze(nights)
        row = sante.resp_row(nights, D, {})
    assert (row["word"], row["detail"], row["tone"]) == ("en construction", f"prête dans 3{NB}nuits", "none")


def test_the_alert_says_the_breathing_rate_too_when_it_is_up():
    score = {"value": 39, "tone": "danger", "reason": "ill"}
    alone = td.state(score, {"alert": {"resp_up": False}})
    both = td.state(score, {"alert": {"resp_up": True}})
    assert alone["text"] == td.ILL and both["text"] == f"{td.ILL} {td.ILL_RESP}"
    assert both["aria"].endswith("Ta respiration aussi est plus rapide que d'habitude.")
