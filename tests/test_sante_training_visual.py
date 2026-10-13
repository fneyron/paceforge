"""Recorded daily training and fitness collection progress use real model inputs."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.services import sante_training as st

TODAY = date(2026, 10, 13)


def session(days_ago, **kwargs):
    day = TODAY - timedelta(days=days_ago)
    values = dict(id=days_ago, start=datetime.combine(day, datetime.min.time(), tzinfo=UTC)
                  + timedelta(hours=8), day=day, sport="Run", minutes=50, dplus=40, km=10,
                  speed=10 / 3, hr=140, hr_peak=185, workout_type=0, temp=15, elapsed=50)
    values.update(kwargs)
    return st.Session(**values)


def test_daily_training_retains_duration_before_a_baseline_exists():
    chart = st.training_days([session(0, minutes=30), session(0, id=10, minutes=20)], TODAY)
    data = json.loads(chart["data"])
    assert chart["duration"] == "50 min" and chart["minutes"] == 50 and chart["count"] == 2
    assert chart["days"] == len(chart["bars"]) == len(data["d"]) == 7
    assert chart["read"] == ["50 min", "7 derniers jours", ""]
    assert data["r"][-1] == ["50 min", "2 séances", "mar. 13 oct."]
    assert data["r"][0][0:2] == ["0 min", "Aucune séance enregistrée"]
    assert chart["bars"][0]["h"] == 0 and not chart["bars"][0].get("miss")
    assert chart["bars"][-1]["h"] > 0
    assert "repos" not in chart["data"]


def test_daily_training_keeps_explicit_missing_days_distinct_from_no_recorded_session():
    missing = {TODAY - timedelta(days=1), TODAY}
    chart = st.training_days([session(0)], TODAY, missing_days=missing)
    data = json.loads(chart["data"])
    assert chart["bars"][-2]["miss"]
    assert data["r"][-2][:2] == ["—", "Données indisponibles"]
    assert not chart["bars"][0].get("miss") and chart["bars"][0]["h"] == 0
    assert chart["bars"][-1]["h"] > 0  # the measured part is never discarded
    assert data["r"][-1][:2] == ["50 min", "1 séance · données partielles"]
    assert chart["duration"] == "50 min"
    assert "Certaines données sont indisponibles" in chart["summary"]


def test_training_uses_seven_calendar_days_even_while_the_sleep_cycle_is_yesterday():
    old = session(7, minutes=300)
    first = session(6, minutes=20)
    last = session(0, minutes=30)
    future = session(-1, minutes=500)
    chart = st.training_days([old, first, last, future], TODAY - timedelta(days=1), until=TODAY)
    data = json.loads(chart["data"])
    assert data["d"] == [(TODAY - timedelta(days=k)).isoformat() for k in range(6, -1, -1)]
    assert chart["since"] == TODAY - timedelta(days=6) and chart["until"] == TODAY
    assert chart["days"] == 7 and chart["minutes"] == 50 and chart["count"] == 2


def test_overnight_session_counts_on_its_local_finish_day_without_splitting_its_time():
    start = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)
    overnight = session(7, start=start, offset=2 * 3600, minutes=90, elapsed=120)
    chart = st.training_days([overnight], TODAY)
    data = json.loads(chart["data"])
    assert overnight.end_day == date(2026, 10, 7)
    assert chart["duration"] == "1h30" and chart["count"] == 1
    assert data["r"][0][0] == "1h30"
    assert all(row[0] == "0 min" for row in data["r"][1:])


def test_fitness_progress_uses_the_models_real_qualifying_runs():
    good = [session(0), session(5), session(41)]
    rejected = [
        session(42), session(-1), session(365), session(1, hr=None), session(2, speed=None),
        session(3, sport="Ride"), session(4, km=4.9), session(6, minutes=24),
        session(7, minutes=151), session(8, dplus=121), session(9, hr=170),
        session(10, workout_type=1), session(11, workout_type=3), session(12, temp=30),
        session(13, temp=15, feels=30),
    ]
    progress = st.fitness_progress(good + rejected, TODAY, 185)
    assert progress == {"count": 3, "required": 6, "remaining": 3, "ready": False,
                        "label": "3/6 sorties comparables", "days": 42}
    # Cooler apparent temperature and indoor conditions count just as in the model.
    extra = [session(14, temp=30, feels=20), session(15, temp=30, indoor=True), session(16)]
    all_sessions = good + rejected + extra
    progress = st.fitness_progress(all_sessions, TODAY, 185)
    model = st.easy_model(all_sessions, TODAY, 185)
    assert progress["count"] == 6 and progress["remaining"] == 0 and progress["ready"]
    assert st.line_ok(model, TODAY)
    assert {s.id for s in model["cool"] if s.day > TODAY - timedelta(days=42)} == {
        s.id for s in good + extra
    }


@pytest.mark.parametrize("count", [0, 1, 5, 6, 9])
def test_collection_threshold_matches_the_actual_fitness_line_without_fitting_twice(count):
    runs = [session(i) for i in range(count)]
    progress = st.fitness_progress(runs, TODAY, 185)
    assert progress["count"] == count and progress["required"] == st.LINE_RUNS
    assert progress["ready"] == st.line_ok(st.easy_model(runs, TODAY, 185), TODAY)
    # Historical qualifying runs can fit a model, but cannot advance recent collection.
    old = [replace(session(60 + i), id=100 + i) for i in range(8)]
    assert st.fitness_progress(runs + old, TODAY, 185) == progress
    assert progress["ready"] == st.line_ok(st.easy_model(runs + old, TODAY, 185), TODAY)
