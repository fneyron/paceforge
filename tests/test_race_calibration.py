"""The personal part of the estimate: the athlete's races level the plan
(effort-km/h model) and his matched races shape it (fatigue tilt).

- D+ measured with one algorithm for the course and for the past races
- what counts as a race (no triathlon run leg, relay leg, abandon)
- recency weighting of the races
- the fatigue tilt fitted on every matched checkpoint, combined over races
"""

import math
import random
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.exceptions import StravaAPIError, StravaRateLimitError
from app.models.activity import Activity
from app.models.route import Route
from app.schemas.simulator import AthleteGradientProfile, CourseProfile, CourseSegment, GpxPoint
from app.services import race_calibration as rc
from app.services import race_simulator as rs
from app.services.gpx import build_course_profile, profile_elevation_gain

# ── D+: one algorithm for the course and the races ──


def _climb(step_m: float, noise_m: float = 3.0, seed: int = 1):
    """10 km climbing 1000 m then 10 km back down, altitude read every ``step_m`` with noise."""
    rnd = random.Random(seed)
    n = int(20000 / step_m)
    dist = [i * step_m for i in range(n + 1)]
    alt = [500 + (d / 10 if d <= 10000 else 1000 - (d - 10000) / 10) + rnd.uniform(-noise_m, noise_m) for d in dist]
    return dist, alt


def test_effort_dplus_reads_a_noisy_climb_right():
    gain, loss = profile_elevation_gain(*_climb(10))
    assert gain == pytest.approx(1000, rel=0.03) and loss == pytest.approx(1000, rel=0.03)
    # the per-km raw hysteresis the displayed total uses reads the noise as climb
    from app.services.gpx import _gain_loss_hysteresis

    raw_gain, _ = _gain_loss_hysteresis(_climb(10, noise_m=6)[1], 3.0)
    assert raw_gain > 1100 and profile_elevation_gain(*_climb(10, noise_m=6))[0] < 1060


def test_effort_dplus_barely_depends_on_the_sampling():
    g10 = profile_elevation_gain(*_climb(10))[0]
    g40 = profile_elevation_gain(*_climb(40, seed=2))[0]
    assert abs(g10 - g40) / g10 < 0.03


def test_effort_dplus_edge_cases():
    assert profile_elevation_gain([], []) == (0.0, 0.0)
    assert profile_elevation_gain([0.0], [100.0]) == (0.0, 0.0)
    assert profile_elevation_gain([0, 0, 0], [1, 2, 3]) == (0.0, 0.0)          # no distance
    # missing altitudes and a GPS jump backwards are skipped
    dist, alt = _climb(20)
    alt[5] = None
    dist[40] = dist[39] - 50
    assert profile_elevation_gain(dist, alt)[0] == pytest.approx(1000, rel=0.03)


def test_course_carries_the_effort_dplus():
    dist, alt = _climb(10)
    pts = [GpxPoint(lat=45 + d / 1e6, lon=6.0, elevation=a, distance_from_start=d) for d, a in zip(dist, alt, strict=True)]
    course = build_course_profile(pts)
    assert course.dplus_effort == pytest.approx(1000, rel=0.03)
    assert course.total_elevation_gain > course.dplus_effort  # the display total stays the per-km raw sum
    assert rc.course_dplus(course) == course.dplus_effort


def _course_without_effort_dplus(with_ele: bool = True) -> CourseProfile:
    dist, alt = _climb(25)
    coords = [[45.0, 6.0, d / 1000, a] if with_ele else [45.0, 6.0, d / 1000] for d, a in zip(dist, alt, strict=True)]
    seg = CourseSegment(index=0, start_km=0, end_km=20, distance_m=20000, elevation_gain=1234, elevation_loss=1234,
                        avg_gradient_pct=0, min_elevation=500, max_elevation=1500)
    return CourseProfile(total_distance_km=20, total_elevation_gain=1234, total_elevation_loss=1234,
                         segments=[seg], elevation_points=[], route_coords=coords)


def test_course_dplus_on_a_course_saved_before():
    course = _course_without_effort_dplus()
    assert course.dplus_effort is None
    assert rc.course_dplus(course) == pytest.approx(1000, rel=0.03)
    assert course.dplus_effort is not None  # computed once, kept on the course
    # no elevation on the trace: the displayed total
    assert rc.course_dplus(_course_without_effort_dplus(with_ele=False)) == 1234


def _act(**kw) -> Activity:
    base = dict(strava_activity_id=1, user_id=1, sport_type="TrailRun", name="Trail", distance=20000.0,
                moving_time=7200, elapsed_time=7300, total_elevation_gain=1000.0, raw_data={"workout_type": 1},
                start_date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    return Activity(**{**base, **kw})


def test_activity_dplus_cache_then_stream_then_strava_factor():
    dist, alt = _climb(20)
    cached = _act(raw_data={"workout_type": 1, rc.DPLUS_CACHE_KEY: {"v": 1, "gain": 950.0, "src": "stream"}})
    assert rc.activity_dplus(cached) == (950.0, "stream")
    streamed = _act(streams_data={"distance": dist, "altitude": alt})
    g, src = rc.activity_dplus(streamed)
    assert src == "stream" and g == pytest.approx(1000, rel=0.03)
    alt_only = _act(streams_data={"altitude": alt})  # spread over the activity's distance
    assert rc.activity_dplus(alt_only)[0] == pytest.approx(1000, rel=0.03)
    # a stream that had no altitude is cached as « none »: Strava's total × the factor
    none = _act(raw_data={"workout_type": 1, rc.DPLUS_CACHE_KEY: {"v": 1, "gain": None, "src": "none"}})
    assert rc.activity_dplus(none) == (1000.0 * rc.STRAVA_DPLUS_FACTOR, "strava")
    assert rc.activity_dplus(_act()) == (pytest.approx(1000.0 * rc.STRAVA_DPLUS_FACTOR), "strava")


@pytest.mark.asyncio
async def test_race_model_reads_the_recomputed_dplus(db_session, test_user):
    now = datetime.now(timezone.utc)
    common = dict(user_id=test_user.id, sport_type="TrailRun", elapsed_time=0)
    db_session.add_all([
        Activity(strava_activity_id=81001, name="Ultra A", start_date=now - timedelta(days=100), distance=100000.0,
                 moving_time=12 * 3600, total_elevation_gain=5500.0,
                 raw_data={"workout_type": 1, rc.DPLUS_CACHE_KEY: {"v": 1, "gain": 5000.0, "src": "stream"}}, **common),
        Activity(strava_activity_id=81002, name="Trail B", start_date=now - timedelta(days=200), distance=40000.0,
                 moving_time=4 * 3600, total_elevation_gain=2000.0, raw_data={"workout_type": 1}, **common),
    ])
    await db_session.flush()
    model = await rc.build_race_effort_model(db_session, test_user.id)
    by_name = {p["name"]: p for p in model["races"]}
    assert by_name["Ultra A"]["dplus"] == 5000 and by_name["Ultra A"]["dplus_src"] == "stream"
    assert by_name["Trail B"]["dplus"] == round(2000 * rc.STRAVA_DPLUS_FACTOR)
    assert by_name["Trail B"]["dplus_src"] == "strava"
    assert by_name["Ultra A"]["ekm"] == 150.0


@pytest.mark.asyncio
async def test_backfill_fetches_each_race_stream_once(db_session, test_user):
    dist, alt = _climb(20)
    now = datetime.now(timezone.utc)
    common = dict(user_id=test_user.id, sport_type="TrailRun", elapsed_time=0, moving_time=3 * 3600,
                  total_elevation_gain=1100.0, distance=20000.0)
    race = Activity(strava_activity_id=82001, name="Race", start_date=now - timedelta(days=10), raw_data={"workout_type": 1}, **common)
    gone = Activity(strava_activity_id=82002, name="Race gone", start_date=now - timedelta(days=20), raw_data={"workout_type": 1}, **common)
    training = Activity(strava_activity_id=82003, name="Footing", start_date=now - timedelta(days=5), raw_data={}, **common)
    done = Activity(strava_activity_id=82004, name="Done", start_date=now - timedelta(days=30),
                    raw_data={"workout_type": 1, rc.DPLUS_CACHE_KEY: {"v": 1, "gain": 900.0, "src": "stream"}}, **common)
    db_session.add_all([race, gone, training, done])
    await db_session.flush()

    async def streams(user, sid, types):
        assert types == ["distance", "altitude"]
        if sid == 82002:
            raise StravaAPIError("not found", status_code=404)
        return {"distance": dist, "altitude": alt}

    strava = AsyncMock()
    strava.get_activity_streams.side_effect = streams
    assert await rc.backfill_race_dplus(db_session, test_user, strava) == 2
    asked = sorted(c.args[1] for c in strava.get_activity_streams.call_args_list)
    assert asked == [82001, 82002]  # not the training run, not the race already done
    assert race.raw_data[rc.DPLUS_CACHE_KEY]["gain"] == pytest.approx(1000, rel=0.03)
    assert race.raw_data["workout_type"] == 1  # the rest of raw_data kept
    assert gone.raw_data[rc.DPLUS_CACHE_KEY] == {"v": 1, "gain": None, "src": "none"}  # not asked again
    strava.get_activity_streams.reset_mock()
    assert await rc.backfill_race_dplus(db_session, test_user, strava) == 0
    strava.get_activity_streams.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_stops_on_the_rate_limit_and_skips_transient_errors(db_session, test_user):
    now = datetime.now(timezone.utc)
    acts = [Activity(strava_activity_id=83000 + i, user_id=test_user.id, sport_type="Run", name=f"Race {i}",
                     start_date=now - timedelta(days=i + 1), distance=21100.0, moving_time=5400, elapsed_time=0,
                     total_elevation_gain=50.0, raw_data={"workout_type": 1}) for i in range(3)]
    db_session.add_all(acts)
    await db_session.flush()
    strava = AsyncMock()
    strava.get_activity_streams.side_effect = [StravaAPIError("boom", status_code=502), StravaRateLimitError()]
    assert await rc.backfill_race_dplus(db_session, test_user, strava) == 0
    assert strava.get_activity_streams.call_count == 2  # stopped at the 429, the third not asked
    assert all(rc.DPLUS_CACHE_KEY not in a.raw_data for a in acts)  # retried at the next poll


# ── what counts as a race ──

@pytest.mark.parametrize("name,why", [
    ("IM 70.3 monde - CaP", "course d'un triathlon"),
    ("70.3 Marbella - Run", "course d'un triathlon"),
    ("Half IM Greece - Run", "course d'un triathlon"),
    ("Ironman Nice", "course d'un triathlon"),
    ("Triathlon de l'Alpe d'Huez - course", "course d'un triathlon"),
    ("Alpsman - CAP", "course d'un triathlon"),
    ("Embrunman – C.A.P.", "course d'un triathlon"),
    ("Ultra 01 - Relais 7", "relais"),
    ("Marathon relay leg 3", "relais"),
    ("Lavaredo DNF km 80", "abandon"),
    ("UTMB (abandon Champex)", "abandon"),
    ("Fast Hiking - TMB", "pas une course"),
    ("IM 2025 Frankfurt - Run", "course d'un triathlon"),
    ("IM70.3 Aix", "course d'un triathlon"),
])
def test_efforts_that_are_not_a_standalone_race(name, why):
    assert rc.not_a_race_reason(name) == why


@pytest.mark.parametrize("name", [
    "Cap Corse Trail", "Trail du Cap Fréhel", "Ultra Trail du Cap", "Escapade", "Capitale Trail",
    "UTMB Transjeju 100M - 2eme", "Imperial Trail", "Marathon de Paris", "Semi Pollencia", "Maxi Race",
    "Trail des Relaisiens", "Grand Raid", "Swimrunner's Trail", "Marathon du mont blanc 90km",
    "Trailrun im Allgäu", "Ultratrail im Schwarzwald", "Marathon im Harz", "Berglauf im Pitztal", "Lauf im Park",
])
def test_real_races_are_kept(name):
    assert rc.not_a_race_reason(name) is None


def test_triathlon_legs_and_relays_never_come_back():
    pts = [
        {"name": "UTMB", "hours": 30.0, "ekm_h": 9.0},
        {"name": "70.3 Marbella - Run", "hours": 1.4, "ekm_h": 15.6},
        {"name": "Ultra 01 - Relais 7", "hours": 1.74, "ekm_h": 17.5},
        {"name": "Reco CCC", "hours": 8.0, "ekm_h": 8.0},
    ]
    used, ignored = rc.select_best_efforts(pts)
    # one race left: the recce still helps, the tri leg and the relay don't
    assert {p["name"] for p in used} == {"UTMB", "Reco CCC"}
    assert {p["why"] for p in ignored} == {"course d'un triathlon", "relais"}


def test_long_efforts_help_when_the_tagged_races_are_all_excluded():
    d = date(2026, 6, 1)
    long_runs = [rc.race_point("Long run", 45, 2500, 6.5, False, day=d),
                 rc.race_point("Sortie longue", 60, 3500, 9.5, False, day=d),
                 rc.race_point("Sortie longue 2", 80, 4500, 14, False, day=d)]
    # two tagged races, both a relay or a triathlon leg: the long efforts carry the model
    legs = [rc.race_point("SaintéLyon relais", 44, 1500, 4.5, True, day=d),
            rc.race_point("Ironman 70.3 Nice", 21, 200, 1.9, True, day=d)]
    model = rc.model_from_points(legs + long_runs, today=date(2026, 10, 1))
    assert model and model["n_used"] >= 2
    assert {p["name"] for p in model["races"]} <= {p["name"] for p in long_runs}
    assert {p["name"] for p in model["ignored"]} >= {"SaintéLyon relais", "Ironman 70.3 Nice"}
    # one real race left: still with the long efforts
    one = [rc.race_point("Trail X", 50, 3000, 7, True, day=d), legs[0]]
    model = rc.model_from_points(one + long_runs, today=date(2026, 10, 1))
    assert model["n_used"] >= 2 and "Trail X" in {p["name"] for p in model["races"]}
    # two real races: the tagged races alone
    two = [rc.race_point("Trail X", 50, 3000, 7, True, day=d), rc.race_point("Ultra Y", 100, 6000, 17, True, day=d)]
    model = rc.model_from_points(two + long_runs, today=date(2026, 10, 1))
    assert {p["name"] for p in model["races"]} == {"Trail X", "Ultra Y"}


def test_dplus_same_ruler_only_when_course_and_races_are_measured_alike():
    d = date(2026, 6, 1)

    def model(*srcs):
        return rc.model_from_points([
            rc.race_point(f"Ultra {i}", 50 + 30 * i, 3000 + 1500 * i, 7 + 5 * i, True, day=d, dplus_src=src)
            for i, src in enumerate(srcs)], today=date(2026, 10, 1))

    def ruler(m, course):
        prof = _prof(race_model=m)
        rs.predict_course(course, prof)
        return prof.race_calibration["dplus_same_ruler"]

    assert ruler(model("stream", "stream"), _course_without_effort_dplus()) is True
    assert ruler(model("stream", "strava"), _course_without_effort_dplus()) is False
    assert ruler(model("stream", "stream"), _course_without_effort_dplus(with_ele=False)) is False


OWNER_RACES = [  # the owner's tagged races (name, date, km, Strava D+, moving h)
    ("UTMB Transjeju 100M - 2eme", "2026-10-02", 148.2, 5513, 16.07),
    ("IM 70.3 monde - CaP", "2026-09-13", 21.1, 14, 1.35),
    ("Marathon du mont blanc 90km", "2026-06-26", 87.4, 5977, 13.53),
    ("Alpsman - CAP", "2026-06-06", 16.1, 1344, 2.02),
    ("100k UTMB Chiang Mai", "2025-12-05", 92.3, 4714, 11.16),
    ("Half IM Greece - Run", "2025-10-26", 21.2, 40, 1.40),
    ("TDS UTMB - 30eme", "2025-08-25", 152.9, 8801, 22.92),
    ("Marathon du mont blanc 90k", "2025-06-27", 92.2, 6117, 14.26),
    ("Semi Pollencia", "2025-05-18", 21.1, 29, 1.25),
    ("Trans Inthanon", "2024-12-06", 91.7, 4865, 15.64),
    ("70.3 Marbella - Run", "2024-10-27", 21.0, 82, 1.40),
    ("Ultra 01 - Relais 7", "2024-06-15", 22.5, 801, 1.74),
    ("Maxi Race", "2024-06-01", 93.1, 5247, 12.27),
]


def _owner_points():
    return [rc.race_point(n, km, dp * rc.STRAVA_DPLUS_FACTOR, h, True, day=date.fromisoformat(d))
            for n, d, km, dp, h in OWNER_RACES]


def test_owner_races_selection():
    model = rc.model_from_points(_owner_points(), today=date(2026, 10, 5))
    ignored = {p["name"]: p["why"] for p in model["ignored"]}
    for leg in ("IM 70.3 monde - CaP", "Alpsman - CAP", "Half IM Greece - Run", "70.3 Marbella - Run"):
        assert rc.not_a_race_reason(leg) == "course d'un triathlon"
    assert ignored.get("Ultra 01 - Relais 7") == "relais"
    used = {p["name"] for p in model["races"]}
    assert {"UTMB Transjeju 100M - 2eme", "TDS UTMB - 30eme", "100k UTMB Chiang Mai"} <= used
    assert not used & {"IM 70.3 monde - CaP", "Alpsman - CAP", "Half IM Greece - Run", "70.3 Marbella - Run", "Ultra 01 - Relais 7"}
    # replayed on his Transjeju course (147.9 km, 4854 m by the effort algorithm): 16h-17h30 moving (real 16h04)
    hours = rc.predict_total_s(model, rc.effort_km(147.9, 4854)) / 3600
    assert 16.0 < hours < 17.5


# ── recency ──

def test_recency_weight(monkeypatch):
    monkeypatch.setattr(rc, "RECENCY_HALF_LIFE_DAYS", 548.0)
    today = date(2026, 10, 5)
    assert rc.recency_weight(today, today) == 1.0
    assert rc.recency_weight(today - timedelta(days=548), today) == pytest.approx(0.5)
    assert rc.recency_weight((today - timedelta(days=1096)).isoformat(), today) == pytest.approx(0.25)
    assert rc.recency_weight(None, today) == 1.0 and rc.recency_weight("n/a", today) == 1.0
    monkeypatch.setattr(rc, "RECENCY_HALF_LIFE_DAYS", None)
    assert rc.recency_weight(today - timedelta(days=548), today) == 1.0


def test_recent_races_weigh_more_in_the_fit(monkeypatch):
    today = date(2026, 10, 5)
    pts = [  # the same ultra two years apart, faster now
        rc.race_point("Old ultra", 100, 4000, 10.0, True, day=today - timedelta(days=760)),
        rc.race_point("Recent ultra", 100, 4000, 9.0, True, day=today - timedelta(days=30)),
    ]
    monkeypatch.setattr(rc, "BEST_BIN_FACTOR", 1.05)  # narrow duration bins: both kept
    monkeypatch.setattr(rc, "RECENCY_HALF_LIFE_DAYS", None)
    flat = rc.model_from_points(pts, today=today)
    monkeypatch.setattr(rc, "RECENCY_HALF_LIFE_DAYS", 548.0)
    recent = rc.model_from_points(pts, today=today)
    t_flat = rc.predict_total_s(flat, 140) / 3600
    t_recent = rc.predict_total_s(recent, 140) / 3600
    assert t_recent < t_flat  # pulled toward the recent, faster race
    assert {p["name"] for p in recent["races"]} == {"Old ultra", "Recent ultra"}
    weights = {p["name"]: p["weight"] for p in recent["races"]}
    assert weights["Recent ultra"] > 0.95 and weights["Old ultra"] == pytest.approx(0.5 ** (760 / 548), rel=1e-3)


# ── fatigue tilt from every matched checkpoint ──

def _hilly(km: int = 60) -> dict:
    segs, pts, coords, elev = [], [], [], 300.0
    for i in range(km):
        g = 8 * math.sin(i / 3)
        start = elev
        elev += g * 10
        segs.append(CourseSegment(index=i, start_km=float(i), end_km=float(i + 1), distance_m=1000.0,
                                  elevation_gain=max(0.0, elev - start), elevation_loss=max(0.0, start - elev),
                                  avg_gradient_pct=round(g, 1), min_elevation=min(start, elev), max_elevation=max(start, elev)))
        for k in range(4):
            e = start + (elev - start) * k / 4
            pts.append({"distance_km": i + k / 4, "elevation": e})
            coords.append([45.0 + (i + k / 4) * 0.005, 6.0, i + k / 4, e])
    coords.append([45.0 + km * 0.005, 6.0, float(km), elev])
    pts.append({"distance_km": float(km), "elevation": elev})
    return CourseProfile(name="hilly", total_distance_km=float(km), total_elevation_gain=sum(s.elevation_gain for s in segs),
                         total_elevation_loss=sum(s.elevation_loss for s in segs), segments=segs, elevation_points=pts,
                         route_coords=coords).model_dump()


def _prof(**kw):
    return AthleteGradientProfile(flat_pace_s_per_km=330.0, gradient_factors=dict(rs._DEFAULT_GRADIENT_FACTORS),
                                  data_points=0, sport_types_used=[], **kw)


def _passages(cj, cps, tilt, total_s):
    course = rs.predict_course(CourseProfile(**cj), _prof(fatigue_tilt=tilt), start_hour=6, plan_moving_s=total_s)
    secs = rs.compute_passage_times(course, cps, total_s, 1.0, 6, 0, None)
    return [{"name": s["end_name"], "km": s["end_km"], "time_s": int(s["adjusted_cumulative_time_s"])} for s in secs[:-1]]


def test_tilt_fitted_on_all_checkpoints_recovers_the_runner_shape():
    cj = _hilly()
    cps = [{"name": f"CP{k}", "distance_km": float(k)} for k in (10, 20, 30, 40, 50)]
    total = 8 * 3600
    actual = _passages(cj, cps, 0.32, total)  # a runner who fades more than the field
    res = rc.measure_fatigue_tilt(cj, _prof(), cps, actual, total, 6, 0)
    assert res["raw"] == pytest.approx(0.32, abs=0.011) and res["n"] == 5
    # 5 checkpoints: 5/9 of the way from the neutral tilt
    assert res["tilt"] == pytest.approx(rs.DEFAULT_FATIGUE_TILT + 5 / 9 * (res["raw"] - rs.DEFAULT_FATIGUE_TILT), abs=1e-3)
    # one who fades less
    res = rc.measure_fatigue_tilt(cj, _prof(), cps, _passages(cj, cps, 0.12, total), total, 6, 0)
    assert res["raw"] == pytest.approx(0.12, abs=0.011) and res["tilt"] < rs.DEFAULT_FATIGUE_TILT


def test_tilt_with_one_checkpoint_moves_little_and_none_without():
    cj = _hilly()
    cps = [{"name": "CP", "distance_km": 30.0}]
    total = 8 * 3600
    res = rc.measure_fatigue_tilt(cj, _prof(), cps, _passages(cj, cps, 0.40, total), total, 6, 0)
    assert res["n"] == 1 and abs(res["tilt"] - rs.DEFAULT_FATIGUE_TILT) <= 0.2 * (0.40 - rs.DEFAULT_FATIGUE_TILT) + 1e-3
    assert rc.measure_fatigue_tilt(cj, _prof(), cps, [], total, 6, 0) is None
    # a checkpoint at the finish says nothing: the plan is pinned there
    assert rc.measure_fatigue_tilt(cj, _prof(), [{"name": "F", "distance_km": 59.5}],
                                   [{"km": 59.5, "time_s": total - 60}], total, 6, 0) is None


def test_tilt_ignores_the_race_level_and_a_missing_effort_dplus():
    cj = _hilly()
    cps = [{"name": f"CP{k}", "distance_km": float(k)} for k in (10, 20, 30, 40, 50)]
    total = 8 * 3600
    actual = _passages(cj, cps, 0.30, total)
    plain = rc.measure_fatigue_tilt(cj, _prof(), cps, actual, total, 6, 0)
    d = date(2026, 1, 1)
    m = rc.model_from_points([rc.race_point("A", 50, 3000, 7, True, day=d), rc.race_point("B", 100, 6000, 17, True, day=d)])
    leveled = rc.measure_fatigue_tilt({**cj, "dplus_effort": None}, _prof(race_model=m), cps, actual, total, 6, 0)
    assert leveled == plain
    assert cj.get("dplus_effort") is None  # the stored course is not touched


def test_tilt_is_clamped():
    cj = _hilly()
    cps = [{"name": f"CP{k}", "distance_km": float(k)} for k in range(5, 60, 5)]
    total = 8 * 3600
    real = _passages(cj, cps, 0.22, total)
    # far slower than any tilt allows early on, then catching up: the low clamp
    actual = [{**a, "time_s": int(a["time_s"] * (1.25 if a["km"] < 30 else 1.0))} for a in real]
    res = rc.measure_fatigue_tilt(cj, _prof(), cps, actual, total, 6, 0)
    assert res["raw"] == rc.TILT_MIN and rc.TILT_MIN <= res["tilt"] < rs.DEFAULT_FATIGUE_TILT


@pytest.mark.asyncio
async def test_personal_tilt_is_the_median_of_the_three_latest_races(db_session, test_user):
    def route(name, tilt, day, model=rs.FATIGUE_MODEL):
        return Route(user_id=test_user.id, name=name, total_distance_km=100,
                     result_json={"fatigue_tilt": tilt, "fatigue_model": model, "activity_date": day})
    # matched in a different order than raced: the race date decides
    db_session.add_all([
        route("2026", 0.10, "02/10/2026"),
        route("2023", 0.40, "01/06/2023"),          # 4th most recent: out
        route("2025b", 0.30, "25/08/2025"),
        route("old curve", 0.05, "03/10/2026", model=None),  # measured on another curve: out
        route("2025a", 0.20, "05/12/2025"),
    ])
    await db_session.flush()
    assert await rs._personal_fatigue_tilt(db_session, test_user.id) == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_personal_tilt_prefers_the_tilts_fitted_on_every_checkpoint(db_session, test_user):
    def route(name, tilt, day, cps=None):
        rj = {"fatigue_tilt": tilt, "fatigue_model": rs.FATIGUE_MODEL, "activity_date": day}
        if cps is not None:
            rj["fatigue_tilt_cps"] = cps
        return Route(user_id=test_user.id, name=name, total_distance_km=100, result_json=rj)
    db_session.add_all([
        route("first CP 2026", 0.30, "02/10/2026"),    # measured on the first checkpoint only
        route("first CP 2025b", 0.35, "25/08/2025"),
        route("all CPs 2025a", 0.12, "05/12/2025", cps=9),
    ])
    await db_session.flush()
    assert await rs._personal_fatigue_tilt(db_session, test_user.id) == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_personal_tilt_single_race_and_clamp(db_session, test_user):
    db_session.add(Route(user_id=test_user.id, name="r", total_distance_km=100,
                         result_json={"fatigue_tilt": 0.9, "fatigue_model": rs.FATIGUE_MODEL, "activity_date": ""}))
    await db_session.flush()
    assert await rs._personal_fatigue_tilt(db_session, test_user.id) == 0.40


# ── migration: the effort D+ stored on the courses saved before ──

def test_migration_stores_the_effort_dplus_on_old_courses():
    import importlib.util
    import pathlib

    import sqlalchemy as sa

    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "v6e7f8a9b0c1_course_dplus_effort.py"
    spec = importlib.util.spec_from_file_location("mig_dplus", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "u5d6e7f8a9b0"

    old = _course_without_effort_dplus().model_dump()
    flat = _course_without_effort_dplus(with_ele=False).model_dump()
    new = {**old, "dplus_effort": 777.0}
    eng = sa.create_engine("sqlite://")
    routes = sa.table("routes", sa.column("id", sa.Integer), sa.column("course_json", sa.JSON))
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE routes (id INTEGER PRIMARY KEY, course_json JSON)"))
        for i, cj in enumerate([old, flat, new, None], start=1):
            c.execute(routes.insert().values(id=i, course_json=cj))
        assert mig.apply(c) == 1
        got = dict(c.execute(sa.select(routes.c.id, routes.c.course_json)).all())
        assert got[1]["dplus_effort"] == pytest.approx(1000, rel=0.03)
        assert got[1]["route_coords"] == old["route_coords"]  # the rest kept
        assert got[2].get("dplus_effort") is None  # no elevation on the trace: the displayed total applies
        assert got[3]["dplus_effort"] == 777.0 and got[4] is None
        assert mig.apply(c) == 0  # idempotent
