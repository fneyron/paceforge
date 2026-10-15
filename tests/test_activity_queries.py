"""History and pace profiles must not fetch GPS/source payloads to read summaries."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event

from app.models.activity import Activity
from app.models.user import User
from app.routers import dashboard
from app.services import race_simulator


@pytest.fixture
def queries(engine):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


def _activity(user_id, when, **values):
    fields = dict(
        user_id=user_id, sport_type="Run", name="Footing", start_date=when,
        distance=10000, moving_time=3000, elapsed_time=3100,
        average_speed=1000 / 300, total_elevation_gain=100,
        raw_data={"source_detail": "x" * 20000},
        streams_data={"distance": list(range(4000))},
        laps=[{"elapsed_time": 3100}], best_efforts=[{"distance": 5000}],
        computed_metrics={"detail": "x" * 20000},
    )
    fields.update(values)
    return Activity(**fields)


def _assert_light_summary_query(statement, *, splits):
    projection = statement.partition("FROM")[0]
    for field in ("raw_data", "streams_data", "laps", "best_efforts", "computed_metrics"):
        assert f"activities.{field}" not in projection
    assert ("activities.splits_metric" in projection) is splits


async def test_history_keeps_owner_duplicates_false_starts_and_totals_without_bulk_data(
    db_session, test_user, monkeypatch, queries,
):
    monkeypatch.setattr(dashboard, "spike_ids", AsyncMock(return_value=set()))
    when = datetime.now(UTC) - timedelta(days=1)
    other = User(firstname="Other")
    db_session.add(other)
    await db_session.flush()
    phone = _activity(test_user.id, when, sport_type="TrailRun", name="Phone",
                      splits_metric=[{"distance": 1000}], moving_time=3100)
    watch = _activity(test_user.id, when + timedelta(seconds=30), name="Watch",
                      average_heartrate=140, splits_metric=None)
    false_start = _activity(test_user.id, when + timedelta(hours=1),
                            distance=100, moving_time=20)
    older = _activity(test_user.id, when - timedelta(weeks=8))
    db_session.add_all([phone, watch, false_start, older, _activity(other.id, when)])
    await db_session.flush()

    queries.clear()
    weeks, has_more, _ = await dashboard._week_groups(db_session, test_user.id, 1, None)
    rows = [row for week in weeks for row in week["activities"]]
    assert {row.id for row in rows} == {phone.id, watch.id, false_start.id}
    assert {row.id for row in rows if row.is_duplicate} == {phone.id}
    assert {row.id for row in rows if row.is_false_start} == {false_start.id}
    assert sum(week["count"] for week in weeks) == 1
    assert sum(week["km"] for week in weeks) == 10
    assert sum(week["seconds"] for week in weeks) == 3000
    assert sum(week["dplus"] for week in weeks) == 100
    assert has_more
    _assert_light_summary_query(queries[0], splits=True)

    filtered, _, _ = await dashboard._week_groups(db_session, test_user.id, 1, "trail")
    assert [row.id for week in filtered for row in week["activities"]] == [phone.id]
    assert sum(week["count"] for week in filtered) == 1


async def test_history_preserves_split_truthiness_when_ranking_duplicates(
    db_session, test_user, monkeypatch,
):
    monkeypatch.setattr(dashboard, "spike_ids", AsyncMock(return_value=set()))
    when = datetime.now(UTC) - timedelta(days=2)
    expected_duplicates = set()
    for i, splits in enumerate((None, [], {}, False, 0, 0.0, "", [1], {"old": 1}, "old", True)):
        start = when + timedelta(hours=i)
        longer = _activity(test_user.id, start, moving_time=3100, splits_metric=[])
        split_copy = _activity(test_user.id, start + timedelta(seconds=30), splits_metric=splits)
        db_session.add_all([longer, split_copy])
        await db_session.flush()
        expected_duplicates.add(longer.id if splits else split_copy.id)

    weeks, _, _ = await dashboard._week_groups(db_session, test_user.id, 1, None)
    actual = {row.id for week in weeks for row in week["activities"] if row.is_duplicate}
    assert actual == expected_duplicates


async def test_recent_summaries_match_training_totals_without_fetching_full_activities(
    db_session, test_user, queries,
):
    when = datetime.now(UTC) - timedelta(days=1)
    watch = _activity(test_user.id, when, average_heartrate=140)
    phone = _activity(test_user.id, when + timedelta(seconds=30), moving_time=3100)
    db_session.add_all([watch, phone])
    await db_session.flush()

    queries.clear()
    recent = await dashboard._recent(db_session, test_user.id, when.date().isoformat())
    assert [row.id for row in recent["activities"]] == [watch.id]
    assert recent["hours"] == "50 min" and recent["km"] == 10
    assert recent["dplus_formatted"] == "+100\u202fm"
    _assert_light_summary_query(queries[-1], splits=False)
    assert "activities.user_id =" in queries[-1]


async def test_gradient_profile_uses_owner_splits_once_without_unrelated_payloads(
    db_session, test_user, monkeypatch, queries,
):
    monkeypatch.setattr(race_simulator, "_race_model", AsyncMock(return_value=None))
    monkeypatch.setattr(race_simulator, "_personal_fatigue_tilt", AsyncMock(return_value=0.15))
    when = datetime.now(UTC) - timedelta(days=1)
    other = User(firstname="Other")
    db_session.add(other)
    await db_session.flush()

    def splits(pace):
        return [{"distance": 1000, "moving_time": pace, "elevation_difference": 0}] * 10

    db_session.add_all([
        _activity(test_user.id, when, average_heartrate=140, splits_metric=splits(300)),
        _activity(test_user.id, when + timedelta(seconds=30), splits_metric=splits(600)),
        _activity(other.id, when, splits_metric=splits(900)),
        _activity(test_user.id, when - timedelta(days=220), splits_metric=splits(900)),
        _activity(test_user.id, when - timedelta(days=1),
                  sport_type="Ride", splits_metric=splits(900)),
    ])
    await db_session.flush()

    queries.clear()
    profile = await race_simulator.build_athlete_gradient_profile(db_session, test_user.id)
    assert profile.data_points == 10
    assert profile.flat_pace_s_per_km == 300
    assert profile.sport_types_used == ["Run"]
    _assert_light_summary_query(queries[0], splits=True)
    assert len(queries) == 1
