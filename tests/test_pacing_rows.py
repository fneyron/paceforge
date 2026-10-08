"""Pilotage in the passage rows: each leg's heart-rate ceiling (the watch shows
the same), its effort and steep lines."""

import json
import re

import pytest
from httpx import AsyncClient

from app.dependencies import get_current_user
from app.models.user import User
from app.services.pacing_guide import build_pacing_guide, leg_instructions, resolve_hr_caps
from tests.test_race_plan_services import CPS, _course


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


async def _route(client: AsyncClient, target_s: int = 5 * 3600) -> int:
    r = await client.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS),
        "name": "Jeju test", "target_time_s": target_s, "race_date": "2099-10-02",
        "start_hour": 21, "start_minute": 0, "sport_type": "trail", "stop_minutes": 3,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_the_heart_rate_ceiling_is_per_leg_and_the_watch_shows_the_same():
    course = _course()
    cps = [{"name": f"P{k}", "distance_km": float(k), "elevation": 0, "kind": "water", "crew": False, "drop_bag": False, "cutoff_clock": None}
           for k in range(2, 30, 2)]
    from app.services.race_simulator import compute_passage_times

    secs = compute_passage_times(course, cps, 5 * 3600, 1.0, 21, 0, None)
    guide = build_pacing_guide(course, 5 * 3600, hr_cap_climb=140, hr_cap_flat=136, hr_release_descent=130, plan_sections=secs)
    legs = leg_instructions(guide, secs)
    same = [(a, b) for a, b in zip(legs, legs[1:], strict=False) if a["cls"] == b["cls"]]
    assert same and all(b["hr_cap"] < a["hr_cap"] for a, b in same)
    for lg in legs:
        assert f"FC{lg['hr_cap']}" in lg["code"]


def test_effort_and_steep_lines():
    from app.services.pacing_guide import effort_sentence, steep_on_leg, steep_sentence

    assert effort_sentence("flat", 129) == "<b>Cardio sous 129</b>"  # running steady on the flat goes without saying
    assert effort_sentence("flat", None) is None
    assert effort_sentence("descent", 111) == "<b>Cardio vers 111</b> · descente : relâché, sans freiner. Mange avant, en haut."
    assert effort_sentence("climb", 124, 18) == "<b>Cardio sous 124</b> · montée : marche dès que ça dépasse 18 %, cours le reste."
    assert effort_sentence("stairs", None) == "Très raide : marche, mains sur les cuisses."
    alerts = [{"start_km": 92.0, "end_km": 96.0, "max_grade": 21}, {"start_km": 98.0, "end_km": 101.0, "max_grade": 24}]
    on_leg = steep_on_leg(alerts, 90.0, 100.0)
    assert on_leg[-1]["end_km"] == 100.0  # clipped to the leg
    assert steep_sentence(on_leg) == "Raide km 92 → 96 et km 98 → 100, jusqu'à 24 % : marche, mains sur les cuisses."
    assert steep_sentence(on_leg[1:]) == "Raide km 98 → 100, jusqu'à 24 % : marche, mains sur les cuisses."
    assert steep_on_leg(alerts, 60.0, 80.0) == [] and steep_sentence([]) is None


def test_resolve_hr_caps_derives_and_honours_old_values():
    assert resolve_hr_caps({}, 180) == {"climb": 135, "flat": 131, "descent": 125, "walk_grade": 18.0, "source": "activités"}
    assert resolve_hr_caps({"hr_cap_climb": 150}, 180)["flat"] == 146 and resolve_hr_caps({"hr_cap_climb": "150"}, None)["descent"] == 140
    old = resolve_hr_caps({"hr_cap_climb": 140, "hr_cap_flat": 136, "hr_release_descent": 128, "walk_grade": 20}, 185)
    assert old == {"climb": 140, "flat": 136, "descent": 128, "walk_grade": 20.0, "source": "toi"}
    assert resolve_hr_caps({}, None)["climb"] is None


@pytest.mark.asyncio
async def test_rows_and_watch_codes_carry_the_same_ceiling(as_user: AsyncClient):
    rid = await _route(as_user)
    r = await as_user.post(f"/api/simulator/routes/{rid}/params", data={"hr_cap_climb": 150})
    assert r.status_code == 204
    t = (await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(CPS), "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0, "route_id": rid, "stop_minutes": 3,
    })).text
    row_caps = [int(x) for x in re.findall(r"Cardio (?:sous|vers) (\d+)", t)]  # « vers » on a descent (a target, not a ceiling)
    csv = (await as_user.get(f"/api/simulator/routes/{rid}/pace-export?format=csv")).text
    codes = [int(x) for x in re.findall(r"FC(\d+)", csv)]
    assert row_caps and row_caps == codes[:len(row_caps)]
    assert row_caps[0] <= 150 and len(set(row_caps)) > 1  # falls along the race, not one repeated number
