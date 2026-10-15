"""The database after the audit (migration h8e9f0a1b2c3 and the models): the migration is in the chain and
round-trips, a Python None is a SQL NULL, the race model reads columns and stays per worker, the activity page
reads route names, the caches are bounded."""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import event, select

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.route import Route
from app.services import power_calculator as pc
from app.services import race_calibration as rc
from tests.test_sleep_need_migrations import ROOT, _load, _run

NOW = datetime.now(timezone.utc)


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


def _race(user_id, name, days_ago, km, hours, **kw):
    fields = dict(user_id=user_id, sport_type="TrailRun", name=name, start_date=NOW - timedelta(days=days_ago),
                  distance=km * 1000.0, moving_time=int(hours * 3600), elapsed_time=int(hours * 3600),
                  total_elevation_gain=km * 40.0, raw_data={"workout_type": 1})
    fields.update(kw)
    return Activity(**fields)


# ── the migration ──

def test_the_integrity_migration_follows_the_foundation_and_round_trips():
    """In the chain (single head), on SQLite for its portable part: orphans go, dangling links clear, the keys,
    updated_at and the unique constraint come, the covered indexes go; the downgrade restores the schema."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    mig = _load("h8e9f0a1b2c3_integrity_jsonb_updated_at")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert mig.down_revision == "g7d8e9f0a1b2" and len(script.get_heads()) == 1
    assert mig.revision in {r.revision for r in script.walk_revisions()}
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        for ddl in (
            "CREATE TABLE users (id INTEGER PRIMARY KEY, strava_token_expires_at INTEGER)",
            "CREATE TABLE activities (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, start_date DATETIME, raw_data JSON)",
            "CREATE INDEX ix_activities_user_id ON activities (user_id)",
            "CREATE INDEX ix_activities_start_date ON activities (start_date)",
            "CREATE TABLE routes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, result_activity_id INTEGER)",
            "CREATE TABLE route_checkpoints (id INTEGER PRIMARY KEY, route_id INTEGER NOT NULL)",
            "CREATE TABLE simulations (id INTEGER PRIMARY KEY, route_id INTEGER NOT NULL, user_id INTEGER NOT NULL)",
            "CREATE TABLE nutrition_products (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)",
            "CREATE TABLE generated_plans (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)",
            "CREATE TABLE mobile_devices (id INTEGER PRIMARY KEY, prev_token_hash VARCHAR(64))",
            "CREATE UNIQUE INDEX ix_mobile_devices_prev_token_hash ON mobile_devices (prev_token_hash)",
            "CREATE TABLE mobile_daily (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, device_id INTEGER)",
            "CREATE INDEX ix_mobile_daily_user_id ON mobile_daily (user_id)",
            "CREATE TABLE weekly_digests (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)",
            "CREATE INDEX ix_weekly_digests_user_id ON weekly_digests (user_id)",
            "INSERT INTO users (id, strava_token_expires_at) VALUES (1, 2147483000)",
            "INSERT INTO activities (id, user_id) VALUES (10, 1)",
            "INSERT INTO routes (id, user_id, result_activity_id) VALUES (1, 1, 10), (2, 1, 99), (3, 7, NULL)",
            "INSERT INTO simulations (id, route_id, user_id) VALUES (1, 1, 1), (2, 3, 7)",
            "INSERT INTO nutrition_products (id, user_id) VALUES (1, 1), (2, 7)",
            "INSERT INTO generated_plans (id, user_id) VALUES (1, 7)",
            "INSERT INTO mobile_devices (id) VALUES (5)",
            "INSERT INTO mobile_daily (id, user_id, device_id) VALUES (1, 1, 5), (2, 1, 6)",
        ):
            c.execute(sa.text(ddl))
        _run(c, mig.upgrade)
        insp = sa.inspect(c)
        assert c.execute(sa.text("SELECT id FROM routes ORDER BY id")).scalars().all() == [1, 2]
        assert c.execute(sa.text("SELECT result_activity_id FROM routes ORDER BY id")).scalars().all() == [10, None]
        assert c.execute(sa.text("SELECT id FROM simulations")).scalars().all() == [1]
        assert c.execute(sa.text("SELECT id FROM nutrition_products")).scalars().all() == [1]
        assert c.execute(sa.text("SELECT count(*) FROM generated_plans")).scalar() == 0
        assert c.execute(sa.text("SELECT device_id FROM mobile_daily ORDER BY id")).scalars().all() == [5, None]
        keys = {(fk["constrained_columns"][0], fk["referred_table"], fk["options"].get("ondelete"))
                for t in ("routes", "simulations", "nutrition_products", "generated_plans", "mobile_daily")
                for fk in insp.get_foreign_keys(t)}
        assert keys == {("user_id", "users", "CASCADE"), ("result_activity_id", "activities", "SET NULL"),
                        ("device_id", "mobile_devices", "SET NULL")}
        for table in ("activities", "routes", "route_checkpoints"):
            assert "updated_at" in {col["name"] for col in insp.get_columns(table)}
        assert c.execute(sa.text("SELECT updated_at FROM activities")).scalar()  # the existing row: now
        indexes = {ix["name"] for t in ("activities", "mobile_daily", "weekly_digests", "mobile_devices")
                   for ix in insp.get_indexes(t)}
        assert not indexes & {"ix_activities_user_id", "ix_activities_start_date", "ix_mobile_daily_user_id",
                              "ix_weekly_digests_user_id", "ix_mobile_devices_prev_token_hash"}
        assert [u["column_names"] for u in insp.get_unique_constraints("mobile_devices")] == [["prev_token_hash"]]
        assert isinstance(next(col for col in insp.get_columns("users") if col["name"] == "strava_token_expires_at")["type"],
                          sa.BigInteger)
        _run(c, mig.downgrade)
        insp = sa.inspect(c)
        assert not any(insp.get_foreign_keys(t) for t in ("routes", "simulations", "nutrition_products", "mobile_daily"))
        assert "updated_at" not in {col["name"] for col in insp.get_columns("activities")}
        assert {ix["name"] for ix in insp.get_indexes("activities")} == {"ix_activities_user_id", "ix_activities_start_date"}
        assert [ix["name"] for ix in insp.get_indexes("mobile_devices")] == ["ix_mobile_devices_prev_token_hash"]
        assert c.execute(sa.text("SELECT strava_token_expires_at FROM users")).scalar() == 2147483000


# ── the models ──

async def test_a_python_none_is_a_sql_null(db_session, test_user):
    """streams_data=None is NULL, not the JSON null a former row held: IS NOT NULL means « has streams »."""
    empty = _race(test_user.id, "Sans streams", 10, 50, 7, streams_data=None, laps=None)
    full = _race(test_user.id, "Avec streams", 20, 50, 7, streams_data={"altitude": [1, 2]})
    route = Route(user_id=test_user.id, name="r", total_distance_km=42, result_json=None)
    db_session.add_all([empty, full, route])
    await db_session.flush()
    assert (await db_session.execute(
        select(Activity.id).where(Activity.user_id == test_user.id, Activity.streams_data.is_not(None))
    )).scalars().all() == [full.id]
    assert (await db_session.execute(select(Route.id).where(Route.result_json.is_not(None)))).first() is None


# ── the race model's reads ──

async def test_the_race_model_reads_columns_and_the_streams_of_uncached_races_only(db_session, test_user, queries):
    cached = _race(test_user.id, "Ultra A", 100, 100, 12,
                   raw_data={"workout_type": 1, rc.DPLUS_CACHE_KEY: {"v": 1, "gain": 5000.0, "src": "stream"},
                             rc.HEAT_CACHE_KEY: {"v": rc.HEAT_CACHE_VERSION, "h": 1.05}},
                   streams_data={"distance": [0, 1000], "altitude": [0, 999]})
    streamed = _race(test_user.id, "Trail B", 200, 40, 4, streams_data={"distance": [0, 1000, 2000], "altitude": [0, 300, 0]})
    training = _race(test_user.id, "Footing", 5, 12, 1.2, raw_data={"workout_type": 0})
    db_session.add_all([cached, streamed, training])
    await db_session.flush()

    keys = set(rc.candidate_columns(test_user.id).selected_columns.keys())
    assert {"id", "name", "distance", "moving_time", "workout_type", "dplus_cache", "heat_cache"} <= keys
    assert not keys & {"raw_data", "streams_data", "laps", "splits_metric", "best_efforts", "computed_metrics"}

    queries.clear()
    points = {p["name"]: p for p in await rc.collect_race_points(db_session, test_user.id)}
    # the candidates, the result links that confirm a race (activity_identity), then Trail B's stream alone
    assert len(queries) == 3 and "routes" in queries[1] and "streams_data" in queries[2] and "IN" in queries[2]
    assert points["Ultra A"]["dplus"] == 5000 and points["Ultra A"]["dplus_src"] == "stream"
    assert points["Ultra A"]["heat"] == 1.05
    assert 280 <= points["Trail B"]["dplus"] <= 300 and points["Trail B"]["dplus_src"] == "stream"  # smoothed
    assert "Footing" not in points


async def test_the_fitted_model_is_kept_per_worker_while_the_races_are_unchanged(db_session, test_user, queries, monkeypatch):
    db_session.add_all([_race(test_user.id, "A", 100, 50, 6), _race(test_user.id, "B", 200, 100, 14)])
    await db_session.flush()
    first = await rc.build_race_effort_model(db_session, test_user.id)
    assert first and first["n_used"] == 2
    queries.clear()
    again = await rc.build_race_effort_model(db_session, test_user.id)
    assert again == first and again is not first  # the caller's own copy
    assert len(queries) == 2 and all("count" in q.lower() for q in queries)  # the versions alone: races, links
    db_session.add(_race(test_user.id, "C", 300, 80, 10))
    await db_session.flush()
    assert (await rc.build_race_effort_model(db_session, test_user.id))["n_used"] == 3  # a new race: refitted
    long_run = _race(test_user.id, "Sortie longue", 50, 45, 6, raw_data={"workout_type": 0})
    db_session.add(long_run)
    await db_session.flush()
    model = await rc.build_race_effort_model(db_session, test_user.id)
    assert model["n_races"] == 3 and {p["name"] for p in model["races"]} == {"A", "B", "C"}
    db_session.add(Route(user_id=test_user.id, name="Trail du lac", total_distance_km=45, result_activity_id=long_run.id))
    await db_session.flush()  # a result link confirms the outing as a race (activity_identity): refitted at once
    model = await rc.build_race_effort_model(db_session, test_user.id)
    assert model["n_races"] == 4 and "Trail du lac" in {p["name"] for p in model["races"]}
    expires, key, kept = rc._RACE_MODELS[test_user.id]
    rc._RACE_MODELS[test_user.id] = (0.0, key, kept)  # past the TTL
    queries.clear()
    await rc.build_race_effort_model(db_session, test_user.id)
    assert len(queries) >= 2  # read again
    for uid in range(rc.RACE_MODEL_CACHE_SIZE + 40):
        rc._remember_model(10_000 + uid, (), None, 1e12)
    assert len(rc._RACE_MODELS) == rc.RACE_MODEL_CACHE_SIZE and 10_000 not in rc._RACE_MODELS


def test_select_races_reads_the_newest_forty():
    pts = [rc.race_point(f"R{i}", 50, 2500, 7.0, True, day=(NOW - timedelta(days=10 * i)).date()) for i in range(60)]
    used, ignored = rc.select_races(pts)
    assert len(used) == rc.SELECT_MAX_POINTS == 40 and not ignored
    assert {p["name"] for p in used} == {f"R{i}" for i in range(40)}
    assert len(rc.select_races(pts[:40])[0]) == 40


def test_the_ftp_cache_keeps_the_latest_athletes():
    pc._FTP_CACHE.clear()
    for uid in range(pc.FTP_CACHE_SIZE + 44):
        pc._remember_ftp(uid, NOW, None)
    assert len(pc._FTP_CACHE) == pc.FTP_CACHE_SIZE == 256
    assert 0 not in pc._FTP_CACHE and 43 not in pc._FTP_CACHE and 44 in pc._FTP_CACHE
    pc._FTP_CACHE.clear()


# ── the activity page ──

async def test_the_activity_page_reads_route_names_not_courses(client, db_session, test_user, test_activity, queries):
    big = {"total_distance_km": 10.0, "route_coords": [[45.0, 4.0, i / 100, 300.0] for i in range(2000)]}
    db_session.add_all([Route(user_id=test_user.id, name="Boucle du lac", total_distance_km=10.5, course_json=big),
                        Route(user_id=test_user.id, name="Marathon", total_distance_km=42.2, course_json=big)])
    await db_session.flush()
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    queries.clear()
    response = await client.get(f"/activity/{test_activity.id}")
    assert response.status_code == 200 and "Boucle du lac" in response.text and "Marathon" not in response.text
    assert not any("course_json" in q or "FROM analyses" in q for q in queries), queries
