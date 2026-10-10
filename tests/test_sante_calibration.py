"""Regression contracts for estimated sleep, progressive precautions and honest curves."""
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


def test_sleep_base_waits_for_enough_comparable_nights_and_calendar_span():
    assert not sl.sleep_baseline(history(13), D)["adapted"]
    assert not sl.sleep_baseline(history(20), D)["adapted"]
    nights = history(21)
    for k in range(2, 9):
        nights.pop(D-timedelta(days=k))
    b = sl.sleep_baseline(nights, D)
    assert (b["n"], b["span"], b["adapted"]) == (14, 21, True)
    assert 480 < b["value"] < 540  # evidence is shrunk towards the starting base
    assert "personnelle estimée" in sl.baseline_note(b)["label"]
    assert "apprentissage" in sl.baseline_note(sl.sleep_baseline(history(8), D))["label"]


def test_sleep_base_cannot_learn_chronic_short_sleep_or_disrupted_nights():
    short = sl.sleep_baseline(history(60, 360), D)
    assert (short["value"], short["n"], short["adapted"]) == (480, 0, False)
    for tag in (*nt.CONTEXT, "ill"):
        nights = {d: replace(n, tags={tag}) for d, n in history().items()}
        assert sl.sleep_baseline(nights, D)["n"] == 0, tag
    nights = history()
    assert sl.sleep_baseline(nights, D, frozenset(nights))["n"] == 0
    for minutes in (420, 480, 600):
        assert 420 <= sl.sleep_baseline(history(60, minutes), D)["value"] <= 540
    assert sl.sleep_baseline(history(60, 601), D)["n"] == 0


def test_baseline_is_source_specific_and_never_reads_the_current_or_future_duration():
    nights = history()
    original = sl.sleep_baseline(nights, D)
    today = replace(nights[D-timedelta(days=1)], day=D, asleep=300)
    # The current night selects the source, but cannot lower its own target.
    nights[D] = today
    nights[D+timedelta(days=1)] = replace(today, source="COROS", asleep=600)
    assert sl.sleep_baseline(nights, D) == original
    nights[D] = replace(today, source="COROS")
    b = sl.sleep_baseline(nights, D)
    assert (b["value"], b["n"], b["source"], b["adapted"]) == (480, 0, "COROS", False)


def test_retrospective_alert_annotations_do_not_rewrite_the_sleep_reference():
    nights = history()
    before = sl.sleep_baseline(nights, D)
    # The episode annotation is recomputed for the page, while score history
    # rebuilds its own tags. It cannot decide baseline membership; raw signals do.
    annotated = {d: replace(n, tags={"alert"}) for d, n in nights.items()}
    assert sl.sleep_baseline(annotated, D) == before


@pytest.mark.parametrize("metric,value", [("hr", 65), ("hrv", 20), ("resp", 24)])
def test_learning_rejects_abnormal_nights_against_prior_same_source_reference(metric, value):
    nights = {d: replace(n, resp=14, resp_source="Garmin") for d, n in history().items()}
    original = sl.sleep_baseline(nights, D)["n"]
    recent = D-timedelta(days=1)
    nights[recent] = replace(nights[recent], **{metric: value})
    assert sl.sleep_baseline(nights, D)["n"] == original - 1


def test_need_has_no_future_leak_and_naps_repay_debt_once():
    nights = history(40, 540)
    first = sl.sleep_need(nights, D)
    tomorrow = D+timedelta(days=1)
    nights[tomorrow] = replace(nights[D-timedelta(days=1)], day=tomorrow, asleep=300, source="COROS")
    assert sl.sleep_need(nights, D) == first
    assert first["total"] == sum(first[k] for k in ("base", "effort", "debt"))
    # Nap allocation is shared by the baseline and debt calculation.
    short = history(8, 360)
    yesterday = D-timedelta(days=1)
    start = datetime.combine(yesterday, time(14))
    short[yesterday].naps = [(start, start+timedelta(hours=2), 120)]
    short[yesterday].nap_source = "Garmin"
    short[D] = replace(short[yesterday], day=D, naps=[], start=datetime.combine(yesterday, time(23)),
                       end=datetime.combine(D, time(7)))
    assert nt.slept_before_wake(short, yesterday) == 360
    assert nt.slept_before_wake(short, D) == 480
    assert sl.sleep_need(short, D)["base"] == 480  # insufficient comparable history


@pytest.mark.parametrize("base", [420, 480, 540])
def test_sleep_guard_is_monotone_continuous_and_relative_to_the_base(base):
    limits = [sc.sleep_limit(m, base) for m in range(0, 601)]
    effective = [100 if v is None else v for v in limits]
    assert effective == sorted(effective)
    assert max(b-a for a, b in zip(effective, effective[1:])) < 1
    assert sc.sleep_limit(base*3/4, base) == 65
    assert sc.sleep_limit(None, base) is None
    assert sc.sleep_limit(480, base) is None
    assert effective[359] < effective[360] < effective[361]


def test_all_effort_windows_relax_daily_and_overlapping_efforts_use_current_precaution():
    for kind, rules in st.EFFORT_RULES.items():
        e = st.Effort(1, kind, 700, datetime.combine(D, time(18)), D, caps=rules)
        caps = [st.effort_window([e], D+timedelta(days=k))["cap"] for k in range(1, rules[-1][0]+1)]
        assert caps[0] == rules[0][1]
        assert all(a < b < 100 for a, b in zip(caps, caps[1:]))
        assert st.effort_window([e], D+timedelta(days=rules[-1][0]+1)) is None
    old = st.Effort(1, "ultra", 700, datetime.combine(D, time(18)), D-timedelta(days=8), caps=st.EFFORT_RULES["ultra"])
    recent = replace(old, session_id=2, kind="long", day=D-timedelta(days=1), caps=st.EFFORT_RULES["long"])
    assert st.effort_window([old, recent], D)["effort"].session_id == 2


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
    assert "moyenne 7 jours" not in json.loads(changed["data"])["r"][-1][2]


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
