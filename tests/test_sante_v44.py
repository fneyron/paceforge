"""Santé v4.4 — the same features for every user (brief V44, research_data.md §4 R2–R4): race-like efforts and a
real max HR without Strava (R3), « en altitude » where the athlete sleeps (R2), « journée chaude » from the weather
(R4). Every threshold below is a PaceForge heuristic (H). No network: Open-Meteo is an httpx.MockTransport."""
from datetime import date, datetime, time, timedelta, timezone
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.services import coros, sante
from app.services import nights as nt
from app.services import sante_training as st
from app.services.sante_training import Session
from tests import owner_coros as oc
from tests import test_coros
from tests.test_coros import _link
from tests.test_nights import night_rows

fake, no_commit = test_coros.fake, test_coros.no_commit
D = date(2026, 10, 8)


def run(day, minutes=50, hr=130, hour=8, sid=1, sport="Run", dplus=0, offset=0, **kw) -> Session:
    start = datetime.combine(day, time(hour), tzinfo=timezone.utc) - timedelta(seconds=offset)
    return Session(id=sid, start=start, day=day, sport=sport, minutes=minutes, dplus=dplus, km=minutes / 6,
                   speed=2.8, hr=hr, hr_peak=kw.pop("hr_peak", None), workout_type=kw.pop("workout_type", 0),
                   temp=kw.pop("temp", None), elapsed=minutes, offset=offset, **kw)


# ── R3: COROS's max HR, from its laps ─────────────────────────────────────

def test_coros_laps_give_the_whole_activitys_max_hr():
    """queryActivityLapData answers JSON (Live 2026-10-08): the whole-activity row (type −1) holds the max HR; a
    pilates session has one plain lap; a triathlon one block per leg. Anything else: no value, never a guess."""
    assert coros.parse_laps(oc.LAPS_2026_09_30) == {"hr_max": 171}
    assert coros.parse_laps(oc.LAPS_PILATES) == {"hr_max": 98}
    assert coros.parse_laps(oc.LAPS_TRIATHLON) == {"hr_max": 162}  # the run leg's
    assert coros.parse_laps("Lap 1\nMax HR: 171 bpm") == {"hr_max": 171}  # prose: the figure it carries
    assert coros.parse_laps("") == {} and coros.parse_laps("Erreur interne") == {} and coros.parse_laps(None) == {}
    glitch = '{"lapGroups":[{"type":-1,"laps":[{"maxHr":0}]},{"type":2,"laps":[{"maxHr":300},{"maxHr":"x"}]}]}'
    assert coros.parse_laps(glitch) == {}  # a sensor without HR, a spike: nothing


async def test_a_coros_only_athlete_gets_a_real_max_hr_and_the_evening_tag(db_session: AsyncSession,
                                                                         test_user: User, fake, no_commit):
    """COROS's detail has no max HR, so a COROS-only athlete ran on the 190 default (« sortie intense le soir »
    skewed). The laps are read with the detail, for the same sessions, under the call cap: hr_max() gets real
    peaks, and a hard evening session (150 bpm, ≥ 80 % of a 45–171 reserve) drops the night after it."""
    now = datetime.now(timezone.utc).replace(hour=3, minute=0, second=0, microsecond=0)
    fake.sessions = [{"label": 500 + i, "code": 100, "start": now - timedelta(days=2 + 3 * i), "seconds": 3000,
                      "km": 10} for i in range(coros.DETAILS_PER_SYNC)]
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    names = [n for n, _ in fake.tool_calls]
    assert names.count("queryActivityLapData") == names.count("getActivityDetail") == coros.DETAILS_PER_SYNC
    assert len(names) <= coros.MAX_CALLS  # a first sync: 60 days of nights, 180 of sessions, 10 × (detail + laps)
    rows = (await db_session.execute(select(Activity).where(Activity.user_id == test_user.id))).scalars().all()
    assert {a.max_heartrate for a in rows} == {171} and all(a.raw_data["lap_data"] == {"hr_max": 171} for a in rows)
    today = now.date()
    sessions = await st.load_sessions(db_session, test_user.id, today)
    assert len(sessions) == 10 and st.hr_max(sessions, today) == 171  # was 190: no peak at all

    nights = nt.build_nights(night_rows(range(0, 20), today=today, hr=45.0), today)  # asleep at 23:00
    evening = run(today - timedelta(days=1), minutes=40, hr=150, hour=21, sid=99)  # ends 21:40
    nt.tag_nights(nights, [evening], [], {}, rest=45, peak=st.hr_max(sessions, today))
    assert "late" in nights[today].tags
    nights = nt.build_nights(night_rows(range(0, 20), today=today, hr=45.0), today)
    nt.tag_nights(nights, [evening], [], {}, rest=45, peak=190)  # the old default: 150 < 161
    assert "late" not in nights[today].tags

    # read once: the next sync asks neither again
    fake.tool_calls.clear()
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=3)
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert not {"queryActivityLapData", "getActivityDetail"} & {n for n, _ in fake.tool_calls}
    await db_session.refresh(rows[0])
    assert rows[0].raw_data["lap_data"] == {"hr_max": 171}  # the session list rewrote raw_data, the laps stay


async def test_sessions_detailed_before_v44_get_their_laps_only(db_session: AsyncSession, test_user: User, fake,
                                                                no_commit):
    """A session whose detail was read before v4.4: only its laps are asked (one call, not two)."""
    now = datetime.now(timezone.utc).replace(hour=3, minute=0, second=0, microsecond=0)
    fake.sessions = [{"label": 601, "code": 102, "start": now - timedelta(days=1), "seconds": 3000, "km": 10}]
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    act = (await db_session.execute(select(Activity).where(Activity.coros_activity_id == 601))).scalar_one()
    act.raw_data = {k: v for k, v in act.raw_data.items() if k != "lap_data"}
    act.max_heartrate = None
    await db_session.flush()
    fake.tool_calls.clear()
    fake.laps = oc.LAPS_PILATES
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=3)
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert [a for n, a in fake.tool_calls if n == "queryActivityLapData"] == [{"labelId": "601", "sportType": 102}]
    assert not [n for n, _ in fake.tool_calls if n == "getActivityDetail"]
    await db_session.refresh(act)
    assert act.max_heartrate == 98 and act.raw_data["detail"]["dplus"] == 110


# ── R3: a Longue run like a race, whoever measured it ─────────────────────

REST = 45.0  # the athlete's median nightly HR; no peak: 190 (H) → 80 % of the reserve = 161 bpm


def _rest(d):
    return REST


def _caps(s, rest_of=_rest):
    [e] = st.efforts([s], rest_of)
    return e.kind, e.caps


def test_a_longue_at_80_percent_of_the_reserve_keeps_the_race_window():
    """R3 (H): a Longue (≥ 3 h, or ≥ 1 500 m D+ on foot) gets 65 until D+5 when it is marked as a race on Strava
    (as before), OR its average HR ≥ 80 % of the heart-rate reserve (the « vigorous » test: rest = the median
    nightly HR, peak = hr_max), OR Strava's perceived exertion ≥ 8. An easy 3 h run keeps D+3."""
    d = D - timedelta(days=4)
    assert _caps(run(d, 200, hr=165)) == ("long", ((5, 65),))  # 165 ≥ 161, no Strava flag
    assert _caps(run(d, 200, hr=161)) == ("long", ((5, 65),))  # at the line
    assert _caps(run(d, 200, hr=150)) == ("long", ((3, 65),))  # an easy 3 h run: D+3
    assert _caps(run(d, 170, hr=165, dplus=1600)) == ("long", ((5, 65),))  # the legs rule is a Longue too
    assert _caps(run(d, 200, hr=150, workout_type=1)) == ("long", ((5, 65),))  # marked on Strava, as before
    assert _caps(run(d, 200, hr=150, rpe=8)) == ("long", ((5, 65),))  # rated 8/10 on Strava
    assert _caps(run(d, 200, hr=150, rpe=7)) == ("long", ((3, 65),))
    assert _caps(run(d, 200, hr=None, rpe=9)) == ("long", ((5, 65),))  # no HR needed for the rating
    assert _caps(run(d, 200, hr=165), rest_of=None) == ("long", ((3, 65),))  # no bounds: no HR test
    # Très longue and Ultra unchanged, however hard (their windows already run longer)
    assert _caps(run(d, 400, hr=170)) == _caps(run(d, 400, hr=120)) == ("very_long", ((2, 45), (5, 65)))
    assert _caps(run(d, 700, hr=170)) == _caps(run(d, 700, hr=120)) == ("ultra", ((3, 35), (10, 65)))
    e = st.efforts([run(d, 200, hr=165)], _rest)[0]
    assert st.effort_window([e], d + timedelta(days=5))["cap"] == 65
    assert st.effort_window([e], d + timedelta(days=6)) is None
    assert st.INTENSE_RPE == 8 and nt.VIGOROUS_HRR == 0.8  # (H)


def test_the_heart_rate_test_reads_the_bounds_as_of_the_effort():
    """The athlete's bounds as of the day the effort started: the peaks of the 12 months up to it, the nights'
    resting HR up to it (rest_of), so every later day sees the same window. A chain is judged on its average HR
    by time."""
    d = D - timedelta(days=4)
    before = [run(d - timedelta(days=10 + k), 50, hr=130, hr_peak=170, sid=100 + k) for k in range(12)]
    after = [run(d + timedelta(days=1 + k % 3), 50, hr=130, hr_peak=200, sid=200 + k) for k in range(12)]
    long = run(d, 200, hr=150, sid=1)
    assert st.hr_max(before + after, d) == 170 and st.hr_max(before + after, D) == 200
    # 150 ≥ 45 + 0.8 × (170 − 45) = 145: run like a race, with the peak it had then (200 later would say 161)
    [e] = [x for x in st.efforts(before + after + [long], _rest) if x.session_id == 1]
    assert e.caps == ((5, 65),)
    chain = [run(d, 120, hr=170, sid=1), run(d, 100, hr=140, hour=10, sid=2)]  # 10:00, 0 min after the first
    [c] = st.efforts(chain, _rest)
    assert c.kind == "long" and c.caps == ((3, 65),)  # (120·170 + 100·140) / 220 = 156 < 161


async def test_strava_perceived_exertion_is_read(db_session: AsyncSession, test_user: User):
    """`perceived_exertion` (Strava's detail, 1–10, only when the athlete rated it): read; odd values left out."""
    start = datetime(2026, 10, 1, 6, tzinfo=timezone.utc)
    for i, rpe in enumerate((8, None, 11, "7")):
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=4400 + i, sport_type="Run", name=f"r{i}",
                                start_date=start - timedelta(days=i), distance=30_000, moving_time=12_000,
                                elapsed_time=12_100, total_elevation_gain=300, average_heartrate=140,
                                raw_data={"perceived_exertion": rpe, "utc_offset": 7200}))
    await db_session.flush()
    ss = {s.id: s for s in await st.load_sessions(db_session, test_user.id, D)}
    by = {a.strava_activity_id: a.id for a in (await db_session.execute(
        select(Activity).where(Activity.user_id == test_user.id))).scalars()}
    assert [ss[by[4400 + i]].rpe for i in range(4)] == [8, None, None, 7]
    assert st.efforts([ss[by[4400]]], _rest)[0].caps == ((5, 65),)


def test_the_history_reads_each_longue_as_its_own_day_saw_it():
    """The 14-day card: each past day's score is what the page computed that day (rows stored and activities
    finished by then), the Longue's heart-rate test included."""
    rows = night_rows(range(0, 60), today=D, hr=lambda k: 44.0 + k % 3, hrv=60.0)
    runs = [run(D - timedelta(days=3 + 4 * i), 50, hr=130, sid=100 + i) for i in range(8)]  # never on D-6
    sessions = runs + [run(D - timedelta(days=6), 200, hr=163, sid=9)]

    def page(rows, sessions, today):
        nights = nt.build_nights(rows, today)
        nt.tag_activities(nights, sessions, st.efforts(sessions), nt.rest_hr(nights, today), st.hr_max(sessions, today))
        nt.tag_alerts(nights, today)
        rest_of = partial(nt.rest_hr, nights)
        efforts = nt.anchor_efforts(nights, st.efforts(sessions, rest_of))
        with nt.memo():
            nt.freeze(nights)
            return (sante._assess(nights, sessions, efforts, today),
                    sante._history(nights, sessions, efforts, today, rest_of))

    day, hist = page(rows, sessions, D)
    assert day["window"] is None  # D+6: past D+5
    seen = {}
    for d, state, score in hist:
        past = {m: {x: v for x, v in per.items() if x <= d} for m, per in rows.items()}
        midnight = datetime.combine(d + timedelta(days=1), time(0))
        then = page(past, [s for s in sessions if st.local_end(s) <= midnight], d)[0]
        assert score["value"] == then["score"]["value"], d
        seen[(d - (D - timedelta(days=6))).days] = (then["window"] or {}).get("cap")
    assert seen[0] == seen[4] == seen[5] == 65 and seen[-1] is None  # D+0 → D+5: the race window
