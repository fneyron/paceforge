"""The training model (sante_training): weeks, heart-rate bounds, easy-pace HR (no Relative Effort, v4.4)."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.services import sante_training as st
from app.services.sante_training import Session

T = date(2026, 10, 6)


def S(days_ago: int, minutes: float = 60, sport: str = "Run", dplus: float = 50, km: float = 10,
      hr: float | None = 140, i: int = 0, **kw) -> Session:
    day = T - timedelta(days=days_ago)
    start = datetime(day.year, day.month, day.day, 7, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    speed = km * 1000 / (minutes * 60) if km else None
    return Session(id=kw.pop("id", days_ago * 100 + i), start=start, day=day, sport=sport, minutes=minutes,
                   dplus=dplus, km=km, speed=speed, hr=hr, hr_peak=kw.pop("hr_peak", 180),
                   workout_type=kw.pop("workout_type", 0), temp=kw.pop("temp", None), **kw)


def test_the_relative_effort_path_is_gone():
    """v4.4 (research « Also »): Strava's Relative Effort is a brand metric (« only available for subscribers »).
    Nothing reads it any more: no Session.suffer, no load model built on it (set_loads, daily_loads, fitness and
    what only they used)."""
    for name in ("set_loads", "daily_loads", "fitness", "trimp", "_family", "CTL_DAYS", "ATL_DAYS",
                 "MIN_HISTORY_DAYS", "MIN_SESSIONS"):
        assert not hasattr(st, name), name
    fields = {f.name for f in st.Session.__dataclass_fields__.values()}
    assert "suffer" not in fields and "load" not in fields


def test_hr_bounds():
    ss = [S(k, hr_peak=170 + k % 10, i=k) for k in range(30)] + [S(3, hr_peak=240, id=5000)]
    assert 178 <= st.hr_max(ss, T) <= 215
    assert st.hr_max(ss[:5], T) == 190
    assert st.hr_rest({T: 41, T - timedelta(days=1): 43, T - timedelta(days=2): 42}, {}, T) == 42
    assert st.hr_rest({}, {T: 48, T - timedelta(days=1): 50, T - timedelta(days=3): 52}, T) == 50
    assert st.hr_rest({}, {}, T) == 50


def test_weeks_match_activites():
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)  # Wednesday
    ss = [S(1, minutes=90, dplus=800), S(2, minutes=45), S(9, minutes=200, i=1), S(10, minutes=30)]
    w = st.weeks(ss, now)
    assert len(w) == 12 and w[-1]["current"] and w[-1]["key"] == "2026-10-05"
    assert w[-1]["minutes"] == 90 and w[-1]["dplus"] == 800  # Monday 5 and Sunday 4 split the weeks
    assert w[-2]["minutes"] == 45 and w[-3]["minutes"] == 230 and w[-3]["longest"] == 200
    assert w[-2]["href"] == "/activities?page=1#week-2026-09-28"
    assert w[0]["href"] == "/activities?page=2#week-2026-07-20"


def test_easy_runs_and_theil_sen():
    runs = [S(k, km=10, minutes=55, hr=140, i=k) for k in range(10)]
    hilly = S(1, km=10, minutes=60, dplus=400, id=1)
    race = S(2, km=10, minutes=45, workout_type=1, id=2)
    hot = S(3, km=10, minutes=55, temp=30, id=3)
    hard = S(4, km=10, minutes=45, hr=170, id=4)
    ride = S(5, sport="Ride", km=40, minutes=90, id=5)
    easy = st.easy_runs(runs + [hilly, race, hot, hard, ride], 185)
    assert {s.id for s in easy} == {s.id for s in runs} | {3}  # a hot run stays (drawn hollow), flagged as hot
    assert [s.id for s in easy if st.is_hot(s)] == [3]
    a, b = st.theil_sen([1, 2, 3, 4, 5], [11, 13, 15, 17, 99])  # one outlier: still 2x + 9
    assert b == pytest.approx(2) and a == pytest.approx(9)


def test_easy_model_moves_hr_to_the_reference_pace_and_watches_the_2_latest_runs():
    def run(k, hr, minutes=55, temp=None, i=0):
        return S(k, km=10, minutes=minutes, hr=hr, temp=temp, i=i, id=7000 + k * 10 + i)
    # 0.8 bpm per minute slower: the slope moves every run to the median pace
    base = [run(k, 130 + (60 - m) * 0.8, m) for k, m in zip(range(15, 200, 4), [50, 52, 54, 56, 58, 60] * 8)]
    m = st.easy_model(base, T, 185)
    assert m["pace"] == 325  # 5:25/km: the median speed of the runs (54 min for 10 km), to 5 s
    assert all(abs(m["value"][s.id] - m["value"][base[0].id]) < 0.5 for s in base)
    assert st.easy_watch(m, T) is None  # no run in the last 14 days
    high = base + [run(9, 130 + 4 * 0.8 + 4, 56), run(3, 130 + 4 * 0.8 + 5, 56)]
    w = st.easy_watch(st.easy_model(high, T, 185), T)
    assert w["flag"] and [d for d, _ in w["deltas"]] == [T - timedelta(days=9), T - timedelta(days=3)]
    # each run against the cool runs of the 14 days before it (H; Nuuttila 2022): the 2nd one's normal holds the 1st
    assert [v for _, v in w["deltas"]] == [pytest.approx(4, abs=0.3), pytest.approx(3, abs=0.3)]
    assert w["value"] == pytest.approx(3.5, abs=0.3)
    hot = base + [run(9, 150, 56, temp=30), run(3, 150, 56, temp=30)]
    assert st.easy_watch(st.easy_model(hot, T, 185), T) is None  # hot runs: out of the normal and the watch
    assert len(st.easy_deltas(st.easy_model(high, T, 185))) >= len(base) - 3
    assert st.easy_model(base[:5], T, 185) is None and st.easy_deltas(None) == [] and st.easy_watch(None, T) is None
    assert st.MIN_FIT_RUNS == 6 and (st.FLAG_REF_DAYS, st.FLAG_REF_MIN) == (14, 2) and (st.LINE_DAYS, st.LINE_RUNS) \
        == (42, 6)  # (H)


def test_easy_flag_reads_the_14_days_before_each_run_not_the_28():
    """F10: 4 cool runs 28–15 days ago at 133 bpm, then 137 bpm from 14 days
    ago on: against the 28-day median (133) both latest runs read +4; against
    the 14 days before each (137) they read +0: no « à surveiller » (the
    finding's scenario: 2 runs 14–3 days ago, then the 2 latest)."""
    def run(k, hr, i=0):
        return S(k, km=10, minutes=55, hr=hr, i=i, id=8000 + k * 10 + i)
    older = [run(k, 133) for k in range(60, 110, 7)]  # enough for the slope (all at one pace)
    ss = older + [run(k, 133) for k in (27, 23, 19, 16)] + [run(k, 137) for k in (13, 9, 2, 1)]
    m = st.easy_model(ss, T, 185)
    assert m["centre"](T - timedelta(days=1)) == 133  # the drawn band: the 28 days before
    assert [round(v) for _, v in st.easy_deltas(m)[-2:]] == [4, 4]  # what the 28-day median would have flagged
    w = st.easy_watch(m, T)
    assert not w["flag"] and [round(v) for _, v in w["deltas"]] == [0, 0]


def test_the_easy_pace_line_needs_6_runs_in_6_weeks():
    """F10: 8 cool runs over a year, 1 in the last 6 weeks: no line, no flag;
    6 within 6 weeks: the line (evidence row 16)."""
    def run(k, hr=140, i=0):
        return S(k, km=10, minutes=55, hr=hr, i=i, id=9000 + k * 10 + i)
    spread = [run(k) for k in (5, 60, 100, 150, 200, 250, 300, 350)]
    m = st.easy_model(spread, T, 185)
    assert m is not None and not st.line_ok(m, T) and st.easy_watch(m, T) is None
    recent = [run(k) for k in (2, 9, 16, 23, 30, 37)]
    assert st.line_ok(st.easy_model(recent, T, 185), T)


async def test_load_sessions_reads_strava_fields_and_drops_duplicates(db_session: AsyncSession, test_user: User):
    def add(i, start, km, s, **raw):
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=9000 + i, sport_type="Run", name=f"r{i}",
                                start_date=start, distance=km * 1000, moving_time=s, elapsed_time=s,
                                total_elevation_gain=30, average_heartrate=140, max_heartrate=175,
                                suffer_score=50, raw_data=raw))
    t = datetime(2026, 10, 5, 22, 30, tzinfo=timezone.utc)
    add(1, t, 10, 3300, workout_type=3, average_temp=12.5, utc_offset=32400)
    add(2, t + timedelta(seconds=40), 10.1, 3200)  # the phone's copy
    add(3, t - timedelta(days=1), 0.2, 60)  # a false start
    await db_session.flush()
    ss = await st.load_sessions(db_session, test_user.id, T)
    assert len(ss) == 1
    s = ss[0]
    assert s.day == date(2026, 10, 6)  # 22:30 UTC is the next morning in Tokyo
    assert s.workout_type == 3 and s.temp == 12.5 and s.minutes == 55 and not hasattr(s, "suffer")
    assert await st.utc_offset(db_session, test_user.id) == 32400
