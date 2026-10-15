"""Regression contracts for the sleep owed, the effort windows and honest curves (owner, 2026-10-14: scientific
basis — the learnt sleep base, its debt in the need and the continuous sleep cap are gone)."""
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from app.models.health import HealthMetric
from app.services import nights as nt, sante, sante_daily, sante_score as sc, sante_sleep as sl
from app.services import sante_training as st, viz

D = date(2026, 10, 10)


def history(count=40, minutes=540, source="Garmin"):
    out = {}
    for k in range(1, count + 1):
        d = D - timedelta(days=k)
        end = datetime.combine(d, time(8))
        out[d] = nt.Night(day=d, source=source, start=end-timedelta(minutes=minutes+20), end=end,
                          asleep=minutes, hr=45, hr_source=source, hrv=60, hrv_source=source)
    return out


def test_the_need_never_learns_from_the_athletes_nights():
    """The need is 8 h for everyone: the upper quartile of the athlete's own nights made a base a sleeper as long
    as usual could never reach (sim: a stable 8-h sleeper's dial at 89 %); now a 9-h sleeper owes nothing and a
    7-h one owes an hour a night, said apart."""
    assert sl.deficit(history(40, 540), D)["minutes"] == 0
    assert sl.deficit(history(40, 480), D)["minutes"] == 0
    assert sl.deficit(history(40, 420), D)["minutes"] == 7 * 60
    assert not hasattr(sl, "sleep_baseline") and not hasattr(sl, "baseline_note") and not hasattr(sl, "BASE_DAYS")


def test_the_deficit_waits_for_4_of_the_7_nights():
    """Was: the learnt base waited for 14 comparable nights over 21 days. The sleep owed waits for 4 of the 7
    nights measured (a sparse week says too little: Walsh 2021), scaled to 7 (a night not measured is never 0 h)."""
    nights = history(40, 420)
    for k in range(4, 8):  # 3 of the 7 left: D−1, D−2, D−3
        nights.pop(D - timedelta(days=k))
    thin = sl.deficit(nights, D)
    assert (thin["minutes"], thin["n"], thin["days"], thin["text"]) == (None, 3, 7, "écart non estimable (3/7 nuits)")
    nights[D - timedelta(days=4)] = replace(nights[D - timedelta(days=1)], day=D - timedelta(days=4))
    assert sl.deficit(nights, D)["minutes"] == 7 * 60  # 7/4 × 4 × 1 h


def test_chronic_short_sleep_is_owed_never_learnt():
    """6 h every night (none yet this morning): 2 h owed each night, 14 h over the 7 (it never lowered a learnt
    base either); a night's
    context (an effort, a time zone, « malade ») changes nothing: each measured night counts."""
    owed = sl.deficit(history(60, 360), D)  # 7/6 × 6 × 2 h, far over its noise floor (56 min)
    assert (owed["minutes"], owed["n"], owed["days"], owed["text"]) == (840, 6, 7, "écart estimé au repère sur 7 jours\u00a0: 14h00")
    assert owed["raw"] > owed["floor"]
    for tag in (*nt.CONTEXT, "ill"):
        nights = {d: replace(n, tags={tag}) for d, n in history(60, 360).items()}
        assert sl.deficit(nights, D)["minutes"] == 840, tag


def test_retrospective_alert_annotations_do_not_rewrite_the_sleep_owed():
    """The illness episode's annotation is recomputed for the page; it never changes what the nights owe."""
    nights = history(60, 360)
    before = sl.deficit(nights, D)
    annotated = {d: replace(n, tags={"alert"}) for d, n in nights.items()}
    assert sl.deficit(annotated, D) == before


def test_the_deficit_never_reads_a_later_night():
    """Past days are recomputed without later data: tomorrow's night never changes today's sleep owed; this
    morning's own night is one of its 7."""
    nights = history(40, 480)
    first = sl.deficit(nights, D)
    nights[D + timedelta(days=1)] = replace(nights[D - timedelta(days=1)], day=D + timedelta(days=1), asleep=300)
    assert sl.deficit(nights, D) == first
    nights[D] = replace(nights[D - timedelta(days=1)], day=D, asleep=300)
    assert sl.deficit(nights, D)["minutes"] == 180  # 3 h short this morning (D−6 → D)


@pytest.mark.parametrize("metric,value", [("hr", 65), ("hrv", 20), ("resp", 24)])
def test_an_abnormal_heart_night_still_counts_its_sleep(metric, value):
    """The sleep owed reads the 24 h only: a night with an unusual HR, HRV or breathing rate counts its sleep like
    any other (the learnt base used to leave it out of its sample)."""
    nights = {d: replace(n, resp=14, resp_source="Garmin") for d, n in history(40, 420).items()}
    recent = D - timedelta(days=1)
    nights[recent] = replace(nights[recent], **{metric: value})
    assert sl.deficit(nights, D)["minutes"] == 420


def test_need_has_no_future_leak_and_naps_repay_debt_once():
    nights = history(40, 540)
    first = sl.deficit(nights, D)
    tomorrow = D+timedelta(days=1)
    nights[tomorrow] = replace(nights[D-timedelta(days=1)], day=tomorrow, asleep=300, source="COROS")
    assert sl.deficit(nights, D) == first
    # Nap allocation is shared by the 24 h and the sleep owed: each nap counted once.
    short = history(8, 360)
    yesterday = D-timedelta(days=1)
    start = datetime.combine(yesterday, time(14))
    short[yesterday].naps = [(start, start+timedelta(hours=2), 120)]
    short[yesterday].nap_source = "Garmin"
    short[D] = replace(short[yesterday], day=D, naps=[], start=datetime.combine(yesterday, time(23)),
                       end=datetime.combine(D, time(7)))
    assert nt.slept_before_wake(short, yesterday) == 360
    assert nt.slept_before_wake(short, D) == 480
    assert sl.deficit(short, D)["minutes"] == 6 * 120  # 6 nights of 6 h, this morning's 8 h with the nap


@pytest.mark.parametrize("need", [420, 480, 540])
def test_the_sleep_dial_is_monotone_and_short_sleep_is_a_reason_under_6_h(need):
    """The dial rises with the 24 h, never by a step (min(100 %, 24 h ÷ the need)); « court » and the short-sleep
    reason from under 6 h (Craven 2022; the continuous cap relative to a learnt base is gone)."""
    values = [int(sante.sleep_dial(m, need, None)["value"]) for m in range(0, 601)]
    assert values == sorted(values) and max(b - a for a, b in zip(values, values[1:])) <= 1
    assert sante.sleep_dial(359, need, None)["sub"] == "court" and sante.sleep_dial(360, need, None)["sub"] != "court"
    assert not hasattr(sc, "sleep_limit") and not hasattr(sc, "CAP_SHORT")


def test_all_effort_windows_end_on_their_last_day_and_overlapping_efforts_use_the_strongest():
    for kind, last in st.EFFORT_WINDOWS.items():
        e = st.Effort(1, kind, 700, datetime.combine(D, time(18)), D, last=last)
        tones = [st.effort_window([e], D+timedelta(days=k))["tone"] for k in range(0, last+1)]
        assert tones == ["danger" if kind == "ultra" and k <= 1 else "warn" for k in range(0, last + 1)]
        assert st.effort_window([e], D+timedelta(days=last+1)) is None
    old = st.Effort(1, "ultra", 700, datetime.combine(D, time(18)), D-timedelta(days=8), last=st.EFFORT_WINDOWS["ultra"])
    recent = replace(old, session_id=2, kind="long", day=D-timedelta(days=1), last=st.EFFORT_WINDOWS["long"])
    assert st.effort_window([old, recent], D)["effort"].session_id == 2  # the ultra's window is over


def test_valid_mean_survives_missing_raw_night_but_does_not_create_a_score():
    nights = history(21, 480)
    # There is still a usable rolling window today, but no new night.
    day = sante._assess(nights, [], [], D)
    assert nt.mean7(nights, "hr", D)["n"] == 6
    assert day["score"]["value"] is None
    chart = sante._night_card(nights, "hr", D, day)
    data = json.loads(chart["data"])
    assert data["r"][-1][0] == "—" and "moyenne 7 jours" in data["r"][-1][3]
    assert "6 nuits" in data["r"][-1][3] and "Garmin" in data["r"][-1][3]
    assert chart["dots"][-1]["i"] == chart["n"]-2
    assert str(data["x"][-1]) in chart["mean"]


def test_long_sleep_mean_uses_one_watch_and_can_cross_a_missing_bar():
    nights = history(25, 480)
    chart = sl.bars(nights, D, "90")
    data = json.loads(chart["data"])
    assert chart["bars"][-1]["miss"] and "6 nuits" in data["r"][-1][2]
    assert str(data["x"][-1]) in chart["trend"]
    nights[D] = replace(nights[D-timedelta(days=1)], day=D, source="COROS")
    changed = sl.bars(nights, D, "90")
    # one COROS night among 24 Garmin ones: the series stays the most worn watch's (owner, 2026-10-14: scientific
    # basis — it switched to COROS at once, and the line broke), the COROS night never in its mean
    read = json.loads(changed["data"])["r"][-1][2]
    assert "moyenne 7 jours" in read and "6 nuits" in read and "Garmin" in read


def test_daily_reference_uses_elapsed_days_same_source_and_preserves_zero():
    def row(k, value, source="Garmin"):
        return HealthMetric(date=D-timedelta(days=k), metric="steps", value=value, source=source)
    rows = [row(0, 150000), row(1, 0), row(2, 10000), row(3, 20000), row(4, 30000),
            row(5, 150000, "COROS"), row(8, 150000)]
    card = sante_daily.daily_card("steps", rows, D)
    assert "15000" in card["chart"]["ref"]["label"]
    assert "4 jours" in card["trend"]
    assert card["chart"]["dots"][-2]  # measured zero is retained as a point
    assert sante_daily.daily_card("steps", rows[:4], D)["chart"]["ref"] is None
