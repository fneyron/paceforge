"""The plan's shape as fitted on 115 UTMB Live races (scripts/race_data/fit_model.py):
gradient curve, fatigue by hours on course, night; the personal tilt around the
population curve; fatigue and night read on the clock of the level planned."""

import pytest

from app.models.route import Route
from app.schemas.simulator import AthleteGradientProfile
from app.services import race_simulator as rs
from app.services.race_calibration import effort_km
from tests.test_race_plan_services import _course


def _profile(flat=330.0, **kw):
    return AthleteGradientProfile(flat_pace_s_per_km=flat, gradient_factors=dict(rs._DEFAULT_GRADIENT_FACTORS),
                                  data_points=0, sport_types_used=[], **kw)


def _times(course):
    return [s.predicted_time_s for s in course.segments]


# ── gradient curve ──

def test_default_curve_is_the_fitted_one_from_minus_15_up():
    f = rs._get_default_factor
    assert f(0) == 1.0
    assert (f(3), f(8), f(15), f(25), f(30)) == (1.145, 1.516, 2.065, 3.123, 3.508)
    assert (f(-3), f(-8), f(-15)) == (0.896, 0.836, 1.011)
    assert f(-20) == 1.10 and f(-18) == pytest.approx(1.064, abs=1e-3)  # below -15 %: the previous value, interpolated
    assert rs._get_factor(_profile(), -35) == 1.10                       # the app still clamps at -20 %
    assert all(f(g) >= f(g - 1) for g in range(1, 31))                   # climbing steeper is never cheaper
    assert min(range(-20, 1), key=f) == -8                               # the fastest descent


# ── fatigue by hours on course ──

def test_population_fatigue_by_hours():
    f = rs._population_fatigue
    assert f(0) == pytest.approx(0.9854, abs=1e-4)   # fresh legs: a touch under 1
    assert f(3) == pytest.approx(1.3478, abs=1e-3)
    assert f(10) == pytest.approx(1.6347, abs=1e-4)
    assert f(20) == pytest.approx(1.8980, abs=1e-3)
    hours = [h / 4 for h in range(0, 161)]
    assert all(f(b) > f(a) for a, b in zip(hours, hours[1:], strict=False))
    # relative to the training flat pace: 1 at the level anchor, same shape
    assert rs._fatigue_factor(rs.FATIGUE_REF_H, 0.3, 150) == pytest.approx(1.0)
    assert rs._fatigue_factor(20, 0.3, 150) / rs._fatigue_factor(10, 0.3, 150) == pytest.approx(f(20) / f(10))
    assert rs._fatigue_factor(3, 0.9, 15) == 1.0  # under 20 km the training pace stands


def _flat(km):
    from app.schemas.simulator import CourseProfile, CourseSegment

    n = int(round(km * 10))
    segs = [CourseSegment(index=i, start_km=i / 10, end_km=(i + 1) / 10, distance_m=100.0, elevation_gain=0,
                          elevation_loss=0, avg_gradient_pct=0.0, min_elevation=100, max_elevation=100) for i in range(n)]
    pts = [{"distance_km": i / 10, "elevation": 100.0} for i in range(n + 1)]
    return CourseProfile(name="flat", total_distance_km=n / 10, total_elevation_gain=0, total_elevation_loss=0,
                         segments=segs, elevation_points=pts)


def test_a_longer_course_never_takes_less_time():
    # the curve's sub-1 start switched on whole at 20 km took ~6 min off 20,1 km vs 19,9 km
    for flat in (210.0, 330.0, 480.0):
        totals = [rs.predict_course(_flat(km), _profile(flat), start_hour=12).predicted_total_time_s
                  for km in (19.9, 20.0, 20.1, 21.1, 25, 30, 35, 40, 42.2)]
        assert all(b > a for a, b in zip(totals, totals[1:], strict=False)), (flat, totals)
    assert rs._fatigue_factor(0, 0.0, 20.0) == 1.0                        # continuous at 20 km
    assert rs._fatigue_factor(0, 0.0, 30.0) == pytest.approx((1 + rs._fatigue_factor(0, 0.0, 40.0)) / 2)
    assert rs._fatigue_factor(3, 0.5, 40.0) == rs._fatigue_factor(3, 0.5, 150.0)  # the fit's range: the curve whole


def test_personal_tilt_reshapes_around_the_population_curve():
    pop = [rs._population_fatigue(h) / rs._population_fatigue(rs.FATIGUE_REF_H) for h in (1, 8, 16)]
    # no matched race: exactly the population curve, wherever in the race
    for h, p, want in zip((1, 8, 16), (0.1, 0.5, 0.9), pop, strict=True):
        assert rs._fatigue_factor(h, p, 150) == pytest.approx(want)
        assert rs._fatigue_factor(h, p, 150, rs.DEFAULT_FATIGUE_TILT) == pytest.approx(want)
    # a runner who faded more: faster start, slower finish, unchanged at 45 %
    assert rs._fatigue_factor(1, 0.1, 150, 0.35) < pop[0]
    assert rs._fatigue_factor(16, 0.9, 150, 0.35) > pop[2]
    assert rs._fatigue_factor(8, 0.45, 150, 0.35) == pytest.approx(rs._fatigue_factor(8, 0.45, 150))

    base = rs.predict_course(_course(), _profile())
    faded = rs.predict_course(_course(), _profile(fatigue_tilt=0.35))
    assert _times(faded)[0] < _times(base)[0] and _times(faded)[-1] > _times(base)[-1]
    assert abs(faded.predicted_total_time_s / base.predicted_total_time_s - 1) < 0.03  # a reshape, not a new level


# ── night ──

def test_night_costs_what_the_fit_says():
    at = rs._night_penalty
    assert at(2 * 3600, 21) == rs.NIGHT_FACTOR == 1.0325          # 23:00
    assert at(0, 20.5) == at(30 * 60, 6) == rs.DUSK_DAWN_FACTOR == 1.0122
    assert at(0, 12) == 1.0


# ── hours at the level actually planned ──

def _race_model(course, total_s):
    """An effort model whose total for this course is ``total_s``."""
    b = 0.08
    return {"a": effort_km(course.total_distance_km, course.total_elevation_gain) / (total_s / 3600) ** (1 - b), "b": b}


def test_fatigue_reads_the_race_level_clock():
    training = rs.predict_course(_course(), _profile())
    fast_total = 0.75 * training.predicted_total_time_s
    prof = _profile(race_model=_race_model(training, fast_total))
    raced = rs.predict_course(_course(), prof)
    assert abs(raced.predicted_total_time_s - fast_total) < 2
    assert prof.race_calibration["training_total_s"] == training.predicted_total_time_s
    assert prof.race_calibration["ratio"] == pytest.approx(0.75, abs=1e-3)
    # a faster level is fewer hours on course, so less fatigue: the finish weighs less
    share = lambda c: sum(_times(c)[-10:]) / sum(_times(c))  # noqa: E731
    assert share(raced) < share(training)
    # …exactly what an athlete whose training pace were at that level gets
    flat = 330.0
    for _ in range(8):
        same = rs.predict_course(_course(), _profile(flat))
        flat *= raced.predicted_total_time_s / same.predicted_total_time_s
    for a, b in zip(_times(raced), _times(same), strict=True):
        assert a == pytest.approx(b, abs=1.0)


def test_objective_shapes_the_plan_on_its_own_clock():
    plain = rs.predict_course(_course(), _profile())
    objective = int(0.7 * plain.predicted_total_time_s)
    shaped = rs.predict_course(_course(), _profile(), plan_moving_s=objective)
    assert _times(shaped) == _times(plain)  # the prediction itself does not move
    shares = lambda c: [s.base_time_s / sum(x.base_time_s for x in c.segments) for s in c.segments]  # noqa: E731
    assert shares(shaped)[-1] < shares(plain)[-1]  # a faster objective fades less
    # the objective's shape does not depend on the training pace
    other = rs.predict_course(_course(), _profile(400.0), plan_moving_s=objective)
    for a, b in zip(shares(shaped), shares(other), strict=True):
        assert a == pytest.approx(b, abs=1e-4)


def test_objective_moving_time_leaves_the_stops_out():
    cps = [{"name": "A", "distance_km": 6.0}, {"name": "B", "distance_km": 10.0}, {"name": "Fin", "distance_km": 30.0}]
    assert rs.objective_moving_s(5 * 3600, 30.0, cps, aid_stops={6.0: 120, 10.0: 300, 30.0: 900}) == 5 * 3600 - 420
    assert rs.objective_moving_s(None, 30.0, cps) is None


# ── the personal tilt is stored with the curve it was measured against ──

@pytest.mark.asyncio
async def test_only_tilts_measured_on_the_hours_curve_count(db_session, test_user):
    db_session.add(Route(user_id=test_user.id, name="old", total_distance_km=30, result_json={"fatigue_tilt": 0.38}))
    await db_session.flush()
    assert await rs._personal_fatigue_tilt(db_session, test_user.id) == rs.DEFAULT_FATIGUE_TILT
    db_session.add(Route(user_id=test_user.id, name="new", total_distance_km=30,
                         result_json={"fatigue_tilt": 0.31, "fatigue_model": rs.FATIGUE_MODEL}))
    await db_session.flush()
    assert await rs._personal_fatigue_tilt(db_session, test_user.id) == 0.31
