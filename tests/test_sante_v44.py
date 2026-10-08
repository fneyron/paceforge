"""Santé v4.4 — the same features for every user (brief V44, research_data.md §4 R2–R4): race-like efforts and a
real max HR without Strava (R3), « en altitude » where the athlete sleeps (R2), « journée chaude » from the weather
(R4). Every threshold below is a PaceForge heuristic (H). No network: Open-Meteo is an httpx.MockTransport."""
import json
from datetime import date, datetime, time, timedelta, timezone
from functools import partial

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.activity import Activity
from app.models.health import HealthMetric
from app.models.user import User
from app.services import activity_env, coros, sante, weather
from app.services import nights as nt
from app.services import sante_training as st
from app.services.sante_training import Session
from tests import owner_coros as oc
from tests import test_coros
from tests.test_coros import _link
from tests.test_nights import night_rows

fake, no_commit, as_user = test_coros.fake, test_coros.no_commit, test_coros.as_user
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
    peaks, and a hard evening session (150 bpm, ≥ 80 % of a 45–171 reserve) tags the night after it (it never fires
    the alert)."""
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
    nt.tag_nights(nights, [evening], {}, rest=45, peak=st.hr_max(sessions, today))
    assert "late" in nights[today].tags
    nights = nt.build_nights(night_rows(range(0, 20), today=today, hr=45.0), today)
    nt.tag_nights(nights, [evening], {}, rest=45, peak=190)  # the old default: 150 < 161
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


# ── Open-Meteo: each outdoor activity's altitude and its start's weather, in the syncs only ──

class FakeMeteo:
    """Open-Meteo's Elevation, forecast (past_days) and ERA5 archive APIs, as the app calls them (GMT)."""

    def __init__(self, today: date):
        self.today = today
        self.calls: list[tuple[str, dict]] = []
        self.status: int | None = None  # every call answered with this status (503: down)
        self.elev = lambda lat, lon: 1800.0 if lat > 45.5 else 120.0  # « the Alps » north of 45.5°
        self.feels = lambda lat, lon, hour: 27.0 if hour.hour >= 12 else 18.0

    def handler(self, request):
        url, q = str(request.url).split("?")[0], dict(request.url.params)
        self.calls.append((url, q))
        if self.status:
            return httpx.Response(self.status)
        lats, lons = ([float(v) for v in q[k].split(",")] for k in ("latitude", "longitude"))
        if url == weather.ELEVATION_URL:
            return httpx.Response(200, json={"elevation": [self.elev(a, b) for a, b in zip(lats, lons)]})
        if url == weather.FORECAST_URL:
            first = self.today - timedelta(days=int(q["past_days"]))
            last = self.today + timedelta(days=int(q["forecast_days"]) - 1)
        else:
            first, last = date.fromisoformat(q["start_date"]), date.fromisoformat(q["end_date"])
        hours = [datetime.combine(first + timedelta(days=k), time(h)) for k in range((last - first).days + 1)
                 for h in range(24)]
        return httpx.Response(200, json={"hourly": {"time": [h.strftime("%Y-%m-%dT%H:%M") for h in hours],
                                                    "apparent_temperature": [self.feels(lats[0], lons[0], h)
                                                                             for h in hours]}})

    def named(self, url):
        return [q for u, q in self.calls if u == url]


@pytest.fixture
def meteo(monkeypatch):
    f = FakeMeteo(datetime.now(timezone.utc).date())
    monkeypatch.setattr(weather, "_transport", httpx.MockTransport(f.handler))
    monkeypatch.setattr(settings, "ACTIVITY_ENV_FETCH", True)
    monkeypatch.setattr(activity_env, "_paused_until", 0.0)
    activity_env._IDLE.clear()
    activity_env._ELEV_CACHE.clear()
    return f


def _act(user, start, sport="Run", minutes=50, km=10.0, sid=None, **raw):
    ids = {k: raw.pop(k) for k in ("strava_activity_id", "garmin_activity_id", "coros_activity_id") if k in raw}
    if not ids:
        ids = {"strava_activity_id": sid or int(start.timestamp()) % 10_000_000}
    return Activity(user_id=user.id, sport_type=sport, name="Sortie", start_date=start, distance=km * 1000,
                    moving_time=minutes * 60, elapsed_time=minutes * 60 + 60, total_elevation_gain=50,
                    average_heartrate=135, raw_data=raw, **ids)


async def test_each_outdoor_activity_gets_its_altitude_and_its_runs_their_weather(db_session: AsyncSession,
                                                                                  test_user: User, meteo):
    """Strava's end point, Garmin's end latitude/longitude, COROS's start coordinates: one elevation call for all.
    The runs that can be easy runs: the feels-like at the hour nearest their start, one call per place for the
    last 92 days (forecast past_days), the ERA5 archive before. Indoor and position-less ones: never looked up."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    day = (now - timedelta(days=3)).replace(hour=14, minute=10)
    acts = {
        "strava": _act(test_user, day, end_latlng=[45.92, 6.86], start_latlng=[45.90, 6.80], utc_offset=7200),
        "garmin": _act(test_user, day - timedelta(days=1), sport="Ride", minutes=120, km=60,
                       garmin_activity_id=77, endLatitude=43.70, endLongitude=7.26, source="garmin"),
        "coros": _act(test_user, day - timedelta(days=2), coros_activity_id=88, start_latlng=[45.95, 6.50],
                      source="coros", format=2),
        "treadmill": _act(test_user, day - timedelta(days=4), trainer=True),
        "zwift": _act(test_user, day - timedelta(days=5), sport="VirtualRide", minutes=60, km=30, elev_low=12.0),
        "manual": _act(test_user, day - timedelta(days=6)),  # no position
        "old": _act(test_user, (now - timedelta(days=200)).replace(hour=7, minute=40),
                    end_latlng=[43.30, 5.40], start_latlng=[43.30, 5.40]),
        "long": _act(test_user, day - timedelta(days=7), minutes=200, km=30,  # no easy run: altitude only
                     end_latlng=[45.9201, 6.8601], start_latlng=[45.92, 6.87]),  # ended where « strava » did
    }
    db_session.add_all(acts.values())
    await db_session.flush()
    out = await activity_env.enrich(db_session, test_user.id)
    assert out == {"alt": 5, "feels": 3, "left": 0}
    env = {k: a.raw_data.get(activity_env.ENV_KEY) for k, a in acts.items()}
    assert env["strava"] == {"v": 1, "alt": 1800.0, "feels": 27.0}  # 14:10 → the 14:00 hour, its end in the Alps
    assert env["garmin"] == {"v": 1, "alt": 120.0}  # a ride: no weather looked up
    assert env["coros"] == {"v": 1, "alt": 1800.0, "feels": 27.0}  # COROS: its start
    assert env["old"] == {"v": 1, "alt": 120.0, "feels": 18.0}  # 07:40 → 08:00, from the archive
    assert env["long"] == {"v": 1, "alt": 1800.0}
    assert env["treadmill"] is None and env["zwift"] is None and env["manual"] is None
    [elev] = meteo.named(weather.ELEVATION_URL)
    assert len(elev["latitude"].split(",")) == 4  # 5 activities, 4 places to 3 decimals: one call
    fcs = meteo.named(weather.FORECAST_URL)  # one call per place (2 decimals) for the last 92 days
    assert len(fcs) == 2 and all(q["hourly"] == "apparent_temperature" and q["timezone"] == "GMT" for q in fcs)
    [arch] = meteo.named(weather.ARCHIVE_URL)
    assert arch["start_date"] == arch["end_date"] == (now - timedelta(days=200)).date().isoformat()
    meteo.calls.clear()
    assert await activity_env.enrich(db_session, test_user.id) == {"alt": 0, "feels": 0, "left": 0}
    assert meteo.calls == []  # looked up once


async def test_an_outage_never_breaks_a_sync_and_is_asked_again_later(db_session: AsyncSession, test_user: User,
                                                                      meteo):
    """Open-Meteo down (a 503, a timeout, a 429): nothing is stored, nothing raises, the worker leaves Open-Meteo
    alone for 10 min (the next users' syncs never wait on it), then asks again."""
    now = datetime.now(timezone.utc)
    a = _act(test_user, now - timedelta(days=1), end_latlng=[45.92, 6.86], start_latlng=[45.92, 6.86])
    db_session.add(a)
    await db_session.flush()
    meteo.status = 503
    assert await activity_env.enrich(db_session, test_user.id) == {"alt": 0, "feels": 0, "left": 2}
    assert activity_env.ENV_KEY not in a.raw_data and len(meteo.calls) == 1  # stopped at the first failure
    meteo.status = None
    assert await activity_env.enrich(db_session, test_user.id) == {} and len(meteo.calls) == 1  # paused
    activity_env._paused_until = 0.0  # 10 min later
    assert await activity_env.enrich(db_session, test_user.id) == {"alt": 1, "feels": 1, "left": 0}
    env = a.raw_data[activity_env.ENV_KEY]
    assert env["alt"] == 1800.0 and env["feels"] in (18.0, 27.0)

    def boom(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    b = _act(test_user, now - timedelta(days=2), end_latlng=[44.0, 6.0], start_latlng=[44.0, 6.0])
    db_session.add(b)
    await db_session.flush()
    weather._transport = httpx.MockTransport(boom)
    assert (await activity_env.enrich(db_session, test_user.id))["left"] == 2  # a timeout: the same
    assert activity_env.ENV_KEY not in b.raw_data


async def test_a_page_never_looks_anything_up(as_user, db_session: AsyncSession, test_user: User, meteo):
    """Santé and Activités read what the syncs stored, never Open-Meteo."""
    now = datetime.now(timezone.utc)
    db_session.add(_act(test_user, now - timedelta(days=1), end_latlng=[45.92, 6.86], start_latlng=[45.92, 6.86]))
    await db_session.flush()
    for url in ("/sante", "/activities"):
        assert (await as_user.get(url)).status_code == 200
    assert meteo.calls == []


def test_coros_start_coordinates_are_read():
    """querySportRecords' « Start Coordinates » (Live 2026-10-08), outdoor GPS sessions only: stored as
    Strava's start_latlng; an indoor run has none."""
    from tests.test_activity_sources import RECORDS
    recs = {r["label"]: r for r in coros.parse_sport_records(RECORDS)}
    assert recs[480768797007970403]["start_latlng"] == [33.245998, 126.510002]
    indoor = [r for r in recs.values() if r["code"] == 101]
    assert indoor and all(r["start_latlng"] is None for r in indoor)
    f = coros.session_fields(recs[480768797007970403], 9 * 3600.0)
    assert f["raw_data"]["start_latlng"] == [33.245998, 126.510002] and f["raw_data"]["format"] == 2
    assert activity_env.end_point(f["raw_data"]) == (33.245998, 126.510002)
    weird = "1. Outdoor Run — 2026-10-01\n   Start Coordinates: 0.000000, 0.000000\n   Time Window: " \
            "startTimestamp=1 | endTimestamp=2\n   LabelId: 5 | SportType: 100\n"
    assert coros.parse_sport_records(weird)[0]["start_latlng"] is None  # no fix: no place


async def test_coros_sessions_read_before_v44_are_read_once_more(db_session: AsyncSession, test_user: User, fake,
                                                                no_commit):
    """Sessions stored before the coordinates were parsed: the 180 days are read once more (3 calls), what was
    read once for them (detail, laps, the looked-up altitude) stays; then the last week again."""
    now = datetime.now(timezone.utc).replace(hour=3, minute=0, second=0, microsecond=0)
    fake.sessions = [{"label": 701, "code": 100, "start": now - timedelta(days=40), "seconds": 3000, "km": 10}]
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    act = (await db_session.execute(select(Activity).where(Activity.coros_activity_id == 701))).scalar_one()
    act.raw_data = {k: v for k, v in act.raw_data.items() if k not in ("format", "start_latlng")} | {
        activity_env.ENV_KEY: {"v": 1, "alt": 640.0}}
    await db_session.flush()
    for expected in (3, 1):
        fake.tool_calls.clear()
        conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=3)
        assert (await coros.run_sync(db_session, conn))["ok"]
        assert len([1 for n, _ in fake.tool_calls if n == "querySportRecords"]) == expected
    await db_session.refresh(act)
    assert act.raw_data["format"] == 2 and act.raw_data[activity_env.ENV_KEY] == {"v": 1, "alt": 640.0}
    assert act.raw_data["detail"] and act.raw_data["lap_data"] == {"hr_max": 171}


def test_garmin_day_altitude_and_indoor_sessions():
    """The day's average altitude where the watch was worn (`averageMonitoringEnvironmentAltitude`; key from the
    libraries, not seen on a live account: without it the activities place the night) on the day's steps row; a
    treadmill run marked `trainer` like Strava's and COROS's."""
    from app.services import garmin
    from tests.test_garmin import activity, summary
    d = date(2026, 10, 5)
    s = garmin.parse_summary({**summary(d), "averageMonitoringEnvironmentAltitude": 1712.4})
    assert s["alt"] == 1712.4
    assert "alt" not in garmin.parse_summary(summary(d))
    assert "alt" not in garmin.parse_summary({**summary(d), "averageMonitoringEnvironmentAltitude": 99999})
    rows = garmin.build_daily({"summaries": {d: s}, "nights": {}, "naps": {}}, d)
    assert rows[0].metric == "steps" and rows[0].details == {"kcal": 2650.0, "exercise": 55, "alt": 1712.4}
    start = datetime(2026, 10, 5, 6, tzinfo=timezone.utc)
    assert garmin.activity_fields(activity(1, start, "treadmill_running"))["raw_data"]["trainer"] is True
    assert "trainer" not in garmin.activity_fields(activity(2, start, "trail_running"))["raw_data"]


async def test_garmin_keeps_what_was_looked_up_when_it_rewrites_a_session(db_session: AsyncSession,
                                                                          test_user: User):
    from app.services import garmin
    from tests.test_garmin import activity
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=1)
    raw = {**activity(31, start), "endLatitude": 45.9, "endLongitude": 6.8}
    await garmin.import_activities(db_session, test_user.id, [raw])
    act = (await db_session.execute(select(Activity).where(Activity.garmin_activity_id == 31))).scalar_one()
    act.raw_data = {**act.raw_data, activity_env.ENV_KEY: {"v": 1, "alt": 1650.0}}
    await db_session.flush()
    assert (await garmin.import_activities(db_session, test_user.id, [raw]))["updated"] == 1
    assert act.raw_data[activity_env.ENV_KEY] == {"v": 1, "alt": 1650.0} and act.raw_data["endLatitude"] == 45.9


# ── R2: « en altitude » where the athlete sleeps, for every user ────────────

def _nights(days=12, today=D):
    return nt.build_nights(night_rows(range(0, days), today=today), today)  # asleep 23:00 → 06:40


def _out(day, hour=9, minutes=60, alt=None, sid=1, elev_low=None, located=True, indoor=False, sport="Run"):
    """An activity ended at `alt` m (looked up in the sync), local time = UTC here."""
    return run(day, minutes, hour=hour, sid=sid, sport=sport, alt=alt, elev_low=elev_low, located=located,
               indoor=indoor)


def _alt_tags(nights):
    return sorted(d for d, n in nights.items() if "altitude" in n.tags)


def test_the_night_is_where_the_day_ended_first_from_garmins_day_altitude():
    """Night altitude, first available of (H): (1) Garmin's average altitude of the day before; (2) the ground
    altitude where the last outdoor activity ended before sleep onset (the day before or that day); (3) its
    lowest point (Strava elev_low). Tagged from 1 600 m (Latshang 2013)."""
    d = lambda k: D - timedelta(days=k)  # noqa: E731
    nights = _nights()
    nt.tag_nights(nights, [_out(d(8), alt=600, sid=2)], day_alt={d(9): 1750.0})  # (1) wins over the activity
    assert "altitude" in nights[d(8)].tags  # the night after Garmin's day at 1 750 m
    nights = _nights()
    nt.tag_nights(nights, [_out(d(8), alt=2000, sid=2)], day_alt={d(8): 900.0})  # Garmin says 900 m: not up there
    assert "altitude" not in nights[d(7)].tags
    nights = _nights()
    sessions = [_out(d(5), hour=8, alt=2100, sid=3), _out(d(5), hour=15, alt=700, sid=4)]  # back down by evening
    nt.tag_nights(nights, sessions)
    assert "altitude" not in nights[d(4)].tags  # (2) the last activity before sleep: in the valley
    nights = _nights()
    nt.tag_nights(nights, [_out(d(5), hour=15, alt=None, elev_low=1700.0, sid=5)])  # (3) no lookup: elev_low
    assert "altitude" in nights[d(4)].tags
    nights = _nights()
    nt.tag_nights(nights, [_out(d(5), hour=15, alt=1599.0, sid=6)])
    assert "altitude" not in nights[d(4)].tags and nt.ALTITUDE_M == 1600  # (H)
    # an activity after the night says nothing of it: a morning run up there, after a night down here
    nights = _nights()
    nt.tag_nights(nights, [_out(d(5), hour=7, alt=2400, sid=7)])
    assert _alt_tags(nights) == [d(4), d(3), d(2), d(1)]  # the night after it, then carried 3 nights


def test_a_summit_day_from_a_valley_is_no_night_at_altitude():
    """The old highest-point rule goes: a day climbing to 3 000 m and back to a 700 m valley tags no night."""
    d = lambda k: D - timedelta(days=k)  # noqa: E731
    nights = _nights()
    summit = _out(d(5), hour=6, minutes=480, alt=700, sid=1)  # its highest point was 3 000 m; it ended at 700 m
    nt.tag_nights(nights, [summit])
    assert _alt_tags(nights) == []


def test_altitude_carries_three_nights_without_any_activity():
    """A rest day up there is still a night up there: the tag carries to the following nights without any
    activity, 3 at most (H); an activity lower down, or one that cannot place the athlete, stops it."""
    d = lambda k: D - timedelta(days=k)  # noqa: E731
    nights = _nights()
    nt.tag_nights(nights, [_out(d(10), hour=15, alt=1900, sid=1)])
    assert _alt_tags(nights) == [d(9), d(8), d(7), d(6)] and nt.ALTITUDE_CARRY == 3  # (H)
    nights = _nights()
    nt.tag_nights(nights, [_out(d(10), hour=15, alt=1900, sid=1), _out(d(8), hour=15, alt=500, sid=2)])
    assert _alt_tags(nights) == [d(9), d(8)]  # down on d(8): no carry past it
    nights = _nights()
    nt.tag_nights(nights, [_out(d(10), hour=15, alt=1900, sid=1),
                           _out(d(9), hour=15, located=False, sid=2)])  # a run without a position
    assert _alt_tags(nights) == [d(9)]
    nights = _nights()
    nt.tag_nights(nights, [_out(d(10), hour=15, alt=1900, sid=1), _out(d(9), hour=15, indoor=True, sid=2)])
    assert _alt_tags(nights) == [d(9)]  # a treadmill places nobody


def test_a_failed_lookup_leaves_the_night_untagged():
    """An outdoor activity whose altitude lookup failed (no value, no Strava elev_low): the night is untagged,
    never guessed, and nothing carries across it."""
    d = lambda k: D - timedelta(days=k)  # noqa: E731
    nights = _nights()
    nt.tag_nights(nights, [_out(d(10), hour=15, alt=1900, sid=1), _out(d(9), hour=15, alt=None, sid=2)])
    assert _alt_tags(nights) == [d(9)]
    nights = _nights()
    nt.tag_nights(nights, [_out(d(5), hour=15, alt=None, sid=3)])
    assert _alt_tags(nights) == []


def test_an_overnight_activity_places_the_night_after_it():
    """By the day it ended: a hike through the night ending at 06:00 at 2 500 m places the night after it."""
    d = lambda k: D - timedelta(days=k)  # noqa: E731
    nights = _nights()
    nt.tag_nights(nights, [_out(d(6), hour=21, minutes=540, alt=2500, sid=1, sport="Hike")])  # → d(5) 06:00
    assert _alt_tags(nights)[:1] == [d(4)]  # not the night it ran through (asleep from 23:00 on d(6))


async def test_every_user_gets_the_tag_on_the_page(db_session: AsyncSession, test_user: User):
    """End to end: a COROS-only outing that ended up there (its start coordinates looked up in the sync) and a
    Garmin day at 1 750 m each tag the night after them on the page's nights table; a summit day back to the
    valley does not."""
    from tests.test_sante import _seed_rows
    await _seed_rows(db_session, test_user, night_rows(range(0, 30), today=D))
    d = lambda k: D - timedelta(days=k)  # noqa: E731

    def act(day, alt, **ids):
        start = datetime.combine(day, time(14), tzinfo=timezone.utc)
        return Activity(user_id=test_user.id, sport_type="TrailRun", name="Sortie", start_date=start,
                        distance=12_000, moving_time=7200, elapsed_time=7300, total_elevation_gain=900,
                        average_heartrate=130, **ids,
                        raw_data={"source": "coros", "format": 2, "start_latlng": [45.9, 6.8], "utc_offset": 0,
                                  activity_env.ENV_KEY: {"v": 1, "alt": alt}})
    db_session.add_all([act(d(20), 2050.0, coros_activity_id=1), act(d(12), 650.0, coros_activity_id=2)])
    db_session.add(HealthMetric(user_id=test_user.id, date=d(6), metric="steps", value=9000, source="Garmin",
                                details={"kcal": 2400, "exercise": 30, "alt": 1750.0}, n_samples=1))
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=D)
    marks = {r["iso"]: r["marks"] for r in page["sleep"]["rows"]}
    up = sorted((D - date.fromisoformat(iso)).days for iso, m in marks.items() if "en altitude" in m)
    # the night after the COROS outing that ended at 2 050 m (d19) and after Garmin's day at 1 750 m (d5), each
    # carried 3 nights without activity; the summit day that ended in the valley (d12) tags nothing
    assert up == [2, 3, 4, 5, 16, 17, 18, 19]
    ss = {s.id: s for s in await st.load_sessions(db_session, test_user.id, D)}
    assert sorted((s.alt, s.located, s.indoor) for s in ss.values()) == [(650.0, True, False), (2050.0, True, False)]


def _page(rows, sessions, today, day_alt=None):
    """What health_page computes for `today`: (its day, its 14-day history)."""
    nights = nt.build_nights(rows, today)
    nt.tag_activities(nights, sessions, st.efforts(sessions), nt.rest_hr(nights, today), st.hr_max(sessions, today),
                      day_alt)
    nt.tag_alerts(nights, today)
    rest_of = partial(nt.rest_hr, nights)
    efforts = nt.anchor_efforts(nights, st.efforts(sessions, rest_of))
    with nt.memo():
        nt.freeze(nights)
        return (sante._assess(nights, sessions, efforts, today),
                sante._history(nights, sessions, efforts, today, rest_of, day_alt))


def test_the_history_tags_altitude_as_each_day_knew_it():
    """A week up there (an outing ended at 2 100 m, Garmin's days at 1 900 m), nightly HR + 6 bpm: each past day
    of the 14-day card reads the nights as that day tagged them (rows and activities known then)."""
    d = lambda k: D - timedelta(days=k)  # noqa: E731
    rows = night_rows(range(0, 60), today=D, hr=lambda k: 52.0 if 4 <= k <= 9 else 44.0 + k % 3, hrv=60.0)
    sessions = [run(D - timedelta(days=12 + 4 * i), 50, hr=130, sid=100 + i) for i in range(8)]  # before the trip
    sessions += [_out(d(11), hour=15, alt=2100.0, sid=1)]
    day_alt = {d(9): 1900.0, d(8): 1900.0}
    day, hist = _page(rows, sessions, D, day_alt)
    for x, state, score in hist:
        past = {m: {k: v for k, v in per.items() if k <= x} for m, per in rows.items()}
        midnight = datetime.combine(x + timedelta(days=1), time(0))
        known = [s for s in sessions if st.local_end(s) <= midnight]
        then = _page(past, known, x, {k: v for k, v in day_alt.items() if k < x})[0]
        assert score["value"] == then["score"]["value"], x
    nights = nt.build_nights(rows, D)
    nt.tag_activities(nights, sessions, (), 45, 190, day_alt)
    # d10 (the outing), d9 (carried), d8 and d7 (Garmin's days), then d6 → d4 carried: 3 nights at most
    assert _alt_tags(nights) == [d(10), d(9), d(8), d(7), d(6), d(5), d(4)]


# ── R4: « journée chaude » from the weather, for every user ─────────────────

def test_a_run_is_hot_from_the_weather_else_its_device_never_indoors():
    """R4 (H): 25 °C or more felt at its start place and hour (Open-Meteo's apparent temperature, looked up in
    the sync), else the device's temperature as before; a treadmill or a home trainer is never hot."""
    d = D - timedelta(days=3)
    assert st.is_hot(run(d, feels=27.0))  # a COROS-only or Garmin-only run: the weather says it
    assert st.is_hot(run(d, feels=25.0)) and not st.is_hot(run(d, feels=24.9)) and st.HOT_C == 25  # (H)
    assert not st.is_hot(run(d, feels=21.0, temp=31.0))  # the wrist read body heat: the weather wins
    assert st.is_hot(run(d, temp=31.0)) and not st.is_hot(run(d))  # no lookup: the device, as before
    assert not st.is_hot(run(d, temp=31.0, indoor=True)) and not st.is_hot(run(d, feels=30.0, indoor=True))
    # the lookups gate on the easy-run gates (sante_training.easy_runs): the same numbers in both places
    assert (activity_env.RUN_KM, activity_env.RUN_MIN_MIN, activity_env.RUN_MAX_MIN) == (
        st.EASY_KM, st.EASY_MIN_MIN, st.EASY_MAX_MIN) and set(activity_env.RUN_SPORTS) == st.RUNS


def test_a_hot_run_from_the_weather_is_a_hollow_dot_on_activites():
    """Activités › FC en footing: the same hollow dot and « journée chaude » as before, now for every user."""
    from app.services import training_view as tv
    base = [run(D - timedelta(days=k), 60, hr=140 + (k % 3) - 1, sid=50_000 + k) for k in range(3, 200, 4)]
    hot = run(D - timedelta(days=6), 60, hr=150, hour=9, sid=7, feels=28.0)
    indoor = run(D - timedelta(days=10), 60, hr=150, hour=9, sid=8, temp=33.0, indoor=True)
    c = tv.footing(base + [hot, indoor], D, 185)
    assert len([p for p in c["dots"] if p["hot"]]) == 1  # the weather's, never the treadmill's 33 °C
    rows = json.loads(c["data"].replace("<\\/", "</"))["r"]
    assert [r[0] for r in rows if "journée chaude" in r[2]] == ["ven. 2 oct."]  # D − 6: the weather's hot run


async def test_the_looked_up_weather_reaches_the_sessions(db_session: AsyncSession, test_user: User):
    """What the sync stored on the activity (raw_data) is what the pages read: feels-like, indoor, position."""
    start = datetime(2026, 10, 1, 6, tzinfo=timezone.utc)
    db_session.add_all([
        _act(test_user, start, end_latlng=[45.9, 6.8], start_latlng=[45.9, 6.8],
             **{activity_env.ENV_KEY: {"v": 1, "alt": 1050.0, "feels": 26.5}}),
        _act(test_user, start - timedelta(days=1), trainer=True, average_temp=31.0),
        _act(test_user, start - timedelta(days=2), **{activity_env.ENV_KEY: {"v": 0, "feels": 40.0}}),  # older format
    ])
    await db_session.flush()
    ss = sorted(await st.load_sessions(db_session, test_user.id, D), key=lambda s: s.start, reverse=True)
    assert [(s.feels, s.alt, s.located, s.indoor) for s in ss] == [
        (26.5, 1050.0, True, False), (None, None, False, True), (None, None, False, False)]
    assert [st.is_hot(s) for s in ss] == [True, False, False]


async def test_coordinates_added_in_place_are_looked_up(db_session: AsyncSession, test_user: User, meteo):
    """A pass that found nothing to do is skipped until the activities change, a rewrite in place included
    (COROS's sessions read again with their start coordinates: no new row)."""
    a = _act(test_user, datetime.now(timezone.utc) - timedelta(days=2), coros_activity_id=91, source="coros")
    db_session.add(a)
    await db_session.flush()
    assert await activity_env.enrich(db_session, test_user.id) == {"alt": 0, "feels": 0, "left": 0}
    a.raw_data = {**a.raw_data, "start_latlng": [45.92, 6.86], "format": 2}
    await db_session.flush()
    assert (await activity_env.enrich(db_session, test_user.id))["alt"] == 1
