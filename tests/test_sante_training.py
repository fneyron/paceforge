"""Santé's training model: load, fond and fatigue, weeks, legs, easy-pace HR."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.services import sante_training as st
from app.services.sante_training import Session

T = date(2026, 10, 6)


def S(days_ago: int, minutes: float = 60, sport: str = "Run", dplus: float = 50, km: float = 10,
      hr: float | None = 140, suffer: float | None = None, i: int = 0, **kw) -> Session:
    day = T - timedelta(days=days_ago)
    start = datetime(day.year, day.month, day.day, 7, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    speed = km * 1000 / (minutes * 60) if km else None
    return Session(id=kw.pop("id", days_ago * 100 + i), start=start, day=day, sport=sport, minutes=minutes,
                   dplus=dplus, km=km, speed=speed, hr=hr, hr_peak=kw.pop("hr_peak", 180), suffer=suffer,
                   workout_type=kw.pop("workout_type", 0), temp=kw.pop("temp", None), **kw)


def test_trimp_grows_with_intensity_and_time():
    easy, hard = st.trimp(60, 135, 50, 185), st.trimp(60, 165, 50, 185)
    assert 60 < easy < 110 and hard > 1.5 * easy
    assert st.trimp(120, 135, 50, 185) == pytest.approx(2 * easy)
    assert st.trimp(60, 40, 50, 185) == 0  # below rest: nothing


def test_loads_put_relative_effort_on_the_trimp_scale_and_fill_sessions_without_hr():
    ss = [S(k, hr=140, suffer=60, i=k) for k in range(12)]
    ss.append(S(20, minutes=60, hr=None, suffer=None, id=999))  # no heart rate
    st.set_loads(ss, 50, 185)
    trimp = st.trimp(60, 140, 50, 185)
    assert ss[0].load == pytest.approx(trimp)  # k = trimp / 60 on every pair
    assert ss[-1].load == pytest.approx(trimp)  # the athlete's own load per minute on foot


def test_hr_bounds():
    ss = [S(k, hr_peak=170 + k % 10, i=k) for k in range(30)] + [S(3, hr_peak=240, id=5000)]
    assert 178 <= st.hr_max(ss, T) <= 215
    assert st.hr_max(ss[:5], T) == 190
    assert st.hr_rest({T: 41, T - timedelta(days=1): 43, T - timedelta(days=2): 42}, {}, T) == 42
    assert st.hr_rest({}, {T: 48, T - timedelta(days=1): 50, T - timedelta(days=3): 52}, T) == 50
    assert st.hr_rest({}, {}, T) == 50


def test_fatigue_needs_six_weeks_then_reads_the_last_week_against_the_fond():
    steady = [S(k, i=k) for k in range(1, 100)]
    st.set_loads(steady, 50, 185)
    f = st.form(steady, T)
    assert f is not None and -5 <= f["pct"] <= 10 and f["word"] == "équilibré"
    assert st.form(steady[:30], T) is None  # 30 days only
    heavy = steady + [S(k, minutes=120, i=500 + k) for k in range(0, 7)]
    st.set_loads(heavy, 50, 185)
    f = st.form(heavy, T)
    assert f["pct"] > 30 and f["key"] == "loaded"
    assert len(f["history"]) == 99 - 42 + 1 and f["history"][T] == f["pct"]  # from 6 weeks of history on


def test_weeks_match_activites():
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)  # Wednesday
    ss = [S(1, minutes=90, dplus=800), S(2, minutes=45), S(9, minutes=200, i=1), S(10, minutes=30)]
    w = st.weeks(ss, now)
    assert len(w) == 12 and w[-1]["current"] and w[-1]["key"] == "2026-10-05"
    assert w[-1]["minutes"] == 90 and w[-1]["dplus"] == 800  # Monday 5 and Sunday 4 split the weeks
    assert w[-2]["minutes"] == 45 and w[-3]["minutes"] == 230 and w[-3]["longest"] == 200
    assert w[-2]["href"] == "/activities?page=1#week-2026-09-28"
    assert w[0]["href"] == "/activities?page=2#week-2026-07-20"


def test_legs():
    base = [S(k, minutes=60, dplus=100, i=k) for k in range(3, 95, 2)]
    assert st.legs(base, T)["key"] == "fresh"
    big = base + [S(1, minutes=250, dplus=2100, id=1)]
    lg = st.legs(big, T)
    assert lg["key"] == "loaded" and lg["word"] == "chargées" and lg["big"].id == 1
    assert lg["minutes"] == 250 and lg["dplus"] == 2100
    huge = base + [S(0, minutes=320, dplus=900, id=2)]
    assert st.legs(huge, T)["key"] == "heavy"
    assert st.legs(base + [S(2, minutes=320, id=3)], T)["key"] != "heavy"  # 48 h only
    assert st.legs([S(0, sport="Ride", minutes=300)], T) is None


def test_easy_runs_and_theil_sen():
    runs = [S(k, km=10, minutes=55, hr=140, i=k) for k in range(10)]
    hilly = S(1, km=10, minutes=60, dplus=400, id=1)
    race = S(2, km=10, minutes=45, workout_type=1, id=2)
    hot = S(3, km=10, minutes=55, temp=30, id=3)
    hard = S(4, km=10, minutes=45, hr=170, id=4)
    ride = S(5, sport="Ride", km=40, minutes=90, id=5)
    assert {s.id for s in st.easy_runs(runs + [hilly, race, hot, hard, ride], 185)} == {s.id for s in runs}
    a, b = st.theil_sen([1, 2, 3, 4, 5], [11, 13, 15, 17, 99])  # one outlier: still 2x + 9
    assert b == pytest.approx(2) and a == pytest.approx(9)


def test_easy_hr_flags_a_heart_running_high_at_the_same_pace():
    def run(k, hr, minutes):
        return S(k, km=10, minutes=minutes, hr=hr, i=k)
    base = [run(k, 130 + (60 - m) * 0.8, m) for k, m in zip(range(15, 100, 4), [50, 52, 54, 56, 58, 60] * 4)]
    ok = st.easy_hr(base + [run(3, 130 + (60 - 55) * 0.8, 55), run(6, 130 + (60 - 58) * 0.8, 58)], T)
    assert ok["n"] == 2 and abs(ok["delta"]) < 0.5
    high = st.easy_hr(base + [run(3, 130 + (60 - 55) * 0.8 + 5, 55), run(6, 130 + (60 - 58) * 0.8 + 4.5, 58)], T)
    assert high["delta"] == pytest.approx(4.75, abs=0.3) and high["high"] == 2
    assert st.easy_hr(base[:5], T) is None


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
    assert s.workout_type == 3 and s.temp == 12.5 and s.minutes == 55 and s.suffer == 50
    assert await st.utc_offset(db_session, test_user.id) == 32400
