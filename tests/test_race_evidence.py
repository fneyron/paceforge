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
        return {f"{first.isoformat()}T{h:02d}:00": (31.0, 40.0) for h in range(24)}

    monkeypatch.setattr(weather, "temperature_humidity", fake)
    now = datetime.now(timezone.utc)
    race = Activity(strava_activity_id=91001, user_id=test_user.id, sport_type="TrailRun", name="Ultra chaud",
                    start_date=(now - timedelta(days=40)).replace(hour=5, minute=0), distance=60000.0, moving_time=8 * 3600,
                    elapsed_time=8 * 3600 + 900, total_elevation_gain=2000.0,
                    raw_data={"workout_type": 1, "start_latlng": [43.3, 5.4]})
    nowhere = Activity(strava_activity_id=91002, user_id=test_user.id, sport_type="TrailRun", name="Sans GPS",
                       start_date=now - timedelta(days=60), distance=50000.0, moving_time=7 * 3600, elapsed_time=7 * 3600,
                       total_elevation_gain=1500.0, raw_data={"workout_type": 1})
    db_session.add_all([race, nowhere])
    await db_session.flush()
    assert await rc.backfill_race_heat(db_session, test_user.id) == 2
    assert len(calls) == 1  # the race without a position is not asked, only marked
    assert race.raw_data[rc.HEAT_CACHE_KEY]["h"] == pytest.approx(weather.compute_heat_factor(31.0, 40.0), abs=1e-3)
    assert nowhere.raw_data[rc.HEAT_CACHE_KEY] == {"v": 1, "h": None, "src": "none"}
    assert await rc.backfill_race_heat(db_session, test_user.id) == 0 and len(calls) == 1  # never asked again
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

    assert await weather.lookups_paused() is False  # Redis unreachable here: nothing shared, nothing paused
    await weather.pause_lookups()  # and never an error
    monkeypatch.setattr(settings, "ACTIVITY_ENV_FETCH", True)
    marker = {}

    async def paused():
        return bool(marker)

    async def pause():
        marker["down"] = True

    monkeypatch.setattr(weather, "lookups_paused", paused)
    monkeypatch.setattr(weather, "pause_lookups", pause)
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
    assert await rc.backfill_race_heat(db_session, test_user.id) == 0 and len(calls) == 1 and marker  # failed: shared
    assert await rc.backfill_race_heat(db_session, test_user.id) == 0 and len(calls) == 1  # every worker waits


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
