"""Repeated Santé calculations keep their results and their isolation, and the page stays within its budget (owner,
2026-10-14: the page cost 1.5–2.8 s of CPU per load: the Charge accumulée recomputed every session's load for each
of 400 days, the 14-day history its sleep bases, and every daily row was read whole)."""
import math
import time
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import event

from app.models.activity import Activity
from app.models.health import HealthMetric
from app.models.mobile import MobileDaily
from app.services import nights as nt
from app.services import sante
from app.services import sante_training as st

TODAY = date(2026, 10, 13)


def nights(hr=48.0, source="COROS"):
    return {TODAY - timedelta(days=i): nt.Night(
        day=TODAY - timedelta(days=i), source=source, asleep=480, hr=hr + i % 3, hr_source=source,
        hrv=60.0 * math.exp(0.05 * (i % 5 - 2)), hrv_source=source,
    ) for i in range(150)}


def test_the_history_reuses_each_trend_without_changing_it():
    """Fourteen days read their trends and baselines once per (metric, day) inside the page's memo; the results
    are those computed without it."""
    rows = nights()
    days = [TODAY - timedelta(days=i) for i in range(14)]
    expected = [nt.trend(rows, m, d) for d in days for m in ("hrv", "hr")]
    with nt.memo():
        nt.freeze(rows)
        with patch.object(nt, "_trend", wraps=nt._trend) as calculate:
            assert [nt.trend(rows, m, d) for d in days for m in ("hrv", "hr")] == expected
            assert calculate.call_count == 28
            assert [nt.trend(rows, m, d) for d in days for m in ("hrv", "hr")] == expected
            assert calculate.call_count == 28


def test_trends_do_not_leak_between_accounts_or_requests():
    a, b = nights(48.0), nights(55.0, "Garmin")
    expected_a, expected_b = nt.trend(a, "hr", TODAY), nt.trend(b, "hr", TODAY)
    assert expected_a != expected_b
    with nt.memo():
        nt.freeze(a)
        nt.freeze(b)
        assert nt.trend(a, "hr", TODAY) == expected_a and nt.trend(b, "hr", TODAY) == expected_b
    for night in a.values():
        night.hr += 5
    with nt.memo():
        nt.freeze(a)
        assert nt.trend(a, "hr", TODAY) != expected_a


def test_mutable_nights_are_not_cached_without_explicit_freeze():
    rows = nights()
    with nt.memo():
        before = nt.trend(rows, "hr", TODAY)
        for night in rows.values():
            night.hr += 5
        assert nt.trend(rows, "hr", TODAY) != before


def test_a_session_load_is_memoized_per_its_bounds():
    """One session's load is computed once per (session, its heart-rate fields, the day's bounds)."""
    s = st.Session(id=987_001, start=datetime(2026, 10, 1, 7, tzinfo=timezone.utc), day=date(2026, 10, 1),
                   sport="Run", minutes=60, dplus=0, km=10, speed=2.8, hr=150, hr_peak=180, workout_type=0,
                   temp=None, segs=((30, 140.0), (30, 160.0)))
    with patch.object(st, "_session_load", wraps=st._session_load) as calculate:
        first = st.session_load(s, 50, 190)
        assert st.session_load(s, 50, 190) == first and calculate.call_count == 1
        assert st.session_load(s, 48, 190) != first and calculate.call_count == 2  # other bounds: computed anew


async def test_the_page_stays_within_its_budget(db_session, test_user):
    """500 activities with laps and splits, 400 nights of Garmin rows, Garmin day rows carrying their raw readings:
    the whole page in a few statements and well under a second (it took 1.5–2.8 s of CPU), on SQLite and on
    PostgreSQL alike: the statements and the process's CPU (the page's own work, the driver's decoding included),
    and the wall time, which holds the database's (PostgreSQL's server and its transfers: reading the raw readings
    would show there). The first load reads the sessions (their raw data, laps and splits); the next one reuses
    them, as a worker does until an activity is added."""
    for k in range(400):
        d = TODAY - timedelta(days=k)
        start = datetime.combine(d - timedelta(days=1), datetime.min.time()).replace(hour=23)
        rows = (("sleep", 440 + k % 7 * 10, {"main_start": start.isoformat(timespec="minutes"),
                                             "main_end": f"{d}T07:00", "tz": 120}),
                ("hrv", 60 * math.exp(0.05 * (k % 5 - 2)), {"method": "ln_mean_main", "n": 80}),
                ("hr_night", 48 + k % 3, {"method": "points"}), ("resp_night", 14 + k % 2 * 0.2, {"method": "points"}),
                ("steps", 9000, {}), ("hr_day", 62, {"readings": [[i, 60 + i % 30] for i in range(700)]}))
        for metric, value, details in rows:
            db_session.add(HealthMetric(user_id=test_user.id, date=d, metric=metric, value=value, source="Garmin",
                                        details=details))
        if k % 3 == 0:
            db_session.add(MobileDaily(user_id=test_user.id, platform="ios", date=d, metric="weight", value=70,
                                       sources=["Balance"], measured_at=datetime.combine(d, datetime.min.time(),
                                                                                         tzinfo=timezone.utc)))
    for i in range(500):
        d = TODAY - timedelta(days=int(i * 0.8))
        laps = [{"moving_time": 300, "average_heartrate": 140 + i % 20, "distance": 1000} for _ in range(12)]
        splits = [{"distance": 1000, "moving_time": 330, "average_heartrate": 140, "elevation_difference": 3}] * 10
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=50_000 + i, sport_type="Run", name=f"r{i}",
                                start_date=datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
                                + timedelta(hours=7), distance=10_000, moving_time=3600, elapsed_time=3700,
                                total_elevation_gain=50, average_speed=2.8, average_heartrate=140 + i % 20,
                                max_heartrate=175, raw_data={"utc_offset": 7200}, laps=laps, splits_metric=splits))
    await db_session.flush()
    st._CACHE.clear()
    st._SEGS.clear()
    engine = db_session.get_bind()
    count = [0]

    def counted(*a, **k):
        count[0] += 1
    event.listen(engine, "before_cursor_execute", counted)
    try:
        now = datetime(2026, 10, 13, 8, tzinfo=timezone.utc)
        cold_wall = time.perf_counter()
        await sante.health_page(db_session, test_user.id, today=TODAY, now=now)  # cold: the sessions read
        cold_wall, cold_count = time.perf_counter() - cold_wall, count[0]
        count[0] = 0
        started, wall = time.process_time(), time.perf_counter()
        page = await sante.health_page(db_session, test_user.id, today=TODAY, now=now)
        cpu, wall = time.process_time() - started, time.perf_counter() - wall
    finally:
        event.remove(engine, "before_cursor_execute", counted)
    assert page["state"] and page["load"] and page["fitness_progress"]
    # measured 2026-10-14, SQLite and PostgreSQL alike: 14 statements and ≈ 0,26 s cold; 12 and ≈ 0,2 s warm
    # (≈ 3 s of CPU before)
    assert cold_count <= 18 and cold_wall < 1.5, (cold_count, cold_wall)
    assert count[0] <= 16, count[0]
    assert cpu < 0.6 and wall < 1.0, (cpu, wall)
