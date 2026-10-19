"""The saved plan, watch export, printed band and debrief share the HR reference."""
import json
import re
from datetime import UTC, datetime, time, timedelta

import pytest

from app.models.activity import Activity
from app.models.user import User
from app.services import sante_training as st
from app.services.debrief import leg_debrief
from tests.test_race_plan_services import _splits
from tests.test_sante_score import D
from tests.test_simulator_routes import _create_bike_route, _create_route, as_user  # noqa: F401


def test_debrief_uses_each_legs_actual_ceiling_for_reasons_and_verdict():
    sections = [{"start_km": i * 5, "end_km": (i + 1) * 5,
                 "start_name": f"P{i}", "end_name": f"P{i + 1}", "predicted_time_s": 1800,
                 "adjusted_time_s": 1800, "stop_s": 0} for i in range(2)]
    splits = _splits(10, lambda _: 300, hr=133)
    old = leg_debrief(splits, sections, 10, True, hr_cap=135)
    assert not any(leg["hr_over"] for leg in old["legs"])
    actual = leg_debrief(splits, sections, 10, True, hr_cap=135, hr_caps=[130, 120])
    assert [leg["hr_cap"] for leg in actual["legs"]] == [130, 120]
    assert all(leg["hr_over"] and leg["tag"] == "over" for leg in actual["legs"])
    assert actual["legs"][0]["reason"] == "FC au-dessus du plafond (130)"
    assert actual["legs"][1]["reason"] == "FC au-dessus du plafond (120)"
    assert "FC au-dessus du plafond sur les premiers tronçons" in actual["summary"]["verdict"]


@pytest.mark.parametrize("caps", [[None, None], [], [120]])
def test_missing_or_misaligned_ceilings_never_invent_a_cardio_comparison(caps):
    sections = [{"start_km": i * 5, "end_km": (i + 1) * 5,
                 "start_name": f"P{i}", "end_name": f"P{i + 1}", "predicted_time_s": 1800,
                 "adjusted_time_s": 1800, "stop_s": 0} for i in range(2)]
    result = leg_debrief(_splits(10, lambda _: 300, hr=150), sections, 10, True,
                         hr_cap=135, hr_caps=caps)
    assert all(leg["hr_cap"] is None and not leg["hr_over"] and leg["tag"] != "over"
               for leg in result["legs"])
    assert "FC au-dessus" not in result["summary"]["verdict"]


async def _seed_reference(db, user, mode):
    user.timezone = "UTC"
    user.max_heart_rate = 183 if mode == "confirmed" else None
    other = User(firstname="Other", max_heart_rate=220)
    db.add(other)
    await db.flush()
    # Both athletes own plausible detailed evidence, while this athlete also
    # has a summary spike. An unavailable reference must not borrow either.
    for owner, peaks in ((user, (179, 178)), (other, (215, 214))):
        for offset, peak in zip((1, 3), peaks, strict=True):
            laps = [{"moving_time": m * 60, "average_heartrate": hr}
                    for m, hr in ((5, 140), (4, peak - 5), (4, peak - 6))]
            db.add(Activity(user_id=owner.id, sport_type="Run", name="Course détaillée",
                            start_date=datetime.combine(D - timedelta(days=offset), time(10), tzinfo=UTC),
                            distance=7500, moving_time=2700, elapsed_time=2700,
                            average_heartrate=140, max_heartrate=peak,
                            laps=laps if owner is other or mode != "unavailable" else None, raw_data={}))
    db.add(Activity(user_id=user.id, sport_type="Run", name="Pic sans passages",
                    start_date=datetime.combine(D - timedelta(days=4), time(10), tzinfo=UTC),
                    distance=7500, moving_time=2700, elapsed_time=2700,
                    average_heartrate=140, max_heartrate=195, raw_data={}))
    await db.flush()
    st._CACHE.clear()


@pytest.mark.parametrize("mode,maximum,climb,own_cap", [
    ("confirmed", 183, 137, None), ("observed", 179, 134, None),
    ("unavailable", None, None, None), ("confirmed", 183, 137, 151),
])
async def test_plan_page_guide_print_and_watch_export_share_the_reference(
        as_user, db_session, test_user, monkeypatch, mode, maximum, climb, own_cap):
    async def today(*args, **kwargs):
        return D

    monkeypatch.setattr(st, "athlete_today", today)
    await _seed_reference(db_session, test_user, mode)
    route_id = await _create_route(as_user)
    if own_cap is not None:
        assert (await as_user.post(f"/api/simulator/routes/{route_id}/params",
                                  data={"hr_cap_climb": own_cap})).status_code == 204
    page = await as_user.get(f"/simulator/routes/{route_id}")
    guide = await as_user.get(f"/partials/simulator/pacing/{route_id}")
    printed = await as_user.get(f"/simulator/routes/{route_id}/print")
    exported = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=csv")
    assert all(response.status_code == 200 for response in (page, guide, printed, exported))
    placeholder = str(climb) if climb is not None else "—"
    assert re.search(r'name="hr_cap_climb"[^>]*placeholder="' + placeholder + '"', page.text)
    ceiling = own_cap or climb
    assert re.search(r'name="hr_cap_climb"[^>]*value="' + (str(ceiling) if ceiling else '') + '"', guide.text)
    data = json.loads(page.text.split('id="plan-data">')[1].split("</script>")[0])
    print_caps = [int(value) for value in re.findall(r"FC (?:≤|≈) (\d+)", printed.text)]
    export_caps = [int(value) for value in re.findall(r"FC(\d+)", exported.text)]
    assert print_caps == export_caps
    if maximum is None:
        assert not print_caps and all(block["hr_cap"] is None for block in data["blocks"])
    else:
        assert print_caps and all(value <= ceiling for value in print_caps)
        assert any(block["hr_cap"] is not None for block in data["blocks"])
    assert "observée sur Strava" not in guide.text

    from app.services import debrief

    actual_debrief, compared = debrief.leg_debrief, []

    def record_comparison(*args, **kwargs):
        result = actual_debrief(*args, **kwargs)
        compared.append(result)
        return result

    monkeypatch.setattr(debrief, "leg_debrief", record_comparison)
    recorded = Activity(user_id=test_user.id, sport_type="TrailRun", name="Course réalisée",
                        start_date=datetime(2026, 10, 2, 21, tzinfo=UTC),
                        distance=30000, moving_time=9000, elapsed_time=9000, average_heartrate=160,
                        splits_metric=_splits(30, lambda _: 300, hr=160), raw_data={})
    db_session.add(recorded)
    await db_session.flush()
    result = await as_user.post(f"/api/simulator/routes/{route_id}/result", data={"activity_id": recorded.id})
    assert result.status_code == 200 and compared
    # Linking a result can recalibrate the profile, so compare the same refreshed plan.
    printed = await as_user.get(f"/simulator/routes/{route_id}/print")
    exported = await as_user.get(f"/api/simulator/routes/{route_id}/pace-export?format=csv")
    print_caps = [int(value) for value in re.findall(r"FC (?:≤|≈) (\d+)", printed.text)]
    export_caps = [int(value) for value in re.findall(r"FC(\d+)", exported.text)]
    compared_caps = [leg["hr_cap"] for leg in compared[-1]["legs"]]
    if maximum is None:
        assert not print_caps and not export_caps and all(cap is None for cap in compared_caps)
        assert not any(leg["hr_over"] for leg in compared[-1]["legs"])
    else:
        assert compared_caps == print_caps == export_caps
        assert all(leg["hr_over"] for leg in compared[-1]["legs"])


async def test_bike_debrief_never_borrows_running_hr_ceilings(
        as_user, db_session, test_user, monkeypatch, cycling_on):
    from app.models.route import Route
    from app.routers import simulator

    test_user.max_heart_rate = 183
    route_id = await _create_bike_route(as_user)
    recorded = Activity(user_id=test_user.id, sport_type="Ride", name="Sortie vélo",
                        start_date=datetime(2026, 10, 2, 8, tzinfo=UTC),
                        distance=30000, moving_time=3600, elapsed_time=3600, average_heartrate=160,
                        splits_metric=_splits(30, lambda _: 120, hr=160), raw_data={})
    db_session.add(recorded)
    await db_session.flush()
    route = await db_session.get(Route, route_id)
    route.result_json = {"activity_id": recorded.id, "activity_name": recorded.name,
                         "total_elapsed_s": 3600}
    await db_session.flush()

    async def no_running_guide(*args, **kwargs):
        raise AssertionError("Bike guidance must not use running HR ceilings")

    monkeypatch.setattr(simulator, "_pacing_guide_for", no_running_guide)
    result = await as_user.get(f"/api/simulator/routes/{route_id}/result")
    assert result.status_code == 200 and "Tronçon par tronçon" in result.text
    assert "FC au-dessus du plafond" not in result.text
