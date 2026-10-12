"""Behavioural checks of cumulative exposure; not physiological validation."""
from dataclasses import replace
from datetime import datetime, time, timedelta

import pytest

from app.services import nights as nt, sante_recovery as sr, sante_score as sc, sante_training as st
from tests.test_sante_score import D, _day, _history, _nights, _rich, _session


def base(sport="Run", hr=130):
    return [_session(D - timedelta(days=k), 45, sid=k, sport=sport, hr=hr)
            for k in range(10, 100, 3)]


def block(sport="Run", hr=165, minutes=75):
    return [_session(D - timedelta(days=k), minutes, sid=1000+k, sport=sport, hr=hr)
            for k in range(1, 5)]


def channel(result, key):
    return next(c for c in result["channels"] if c["key"] == key)


def test_ordinary_sessions_accumulate_despite_identical_good_nights():
    rows = _rich(days=110, asleep=480)
    rest = _day(rows, base())
    isolated = _day(rows, base() + block()[:1])
    series = _day(rows, base() + block())
    assert st.efforts(base() + block()) == []  # all below the old long-effort threshold
    assert series["score"]["raw0"] == isolated["score"]["raw0"] == rest["score"]["raw0"]
    assert series["score"]["value"] < isolated["score"]["value"] < rest["score"]["value"]
    assert "load" in series["score"]["caps"]
    assert series["score"]["value"] >= 60  # exposure alone is not an illness/fatigue diagnosis


def test_no_new_session_means_gradual_decay_over_several_mornings():
    nights = _nights(_rich(days=110))
    ss = base() + block()
    results = [sr.assessment(ss, nights, D + timedelta(days=i)) for i in range(6)]
    values = [channel(r, "cardio")["current"] for r in results]
    assert all(a > b > 0 for a, b in zip(values, values[1:]))
    assert results[1]["word"] == "En baisse"
    limits = [r["cap"] if r["cap"] is not None else 100 for r in results]
    assert limits == sorted(limits)
    assert limits[1] < 100  # one rest day does not reset accumulation


def test_future_and_after_wake_sessions_do_not_rewrite_the_morning():
    nights = _nights(_rich(days=110))
    ss = base() + block()
    before = sr.assessment(ss, nights, D)
    late = _session(D, 120, sid=2001, hour=18, hr=190)
    future = _session(D+timedelta(days=1), 120, sid=2002, hr=210)
    assert sr.assessment(ss+[late, future], nights, D) == before
    fresh_nights = {**nights, D+timedelta(days=1): nt.Night(D+timedelta(days=1), hr=150)}
    assert sr.assessment(ss+[late, future], fresh_nights, D) == before
    history = _history(_rich(days=110), ss)
    assert _history(_rich(days=110), ss+[late, future]) == history


def test_utc_wake_and_overnight_end_not_session_start_choose_the_cycle():
    wake = datetime.combine(D, time(7))
    nights = {D: nt.Night(D, end=wake, tz=120)}  # 05:00 UTC
    ended = _session(D, 45, sid=1, hour=5, offset=3600)  # 04:45 UTC, different zone
    running = _session(D, 90, sid=2, hour=5, offset=3600)  # 05:30 UTC
    assert [r.id for r in sr.exposures([ended, running], nights, D)] == [1]
    midnight = _session(D-timedelta(days=1), 90, sid=3, hour=23)
    assert [r.id for r in sr.exposures([midnight], {}, D)] == []  # no invented wake
    assert [r.id for r in sr.exposures([midnight], nights, D)] == [3]


def test_new_or_sparse_history_does_not_create_a_personal_fatigue_estimate():
    nights = _nights(_rich())
    result = sr.assessment(block(), nights, D)
    assert result["cap"] is None and result["provisional"]
    assert result["word"] == "Repères en cours"
    sparse = [_session(D-timedelta(days=k), 45, sid=k) for k in (90, 40, 20, 1)]
    assert sr.assessment(sparse, nights, D)["cap"] is None
    empty = sr.assessment([], nights, D)
    assert empty["cap"] is None and empty["word"] == "Aucune activité reçue"


def test_no_fresh_night_no_score_even_with_large_accumulation():
    rows = _rich(days=110)
    for values in rows.values():
        values.pop(D, None)
    day = _day(rows, base()+block())
    assert day["recovery_load"]["cap"] is not None
    assert day["score"]["value"] is None


def test_muscle_exposure_keeps_own_sport_history_and_not_low_heart_rate():
    nights = _nights(_rich(days=110))
    low = sr.assessment(base("WeightTraining", 60)+block("WeightTraining", 65), nights, D)
    high = sr.assessment(base("WeightTraining", 170)+block("WeightTraining", 175), nights, D)
    assert channel(low, "strength") == channel(high, "strength")
    assert 80 <= low["cap"] < 100
    assert "run" not in {c["key"] for c in low["channels"]}
    running = sr.assessment(base()+block(), nights, D)
    cycling = sr.assessment(base()+block("VirtualRide"), nights, D)
    assert channel(running, "run")["current"] > channel(cycling, "run")["current"]
    assert channel(cycling, "cardio")["current"] > 0


def test_missing_hr_retains_exposure_but_cannot_make_a_red_recovery_state():
    nights = _nights(_rich(days=110))
    result = sr.assessment(base(hr=None)+block(hr=None, minutes=170), nights, D)
    c = channel(result, "volume")
    assert c["current"] > c["usual"] and not result["intensity_used"]
    assert channel(result, "cardio")["cap"] is None
    assert 80 <= result["cap"] < 100


def test_adding_a_heart_sensor_is_not_mistaken_for_increased_training():
    nights = _nights(_rich(days=110))
    before = sr.assessment(base(hr=None)+block(hr=None), nights, D)
    sensor = sr.assessment(base(hr=None)+block(hr=165), nights, D)
    assert sensor["cap"] == before["cap"]
    assert not sensor["intensity_used"]
    assert channel(sensor, "cardio")["cap"] is None
    lost = sr.assessment(base()+block(hr=None), nights, D)
    assert lost["intensity_partial"] and lost["cap"] >= 80


@pytest.mark.parametrize("sport,hr", [("Run", None), ("WeightTraining", 65)])
def test_an_extra_workout_without_cardio_data_cannot_improve_recovery(sport, hr):
    nights = _nights(_rich(days=110))
    ss = base()+block()
    before = sr.assessment(ss, nights, D)
    extra = _session(D-timedelta(days=1), 170, sid=9876, sport=sport, hr=hr, hour=15)
    after = sr.assessment(ss+[extra], nights, D)
    assert after["cap"] <= before["cap"]
    assert channel(after, "cardio")["current"] == channel(before, "cardio")["current"]


def test_a_new_sport_cannot_borrow_years_of_another_sports_reference():
    nights = _nights(_rich(days=110))
    new = [_session(D-timedelta(days=k), 45, sid=2000+k, sport="WeightTraining", hr=65)
           for k in range(10, 38, 3)]
    result = sr.assessment(base()+new, nights, D)
    assert channel(result, "cardio")["ready"]
    assert not channel(result, "strength")["ready"]
    assert channel(result, "strength")["cap"] is None and result["provisional"]


def test_precautions_combine_by_minimum_not_an_added_second_penalty():
    day = _day(_rich(days=110), base()+block())
    load_cap = day["recovery_load"]["cap"]
    day["window"] = {"cap": load_cap-5}
    both = sc.score_of(day)
    assert both["raw"] == pytest.approx(load_cap-5)
    assert both["reason"] == "effort"
    day["window"] = None
    assert sc.score_of(day)["raw"] == pytest.approx(load_cap)


def test_same_activity_id_and_import_order_do_not_double_the_effect():
    nights = _nights(_rich(days=110))
    ss = base()+block()
    expected = sr.assessment(ss, nights, D)
    assert sr.assessment(list(reversed(ss))+[ss[-1]], nights, D) == expected
    invalid = replace(ss[-1], id=9999, minutes=float("nan"))
    assert sr.assessment(ss+[invalid], nights, D) == expected


@pytest.mark.parametrize("tau", [1.5, 2, 3, 4])
def test_order_and_decay_are_not_specific_to_one_chosen_time_constant(tau):
    nights = _nights(_rich(days=110))
    ss = base()+block()
    rows = sr.exposures(ss, nights, D)
    isolated = sr.exposures(base()+block()[:1], nights, D)
    current, _ = sr.residual(rows, nights, D, "cardio", tau)
    single, _ = sr.residual(isolated, nights, D, "cardio", tau)
    later, _ = sr.residual(rows, nights, D+timedelta(days=1), "cardio", tau)
    assert current > single > 0 and 0 < later < current
