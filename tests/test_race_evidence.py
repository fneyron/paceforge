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


def test_the_athletes_own_stops():
    field = rc.STOP_SHARE_18H * (20 / 18) ** rc.STOP_SHARE_EXP  # the field's share at 20 h elapsed
    one = [{"hours": 20 * (1 - 2 * field), "elapsed_h": 20.0, "weight": 1.0}]  # he stops twice as long as the field
    assert rc.stop_factor(one) == pytest.approx(math.sqrt(2), rel=1e-3)  # halfway (in ln) to the field
    assert rc.stop_factor([{"hours": 10.0, "elapsed_h": 10.0}]) == 1.0  # a watch that stops its clock: says nothing
    assert rc.stop_factor([{"hours": 2.0, "elapsed_h": 2.5}]) == 1.0  # too short
    assert rc.stop_factor([{"hours": 10.0, "elapsed_h": 20.0}]) == rc.STOP_FACTOR_RANGE[1]  # bounded


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


def test_scenarios_switch_at_five_percent_and_the_safety_columns_longer_stops():
    sections = rs.compute_passage_times(_course(), CPS, 5 * 3600, 1.0, 6, 0, None, aid_stops={6.0: 120, 10.0: 300})
    sc = rs.build_scenarios(sections, 6 * 3600, 5 * 3600, fast_pct=9.8, safe_pct=10.9, safe_extra_stops={6.0: 100, 10.0: 200})
    sw = sc["switch"]
    assert sw["km"] == 20.0 and sc["switch_late_pct"] == 5
    assert sc["switch_late_s"] == round((sw["target_s"] - 6 * 3600) * 0.05 / 60) * 60  # 5 % of the plan there, to the minute
    rows = {r["km"]: r for r in sc["rows"]}
    assert rows[6.0]["safe_extra_s"] == 0 and rows[10.0]["safe_extra_s"] == 100 and rows[20.0]["safe_extra_s"] == 300
    plain = rs.build_scenarios(sections, 6 * 3600, 5 * 3600, fast_pct=9.8, safe_pct=10.9)
    assert sc["totals"]["safe_s"] - plain["totals"]["safe_s"] == 300
    assert sc["totals"]["target_s"] == plain["totals"]["target_s"] and sc["totals"]["fast_s"] == plain["totals"]["fast_s"]


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


# ── 3. stops: the median stop at the runner's finish, the 80th percentile on the safety column ──

def test_the_stop_model():
    # a water point mid-race for an 18 h finisher: median 3.9 min (the fit), the planned stop
    med = rs.stop_median_s("water", 18 * 3600, 0.5)
    assert med / 60 == pytest.approx(math.exp(1.149 + 0.441 * 0.5), rel=1e-6) and 3.8 < med / 60 < 4.0
    assert rs.default_stop_s("water", 18 * 3600, 0.5) == round(med)
    assert rs.safe_stop_s("water", 18 * 3600, 0.5) == round(med * 2.6)
    # longer for a slower runner, later in the race, at a bigger station
    assert rs.default_stop_s("full", 30 * 3600, 0.5) > rs.default_stop_s("full", 10 * 3600, 0.5)
    assert rs.default_stop_s("full", 18 * 3600, 0.9) > rs.default_stop_s("full", 18 * 3600, 0.1)
    assert rs.default_stop_s("base", 18 * 3600, 0.5) > rs.default_stop_s("full", 18 * 3600, 0.5) > rs.default_stop_s("water", 18 * 3600, 0.5)
    assert rs.default_stop_s("none", 18 * 3600, 0.5) == 0
    # his own factor
    assert rs.default_stop_s("full", 18 * 3600, 0.5, factor=2.0) == pytest.approx(2 * rs.default_stop_s("full", 18 * 3600, 0.5), abs=1)


def test_the_stop_he_sets_always_wins():
    from app.routers.simulator import _aid_stops, _safe_extra_stops

    cps = [dict(CPS[0], stop_s=0), dict(CPS[1], stop_s=420), CPS[2]]
    stops = _aid_stops(cps, None, 20 * 3600, 30.0)
    assert 6.0 not in stops and stops[10.0] == 420 and stops[20.0] == rs.default_stop_s("full", 20 * 3600, 20 / 30)
    assert _aid_stops(cps, 4, 20 * 3600, 30.0) == {10.0: 420, 20.0: 240}  # one value everywhere, the point's own first
    # the safety column's longer stop only where the model sets it
    assert set(_safe_extra_stops(cps, None, 20 * 3600, 30.0)) == {20.0}
    assert _safe_extra_stops(cps, 4, 20 * 3600, 30.0) == {}


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


