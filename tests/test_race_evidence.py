"""The race formulas set on evidence (2026-10-14): the finish-time level (population
exponent 1.28, shrunk toward the athlete's own), honest Optimiste / Sécurité
(20th / 80th percentiles), the stop model, the heat in the plan and out of past
races, night by the sun, the pin floor, the tilt over races, the steep-split
pace cap, the cycling air density and drivetrain, the race-week weight, and the
back-end fixes of the race side (weather endpoint, save order, « Mes courses »)."""

import json
import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.user import User
from app.schemas.simulator import AthleteGradientProfile, GpxPoint
from app.services import race_calibration as rc
from app.services import race_simulator as rs
from tests.test_race_plan_services import CPS, _course


def _pt(name, ekm, hours, day=None, **kw):
    """A race of ``ekm`` effort-km (all distance) in ``hours``."""
    return rc.race_point(name, ekm, 0, hours, True, day=day, **kw)


# ── 1. the level: ln T = c + k ln E, k shrunk toward the population's 1.28 ──

def test_one_race_takes_the_population_exponent():
    m = rc.fit_effort_model([_pt("A", 100, 12.0)])
    assert m["k"] == rc.K_PRIOR == 1.28 and m["b"] == pytest.approx(1 - 1 / 1.28) and not m["fitted"]
    # T = 12 h × (E / 100)^1.28: a 160 effort-km race 1.83 × longer (the former b = 0.08 said 1.67 ×)
    assert rc.predict_total_s(m, 160) / 3600 == pytest.approx(12 * 1.6 ** 1.28, rel=1e-3)
    assert rc.DEFAULT_DECAY == 0.22 and (rc.DECAY_MIN, rc.DECAY_MAX) == (0.10, 0.35)


def test_races_of_different_lengths_move_the_exponent_toward_the_athlete():
    # an athlete whose own law is T ∝ E^1.10, read without noise on races of 40 to 160 effort-km
    races = [_pt(f"R{E}", E, 4.0 * (E / 40) ** 1.10) for E in (40, 80, 160)]
    k = rc.fit_effort_model(races)["k"]
    assert 1.10 < k < 1.28
    # k̂ = (Sxy/σ² + k0/τ²) / (Sxx/σ² + 1/τ²)
    xs = [math.log(p["ekm"]) for p in races]
    mx = sum(xs) / 3
    sxx = sum((x - mx) ** 2 for x in xs)
    expected = (1.10 * sxx / 0.01 + 1.28 / 0.0064) / (sxx / 0.01 + 1 / 0.0064)
    assert k == pytest.approx(expected, abs=2e-3)
    # more spread, more of his own: 20 to 320 effort-km
    wide = [_pt(f"W{E}", E, 2.0 * (E / 20) ** 1.10) for E in (20, 80, 320)]
    assert rc.fit_effort_model(wide)["k"] < k
    # bounded: an exponent of 2 is read as b = 0.35 at most
    steep = [_pt(f"S{E}", E, (E / 20) ** 2.0) for E in (20, 80, 320)]
    assert rc.fit_effort_model(steep)["k"] == pytest.approx(1 / (1 - rc.DECAY_MAX))


def test_the_level_is_the_mean_of_the_races_not_the_best():
    # the same effort-km in 10 h and 12 h, equal weights: the level between them, not the fastest
    m = rc.model_from_points([_pt("A", 100, 10.0), _pt("B", 100, 12.0)])
    assert rc.predict_total_s(m, 100) / 3600 == pytest.approx(math.sqrt(10 * 12), rel=1e-3)
    assert {p["name"] for p in m["races"]} == {"A", "B"} and m["n_eff"] == pytest.approx(2)


@pytest.mark.parametrize("slower", [0.10, 0.20, 0.30, 0.40])
def test_one_race_beats_long_training_runs(slower):
    """One tagged race and long untagged runs 10-40 % slower than its curve: the level is the race's alone (the
    runs pulled it 5-40 % slow, and from 40 % slower set the race itself aside)."""
    today = date(2026, 10, 14)
    race = rc.race_point("Trail des Cimes", 60, 3000, 8.0, True, day=today - timedelta(days=60))
    runs = [rc.race_point(f"Sortie longue {i}", km, dp, 8.0 * ((km + dp / 100) / 90) ** 1.28 * (1 + slower), False,
                          day=today - timedelta(days=15 * i + 10))
            for i, (km, dp) in enumerate(((42, 1200), (45, 1500), (48, 1800), (50, 2000)))]
    alone = rc.model_from_points([race], today=today)
    m = rc.model_from_points([race, *runs], today=today)
    E = rc.effort_km(100, 6000)
    assert rc.predict_total_s(m, E) == rc.predict_total_s(alone, E)
    assert [p["name"] for p in m["races"]] == ["Trail des Cimes"] and m["n_eff"] == 1


def test_training_runs_alone_count_as_no_race_in_the_spread():
    today = date(2026, 10, 14)
    runs = [rc.race_point(f"Sortie longue {i}", 42 + 2 * i, 1500, 6.5 + 0.3 * i, False, day=today - timedelta(days=20 * i + 10))
            for i in range(4)]
    m = rc.model_from_points(runs, today=today)
    assert m and m["n_used"] == 4 and m["n_eff"] == 0  # the level read on them, its spread as without a race
    E = rc.effort_km(100, 6000)
    assert rc.scenario_pcts(m, E) == rc.scenario_pcts(None, E)


def test_a_hot_race_is_read_without_its_heat():
    cool = rc.model_from_points([_pt("Cool", 100, 11.0)])
    hot = rc.model_from_points([_pt("Hot", 100, 11.0, heat=1.10)])  # run at ~30 °C: 10 % of its time was heat
    assert rc.fit_hours(hot["races"][0]) == pytest.approx(10.0)
    assert rc.predict_total_s(hot, 100) == pytest.approx(rc.predict_total_s(cool, 100) / 1.10, rel=1e-3)
    assert hot["races"][0]["hours"] == 11.0  # what the athlete sees is his real time


def test_a_cool_damp_hour_is_not_heat():
    from app.services.weather import compute_heat_factor

    assert compute_heat_factor(10.0, 90.0) == 1.0 and compute_heat_factor(14.9, 100.0) == 1.0  # was 1.025, 1.035
    # phased in from 15 to 20 °C: no step at 15 °C (14.9 °C / 95 % 1.000, 15.0 °C 1.030 before), full from 20 °C
    assert compute_heat_factor(15.0, 95.0) == 1.0
    assert compute_heat_factor(17.5, 95.0) == pytest.approx(1.0 + 2.5 * 0.004 + 0.030 * 0.5)
    assert compute_heat_factor(20.0, 85.0) == pytest.approx(1.04) and compute_heat_factor(30.0, 90.0) == pytest.approx(1.145)


def test_mean_heat_over_the_race_hours():
    from app.services.weather import compute_heat_factor

    start = datetime(2026, 7, 4, 6, 30, tzinfo=timezone.utc)
    series = {f"2026-07-04T{h:02d}:00": (10.0 + 2 * h, 50.0) for h in range(24)}
    h, t = rc.mean_heat(series, start, 3.2)  # 06:00 → 10:00, five hours read
    expected = [compute_heat_factor(10.0 + 2 * hh, 50.0) for hh in range(6, 11)]
    assert h == pytest.approx(sum(expected) / 5) and t == pytest.approx(sum(10 + 2 * hh for hh in range(6, 11)) / 5)
    assert rc.mean_heat({}, start, 3) is None


@pytest.mark.asyncio
async def test_the_races_heat_is_looked_up_once_and_read_by_the_model(db_session, test_user, monkeypatch):
    from app.config import settings
    from app.services import weather

    monkeypatch.setattr(settings, "ACTIVITY_ENV_FETCH", True)
    calls = []

    async def fake(lat, lon, first, last, today):
        calls.append((round(lat, 2), first.isoformat()))
        return {f"{first.isoformat()}T{h:02d}:00": (31.0, 40.0) for h in range(24)}, 500.0  # values for 500 m

    monkeypatch.setattr(weather, "temperature_humidity", fake)
    now = datetime.now(timezone.utc)
    race = Activity(strava_activity_id=91001, user_id=test_user.id, sport_type="TrailRun", name="Ultra chaud",
                    start_date=(now - timedelta(days=40)).replace(hour=5, minute=0), distance=60000.0, moving_time=8 * 3600,
                    elapsed_time=8 * 3600 + 900, total_elevation_gain=2000.0,
                    raw_data={"workout_type": 1, "start_latlng": [43.3, 5.4], "elev_low": 300.0, "elev_high": 700.0})
    high = Activity(strava_activity_id=91003, user_id=test_user.id, sport_type="TrailRun", name="Ultra en altitude",
                    start_date=(now - timedelta(days=50)).replace(hour=5, minute=0), distance=60000.0, moving_time=9 * 3600,
                    elapsed_time=9 * 3600, total_elevation_gain=3000.0,
                    raw_data={"workout_type": 1, "start_latlng": [45.9, 6.9], "elev_low": 1000.0, "elev_high": 3000.0})
    nowhere = Activity(strava_activity_id=91002, user_id=test_user.id, sport_type="TrailRun", name="Sans GPS",
                       start_date=now - timedelta(days=60), distance=50000.0, moving_time=7 * 3600, elapsed_time=7 * 3600,
                       total_elevation_gain=1500.0, raw_data={"workout_type": 1})
    db_session.add_all([race, high, nowhere])
    await db_session.flush()
    assert await rc.backfill_race_heat(db_session, test_user.id) == 3
    assert len(calls) == 2  # the race without a position is not asked, only marked
    assert race.raw_data[rc.HEAT_CACHE_KEY]["h"] == pytest.approx(weather.compute_heat_factor(31.0, 40.0), abs=1e-3)
    # the race's own altitude, as the course's forecast: 1 500 m above the values' 500 m, 9.75 °C cooler
    assert high.raw_data[rc.HEAT_CACHE_KEY]["dz"] == 1500 and high.raw_data[rc.HEAT_CACHE_KEY]["t"] == pytest.approx(21.2, abs=0.1)
    assert high.raw_data[rc.HEAT_CACHE_KEY]["h"] == pytest.approx(weather.compute_heat_factor(21.25, 40.0), abs=1e-3)
    assert nowhere.raw_data[rc.HEAT_CACHE_KEY] == {"v": rc.HEAT_CACHE_VERSION, "h": None, "src": "none"}
    assert await rc.backfill_race_heat(db_session, test_user.id) == 0 and len(calls) == 2  # never asked again
    model = await rc.build_race_effort_model(db_session, test_user.id)
    hot = next(p for p in model["races"] if p["name"] == "Ultra chaud")
    assert hot["heat"] > 1.1 and rc.fit_hours(hot) < hot["hours"]
    # off in the tests and the CI (no Open-Meteo call)
    monkeypatch.setattr(settings, "ACTIVITY_ENV_FETCH", False)
    assert await rc.backfill_race_heat(db_session, test_user.id) == 0


@pytest.mark.asyncio
async def test_an_open_meteo_outage_stops_the_heat_lookups_for_every_worker(db_session, test_user, monkeypatch):
    from app.config import settings
    from app.services import weather

    # Redis unreachable (a closed port, never a real server): nothing shared, nothing paused, never an error
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")
    assert await weather.RedisMarker().is_set() is False
    await weather.RedisMarker().set()
    # the shared marker (in memory here, conftest): a failure pauses every pass for OUTAGE_S
    monkeypatch.setattr(settings, "ACTIVITY_ENV_FETCH", True)
    assert await weather.lookups_paused() is False
    calls = []

    async def down(lat, lon, first, last, today):
        calls.append(lat)
        raise weather.LookupUnavailable("HTTP 503")

    monkeypatch.setattr(weather, "temperature_humidity", down)
    now = datetime.now(timezone.utc)
    db_session.add(Activity(strava_activity_id=93001, user_id=test_user.id, sport_type="TrailRun", name="Ultra",
                            start_date=now - timedelta(days=30), distance=60000.0, moving_time=8 * 3600,
                            elapsed_time=8 * 3600, total_elevation_gain=2000.0,
                            raw_data={"workout_type": 1, "start_latlng": [43.3, 5.4]}))
    await db_session.flush()
    assert await rc.backfill_race_heat(db_session, test_user.id) == 0 and len(calls) == 1  # failed: marker set
    assert await weather.lookups_paused() is True
    assert await rc.backfill_race_heat(db_session, test_user.id) == 0 and len(calls) == 1  # every worker waits


@pytest.mark.asyncio
async def test_no_race_to_read_no_marker_asked(db_session, test_user, monkeypatch):
    """The heat pass runs for every athlete at every poll: with no race left to read it does not ask the shared
    marker (one Redis connection per athlete and poll)."""
    from app.config import settings
    from app.services import weather

    monkeypatch.setattr(settings, "ACTIVITY_ENV_FETCH", True)
    asked = []

    async def paused():
        asked.append(1)
        return False

    monkeypatch.setattr(weather, "lookups_paused", paused)
    assert await rc.backfill_race_heat(db_session, test_user.id) == 0 and not asked
    now = datetime.now(timezone.utc)
    db_session.add(Activity(strava_activity_id=93002, user_id=test_user.id, sport_type="TrailRun", name="Ultra",
                            start_date=now - timedelta(days=30), distance=60000.0, moving_time=8 * 3600,
                            elapsed_time=8 * 3600, total_elevation_gain=2000.0, raw_data={"workout_type": 1}))
    await db_session.flush()
    await rc.backfill_race_heat(db_session, test_user.id)
    assert asked  # a race to read: the marker first


def test_the_stops_are_the_stop_models_for_everyone():
    """No personal stop factor: his Strava races' elapsed − moving is not what was checked (stations timed in and
    out), and one race with a forgotten pause pushed it to its cap."""
    m = rc.model_from_points([_pt("A", 100, 10.0), _pt("B", 160, 18.0)])
    assert "stop_factor" not in m and not hasattr(rc, "stop_factor")
    from app.routers.simulator import _aid_stops

    cps = [dict(cp) for cp in CPS]
    assert _aid_stops(cps, None, 5 * 3600, 30.0) == {
        round(cp["distance_km"], 1): rs.default_stop_s(cp["kind"], 5 * 3600, cp["distance_km"] / 30.0) for cp in cps}


@pytest.mark.parametrize("name", ["Shikoku Trail 100", "Walker Ridge 50K", "Treklanders 60K",
                                  "Trail des Marches de Bretagne", "Recoleta Trail", "Hikers Peak Ultra"])
def test_names_are_read_as_whole_words(name):
    assert rc.not_a_race_reason(name) is None


@pytest.mark.parametrize("name", ["Hike to the lake", "Randonnée du Vercors", "Marche nordique", "Nordic walking",
                                  "Trekking Annapurna", "Balade en famille", "Reco CCC", "Reconnaissance TDS",
                                  "Recce UTMB", "Pacer for Anna", "Accompagnement d'un ami"])
def test_hikes_walks_and_recces_are_not_races(name):
    assert rc.not_a_race_reason(name) == "pas une course"


# ── 2. Optimiste / Sécurité: the 20th / 80th percentiles of the finish time ──

def test_the_finish_time_spread():
    base = 0.07 ** 2 + 0.044 ** 2 + 0.07 ** 2
    assert rc.finish_spread({"n": 1, "n_eff": 1}, None) == pytest.approx(math.sqrt(base + 0.01))
    assert rc.finish_spread({"n": 3, "n_eff": 3}, None) == pytest.approx(math.sqrt(base + 0.01 / 3))
    # a course he raced before: its own difficulty is known better
    known = rc.finish_spread({"n": 3, "n_eff": 3}, None, known_course=True)
    assert known == pytest.approx(math.sqrt(0.07 ** 2 + 0.044 ** 2 + 0.04 ** 2 + 0.01 / 3))
    # far from his races: the exponent's spread between athletes
    m = {"n": 3, "n_eff": 3, "log_ekm_mean": math.log(100)}
    assert rc.finish_spread(m, 250) == pytest.approx(math.sqrt(base + 0.01 / 3 + (0.08 * math.log(2.5)) ** 2))
    # no race at all: the level from training (H)
    assert rc.finish_spread(None, 100) == pytest.approx(math.sqrt(base + 0.15 ** 2))
    # e^(∓0.84 σ) − 1
    assert rc.scenario_pcts({"n": 3, "n_eff": 3}, None) == (9.8, 10.9)
    fast, safe = rc.scenario_pcts(None, 100)
    assert fast > 12 and safe > 15


def test_scenarios_switch_at_five_percent_and_scale_the_elapsed_time():
    sections = rs.compute_passage_times(_course(), CPS, 5 * 3600, 1.0, 6, 0, None, aid_stops={6.0: 120, 10.0: 300})
    sc = rs.build_scenarios(sections, 6 * 3600, 5 * 3600, fast_pct=9.8, safe_pct=10.9)
    sw = sc["switch"]
    assert sw["km"] == 20.0 and sc["switch_late_pct"] == 5
    assert sc["switch_late_s"] == round((sw["target_s"] - 6 * 3600) * 0.05 / 60) * 60  # 5 % of the plan there, to the minute
    # the percentiles were read on finish times, stops included: they scale the elapsed time at every point
    for r in sc["rows"]:
        elapsed = r["target_s"] - 6 * 3600
        assert abs(r["fast_s"] - 6 * 3600 - elapsed * (1 - 0.098)) <= 1 and abs(r["safe_s"] - 6 * 3600 - elapsed * 1.109) <= 1
        assert "safe_extra_s" not in r
    assert sc["totals"]["target_s"] == 5 * 3600 and abs(sc["totals"]["safe_s"] - 5 * 3600 * 1.109) <= 1  # its P80


def test_scenario_settings_his_own_values_win_the_former_defaults_are_automatic():
    from app.routers.simulator import _apply_scenarios, _scenario_settings

    course = _course()
    auto = _scenario_settings({}, None, course)
    assert (auto["fast_pct"], auto["safe_pct"]) == rc.scenario_pcts(None, rc.effort_km(30.0, rc.course_dplus(course)))
    assert auto["fast_own"] is None and auto["safe_own"] is None
    own = _scenario_settings({"scenario_fast_pct": 4.0, "scenario_safe_pct": 25.0}, None, course)
    assert (own["fast_pct"], own["safe_pct"], own["fast_own"]) == (4.0, 25.0, 4.0)
    legacy = _scenario_settings({"scenario_fast_pct": 5.0, "scenario_safe_pct": 10.0}, None, course)
    assert (legacy["fast_pct"], legacy["safe_pct"]) == (auto["fast_pct"], auto["safe_pct"])
    p = {"scenario_fast_pct": 7.0}
    _apply_scenarios({"scenario_fast_pct": "", "scenario_safe_pct": "20", "switch_km": ""}, p, type("R", (), {"total_distance_km": 30})())
    assert "scenario_fast_pct" not in p and p["scenario_safe_pct"] == 20.0  # empty = automatique
    # saved by today's form (its hidden version field), 5 / 10 are his own choice
    p, r30 = {}, type("R", (), {"total_distance_km": 30})()
    _apply_scenarios({"scenario_fast_pct": "5", "scenario_safe_pct": "10", "switch_km": "", "scenario_v": "2"}, p, r30)
    chosen = _scenario_settings(p, None, course)
    assert (chosen["fast_pct"], chosen["safe_pct"], chosen["fast_own"], chosen["safe_own"]) == (5.0, 10.0, 5.0, 10.0)
    # a page opened before the release posts its former 5 / 10 without it: still « automatique », the mark dropped
    _apply_scenarios({"scenario_fast_pct": "5", "scenario_safe_pct": "10", "switch_km": ""}, p, r30)
    old_tab = _scenario_settings(p, None, course)
    assert "scenario_v" not in p and (old_tab["fast_own"], old_tab["safe_own"]) == (None, None)


@pytest.mark.asyncio
async def test_a_course_is_known_once_he_has_raced_it(db_session: AsyncSession, test_user: User):
    from app.models.route import Route
    from app.routers.simulator import _known_course

    raced = Route(user_id=test_user.id, name="Ultra 2025", total_distance_km=80.0, total_elevation_gain=4000.0,
                  result_json={"activity_id": 1})
    new = Route(user_id=test_user.id, name="Ultra 2026", total_distance_km=81.5, total_elevation_gain=4200.0)
    other = Route(user_id=test_user.id, name="Autre", total_distance_km=60.0, total_elevation_gain=4000.0)
    db_session.add_all([raced, new, other])
    await db_session.flush()
    assert await _known_course(db_session, test_user.id, new)  # within 3 % of its km and 10 % of its D+
    assert not await _known_course(db_session, test_user.id, other)
    raced.result_json = None  # « Ce n'est pas la bonne activité »: the result unlinked, stored as JSON null
    await db_session.flush()
    assert not await _known_course(db_session, test_user.id, new)


# ── 3. stops: the median stop at the runner's finish ──

def test_the_stop_model():
    # a water point mid-race for an 18 h finisher: median 3.9 min (the fit), the planned stop
    med = rs.stop_median_s("water", 18 * 3600, 0.5)
    assert med / 60 == pytest.approx(math.exp(1.149 + 0.441 * 0.5), rel=1e-6) and 3.8 < med / 60 < 4.0
    assert rs.default_stop_s("water", 18 * 3600, 0.5) == round(med)
    # longer for a slower runner, later in the race, at a bigger station
    assert rs.default_stop_s("full", 30 * 3600, 0.5) > rs.default_stop_s("full", 10 * 3600, 0.5)
    assert rs.default_stop_s("full", 18 * 3600, 0.9) > rs.default_stop_s("full", 18 * 3600, 0.1)
    assert rs.default_stop_s("base", 18 * 3600, 0.5) > rs.default_stop_s("full", 18 * 3600, 0.5) > rs.default_stop_s("water", 18 * 3600, 0.5)
    assert rs.default_stop_s("none", 18 * 3600, 0.5) == 0


def test_the_stop_he_sets_always_wins():
    from app.routers.simulator import _aid_stops

    cps = [dict(CPS[0], stop_s=0), dict(CPS[1], stop_s=420), CPS[2]]
    stops = _aid_stops(cps, None, 20 * 3600, 30.0)
    assert 6.0 not in stops and stops[10.0] == 420 and stops[20.0] == rs.default_stop_s("full", 20 * 3600, 20 / 30)
    assert _aid_stops(cps, 4, 20 * 3600, 30.0) == {10.0: 420, 20.0: 240}  # one value everywhere, the point's own first


# ── 4. heat: in the objective plan as in the prediction ──

def test_the_objective_plan_gives_a_hot_section_its_heat():
    pts = [GpxPoint(lat=45.0 + i * 1e-4, lon=6.0, elevation=500.0, distance_from_start=i * 10.0) for i in range(6001)]
    from app.services.gpx import build_course_profile

    course = build_course_profile(pts)
    prof = AthleteGradientProfile(flat_pace_s_per_km=330, gradient_factors=dict(rs._DEFAULT_GRADIENT_FACTORS), data_points=0,
                                  sport_types_used=[])
    course = rs.predict_course(course, prof, start_hour=6, plan_moving_s=6 * 3600)
    temps = [10.0] * 11 + [32.0] * 13  # 32 °C from 11:00
    cps = [{"name": f"CP{k}", "distance_km": float(k)} for k in range(10, 60, 10)]
    secs = rs.compute_passage_times(course, cps, 6 * 3600, 1.0, 6, 0, {"temps": temps, "humidity": [40] * 24})
    assert secs[-1]["heat_factor"] > 1.1 and secs[0]["heat_factor"] == 1.0
    ratios = [s["adjusted_time_s"] / s["predicted_time_s"] for s in secs]
    assert max(ratios) - min(ratios) < 0.01  # the hot last section no longer 10 % short of its prediction


@pytest.mark.asyncio
async def test_a_course_without_weather_is_read_at_the_heat_his_races_were_run_in(db_session, test_user):
    """The level is read without the races' heat; a course with no weather (no date, nothing fetched) takes back
    the heat they were run in — the estimate, the page's table and the printed plan alike — so it stays where it
    was before (not 1-4 % faster). With its own weather the level stays heat-free: race day's heat comes from it."""
    from app.models.route import Route
    from app.routers import simulator as sim
    from app.schemas.simulator import CourseProfile

    m = rc.model_from_points([_pt("A", 100, 10.0, heat=1.04), _pt("B", 160, 18.0, heat=1.02)])
    assert m["heat_mean"] == pytest.approx(1.03)
    assert rc.model_from_points([_pt("A", 100, 10.0)])["heat_mean"] == 1.0
    prof = AthleteGradientProfile(flat_pace_s_per_km=330, gradient_factors=dict(rs._DEFAULT_GRADIENT_FACTORS), data_points=0,
                                  sport_types_used=[], race_model=m)
    dry = rs.predict_course(_course(), prof.model_copy()).predicted_total_time_s
    wet = rs.predict_course(_course(), prof.model_copy(), own_weather=True).predicted_total_time_s
    assert dry == pytest.approx(wet * 1.03, rel=0.002)
    # training alone (no race model): no heat of his races to take back
    bare = prof.model_copy(update={"race_model": None})
    plain = rs.predict_course(_course(), bare).predicted_total_time_s
    assert plain == rs.predict_course(_course(), bare, own_weather=True).predicted_total_time_s

    route = Route(user_id=test_user.id, name="Sans météo", total_distance_km=30.0, total_elevation_gain=800.0,
                  course_json=json.loads(_course().model_dump_json()), sport_type="trail")
    db_session.add(route)
    await db_session.flush()

    async def table():
        ctx = await sim._passage_table_context(db_session, test_user.id, CourseProfile(**route.course_json), [], None,
                                               1.0, 6, 0, None, route, None, None, None, profile=prof.model_copy())
        return ctx["predicted_total"]

    assert (await sim._plan_bundle(route, db_session, test_user, profile=prof.model_copy()))["predicted_total_s"] == dry
    assert await table() == dry
    route.weather_json = {"heat_factor": 1.0, "hourly": {"temps": [10.0] * 48, "humidity": [50] * 48}}
    assert (await sim._plan_bundle(route, db_session, test_user, profile=prof.model_copy()))["predicted_total_s"] == wet
    assert await table() == wet


# ── 5. night by the sun ──

def test_the_suns_elevation():
    # Chamonix at the June solstice: ~67° at solar noon (11:30 UTC), deep under the horizon at midnight
    assert rs.sun_elevation_deg(45.92, 6.87, datetime(2026, 6, 21, 11, 30, tzinfo=timezone.utc)) == pytest.approx(67.5, abs=1.0)
    assert rs.sun_elevation_deg(45.92, 6.87, datetime(2026, 6, 21, 23, 30, tzinfo=timezone.utc)) < -15
    assert rs.sun_night_factor(45.92, 6.87, datetime(2026, 6, 21, 23, 30, tzinfo=timezone.utc)) == rs.NIGHT_FACTOR
    # 19:30 UTC (21:30 CEST): the sun just set, twilight
    assert rs.sun_night_factor(45.92, 6.87, datetime(2026, 6, 21, 19, 30, tzinfo=timezone.utc)) == rs.DUSK_DAWN_FACTOR
    assert rs.sun_night_factor(45.92, 6.87, datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)) == 1.0


def test_the_races_clock():
    assert rs.race_utc_offset_s("2026-08-28", 6.9, weather_offset_s=7200) == 7200  # the forecast's time zone
    assert rs.race_utc_offset_s("2026-08-28", 6.9, zone=ZoneInfo("Europe/Paris")) == 7200  # his zone fits the course
    assert rs.race_utc_offset_s("2026-12-20", 6.9, zone=ZoneInfo("Europe/Paris")) == 3600
    # a race abroad: his zone 8 h off the course's longitude, the longitude decides (Jeju, 126.5° E → +8 h)
    assert rs.race_utc_offset_s("2026-10-02", 126.5, zone=ZoneInfo("Europe/Paris")) == 8 * 3600
    assert rs.race_utc_offset_s(None, 6.9) is None and rs.race_utc_offset_s("2026-10-02", None) is None
    # the start's own zone wins, daylight saving read on the race date at the start's local time
    paris = ZoneInfo("Europe/Paris")
    assert rs.race_utc_offset_s("2026-04-25", -16.9, None, paris, start_zone="Atlantic/Madeira") == 3600  # not lon / 15
    assert rs.race_utc_offset_s("2027-02-13", 176.2, None, paris, start_zone="Pacific/Auckland") == 13 * 3600
    assert rs.race_utc_offset_s("2026-10-25", 2.35, None, paris, start_zone="Europe/Paris", start_hour=1) == 7200
    assert rs.race_utc_offset_s("2026-10-25", 2.35, None, paris, start_zone="Europe/Paris", start_hour=8) == 3600
    # without it, his zone only when the course lies in its band (standard offset within 2 h of lon / 15)
    assert rs.race_utc_offset_s("2026-10-15", 55.45, None, paris) == 4 * 3600  # Réunion: not Paris's +2
    for lon in (-0.58, -1.56, -4.5, -4.8):  # Bordeaux, Biarritz, Crozon, the Picos: Paris's +2 in summer, not UTC
        assert rs.race_utc_offset_s("2026-06-13", lon, None, paris, start_hour=7) == 2 * 3600


@pytest.mark.asyncio
async def test_the_starts_time_zone_comes_with_the_weather_never_from_a_page(db_session, test_user, monkeypatch):
    """The start's own zone is asked with the weather (the page's POST, on a date set or a stale forecast), only
    when no forecast gives the offset, once a day per place (an answer or its absence); the race clock a page
    renders reads it from the saved weather: no lookup, nothing written."""
    from app.config import settings
    from app.models.route import Route
    from app.services import race_match, weather

    calls = []

    async def zone(lat, lon):
        calls.append((lat, lon))
        return "Atlantic/Madeira"

    monkeypatch.setattr(weather, "zone_name", zone)
    monkeypatch.setattr(weather, "_ZONES", {})

    async def normals(lat, lon, race_date):
        return {"source": "moyennes climatiques", "date": race_date.isoformat(), "heat_factor": 1.0,
                "hourly": {"temps": [15.0] * 24, "humidity": [60] * 24}}

    async def forecast(lat, lon, date, points=None):
        return {"source": "prevision", "date": date, "heat_factor": 1.0, "utc_offset_s": 3600, "tz": "Atlantic/Madeira",
                "hourly": {"temps": [15.0] * 48, "humidity": [60] * 48}}

    monkeypatch.setattr(weather, "_fetch_climate", normals)
    monkeypatch.setattr(weather, "_fetch_forecast", forecast)
    far = (date.today() + timedelta(days=60)).isoformat()
    monkeypatch.setattr(settings, "ACTIVITY_ENV_FETCH", False)  # off (tests, CI): no lookup
    assert "tz" in (w := await weather.get_weather_forecast(32.86, -16.9, far)) and w["tz"] is None and not calls
    monkeypatch.setattr(settings, "ACTIVITY_ENV_FETCH", True)
    for _ in range(2):  # asked once a day per place
        assert (await weather.get_weather_forecast(32.86, -16.9, far))["tz"] == "Atlantic/Madeira"
    soon = (date.today() + timedelta(days=3)).isoformat()
    assert (await weather.get_weather_forecast(32.86, -16.9, soon))["tz"] == "Atlantic/Madeira"  # the forecast's own
    assert calls == [(32.86, -16.9)]

    async def down(lat, lon):
        calls.append((lat, lon))
        raise weather.LookupUnavailable("HTTP 503")

    monkeypatch.setattr(weather, "zone_name", down)
    for _ in range(2):  # a failure: none for now, the outage marker set, asked again within 15 min, not a day
        assert (await weather.get_weather_forecast(45.92, 6.87, far))["tz"] is None
    assert calls == [(32.86, -16.9), (45.92, 6.87)] and await weather.lookups_paused()
    import time as _time
    left = {k: until - _time.monotonic() for k, (until, _) in weather._ZONES.items()}
    assert left[(45.92, 6.87)] <= weather.ZONE_RETRY_S and left[(32.86, -16.9)] > 23 * 3600

    async def nowhere(lat, lon):  # an answer without a zone: kept a day
        calls.append((lat, lon))
        return None

    monkeypatch.setattr(weather, "zone_name", nowhere)
    monkeypatch.setattr(weather, "_outage", weather.MemoryMarker())
    assert (await weather.get_weather_forecast(0.0, -160.0, far))["tz"] is None
    assert weather._ZONES[(0.0, -160.0)][0] - _time.monotonic() > 23 * 3600

    # the page's race clock reads the saved weather: no lookup, nothing written
    async def never(lat, lon):
        raise AssertionError("no lookup while a page renders")

    monkeypatch.setattr(weather, "zone_name", never)
    route = Route(user_id=test_user.id, name="MIUT", total_distance_km=115, total_elevation_gain=7000, race_date="2026-04-25",
                  start_hour=0, start_minute=0, course_json={"route_coords": [[32.86, -16.90], [32.70, -16.95]]},
                  weather_json={"date": "2026-04-25", "tz": "Atlantic/Madeira"})
    db_session.add(route)
    await db_session.flush()
    assert (await race_match.race_clock(db_session, test_user.id, route))["utc_offset_s"] == 3600  # WEST, not lon / 15
    assert route.params_json is None
    route.weather_json = None  # no weather yet: the fallback (lon / 15, 2 h off Paris)
    assert (await race_match.race_clock(db_session, test_user.id, route))["utc_offset_s"] == -3600


def _flat(km=40):
    from app.services.gpx import build_course_profile

    pts = [GpxPoint(lat=45.0, lon=6.0 + i * 1e-4, elevation=500.0, distance_from_start=i * 10.0) for i in range(km * 100 + 1)]
    return build_course_profile(pts)


def test_a_december_dusk_start_is_run_in_the_dark():
    prof = AthleteGradientProfile(flat_pace_s_per_km=360, gradient_factors=dict(rs._DEFAULT_GRADIENT_FACTORS), data_points=0,
                                  sport_types_used=[])
    window = rs.predict_course(_flat(), prof, start_hour=17).predicted_total_time_s
    # 45° N on 20 December: dark from ~17:40 (the window waited for 21:00)
    dark = rs.predict_course(_flat(), prof, start_hour=17, race_date="2026-12-20", utc_offset_s=3600).predicted_total_time_s
    assert dark > window * 1.01
    # in June the same start is daylight to ~21:30: no slower than the window
    june = rs.predict_course(_flat(), prof, start_hour=17, race_date="2026-06-21", utc_offset_s=7200).predicted_total_time_s
    assert june <= window
    # a course without a date keeps the window
    assert rs.sun_clock(_flat(), None, 17, 0, 3600) is None


def test_the_profiles_night_bands_follow_the_sun():
    from app.services.plan_view import night_bands_km

    sections = [{"end_km": 10.0 * k, "clock_time_s": 17 * 3600 + 3600 * k} for k in range(1, 5)]  # 17:00 → 21:00, 10 km/h
    assert night_bands_km(sections, 17 * 3600, False) == []  # the window: night only from 21:00, the finish
    sun = rs.sun_clock(_flat(), "2026-12-20", 17, 0, 3600)
    bands = night_bands_km(sections, 17 * 3600, False, dark=sun.is_dark)
    assert len(bands) == 1 and 5 < bands[0]["start_km"] < 15 and bands[0]["end_km"] == 40.0  # dark from ~17:45


def test_the_night_is_read_on_the_clock_with_the_planned_stops():
    """The plan draws its night bands on the clock with the stops in it: the pace's night penalty reads the same
    clock (a 1 h stop at km 10 puts the end of a 17:00 start into the dark an hour earlier)."""
    prof = AthleteGradientProfile(flat_pace_s_per_km=360, gradient_factors=dict(rs._DEFAULT_GRADIENT_FACTORS), data_points=0,
                                  sport_types_used=[])
    plain = rs.predict_course(_flat(), prof, start_hour=17)
    stopped = rs.predict_course(_flat(), prof, start_hour=17, aid_stops={10.0: 3600})
    assert rs.predict_course(_flat(), prof, start_hour=17, aid_stops={}).predicted_total_time_s == plain.predicted_total_time_s
    before = [s.predicted_time_s for s in plain.segments]
    after = [s.predicted_time_s for s in stopped.segments]
    assert after[:10] == before[:10]  # before the stop: the same clock
    assert sum(after[10:]) > sum(before[10:])  # after it, an hour later: more of it in the dusk and the dark
    course = _flat()
    expect = [3600.0 * (s.start_km >= 10 - 1e-6) + 600.0 * (s.start_km >= 20 - 1e-6) for s in course.segments]
    assert rs._stops_before(course, {10.0: 3600, 20.0: 600}) == expect  # a station delays every segment after it


@pytest.mark.asyncio
async def test_the_pages_estimate_reads_the_night_with_its_stops(db_session, test_user):
    """The page sends its estimate back as the table's objective until he moves it: the estimate reads the night on
    the clock with its planned stops, as the table does, so it never reads as an objective he chose (the table would
    then anchor a pinned plan at it)."""
    from app.models.route import Route, RouteCheckpoint
    from app.routers import simulator as sim
    from app.schemas.simulator import CourseProfile
    from app.services.checkpoints import normalize_checkpoint

    route = Route(user_id=test_user.id, name="Nocturne", total_distance_km=40.0, total_elevation_gain=0.0, start_hour=17,
                  start_minute=0, course_json=json.loads(_flat().model_dump_json()), sport_type="trail")
    db_session.add(route)
    await db_session.flush()
    db_session.add(RouteCheckpoint(route_id=route.id, name="Base", distance_km=10.0, kind="base", stop_s=3600))
    await db_session.flush()
    est = (await sim._build_route_context(route, db_session, test_user.id))["course"].predicted_total_time_s
    cps = [normalize_checkpoint({"name": "Base", "distance_km": 10.0, "kind": "base", "stop_s": 3600})]
    target = int((est + 30) // 60) * 60
    table = await sim._passage_table_context(db_session, test_user.id, CourseProfile(**route.course_json), cps, target,
                                             1.0, 17, 0, None, route, None, None, None)
    prof = await rs.build_athlete_gradient_profile(db_session, test_user.id)
    plain = rs.predict_course(_flat(), prof, start_hour=17).predicted_total_time_s  # the night on the moving clock
    assert table["predicted_total"] - plain > 60  # the hour's stop moves enough of it into the night to matter
    assert abs(table["predicted_total"] - est) <= 30 and not rs.has_objective(target, table["predicted_total"])


# ── 5b. the pin floor ──

def test_a_pin_may_not_ask_a_leg_faster_than_70_percent_of_the_model():
    assert rs.MIN_PIN_RATIO == 0.7
    cps = [dict(cp) for cp in CPS]
    plain = rs.compute_passage_times(_course(), cps, None, 1.0, 6, 0, None)
    eau = next(s for s in plain if s["end_name"] == "Eau 1")
    cps[0]["target_s"] = int(eau["predicted_time_s"] * 0.6)  # 60 % of the model's time to Eau 1
    pinned = rs.compute_passage_times(_course(), cps, None, 1.0, 6, 0, None)
    first = next(s for s in pinned if s["end_name"] == "Eau 1")
    assert first["pin_clamped"] and first["adjusted_time_s"] >= 0.7 * eau["predicted_time_s"] - 1


# ── 5c. the tilt over races ──

def test_the_tilt_stored_per_race_and_its_weight():
    assert [rc.tilt_weight(m) for m in (0, 1, 2, 3)] == [0.0, pytest.approx(1 / 3), 0.5, pytest.approx(0.6)]
    assert rc.raw_tilt({"fatigue_tilt": 0.30, "fatigue_tilt_raw": 0.38}) == 0.38
    # stored with the former n / (n + 4): undone (10 checkpoints: 0.22 + 0.08 × 14/10)
    assert rc.raw_tilt({"fatigue_tilt": 0.30, "fatigue_tilt_cps": 10}) == pytest.approx(0.22 + 0.08 * 1.4)
    assert rc.raw_tilt({"fatigue_tilt": 0.30}) == 0.30 and rc.raw_tilt({}) is None


# ── 5f. the steep splits his climbs are read on ──

@pytest.mark.asyncio
async def test_steep_hiked_splits_count(db_session, test_user):
    splits = [{"distance": 1000, "moving_time": 330, "elevation_difference": 0} for _ in range(12)]
    splits += [{"distance": 1000, "moving_time": 1800, "elevation_difference": 250} for _ in range(10)]  # 25 %: 30 min/km
    db_session.add(Activity(strava_activity_id=92001, user_id=test_user.id, sport_type="TrailRun", name="Montée sèche",
                            start_date=datetime.now(timezone.utc) - timedelta(days=5), distance=22000.0, moving_time=22 * 600,
                            elapsed_time=22 * 600, total_elevation_gain=2500.0, raw_data={}, splits_metric=splits))
    await db_session.flush()
    prof = await rs.build_athlete_gradient_profile(db_session, test_user.id)
    assert prof.data_points == 22  # the 30 min/km km on a 25 % slope are read (the cap was 20 min/km)
    own = 1800 / 330
    w = 10 / (10 + rs.GRADIENT_PRIOR_SPLITS)
    assert prof.gradient_factors[25] == pytest.approx(w * own + (1 - w) * rs._get_default_factor(25), abs=1e-3)


# ── 5g. cycling: the air at the course's altitude, the drivetrain ──

def test_air_density_and_drivetrain():
    from app.services.cycling_simulator import (
        DRIVETRAIN_EFFICIENCY,
        air_density,
        cycling_power,
        estimate_cda_from_streams,
    )

    assert air_density(0) == pytest.approx(1.225, abs=1e-3)
    assert air_density(2000) == pytest.approx(1.007, abs=0.005)
    assert air_density(0, 30.0) < air_density(0, 0.0)
    wheel = cycling_power(10.0, 80, 2.0, efficiency=1.0)
    assert cycling_power(10.0, 80, 2.0) == pytest.approx(wheel / DRIVETRAIN_EFFICIENCY)
    # a ride the model would produce (power at the pedals) gives back the CdA it was made with
    n, v, rho = 3600, 9.0, air_density(500)
    p = cycling_power(v, 80, 0.0, cda=0.30, crr=0.005, rho=rho)
    streams = {"watts": [p] * n, "velocity_smooth": [v] * n, "altitude": [500.0] * n, "time": list(range(n))}
    cda, _ = estimate_cda_from_streams(streams, 80, crr=0.005)
    assert cda == pytest.approx(0.30, rel=0.01)


def test_a_mountain_course_is_ridden_in_thinner_air():
    from app.services.cycling_simulator import predict_cycling_course

    low, high = _course(), _course()
    for seg in high.segments:
        seg.min_elevation += 2000
        seg.max_elevation += 2000
    t_low = predict_cycling_course(low, target_power_watts=200, rider_weight_kg=70).predicted_total_time_s
    t_high = predict_cycling_course(high, target_power_watts=200, rider_weight_kg=70).predicted_total_time_s
    assert t_high < t_low
    assert predict_cycling_course(high, target_power_watts=200, rider_weight_kg=70).rho < 1.1
    # a density given is used as is
    assert predict_cycling_course(low, target_power_watts=200, rider_weight_kg=70, rho=1.225).rho == 1.225


# ── 7. race week: the weight by the one rule ──

@pytest.mark.asyncio
async def test_race_week_reads_the_weight_by_the_one_rule(db_session: AsyncSession, test_user: User, monkeypatch):
    from app.models.mobile import MobileDaily
    from app.models.route import Route
    from app.services import race_prep as rp
    from app.services import sante_training as st

    now = datetime.now(timezone.utc)
    test_user.weight_kg = 80.0
    test_user.weight_set_at = now - timedelta(days=60)
    db_session.add(MobileDaily(user_id=test_user.id, date=(now - timedelta(days=2)).date(), metric="weight", value=70.0,
                               platform="ios", sources=["Apple Santé"], measured_at=now - timedelta(days=2)))
    today = await st.athlete_today(db_session, test_user.id, now)
    route = Route(user_id=test_user.id, name="Ultra", total_distance_km=80, total_elevation_gain=4000, course_json=None,
                  race_date=(today + timedelta(days=3)).isoformat(), start_hour=6, start_minute=0, sport_type="trail")
    db_session.add(route)
    await db_session.flush()
    await db_session.refresh(route)  # as the race page has it: every column loaded
    prep = await rp.prep_context(db_session, test_user, route, now)
    assert "pour tes 70 kg" in prep["food"]["load"]  # the weigh-in of 2 days ago, not the value typed 60 days ago


# ── 8. back end ──

@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
async def test_the_weather_endpoint_needs_a_session(client: AsyncClient):
    r = await client.post("/api/simulator/weather", data={"lat": 45.9, "lon": 6.9, "date": "2026-10-20"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/auth/login"


@pytest.mark.asyncio
async def test_the_weather_endpoint_asks_open_meteo_once_per_place_and_day(as_user: AsyncClient, monkeypatch):
    from app.services import weather

    weather._FORECAST_CACHE.clear()
    calls = []

    async def fake(lat, lon, date, points=None):
        calls.append((lat, lon, date))
        return {"temperature_c": 12.0, "heat_factor": 1.0, "date": date, "hourly": {"temps": [12.0] * 48}}

    monkeypatch.setattr(weather, "get_weather_forecast", fake)
    for lat in (45.9211, 45.9213):  # the same place to 100 m
        r = await as_user.post("/api/simulator/weather", data={"lat": lat, "lon": 6.869, "date": "2026-10-20"})
        assert r.status_code == 200 and r.json()["temperature_c"] == 12.0
    assert len(calls) == 1
    r = await as_user.post("/api/simulator/weather", data={"lat": 45.92, "lon": 6.869, "date": "2026-10-21"})
    assert len(calls) == 2  # another day
    assert (await as_user.post("/api/simulator/weather", data={"lat": 145.0, "lon": 6.8, "date": "2026-10-21"})).status_code == 422


@pytest.mark.asyncio
async def test_the_forecast_cache_keeps_what_was_fetched_not_what_was_sent(monkeypatch):
    """A 1 MB points list costs nothing kept: the key is the start and the first 23 distinct places (what one
    Open-Meteo call fetches; the former key held every point sent: 115 MB for 20 such lists); expired entries
    go at every insert."""
    import tracemalloc

    from app.services import weather

    weather._FORECAST_CACHE.clear()
    calls = []

    async def fake(lat, lon, date, points=None):
        calls.append(len(points or []))
        return {"temperature_c": 1.0, "date": date, "hourly": {"temps": [1.0] * 48}}

    monkeypatch.setattr(weather, "get_weather_forecast", fake)
    big = [[45 + i * 1e-5, 6.8, i * 0.01] for i in range(40000)]
    assert len(json.dumps(big)) > 1_000_000
    tracemalloc.start()
    try:
        before, _ = tracemalloc.get_traced_memory()
        for k in range(20):
            await weather.cached_forecast(45.0 + k, 6.8, "2026-10-20", points=big)
        after, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(weather._FORECAST_CACHE) == 20 and after - before < 2_000_000
    assert max(calls) == weather.FORECAST_MAX_LOCS - 1  # the places fetched, not the 40 000 sent
    await weather.cached_forecast(45.0, 6.8, "2026-10-20", points=big[:30000])  # differs beyond them: the same key
    assert len(calls) == 20
    for k, (at, value) in list(weather._FORECAST_CACHE.items()):  # all expired
        weather._FORECAST_CACHE[k] = (at - weather.FORECAST_TTL_S - 1, value)
    await weather.cached_forecast(10.0, 10.0, "2026-10-20")
    assert len(weather._FORECAST_CACHE) == 1
    weather._FORECAST_CACHE.clear()


@pytest.mark.asyncio
async def test_a_new_route_without_its_course_is_refused_before_its_points(as_user: AsyncClient):
    r = await as_user.post("/api/simulator/routes", data={
        "checkpoints_json": json.dumps([{"name": "CP", "distance_km": 12.0}]), "name": "Sans parcours"})
    assert r.status_code == 400 and r.json()["error"] == "Parcours manquant"


@pytest.mark.asyncio
async def test_mes_courses_reads_the_courses_without_their_trace(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    from sqlalchemy import select

    from app.models.route import Route, RouteCheckpoint

    course = _course()
    ids = []
    soon = (date.today() + timedelta(days=20)).isoformat()
    for k, day in enumerate(("2026-10-02", soon)):  # a race run, the next one (its card draws the profile)
        r = await as_user.post("/api/simulator/routes", data={
            "course_json": course.model_dump_json(), "checkpoints_json": json.dumps(CPS), "name": f"Course {k}",
            "target_time_s": 5 * 3600, "race_date": day, "start_hour": 21, "start_minute": 0})
        ids.append(r.json()["id"])
    route = await db_session.get(Route, ids[0])
    route.result_json = {"activity_id": None, "total_actual_s": 5 * 3600, "total_elapsed_s": 5 * 3600 + 900, "actual": []}
    cp = (await db_session.execute(select(RouteCheckpoint).where(RouteCheckpoint.route_id == ids[0]))).scalars().first()
    cp.target_s = 3600
    await db_session.flush()
    db_session.expunge_all()  # what a fresh request reads: the routes come with their course deferred
    page = await as_user.get("/simulator")
    assert page.status_code == 200
    assert 'class="pfp-line"' in page.text and 'd="M0.0 ' in page.text  # the next race's profile, from its elevation points
    assert " vs plan<" in page.text  # the pinned race run still gets its plan (its course loaded for it alone)


# ── 7. the estimate: a finish time, one number everywhere; the objective: only a time he set ──

def _hhmm(s: int) -> str:
    return "%02d:%02d" % ((s % 86400) // 3600, (s % 3600) // 60)


def test_the_estimate_is_a_finish_its_heat_and_stops_in_it():
    from app.routers import simulator as sim

    prof = AthleteGradientProfile(flat_pace_s_per_km=360, gradient_factors=dict(rs._DEFAULT_GRADIENT_FACTORS), data_points=0,
                                  sport_types_used=[])
    aid = sim._aid_kms_from_checkpoints(CPS)
    course, finish, stops = sim._estimate(_course(), prof, 6, 0, {}, CPS, None, 30.0, aid_kms=aid)
    assert stops and finish == pytest.approx(course.predicted_total_time_s + sum(stops.values()), abs=2)
    assert stops == sim._aid_stops(CPS, None, finish, 30.0)  # the stop model read at that very finish (a fixed point)
    # with its weather the estimate carries the forecast's heat, as the table does
    hot, cool = ({"temps": [t] * 48, "humidity": [40] * 48} for t in (30.0, 10.0))
    _, hot_s, _ = sim._estimate(_course(), prof, 6, 0, {}, CPS, None, 30.0, True, 1.0, hot, aid)
    _, cool_s, _ = sim._estimate(_course(), prof, 6, 0, {}, CPS, None, 30.0, True, 1.0, cool, aid)
    assert cool_s == pytest.approx(finish, abs=2) and hot_s > finish * 1.08  # 30 °C: +12 % on the move
    table = rs.compute_passage_times(course, CPS, None, 1.0, 6, 0, hot, aid_kms=aid, aid_stops=stops)
    assert table[-1]["clock_time_s"] - 6 * 3600 >= hot_s * 0.99


@pytest.mark.asyncio
async def test_without_an_objective_the_page_print_phone_exports_and_debrief_finish_together(as_user: AsyncClient,
                                                                                             db_session, test_user):
    """The page plans its « Estimation » as a finish (stops in it): the printed plan, the phone's copy, the exports
    and the débrief run the same plan and arrive at the same minute; with an objective, all of them on it."""
    import re

    from app.models.route import Route
    from app.routers import simulator as sim
    from app.services.pace_export import waypoint_rows

    r = await as_user.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS), "name": "Sans objectif",
        "start_hour": 6, "start_minute": 0, "sport_type": "trail", "target_v": "2"})
    rid = r.json()["id"]
    route = await db_session.get(Route, rid)
    assert route.target_time_s is None and route.params_json == {"target_v": 2}
    for objective in (None, 5 * 3600):
        if objective:
            await as_user.post("/api/simulator/routes", data={"route_id": rid, "checkpoints_json": json.dumps(CPS),
                                                              "target_time_s": objective, "target_v": "2"})
        await db_session.refresh(route)
        ctx = await sim._build_route_context(route, db_session, test_user.id)
        est, obj = ctx["estimate_s"], ctx["objective_s"]
        assert obj == objective and est % 60 == 0
        finish = 6 * 3600 + (obj or est)
        page = int(re.findall(r'data-clock="(\d+)"', ctx["initial_table_html"])[-1])
        assert page == finish  # the page's arrival: the start + its hero number
        printed = (await as_user.get(f"/simulator/routes/{rid}/print")).text
        assert re.search(r'Arrivée</p>.*?<p class="t">(\d\d:\d\d)', printed, re.S).group(1) == _hhmm(finish)
        assert (f"Estimé {est // 3600}h{est % 3600 // 60:02d}" in printed) == (obj is None)
        phone = (await as_user.get(f"/api/simulator/routes/{rid}/offline")).json()
        assert phone["points"][-1]["clock_s"] == finish and phone["planned"] == (obj is not None)
        b = await sim._plan_bundle(route, db_session, test_user)
        assert sim._plan_finish_s(b) == finish - 6 * 3600 == (obj or est)  # the débrief's and Mes courses' plan
        assert waypoint_rows(b["sections"], b["use_target"], b["start_offset_s"])[-1]["clock"] == _hhmm(finish)
        assert b["has_objective"] == (obj is not None)
        stops = sum(s["stop_s"] for s in b["sections"])
        assert stops > 0 and (obj or est) - stops == pytest.approx(sum(s["adjusted_time_s"] for s in b["sections"]), abs=2)


@pytest.mark.asyncio
async def test_no_phantom_objective_once_the_date_and_its_weather_are_saved(as_user: AsyncClient, db_session, test_user):
    """Review repro: the page used to save its estimate in the objective field; once the race date and its season
    normals were saved the estimate moved and the old one read as an objective he never set (« Modifier
    l'objectif »). The page now saves only a time he set, marked (target_v); an unmarked time within 2 % of what
    the older page showed (the moving time at his level from the route's start, read today) reads as none, one
    further off as his; a marked time is his whatever the estimate says, and a later save keeps it."""
    from app.models.route import Route
    from app.routers import simulator as sim

    weather = {"source": "moyennes climatiques", "date": "2026-11-20", "heat_factor": 1.0,
               "hourly": {"temps": [8.0] * 24, "humidity": [80] * 24}}
    profile = await rs.build_athlete_gradient_profile(db_session, test_user.id)
    old_page = rs.predict_course(_course(), profile, start_hour=21).predicted_total_time_s  # what an older tab showed
    shown = int((old_page + 30) // 60 * 60)

    async def page(rid):
        return (await as_user.get(f"/simulator/routes/{rid}")).text

    async def route(label, first, later):
        r = await as_user.post("/api/simulator/routes", data={
            "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS), "name": label,
            "start_hour": 21, "start_minute": 0, **first})
        rid = r.json()["id"]
        await as_user.post("/api/simulator/routes", data={
            "route_id": rid, "checkpoints_json": json.dumps(CPS), "race_date": "2026-11-20",
            "weather_json": json.dumps(weather), **later})
        return rid

    sim._LEGACY.clear()
    for label, first, later in (
        ("this page: nothing saved", {"target_v": "2"}, {"target_v": "2"}),
        ("an older tab: its estimate saved", {"target_time_s": shown}, {"target_time_s": shown}),
        ("an older tab: its estimate, his races read since (+1.5 %)", {"target_time_s": int(shown * 1.015)},
         {"target_time_s": int(shown * 1.015)}),
    ):
        html = await page(await route(label, first, later))
        assert "Modifier l'objectif" not in html and ">Estimation<" in html, label
    # an older tab's objective (8 % off its estimate): his
    rid = await route("an older objective", {"target_time_s": int(shown * 0.92)}, {"target_time_s": int(shown * 0.92)})
    assert "Modifier l'objectif" in await page(rid)
    # a time he typed (marked): his, before and after the weather, even a minute from the estimate
    rid = await route("Objectif", {"target_time_s": 5 * 3600, "target_v": "2"}, {"target_time_s": 5 * 3600, "target_v": "2"})
    html = await page(rid)
    assert "Modifier l'objectif" in html and ">Objectif<" in html and "var objectiveSet = true;" in html
    rt = await db_session.get(Route, rid)
    ctx = await sim._build_route_context(rt, db_session, test_user.id)
    rt.target_time_s = ctx["estimate_s"] + 30  # 30 s from the estimate: still his
    await db_session.flush()
    assert (await sim._build_route_context(rt, db_session, test_user.id))["objective_s"] == ctx["estimate_s"] + 30
    # an older tab re-saving it as it is keeps it his; « Revenir à l'estimation » (no time sent) clears it
    await as_user.post("/api/simulator/routes", data={"route_id": rid, "checkpoints_json": json.dumps(CPS),
                                                      "target_time_s": ctx["estimate_s"] + 30})
    await db_session.refresh(rt)
    assert rt.params_json.get("target_v") == 2 and sim._objective_s(rt) == ctx["estimate_s"] + 30
    await as_user.post("/api/simulator/routes", data={"route_id": rid, "checkpoints_json": json.dumps(CPS), "target_v": "2"})
    await db_session.refresh(rt)
    assert rt.target_time_s is None


def test_the_pacing_guide_plans_the_moving_time_of_its_target():
    from app.services.pacing_guide import build_pacing_guide

    course = rs.predict_course(_course(), AthleteGradientProfile(flat_pace_s_per_km=360, gradient_factors=dict(
        rs._DEFAULT_GRADIENT_FACTORS), data_points=0, sport_types_used=[]), start_hour=6)
    stops = {6.0: 120, 10.0: 300, 20.0: 900}
    secs = rs.compute_passage_times(course, CPS, 5 * 3600, 1.0, 6, 0, None, aid_stops=stops)
    guide = build_pacing_guide(course, 5 * 3600, plan_sections=secs)
    moving = sum(sum(x["time_s"] for x in b["rows"]) if "rows" in b else b.get("time_s", 0) for b in guide["blocks"])
    assert moving == pytest.approx(5 * 3600 - sum(stops.values()), rel=0.002)  # the stops are not run


@pytest.mark.asyncio
async def test_after_an_edit_the_page_arrives_with_the_saved_plan(as_user: AsyncClient, db_session, test_user):
    """Review: the page kept posting its load-time estimate as the target, so after a station, a stop or a forecast
    its table, hero and field stayed on the old number while the saved plan used the new one (and a pinned plan
    was anchored at it). Without an objective the page now posts none: the table reads the estimate again, sends
    it back (plan-data), and arrives where the printed plan and the phone's copy do once the edit is saved."""
    import re

    def finish(html):
        return int(re.findall(r'data-clock="(\d+)"', html)[-1]) - 6 * 3600

    def estimate(html):
        return json.loads(re.search(r'id="plan-data">(.*?)</script>', html, re.S).group(1))["estimate_s"]

    r = await as_user.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS), "name": "En séance",
        "start_hour": 6, "start_minute": 0, "target_v": "2"})
    rid = r.json()["id"]
    page = (await as_user.get(f"/simulator/routes/{rid}")).text
    loaded = int(re.search(r"var predictedTotalS = (\d+);", page).group(1))
    base = CPS + [{"name": "Base", "distance_km": 25.0, "kind": "base", "crew": False, "drop_bag": False, "cutoff_clock": None}]
    hot = {"temps": [22.0 + 8 * (10 <= h % 24 < 18) for h in range(48)], "humidity": [60] * 48}
    pinned = [dict(CPS[0], target_s=3000)] + CPS[1:]
    day = (date.today() + timedelta(days=5)).isoformat()
    for label, cps, extra, before, save in (
        ("a base vie added", base, {}, {}, {}),
        ("the race date set (its sun, its clock)", base, {}, {"race_date": day}, {}),
        ("the forecast arrives", base, {"hourly_json": json.dumps(hot), "heat_factor": 1.06}, {},
         {"weather_json": json.dumps({"date": day, "heat_factor": 1.06, "source": "prevision", "hourly": hot})}),
        ("a time pinned at Eau 1", pinned, {}, {}, {}),
    ):
        if before:  # saved before the table asks (the page saves the race's name, date and start at once)
            await as_user.post("/api/simulator/routes", data={
                "route_id": rid, "checkpoints_json": json.dumps(cps), "target_v": "2", **before})
        r = await as_user.post("/partials/simulator/passage-times", data={
            "route_id": rid, "checkpoints_json": json.dumps(cps), "start_hour": 6, "start_minute": 0, **extra})
        table = r.text
        await as_user.post("/api/simulator/routes", data={
            "route_id": rid, "checkpoints_json": json.dumps(cps), "target_v": "2", **before, **save})
        phone = (await as_user.get(f"/api/simulator/routes/{rid}/offline")).json()["points"][-1]["clock_s"] - 6 * 3600
        printed = (await as_user.get(f"/simulator/routes/{rid}/print")).text
        arrival = re.search(r'Arrivée</p>.*?<p class="t">(\d\d:\d\d)', printed, re.S).group(1)
        assert finish(table) == phone and arrival == _hhmm(6 * 3600 + phone), label
        if label != "a time pinned at Eau 1":  # pinned: the plan follows the pins, the estimate stays the hero's
            assert estimate(table) == finish(table), label
        assert estimate(table) != loaded, label  # the page's hero and field move with it
        loaded = estimate(table)


def test_the_pacing_guide_runs_the_plans_own_legs_on_a_hot_day():
    """The guide spread the plan's moving time by terrain alone, the plan by terrain times each leg's heat: on a
    hot afternoon the guide's paces were not the plan's. It now spreads each leg's own planned time."""
    from app.services.pacing_guide import build_pacing_guide

    course = rs.predict_course(_course(), AthleteGradientProfile(flat_pace_s_per_km=360, gradient_factors=dict(
        rs._DEFAULT_GRADIENT_FACTORS), data_points=0, sport_types_used=[]), start_hour=9)
    hot = {"temps": [12.0 + 20 * (h % 24 >= 11) for h in range(48)], "humidity": [40] * 48}  # 32 °C from 11:00
    secs = rs.compute_passage_times(course, CPS, 5 * 3600, 1.0, 9, 0, hot, aid_stops={6.0: 120, 10.0: 300, 20.0: 900})
    assert max(s["heat_factor"] for s in secs) > 1.1
    guide = build_pacing_guide(course, 5 * 3600, plan_sections=secs)
    # each leg's own planned time over its km by their effort basis: the plan's weights, its heat in them
    per_km = {}
    for s in secs:
        segs = [g for g in course.segments if g.start_km >= s["start_km"] - 1e-6 and g.end_km <= s["end_km"] + 1e-6]
        base = sum(g.base_time_s for g in segs)
        per_km.update({g.start_km: s["adjusted_time_s"] * g.base_time_s / base for g in segs})
    moving = sum(s["adjusted_time_s"] for s in secs)
    terrain_only = {g.start_km: moving * g.base_time_s / sum(x.base_time_s for x in course.segments) for g in course.segments}
    off = 0.0
    for b in guide["blocks"]:
        kms = [k for k in per_km if b["start_km"] - 1e-6 <= k < b["end_km"] - 1e-6]
        assert b["time_s"] == pytest.approx(sum(per_km[k] for k in kms), abs=2), (b["start_km"], b["end_km"])
        off = max(off, abs(sum(terrain_only[k] for k in kms) / b["time_s"] - 1))
    assert off > 0.03  # terrain alone, as before: a block 3 % or more off the plan's own time on this hot day


@pytest.mark.asyncio
async def test_mes_courses_reads_the_objective_by_the_same_rule(as_user: AsyncClient, db_session, test_user):
    """« Mes courses » shows an objective only when it is his: an older page's estimate (unmarked, within 2 % of
    the moving time at his level from the route's start) is none there either."""
    from app.routers import simulator as sim

    profile = await rs.build_athlete_gradient_profile(db_session, test_user.id)
    shown = int((rs.predict_course(_course(), profile, start_hour=6).predicted_total_time_s + 30) // 60 * 60)
    soon = (date.today() + timedelta(days=30)).isoformat()
    sim._LEGACY.clear()
    for name, target in (("Estimée", shown), ("Visée", 5 * 3600)):
        await as_user.post("/api/simulator/routes", data={
            "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS), "name": name,
            "target_time_s": target, "race_date": soon, "start_hour": 6, "start_minute": 0})
    db_session.expunge_all()
    html = (await as_user.get("/simulator")).text
    assert "5h00" in html and f"{shown // 3600}h{shown % 3600 // 60:02d}" not in html


# the owner's races as Strava gives them (moving hours, Strava D+): the older page read his level with the law of
# before 2026-10-14 (race_legacy)
OWNER_RACES = [("UTMB Transjeju 100M - 2eme", "2026-10-02", 148.2, 5513, 16.07),
               ("Marathon du mont blanc 90km", "2026-06-26", 87.4, 5977, 13.53),
               ("100k UTMB Chiang Mai", "2025-12-05", 92.3, 4714, 11.16), ("TDS UTMB - 30eme", "2025-08-25", 152.9, 8801, 22.92),
               ("Marathon du mont blanc 90k", "2025-06-27", 92.2, 6117, 14.26), ("Semi Pollencia", "2025-05-18", 21.1, 29, 1.25),
               ("Trans Inthanon", "2024-12-06", 91.7, 4865, 15.64), ("Maxi Race", "2024-06-01", 93.1, 5247, 12.27)]


def _course_km(n):
    """n km of the test course's hills (about 32 m of D+ per km)."""
    from app.schemas.simulator import CourseProfile, CourseSegment

    grades = [0, 1, -1, 0, 6, 8, 10, 22, 24, 6, -8, -10, -9, -7, -2, 0, 1, 0, -1, 0, 5, 7, 6, -6, -8, -4, 0, 1, 0, -1]
    segs, elev, pts, coords = [], 200.0, [], []
    for i in range(n):
        g, s0 = grades[i % len(grades)], elev
        elev += g * 10
        segs.append(CourseSegment(index=i, start_km=float(i), end_km=float(i + 1), distance_m=1000.0,
                                  elevation_gain=max(0.0, elev - s0), elevation_loss=max(0.0, s0 - elev), avg_gradient_pct=float(g),
                                  min_elevation=min(s0, elev), max_elevation=max(s0, elev)))
        for k in range(4):
            km, e = i + k / 4, s0 + (elev - s0) * k / 4
            pts.append({"distance_km": km, "elevation": e})
            coords.append([45.0 + km * 0.003, 6.8, km, e])
    pts.append({"distance_km": float(n), "elevation": elev})
    coords.append([45.0 + n * 0.003, 6.8, float(n), elev])
    return CourseProfile(name=f"C{n}", total_distance_km=float(n), total_elevation_gain=sum(s.elevation_gain for s in segs),
                         total_elevation_loss=sum(s.elevation_loss for s in segs), segments=segs, elevation_points=pts,
                         route_coords=coords)


@pytest.mark.asyncio
async def test_an_older_pages_estimate_is_read_with_the_law_it_was_made_with(db_session, test_user, time_machine):
    """Review: the older page saved its moving-time estimate with the level law of its day; read with this
    release's law (his estimates 4-10 % slower) it passed for an objective. It is read again with that law, frozen
    (race_legacy): no objective, even saved after a race the import estimate predates. His Transjeju objective,
    18h30 against that page's 16-17 h, stays his."""
    from app.models.route import Route
    from app.routers import simulator as sim
    from app.services import race_legacy as rl

    # the day this was written: both laws read his races by their age (race_calibration.MODEL_MONTHS, the recency
    # half-life), so the older page's estimate, and how far 18h30 stands from it, moved with the calendar
    time_machine.move_to(datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
    for i, (name, day, km, dp, h) in enumerate(OWNER_RACES):
        db_session.add(Activity(strava_activity_id=95000 + i, user_id=test_user.id, sport_type="TrailRun", name=name,
                                start_date=datetime.fromisoformat(day + "T05:00:00+00:00"), distance=km * 1000,
                                moving_time=int(h * 3600), elapsed_time=int(h * 3600), total_elevation_gain=dp,
                                raw_data={"workout_type": 1}))
    await db_session.flush()
    course = _course_km(148)
    profile = await rs.build_athlete_gradient_profile(db_session, test_user.id)
    legacy = await sim._legacy_profile(db_session, test_user.id, profile)
    assert legacy.race_model and "heat_mean" not in legacy.race_model and legacy.race_model["b"] == pytest.approx(
        rl.model_from_points(await rc.collect_race_points(db_session, test_user.id))["b"])
    old_page = rs.predict_course(course.model_copy(deep=True), legacy.model_copy(), start_hour=6).predicted_total_time_s
    today = rs.predict_course(course.model_copy(deep=True), profile.model_copy(), start_hour=6).predicted_total_time_s
    assert 15.5 * 3600 < old_page < 17.5 * 3600 and today > old_page * 1.04  # this release's law: slower for him
    # imported before Transjeju (its estimate stored with the course then), saved by the older page after it
    before = rl.model_from_points([p for p in await rc.collect_race_points(db_session, test_user.id)
                                   if not p["name"].startswith("UTMB Transjeju")])
    imported = rs.predict_course(course.model_copy(deep=True), legacy.model_copy(update={"race_model": before}),
                                 start_hour=6).predicted_total_time_s
    cj = json.loads(course.model_dump_json())
    cj["predicted_total_time_s"] = imported
    for i, (label, target, his) in enumerate((("the older page's estimate", int((old_page + 30) // 60 * 60), False),
                                              ("his Transjeju objective", 18 * 3600 + 30 * 60, True))):
        route = Route(user_id=test_user.id, name=label, total_distance_km=148.0, total_elevation_gain=course.total_elevation_gain,
                      course_json=cj, sport_type="trail", start_hour=6, start_minute=0, target_time_s=target)
        db_session.add(route)
        await db_session.flush()
        assert (await sim._route_objective(db_session, route, profile) is not None) == his, label
        b = await sim._plan_bundle(route, db_session, test_user, profile=profile)
        assert (b["objective_s"] is not None) == his and b["has_objective"] == his, label
    assert abs(imported / old_page - 1) > 0.02  # the import's estimate alone would not have told it
    assert abs(int((old_page + 30) // 60 * 60) / today - 1) > 0.02  # nor this release's law (the previous rule)


@pytest.mark.asyncio
async def test_the_page_reads_its_new_race_date_before_the_save(as_user: AsyncClient, db_session, test_user):
    """Review: a race date set in the session reached the table only once saved (the recalc runs first). The page
    posts its date and the fetched weather's zone with the recalc: night by the sun at once, the same plan the save
    then keeps."""
    import re

    def finish(html):
        return int(re.findall(r'data-clock="(\d+)"', html)[-1]) - 17 * 3600

    rid = (await as_user.post("/api/simulator/routes", data={
        "course_json": _flat().model_dump_json(), "checkpoints_json": "[]", "name": "Le soir",
        "start_hour": 17, "start_minute": 0, "target_v": "2"})).json()["id"]
    window = finish((await as_user.post("/partials/simulator/passage-times", data={
        "route_id": rid, "checkpoints_json": "[]", "start_hour": 17, "start_minute": 0})).text)
    december = finish((await as_user.post("/partials/simulator/passage-times", data={
        "route_id": rid, "checkpoints_json": "[]", "start_hour": 17, "start_minute": 0, "race_date": "2026-12-20"})).text)
    assert december > window  # dark from ~17:30 in December, not from 21:00: read before the save
    await as_user.post("/api/simulator/routes", data={"route_id": rid, "checkpoints_json": "[]", "race_date": "2026-12-20",
                                                      "target_v": "2"})
    saved = (await as_user.get(f"/api/simulator/routes/{rid}/offline")).json()["points"][-1]["clock_s"] - 17 * 3600
    assert saved == december
    # the weather fetched for it brings the start's zone (Tokyo's +9 h, not the athlete's): read before its save too
    tokyo = finish((await as_user.post("/partials/simulator/passage-times", data={
        "route_id": rid, "checkpoints_json": "[]", "start_hour": 17, "start_minute": 0, "race_date": "2026-12-20",
        "wx_tz": "Asia/Tokyo", "wx_date": "2026-12-20"})).text)
    assert tokyo != december
    await as_user.post("/api/simulator/routes", data={"route_id": rid, "checkpoints_json": "[]", "target_v": "2",
                                                      "weather_json": json.dumps({"date": "2026-12-20", "tz": "Asia/Tokyo"})})
    assert (await as_user.get(f"/api/simulator/routes/{rid}/offline")).json()["points"][-1]["clock_s"] - 17 * 3600 == tokyo
    cleared = finish((await as_user.post("/partials/simulator/passage-times", data={
        "route_id": rid, "checkpoints_json": "[]", "start_hour": 17, "start_minute": 0, "race_date": ""})).text)
    assert cleared == window  # the date removed in the page: the fixed window again
