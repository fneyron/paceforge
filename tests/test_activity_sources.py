"""One row per outing across Strava, Garmin and COROS, and the COROS sessions
(parsers on the owner's real answers, import, detail, history once)."""
import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.services import coros, garmin
from app.services.activity_dedupe import find_duplicate_ids
from app.services.activity_sources import adopt_watch_twin, merge_twins
from tests import test_coros
from tests.test_coros import _link

fake, no_commit = test_coros.fake, test_coros.no_commit
ROOT = pathlib.Path(__file__).resolve().parents[1]

# the owner's real querySportRecords answer (2026-10-07)
RECORDS = """Sport Records — 2026-09-28 to 2026-10-07 (5 records)
========================

1. Trail Run — 2026-10-02
   Location: District de Namjeju Trail
   Start Coordinates: 33.245998, 126.510002
   Time Window: startTimestamp=1790942428 | endTimestamp=1791003235
   Duration: 16:53:27 | Distance: 148.15 km
   Average Pace: 6:50 /km | Avg HR: 126 bpm | Calories: 8856 kcal
   LabelId: 480768797007970403 | SportType: 102

2. Outdoor Run — 2026-10-01
   Location: District de Namjeju Course
   Start Coordinates: 33.238998, 126.558998
   Time Window: startTimestamp=1790843353 | endTimestamp=1790844622
   Duration: 18:27 | Distance: 3.77 km
   Average Pace: 4:54 /km | Avg HR: 118 bpm | Calories: 225 kcal
   LabelId: 480726229587099851 | SportType: 100

3. Outdoor Run — 2026-09-30
   Location: J-2 Seogwipo – vallée + Saeseom 2×3  min
   Start Coordinates: 33.248001, 126.557999
   Time Window: startTimestamp=1790763528 | endTimestamp=1790765340
   Duration: 30:06 | Distance: 6.17 km
   Average Pace: 4:53 /km | Avg HR: 132 bpm | Calories: 436 kcal
   LabelId: 480704849237803113 | SportType: 100

4. Indoor Run — 2026-09-29
   Location: Tapis/Salle
   Time Window: startTimestamp=1790675210 | endTimestamp=1790677307
   Duration: 34:57 | Distance: 5.64 km
   Average Pace: 6:11 /km | Avg HR: 116 bpm | Calories: 265 kcal
   LabelId: 480681287854621073 | SportType: 101

5. Indoor Run — 2026-09-28
   Location: Tapis/Salle
   Time Window: startTimestamp=1790584409 | endTimestamp=1790587230
   Duration: 43:33 | Distance: 7.83 km
   Average Pace: 5:34 /km | Avg HR: 123 bpm | Calories: 353 kcal
   LabelId: 480657098596712452 | SportType: 101"""


def test_parsers_read_the_real_coros_answers():
    recs = coros.parse_sport_records(RECORDS)
    assert len(recs) == 5
    ultra = recs[0]
    assert (ultra["label"], ultra["code"], ultra["km"], ultra["hr"], ultra["kcal"]) == (
        480768797007970403, 102, 148.15, 126, 8856)
    assert ultra["duration"] == 16 * 3600 + 53 * 60 + 27 and ultra["end"] - ultra["start"] == 60807
    assert recs[2]["name"] == "J-2 Seogwipo – vallée + Saeseom 2×3  min"
    f = coros.session_fields(ultra, 9 * 3600.0)
    assert (f["sport_type"], f["distance"], f["moving_time"], f["elapsed_time"]) == ("TrailRun", 148150, 60807, 60807)
    assert f["start_date"] == datetime(2026, 10, 2, 12, 0, 28, tzinfo=timezone.utc)  # 21:00 in Jeju
    assert f["raw_data"]["utc_offset"] == 32400 and f["raw_data"]["source"] == "coros"
    treadmill = coros.session_fields(recs[3], None)
    assert treadmill["sport_type"] == "Run" and treadmill["raw_data"]["trainer"]
    local = treadmill["start_date"].replace(tzinfo=None) + timedelta(seconds=treadmill["raw_data"]["utc_offset"])
    assert local.date().isoformat() == "2026-09-29"  # no night to read the offset from: COROS's own date
    d = coros.parse_activity_detail(test_coros.DETAIL)
    assert d == {"dplus": 110, "total": 1812, "moving": 1806, "cadence": 163, "watts": 266, "km": 6.17,
                 "moving_pace": 286}
    assert coros.parse_tz_offset("timestamp=1, timezone=36, hrv=70 ms\ntimestamp=2, timezone=-20, hrv=71 ms") == -18000
    assert coros.parse_sport_records("Sport Records — x (0 records)") == []


def _strava(user: User, start: datetime, km: float, seconds: int, sport: str = "Run", sid: int = 900, **kw):
    return Activity(user_id=user.id, strava_activity_id=sid, sport_type=sport, name=kw.pop("name", "Strava run"),
                    start_date=start, distance=km * 1000, moving_time=seconds, elapsed_time=seconds + 60,
                    total_elevation_gain=kw.pop("dplus", 120.0), raw_data={}, **kw)


async def test_coros_links_what_strava_has_and_adds_what_it_lacks(db_session: AsyncSession, test_user: User,
                                                                  fake, no_commit):
    # 03:00 UTC (12:00 in Korea, the fake's HRV time zone): each session's UTC and local dates agree whatever the
    # hour the suite runs (from `now` itself, a late-evening run gave the sessions another quarter-hour offset)
    now = datetime.now(timezone.utc).replace(hour=3, minute=0, second=0, microsecond=0)
    on_strava = now - timedelta(days=3, hours=2)
    db_session.add(_strava(test_user, on_strava + timedelta(seconds=50), 12.2, 3700, suffer_score=60))
    fake.sessions = [
        {"label": 111, "code": 100, "start": on_strava, "seconds": 3650, "km": 12.0},  # the same outing
        {"label": 222, "code": 102, "type": "Trail Run", "name": "Crêtes", "start": now - timedelta(days=1, hours=3),
         "seconds": 7200, "km": 18.4},  # only COROS has it
    ]
    conn = await _link(db_session, test_user)
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    assert outcome["result"]["activities"] == {"inserted": 1, "linked": 1, "updated": 0, "merged": 0, "detailed": 1}
    acts = (await db_session.execute(select(Activity).where(Activity.user_id == test_user.id)
                                     .order_by(Activity.start_date))).scalars().all()
    assert [(a.strava_activity_id, a.coros_activity_id, a.sport_type) for a in acts] == [
        (900, 111, "Run"), (None, 222, "TrailRun")]
    assert acts[0].name == "Strava run" and acts[0].suffer_score == 60  # Strava's row is left as it is
    trail = acts[1]
    assert trail.total_elevation_gain == 110 and trail.moving_time == 1765  # 6,17 km at 4:46 /km moving
    assert trail.raw_data["detail"]["dplus"] == 110 and trail.raw_data["utc_offset"] == 9 * 3600  # from the HRV series
    asked = [a for n, a in fake.tool_calls if n == "getActivityDetail"]
    assert asked == [{"labelId": "222", "sportType": 102}]  # only the session Strava lacks
    first = [a for n, a in fake.tool_calls if n == "querySportRecords"]
    assert len(first) == 3  # 180 days in 60-day pieces

    # next sync: the last week only, nothing doubled, the detail not asked again
    fake.tool_calls.clear()
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=3)
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"]
    recs = [a for n, a in fake.tool_calls if n == "querySportRecords"]
    assert len(recs) == 1 and (datetime.strptime(recs[0]["endDate"], "%Y%m%d")
                               - datetime.strptime(recs[0]["startDate"], "%Y%m%d")).days == 6
    assert not [n for n, _ in fake.tool_calls if n == "getActivityDetail"]
    n = (await db_session.execute(select(func.count(Activity.id)).where(Activity.user_id == test_user.id))).scalar()
    assert n == 2
    await db_session.refresh(trail)
    assert trail.total_elevation_gain == 110  # the list (no D+) never erases the detail


async def test_strava_takes_over_the_row_a_watch_brought_first(db_session: AsyncSession, test_user: User):
    start = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)
    watch = Activity(user_id=test_user.id, garmin_activity_id=77, sport_type="TrailRun", name="Séance Garmin",
                     start_date=start, distance=21000, moving_time=7000, elapsed_time=7300,
                     total_elevation_gain=900, raw_data={"source": "garmin"})
    db_session.add(watch)
    await db_session.flush()
    new = _strava(test_user, start + timedelta(seconds=40), 21.4, 7050, sport="TrailRun", name="Trail du matin",
                  dplus=950.0, suffer_score=120)
    row = await adopt_watch_twin(db_session, new)
    await db_session.flush()
    assert row is watch and row.strava_activity_id == 900 and row.garmin_activity_id == 77
    assert (row.name, row.suffer_score, row.total_elevation_gain) == ("Trail du matin", 120, 950.0)
    n = (await db_session.execute(select(func.count(Activity.id)).where(Activity.user_id == test_user.id))).scalar()
    assert n == 1
    # another outing is still a new row
    other = await adopt_watch_twin(db_session, _strava(test_user, start + timedelta(hours=8), 10, 3000, sid=901))
    assert other is not watch and other.strava_activity_id == 901


async def test_rows_saved_twice_before_are_merged(db_session: AsyncSession, test_user: User):
    start = datetime(2026, 10, 4, 7, 0, tzinfo=timezone.utc)
    db_session.add_all([
        Activity(user_id=test_user.id, garmin_activity_id=55, sport_type="Run", name="Garmin", start_date=start,
                 distance=10000, moving_time=3000, elapsed_time=3050, total_elevation_gain=80, raw_data={}),
        _strava(test_user, start + timedelta(seconds=90), 10.3, 3020),
        # strength on both, no distance: the same outing by its duration
        Activity(user_id=test_user.id, garmin_activity_id=56, sport_type="WeightTraining", name="Garmin muscu",
                 start_date=start + timedelta(hours=10), distance=0, moving_time=2400, elapsed_time=2400,
                 total_elevation_gain=0, raw_data={}),
        _strava(test_user, start + timedelta(hours=10, seconds=20), 0, 2460, sport="WeightTraining", sid=902),
    ])
    await db_session.flush()
    assert await merge_twins(db_session, test_user.id, start - timedelta(days=1)) == 2
    acts = (await db_session.execute(select(Activity).where(Activity.user_id == test_user.id)
                                     .order_by(Activity.start_date))).scalars().all()
    assert [(a.strava_activity_id, a.garmin_activity_id) for a in acts] == [(900, 55), (902, 56)]


def test_display_dedupe_pairs_sessions_without_distance():
    t = datetime(2026, 10, 4, 7, 0, tzinfo=timezone.utc)
    a = SimpleNamespace(id=1, start_date=t, sport_type="WeightTraining", distance=0, moving_time=2400,
                        splits_metric=None)
    b = SimpleNamespace(id=2, start_date=t + timedelta(seconds=30), sport_type="WeightTraining", distance=0,
                        moving_time=2460, splits_metric=None)
    c = SimpleNamespace(id=3, start_date=t + timedelta(seconds=60), sport_type="WeightTraining", distance=0,
                        moving_time=900, splits_metric=None)
    assert find_duplicate_ids([a, b, c]) == {1}  # 15 min of stretching is another outing


def test_display_dedupe_keeps_the_copy_with_a_heart_rate():
    """Owner, 2026-10-09: two copies of his run of 02/09, 12 s apart; the longer one had no heart rate and was kept,
    so Santé's Entraînement weighed that hour at a rate. The copy with a heart rate wins, then splits, then length."""
    t = datetime(2026, 9, 2, 17, 6, 15, tzinfo=timezone.utc)
    plain = SimpleNamespace(id=1, start_date=t, sport_type="Run", distance=15802, moving_time=3731,
                            splits_metric=[{"distance": 1000}], average_heartrate=None)
    hr = SimpleNamespace(id=2, start_date=t + timedelta(seconds=12), sport_type="Run", distance=15449,
                         moving_time=3690, splits_metric=[{"distance": 1000}], average_heartrate=138.6)
    assert find_duplicate_ids([plain, hr]) == {1}
    assert find_duplicate_ids([SimpleNamespace(**{**vars(plain), "average_heartrate": 130.0}), hr]) == {2}  # both


async def test_garmin_merges_a_strava_row_saved_after_it(db_session: AsyncSession, test_user: User):
    start = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)
    raw = {"activityId": 9100, "startTimeGMT": "2026-10-03 06:00:00", "activityType": {"typeKey": "running"},
           "distance": 10000, "duration": 3000, "elapsedDuration": 3100, "movingDuration": 3000}
    out = await garmin.import_activities(db_session, test_user.id, [raw])
    assert out["inserted"] == 1
    # Strava saved its own row before take-over existed (an older deploy)
    db_session.add(_strava(test_user, start + timedelta(seconds=30), 10.1, 3010))
    await db_session.flush()
    out = await garmin.import_activities(db_session, test_user.id, [raw])
    assert out["merged"] == 1
    acts = (await db_session.execute(select(Activity).where(Activity.user_id == test_user.id))).scalars().all()
    assert [(a.strava_activity_id, a.garmin_activity_id) for a in acts] == [(900, 9100)]


def test_coros_sessions_migration_round_trips():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    path = ROOT / "alembic" / "versions" / "x8a9b0c1d2e3_coros_activities.py"
    spec = importlib.util.spec_from_file_location("mig_coros_sessions", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)  # in the chain (later migrations stack on it), one head overall
    assert mig.revision in {s.revision for s in script.walk_revisions()} and len(script.get_heads()) == 1
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE activities (id INTEGER PRIMARY KEY, strava_activity_id BIGINT, "
                          "garmin_activity_id BIGINT, name VARCHAR(255))"))
        c.execute(sa.text("CREATE TABLE coros_connections (id INTEGER PRIMARY KEY, user_id INTEGER)"))
        c.execute(sa.text("INSERT INTO activities (id, strava_activity_id, name) VALUES (1, 11, 's')"))
        with Operations.context(MigrationContext.configure(c)):
            mig.upgrade()
        c.execute(sa.text("INSERT INTO activities (id, coros_activity_id, name) VALUES (2, 480704849237803113, 'c')"))
        c.execute(sa.text("UPDATE activities SET coros_activity_id = 5 WHERE id = 1"))
        with pytest.raises(sa.exc.IntegrityError):
            c.execute(sa.text("INSERT INTO activities (id, coros_activity_id, name) VALUES (3, 5, 'dup')"))
        assert "sessions_synced_at" in {col["name"] for col in sa.inspect(c).get_columns("coros_connections")}
        with Operations.context(MigrationContext.configure(c)):
            mig.downgrade()
        assert c.execute(sa.text("SELECT id FROM activities")).scalars().all() == [1]  # COROS-only rows go
        assert "sessions_synced_at" not in {col["name"] for col in sa.inspect(c).get_columns("coros_connections")}


def test_short_distances_in_metres_and_each_session_on_its_own_local_day():
    text = RECORDS.replace("Distance: 3.77 km", "Distance: 499 m")
    recs = coros.parse_sport_records(text)
    assert recs[1]["km"] == 0.499
    # the watch now says Jeju (+9), the 30 Sept run was recorded there; a run at 19:30 in France
    # (17:30 UTC) keeps COROS's own date, not the next day
    jeju = coros.session_fields(recs[2], 9 * 3600.0)
    assert jeju["raw_data"]["utc_offset"] == 32400
    france = {**recs[2], "start": int(datetime(2026, 8, 18, 17, 30, tzinfo=timezone.utc).timestamp()),
              "end": None, "day": "2026-08-18"}
    off = coros.session_fields(france, 9 * 3600.0)["raw_data"]["utc_offset"]
    start = datetime(2026, 8, 18, 17, 30)
    assert (start + timedelta(seconds=off)).date().isoformat() == "2026-08-18"
    assert coros.session_offset(int(start.replace(tzinfo=timezone.utc).timestamp()), None, 7200.0) == 7200.0


async def test_a_triathlon_is_not_counted_twice(db_session: AsyncSession, test_user: User, fake, no_commit):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    t0 = now - timedelta(days=2, hours=5)
    tri = {"label": 333, "code": 10000, "type": "Triathlon", "name": "Nice", "start": t0, "seconds": 17430,
           "km": 116.9}
    fake.sessions = [tri]
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    rows = (await db_session.execute(select(Activity).where(Activity.user_id == test_user.id))).scalars().all()
    assert [(r.coros_activity_id, r.sport_type, r.raw_data.get("multisport")) for r in rows] == [(333, "Workout", True)]
    # Strava then brings the three legs: the COROS session gives way, its id goes on the swim
    for i, (sport, km, start, secs) in enumerate((("Swim", 1.99, t0, 2100), ("Ride", 93.8, t0 + timedelta(minutes=40),
                                                  9600), ("Run", 21.1, t0 + timedelta(hours=3, minutes=25), 5400))):
        db_session.add(_strava(test_user, start, km, secs, sport=sport, sid=950 + i))
    await db_session.flush()
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=3)
    assert (await coros.run_sync(db_session, conn))["ok"]
    rows = (await db_session.execute(select(Activity).where(Activity.user_id == test_user.id)
                                     .order_by(Activity.start_date))).scalars().all()
    assert [(r.strava_activity_id, r.coros_activity_id) for r in rows] == [(950, 333), (951, None), (952, None)]


async def test_the_sessions_never_make_the_health_history_count_as_cut_short(db_session: AsyncSession,
                                                                           test_user: User, fake, no_commit):
    fake.tool_status = {"querySportRecords": 503}
    conn = await _link(db_session, test_user)
    for _ in range(2):
        outcome = await coros.run_sync(db_session, conn)
        assert outcome["ok"] and conn.last_error is None and conn.sessions_synced_at is None
        conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=3)
    fake.tool_calls.clear()
    fake.tool_status = {}
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert len([a for n, a in fake.tool_calls if n == "querySportRecords"]) == 3  # the history, asked again
    hrv = [a for n, a in fake.tool_calls if n == "querySleepHrv"]
    assert len(hrv) == 1  # while the nights stay on the last week
    assert conn.sessions_synced_at is not None


async def test_one_lost_piece_of_the_sessions_history_is_asked_again(db_session: AsyncSession, test_user: User,
                                                                     fake, no_commit):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    fake.sessions = [{"label": 1, "code": 100, "start": now - timedelta(days=20), "seconds": 3000, "km": 10},
                     {"label": 2, "code": 100, "start": now - timedelta(days=100), "seconds": 3000, "km": 10}]
    calls = {"n": 0}
    real = fake.records

    def flaky(args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise test_coros.httpx.ReadTimeout("timed out")
        return real(args)

    fake.records = flaky
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert conn.sessions_synced_at is None
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=3)
    assert (await coros.run_sync(db_session, conn))["ok"]
    labels = (await db_session.execute(select(Activity.coros_activity_id).where(
        Activity.user_id == test_user.id).order_by(Activity.coros_activity_id))).scalars().all()
    assert labels == [1, 2] and conn.sessions_synced_at is not None


async def test_a_merge_keeps_what_only_the_watch_measured(db_session: AsyncSession, test_user: User):
    start = datetime(2026, 10, 4, 7, 0, tzinfo=timezone.utc)
    db_session.add_all([
        Activity(user_id=test_user.id, garmin_activity_id=55, sport_type="Ride", name="Garmin", start_date=start,
                 distance=60000, moving_time=7200, elapsed_time=7300, total_elevation_gain=600, average_heartrate=151,
                 max_heartrate=178, average_watts=270, calories=1150, raw_data={}),
        _strava(test_user, start + timedelta(seconds=60), 60.5, 7150, sport="Ride", dplus=0.0),
    ])
    await db_session.flush()
    assert await merge_twins(db_session, test_user.id, start - timedelta(days=1)) == 1
    [row] = (await db_session.execute(select(Activity).where(Activity.user_id == test_user.id))).scalars().all()
    assert (row.strava_activity_id, row.garmin_activity_id, row.name) == (900, 55, "Strava run")
    assert (row.average_heartrate, row.max_heartrate, row.average_watts, row.calories,
            row.total_elevation_gain) == (151, 178, 270, 1150, 600)
