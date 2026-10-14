"""Behavioural checks of the training load as context (owner, 2026-10-14: scientific basis — the cumulative
exposure no longer caps the daily state: the HRV already reads what training does to the night; the load is one
pipeline, sante_load, shown as the Entraînement % and the Charge accumulée); not physiological validation."""
import math
from dataclasses import replace
from datetime import timedelta

import pytest

from app.services import nights as nt, sante, sante_load, sante_training as st
from tests.test_sante_score import D, _day, _example, _history, _nights, _rich, _session


def base(sport="Run", hr=130):
    return [_session(D - timedelta(days=k), 45, sid=k, sport=sport, hr=hr)
            for k in range(10, 100, 3)]


def block(sport="Run", hr=165, minutes=75):
    return [_session(D - timedelta(days=k), minutes, sid=1000+k, sport=sport, hr=hr)
            for k in range(1, 5)]


def _train(sessions, rows=None, today=D):
    """The Entraînement dial's figures on these activities, with the bounds health_page uses."""
    nights = _nights(rows or _rich(days=110), sessions, today)
    return sante.training(sessions, today, rest=nt.rest_hr(nights, today), peak=st.hr_max(sessions, today))


def _assess(nights, sessions, d=D):
    with nt.memo():
        nt.freeze(nights)
        return sante._assess(nights, sessions, nt.anchor_efforts(nights, st.efforts(sessions)), d)


def test_ordinary_sessions_accumulate_in_the_load_not_in_the_state():
    """Was: the accumulation capped the score below 100 with identical good nights. Now the nights decide the state;
    the same accumulation reads in the Entraînement % (its context on the recovery card)."""
    rows = _rich(days=110, asleep=480)
    rest = _day(rows, base())
    isolated = _day(rows, base() + block()[:1])
    series = _day(rows, base() + block())
    assert st.efforts(base() + block()) == []  # all below the long-effort threshold
    assert series["score"] == isolated["score"] == rest["score"]
    assert series["state"]["tone"] == "ok" and series["score"]["reasons"] == [] and series["score"]["caps"] == []
    loads = [_train(ss, rows)["ratio"] for ss in (base(), base() + block()[:1], base() + block())]
    assert loads[2] > loads[1] > loads[0] == 0
    context = sante.training_context({**_train(base() + block(), rows), "chart": None})
    assert context["ratio"] == loads[2] and context["word"] == f"{loads[2]} % de ta semaine habituelle"


def test_no_new_session_means_gradual_decay_over_several_mornings():
    """The Charge accumulée's recent average falls by e^(−1/7) a morning without a session (τ = 7 days); the
    7 days' sum keeps the block whole while it is in them: one rest day resets neither."""
    ss = base() + block()
    load = st.loads(ss, D, 50, 190)
    recent = [sante_load.history(ss, D + timedelta(days=i), load)[-1]["recent"] for i in range(6)]
    assert all(b == pytest.approx(a * math.exp(-1 / 7)) for a, b in zip(recent, recent[1:]))
    assert recent[-1] > 0
    weeks = [sante_load.week_ratio(ss, load, D + timedelta(days=i)) for i in range(6)]
    load7 = [w["load7"] for w in weeks]
    assert load7[1] == load7[0] and load7 == sorted(load7, reverse=True) and load7[-1] > 0
    assert weeks[1]["side"] == "more"  # one rest day does not reset the accumulation


def test_future_and_after_wake_sessions_do_not_rewrite_the_morning():
    rows = _rich(days=110)
    ss = base() + block()
    before = _day(rows, ss)
    late = _session(D, 120, sid=2001, hour=18, hr=190)
    future = _session(D + timedelta(days=1), 120, sid=2002, hr=210)
    after = _day(rows, ss + [late, future])
    assert (after["score"], after["state"]) == (before["score"], before["state"])
    nights = _nights(rows, ss + [late, future])
    fresh_nights = {**nights, D + timedelta(days=1): nt.Night(D + timedelta(days=1), hr=150)}
    assert _assess(fresh_nights, ss + [late, future])["score"] == before["score"]
    assert _history(rows, ss + [late, future]) == _history(rows, ss)
    # the load of the day holds what ended that day (the evening session), never a later one
    assert sante_load.history(ss + [late, future], D) == sante_load.history(ss + [late], D)
    assert sante_load.history(ss + [late], D)[-1]["daily"] > sante_load.history(ss, D)[-1]["daily"]


def test_the_local_end_not_the_utc_day_or_the_start_chooses_a_loads_day():
    """Was: the session had to end before the UTC wake to count for a morning. Now a session's load lands on the
    local day it ended (D per local end day), on its own clock."""
    ended = _session(D, 45, sid=1, hour=5, offset=3600)  # 05:00 → 05:45 local, UTC+1
    korea = _session(D, 60, sid=2, hour=8, offset=32400)  # 08:00 local, 23:00 UTC the day before
    midnight = _session(D - timedelta(days=1), 90, sid=3, hour=23)  # 23:00 → 00:30
    ss = [ended, korea, midnight]
    assert [s.end_day for s in ss] == [D, D, D]
    load = st.loads(ss, D, 50, 190)
    per = sante_load.daily(ss, load, D - timedelta(days=1), D)
    assert dict(per) == {D: pytest.approx(sum(load[s.id][0] for s in ss))}
    assert sante_load.totals(ss, load, D - timedelta(days=1), 7)["count"] == 0
    assert sante_load.totals(ss, load, D, 1)["count"] == 3


def test_new_or_sparse_history_does_not_create_a_usual_week():
    rows = _rich()
    result = _train(block(), rows)
    assert result["ratio"] is None and result["usual"] is None and result["word"] == sante.NO_USUAL
    assert all(r["usual"] is None for r in sante_load.history(block(), D))  # « repères en cours »
    sparse = [_session(D - timedelta(days=k), 45, sid=k) for k in (90, 40, 20, 1)]
    assert _train(sparse, rows)["ratio"] is None  # 2 active weeks of the 11 (4 needed)
    empty = _train([], rows)
    assert empty["ratio"] is None and empty["week"] == "0 min"
    assert sante_load.card([], D) is None
    assert sante.training_context({**empty, "chart": None}) == {"recorded": 0, "word": "0 min sur 7 jours",
                                                                "ratio": None}


def test_no_fresh_night_no_score_even_with_large_accumulation():
    rows = _rich(days=110)
    for values in rows.values():
        values.pop(D, None)
    day = _day(rows, base() + block())
    assert day["score"]["value"] is None and day["score"]["missing"]["key"] == "no_night"
    assert _train(base() + block(), rows)["ratio"] is not None  # the load is still shown, as context


def test_strength_by_time_and_sports_by_their_heart_rate_alone():
    """Was: a strength channel and a run-impact channel. Now one internal load (Impellizzeri 2019): strength
    counts its minutes at the athlete's median rate, whatever its HR; a ride and a run at the same HR weigh the
    same."""
    low = base("WeightTraining", 60) + block("WeightTraining", 65)
    high = base("WeightTraining", 170) + block("WeightTraining", 175)
    ss = base() + block()
    l_low, l_high = st.loads(ss + low, D, 50, 190), st.loads(ss + high, D, 50, 190)
    assert all(not l_low[s.id][1] and l_low[s.id] == l_high[s.id] for s in low)
    run, ride = _session(D, 60, sid=1, hr=150), _session(D, 60, sid=2, hr=150, sport="VirtualRide")
    load = st.loads([run, ride], D, 50, 190)
    assert load[1] == load[2] and load[1][1]


def test_missing_hr_retains_exposure_but_cannot_make_a_red_recovery_state():
    rows = _rich(days=110)
    ss = base(hr=None) + block(hr=None, minutes=170)
    load = st.loads(ss, D, 50, 190)
    assert all(load[s.id][0] > 0 and not load[s.id][1] for s in ss)  # estimated, never zero
    assert _train(ss, rows)["dial"]["sub"] == "plus que d'habitude"
    day = _day(rows, ss)
    assert day["state"]["tone"] == "ok" and day["score"]["reasons"] == []  # the load alone is no reason


def test_adding_a_heart_sensor_is_not_mistaken_for_increased_training():
    rows = _rich(days=110)
    before = _day(rows, base(hr=None) + block(hr=None))
    sensor = _day(rows, base(hr=None) + block(hr=165))
    assert (sensor["score"], sensor["state"]) == (before["score"], before["state"])  # the state reads no load
    assert _train(base(hr=None) + block(hr=165), rows)["intensity"] is None  # the usual weeks unread: no word
    # five sessions read from their HR: the weeks before them borrow their median rate — the time's ratio again
    five = [_session(D - timedelta(days=k), 75, sid=1000 + k, hr=165) for k in range(1, 6)]
    unread = [replace(s, hr=None) for s in five]
    assert _train(base(hr=None) + five, rows)["ratio"] == _train(base(hr=None) + unread, rows)["ratio"]
    lost = _train(base() + block(hr=None), rows)
    assert lost["intensity"] is None
    assert lost["ratio"] == _train(base(hr=None) + block(hr=None), rows)["ratio"]  # the runs' own rate


@pytest.mark.parametrize("sport,hr", [("Run", None), ("WeightTraining", 65)])
def test_an_extra_workout_without_cardio_data_cannot_improve_recovery(sport, hr):
    rows = _rich(days=110)
    ss = base() + block()
    extra = _session(D - timedelta(days=1), 170, sid=9876, sport=sport, hr=hr, hour=15)
    before, after = _day(rows, ss), _day(rows, ss + [extra])
    assert (after["score"], after["state"]) == (before["score"], before["state"])
    assert _train(ss + [extra], rows)["ratio"] > _train(ss, rows)["ratio"]  # its minutes count, estimated


def test_a_sport_without_hr_borrows_its_own_familys_rate_not_another_sports():
    """A ride without HR counts at the rides' median load per minute once 5 rides were read, never the runs'; a
    sport with fewer than 5 read sessions at the median of all of them."""
    rides = [_session(D - timedelta(days=k), 60, sid=3000 + k, sport="Ride", hr=150) for k in range(10, 25, 3)]
    unread_ride = _session(D - timedelta(days=2), 60, sid=4000, sport="Ride", hr=None)
    swim = _session(D - timedelta(days=3), 60, sid=4001, sport="Swim", hr=None)
    ss = base() + rides + [unread_ride, swim]
    load = st.loads(ss, D, 50, 190)
    rate = {k: [load[s.id][0] / s.minutes for s in group] for k, group in (("run", base()), ("ride", rides))}
    assert load[4000] == (pytest.approx(60 * sorted(rate["ride"])[2]), False)
    every = sorted(rate["run"] + rate["ride"])
    assert load[4001][0] == pytest.approx(60 * (every[len(every) // 2 - 1] + every[len(every) // 2]) / 2)
    assert sante_load.card(ss, D, load)["estimated"] == 2


def test_reasons_are_listed_never_an_added_second_penalty():
    """Was: the precautions combined by their minimum cap. Now each reason is listed and the number is the
    night's, held in the state's range: a second reason never lowers it further."""
    w = {"tone": "warn", "text": "fenêtre indicative jusqu'à J+1"}
    one, both = _example(z_hrv=0.0, window=w), _example(z_hrv=0.0, window=w, tst=300)
    assert one["score"]["value"] == both["score"]["value"] == 69
    assert [r["key"] for r in both["score"]["reasons"]] == ["short", "effort"]
    low, low_both = _example(z_hrv=-1.5, window=w), _example(z_hrv=-1.5, window=w, tst=300)
    assert low["score"]["value"] == low_both["score"]["value"] == 50


def test_same_activity_id_and_import_order_do_not_double_the_effect():
    ss = base() + block()
    load = st.loads(ss, D, 50, 190)
    expected = sante_load.history(ss, D, load), sante_load.week_ratio(ss, load, D)
    shuffled = list(reversed(ss)) + [ss[-1]]
    assert (sante_load.history(shuffled, D, load), sante_load.week_ratio(shuffled, load, D)) == expected
    invalid = replace(ss[-1], id=9999, minutes=float("nan"))
    with_invalid = ss + [invalid]
    load_invalid = {**load, 9999: (float("nan"), False)}
    assert (sante_load.history(with_invalid, D, load_invalid),
            sante_load.week_ratio(with_invalid, load_invalid, D)) == expected
    assert sante_load.totals(shuffled, load, D, 7) == sante_load.totals(ss, load, D, 7)


@pytest.mark.parametrize("tau", [1.5, 2, 3, 4, 7])
def test_order_and_decay_are_not_specific_to_one_chosen_time_constant(tau, monkeypatch):
    monkeypatch.setattr(sante_load, "ACUTE_TAU", tau)
    ss, isolated = base() + block(), base() + block()[:1]
    current = sante_load.history(ss, D)[-1]["recent"]
    single = sante_load.history(isolated, D)[-1]["recent"]
    later = sante_load.history(ss, D + timedelta(days=1))[-1]["recent"]
    assert current > single > 0 and 0 < later < current
