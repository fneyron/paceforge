"""History and pace profiles must not fetch GPS/source payloads to read summaries."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event

from app.models.activity import Activity
from app.models.route import Route
from app.models.user import User
from app.dependencies import get_current_user
from app.routers import dashboard
from app.services import race_simulator
from app.services import sante_training as st


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
    assert not any("FROM analyses" in query for query in queries)

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
    assert not any("FROM analyses" in query for query in queries)


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


async def test_detail_keeps_metrics_splits_and_owned_comparisons_without_bulk_payloads(
    client, db_session, test_user, queries,
):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    other = User(firstname="Other")
    db_session.add(other)
    await db_session.flush()
    when = datetime.now(UTC) - timedelta(days=1)
    activity = _activity(test_user.id, when, name="Detail with splits", average_heartrate=143,
                         splits_metric=[{"distance": 1000, "moving_time": 300, "average_heartrate": 142}] * 2,
                         computed_metrics={"cardiac_drift": {"drift_pct": 3.5,
                                                             "first_half_avg": 140, "second_half_avg": 145}})
    private = _activity(other.id, when, name="Private activity")
    db_session.add_all([activity, private])
    routes = []
    for distance in (8, 9, 10, 10.2, 10.4, 11, 14):
        route = Route(user_id=test_user.id, name=f"Owned route {distance}", total_distance_km=distance,
                      sport_type="trail", course_json={"unused": "x" * 20000},
                      weather_json={"unused": "x" * 20000})
        routes.append(route)
        db_session.add(route)
    db_session.add_all([
        Route(user_id=other.id, name="Private route", total_distance_km=10, sport_type="trail"),
        Route(user_id=test_user.id, name="Bike route", total_distance_km=10, sport_type="bike"),
    ])
    await db_session.flush()
    queries.clear()
    response = await client.get(f"/activity/{activity.id}")
    assert response.status_code == 200
    html = response.text
    assert "Detail with splits" in html and "143" in html and "142" in html
    assert "Dérive cardiaque" in html and "3.5%" in html
    expected = sorted(routes, key=lambda r: abs(r.total_distance_km - 10))[:5]
    links = [f'/simulator/routes/{r.id}?compare={activity.id}' for r in expected]
    assert all(link in html for link in links)
    assert [html.index(link) for link in links] == sorted(html.index(link) for link in links)
    assert html.count(f'?compare={activity.id}"') == 5
    assert "Private route" not in html and "Bike route" not in html
    for query in queries:
        projection = query.partition("FROM")[0]
        assert not any(f"activities.{field}" in projection for field in ("raw_data", "streams_data", "best_efforts"))
        assert not any(f"routes.{field}" in projection for field in
                       ("course_json", "weather_json", "live_json", "result_json"))
        assert "FROM analyses" not in query
    assert (await client.get(f"/activity/{private.id}")).status_code == 404


async def test_activity_page_reads_the_day_and_sessions_once_but_refreshes_changes_next_request(
    client, db_session, test_user, monkeypatch, queries,
):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    when = datetime.now(UTC) - timedelta(days=1)
    activity = _activity(test_user.id, when, name="Fresh activity", average_heartrate=140)
    db_session.add(activity)
    await db_session.flush()
    today = AsyncMock(wraps=st.athlete_today)
    sessions = AsyncMock(wraps=st.load_sessions)
    monkeypatch.setattr(st, "athlete_today", today)
    monkeypatch.setattr(st, "load_sessions", sessions)

    for name, distance in (("Fresh activity", 10000), ("Updated activity", 12000)):
        activity.name, activity.distance = name, distance
        await db_session.flush()
        queries.clear()
        response = await client.get("/activities")
        assert response.status_code == 200 and name in response.text
        assert today.await_count == 1 and sessions.await_count == 1
        # The revision-aware cache key is checked once, even when both the
        # spikes and the easy-run chart consume the sessions.
        assert sum("SELECT count(activities.id)" in q and "max(activities.created_at)" in q for q in queries) == 1
        assert sum("SELECT max(health_metrics.date)" in q for q in queries) == 1
        assert sum("SELECT max(mobile_daily.date)" in q for q in queries) == 1
        assert (st._CACHE[test_user.id][1][0].km) == distance / 1000
        today.reset_mock()
        sessions.reset_mock()
