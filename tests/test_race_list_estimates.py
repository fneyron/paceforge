"""The race list shows the current prediction, separately from an athlete's objective."""
from copy import deepcopy
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.route import Route, RouteCheckpoint
from app.models.user import User
from app.routers import simulator as sim
from app.services import race_simulator
from tests import test_simulator_routes
from tests.test_race_plan_services import CPS, _course

as_user = test_simulator_routes.as_user


async def save_race(db, user, *, days=30, target=None, name="Prochaine course", course=None):
    course = course if course is not None else _course().model_dump(mode="json")
    route = Route(user_id=user.id, name=name, total_distance_km=30, total_elevation_gain=800,
                  course_json=course, race_date=(date.today() + timedelta(days=days)).isoformat()
                  if days is not None else None, target_time_s=target, start_hour=21, start_minute=15,
                  stop_minutes=7, params_json={"target_v": sim.TARGET_VERSION}, sport_type="trail")
    db.add(route)
    await db.flush()
    for cp in CPS:
        db.add(RouteCheckpoint(route_id=route.id, **cp))
    await db.flush()
    return route


def card(html, route_id):
    return html.split(f'id="route-card-{route_id}"')[1].split("</a>")[0]


@pytest.mark.parametrize("target", [None, 23 * 3600])
async def test_future_card_matches_detail_prediction_without_setting_an_objective(
        as_user, db_session, test_user, target):
    route = await save_race(db_session, test_user, target=target)
    original_course = deepcopy(route.course_json)
    original_params = deepcopy(route.params_json)
    expected = (await sim._plan_bundle(route, db_session, test_user))["estimate_s"]
    html = (await as_user.get("/simulator")).text
    shown = card(html, route.id)
    assert 'class="pf-label block">Estimation</span>' in shown
    assert f'class="pf-big pf-next-v">{sim._hm(expected)}</span>' in shown
    assert "Objectif 23h00" in shown if target else "Fixe ton objectif" in shown
    await db_session.refresh(route)
    assert route.target_time_s == target
    assert route.params_json == original_params and route.course_json == original_course


async def test_list_recomputes_after_stop_and_weather_edits(as_user, db_session, test_user):
    route = await save_race(db_session, test_user)
    first = (await sim._plan_bundle(route, db_session, test_user))["estimate_s"]
    assert sim._hm(first) in card((await as_user.get("/simulator")).text, route.id)
    route.stop_minutes = 25
    route.weather_json = {"heat_factor": 1.15}
    await db_session.flush()
    expected = (await sim._plan_bundle(route, db_session, test_user))["estimate_s"]
    assert expected != first
    assert sim._hm(expected) in card((await as_user.get("/simulator")).text, route.id)
    assert route.target_time_s is None


async def test_one_profile_for_future_and_undated_races_and_no_other_users_routes(
        as_user, db_session, test_user, monkeypatch):
    own = [await save_race(db_session, test_user, days=days) for days in (30, 60, None)]
    completed = await save_race(db_session, test_user, days=-30)
    completed.result_json = {"total_elapsed_s": 6 * 3600}
    point = (await db_session.execute(select(RouteCheckpoint).where(
        RouteCheckpoint.route_id == completed.id).order_by(RouteCheckpoint.distance_km))).scalars().first()
    assert point is not None
    point.target_s = 3600
    other = User(firstname="Other")
    db_session.add(other)
    await db_session.flush()
    foreign = await save_race(db_session, other, name="Private race")
    calls = []
    real = race_simulator.build_athlete_gradient_profile

    async def counted(*args, **kwargs):
        calls.append(args[1])
        return await real(*args, **kwargs)

    monkeypatch.setattr(race_simulator, "build_athlete_gradient_profile", counted)
    html = (await as_user.get("/simulator")).text
    assert calls == [test_user.id]
    assert "Private race" not in html and f'id="route-card-{foreign.id}"' not in html
    assert "Estimation" in card(html, own[0].id)
    assert all("estimation" in card(html, route.id) for route in own[1:])


async def test_a_bad_future_course_keeps_the_rest_of_the_list_usable(as_user, db_session, test_user):
    bad = await save_race(db_session, test_user, days=10,
                          course={"elevation_points": _course().elevation_points})
    good = await save_race(db_session, test_user, days=30)
    response = await as_user.get("/simulator")
    assert response.status_code == 200
    assert "Estimation indisponible" in card(response.text, bad.id)
    assert "estimation" in card(response.text, good.id)
    assert 'pf-next-v">—' not in response.text


async def test_completed_races_are_not_predicted_again(as_user, db_session, test_user, monkeypatch):
    past = await save_race(db_session, test_user, days=-30)
    past.result_json = {"total_elapsed_s": 16 * 3600 + 53 * 60}
    await db_session.flush()

    async def unused(*args, **kwargs):
        raise AssertionError("Completed races do not need a new prediction")

    monkeypatch.setattr(race_simulator, "build_athlete_gradient_profile", unused)
    response = await as_user.get("/simulator")
    assert response.status_code == 200
    assert "16h53" in card(response.text, past.id) and "temps réel" in card(response.text, past.id)
