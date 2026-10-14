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


