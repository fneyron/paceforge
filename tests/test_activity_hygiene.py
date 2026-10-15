"""Import corrections and short stationary sessions: data must not silently disappear or remain stale."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.services.activity_dedupe import is_false_start
from app.services.strava_activities import DETAIL_ASKED, activity_from_strava, apply_detail
from app.services import strava_state


@pytest.mark.parametrize("sport", ["Workout", "Yoga", "Pilates", "WeightTraining", "Stretching", "Unknown"])
def test_short_stationary_sessions_are_not_false_starts(sport):
    assert not is_false_start(SimpleNamespace(sport_type=sport, distance=0, moving_time=240))


@pytest.mark.parametrize("sport", ["Run", "TrailRun", "VirtualRun", "Ride", "Walk", "Hike"])
def test_tiny_travelling_recordings_still_are_false_starts(sport):
    assert is_false_start(SimpleNamespace(sport_type=sport, distance=200, moving_time=60))
    assert not is_false_start(SimpleNamespace(sport_type=sport, distance=200, moving_time=600))


def _row():
    return activity_from_strava({"id": 10, "name": "Course", "sport_type": "TrailRun",
                                 "start_date": "2026-10-03T02:00:00Z", "distance": 100_000,
                                 "moving_time": 40_000, "elapsed_time": 45_000,
                                 "average_heartrate": 145, "resource_state": 3,
                                 "paceforge_revision": 5, "paceforge_dplus": {"gain": 3000},
                                 DETAIL_ASKED: True}, 1)


def test_corrected_metrics_replace_columns_and_invalidate_derived_data_once():
    row = _row()
    row.streams_data = {"time": [0, 1, 2]}
    row.computed_metrics = {"pace": 400}
    row.calories, row.max_watts, row.laps = 4000, 300, [{"source": "coros"}]
    corrected = {"id": 10, "distance": 148_000, "moving_time": 56_000, "elapsed_time": 60_000,
                 "average_heartrate": 138, "max_heartrate": 180, "average_speed": 2.64,
                 "total_elevation_gain": 7000, "start_date": "2026-10-03T01:30:00Z",
                 "calories": None, "max_watts": None, "resource_state": 3}
    assert apply_detail(row, corrected)
    assert (row.distance, row.moving_time, row.elapsed_time, row.average_heartrate) == (148_000, 56_000, 60_000, 138)
    assert row.start_date == datetime(2026, 10, 3, 1, 30, tzinfo=timezone.utc)
    assert row.total_elevation_gain == 7000 and row.max_heartrate == 180 and row.average_speed == 2.64
    assert row.streams_data is None and row.computed_metrics is None
    assert (row.calories, row.max_watts, row.laps) == (4000, 300, [{"source": "coros"}])
    assert row.raw_data["paceforge_revision"] == 6 and "paceforge_dplus" not in row.raw_data
    assert not apply_detail(row, corrected) and row.raw_data["paceforge_revision"] == 6


def test_known_summary_preserves_detail_but_metric_correction_reopens_detail_backlog():
    row = _row()
    row.splits_metric = [{"distance": 1000}]
    summary = {"id": 10, "name": "Course", "sport_type": "TrailRun", "distance": 100_000,
               "moving_time": 40_000, "resource_state": 2}
    assert not apply_detail(row, summary, summary=True)
    assert row.raw_data["resource_state"] == 3 and row.raw_data[DETAIL_ASKED]
    assert apply_detail(row, {**summary, "moving_time": 42_000}, summary=True)
    assert row.moving_time == 42_000 and row.raw_data["resource_state"] == 2
    assert DETAIL_ASKED not in row.raw_data
    assert row.splits_metric == [{"distance": 1000}]  # usable until the refreshed detail arrives


def test_same_size_detail_changes_increment_revision_and_names_do_not_discard_streams():
    row = _row()
    row.laps = [{"moving_time": 100}]
    row.streams_data = {"time": [0, 1]}
    assert apply_detail(row, {"name": "Trail!", "laps": [{"moving_time": 101}]})
    assert row.raw_data["paceforge_revision"] == 6
    assert row.laps == [{"moving_time": 101}] and row.streams_data == {"time": [0, 1]}


def test_equivalent_naive_utc_date_is_not_a_metric_correction():
    row = _row()
    row.start_date = row.start_date.replace(tzinfo=None)
    assert not apply_detail(row, {"start_date": "2026-10-03T02:00:00Z"})


def test_history_cursor_waits_for_outer_commit_and_never_survives_rollback(monkeypatch):
    stored = {}
    monkeypatch.setattr(strava_state, "keep", lambda key, value: stored.__setitem__(key, value))
    with Session() as session:  # transaction events only; no database or connection needed
        db = SimpleNamespace(sync_session=session)
        session.begin()
        strava_state.reconciliation_after_commit(db, 1, 22, 4)
        with session.begin_nested():
            strava_state.reconciliation_after_commit(db, 1, 22, 6)
        assert stored == {}  # releasing a savepoint is not durable
        session.rollback()
        session.begin()
        session.commit()
        assert stored == {}  # the rolled-back cursor does not leak into the next transaction
        session.begin()
        strava_state.reconciliation_after_commit(db, 1, 22, 4)
        session.commit()
        assert stored == {"history:1:22": "4"}
