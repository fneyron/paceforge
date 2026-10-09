"""The race's activity found by itself (app.services.race_match): the rule, then the débrief card
and « Mes courses » using it — no picker anywhere."""

import json
import re
from datetime import date, datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.route import Route
from app.models.user import User
from app.services.race_match import find_race_activity, race_start
from tests.test_race_plan_services import CPS, _course

TODAY = date(2026, 10, 9)


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


async def _act(db: AsyncSession, user: User, start: datetime, km: float, sport: str = "TrailRun", race: bool = False, splits: bool = False, name: str = "x") -> Activity:
    a = Activity(user_id=user.id, sport_type=sport, name=name, start_date=start, distance=km * 1000, moving_time=int(km * 600),
                 elapsed_time=int(km * 630), total_elevation_gain=100.0, raw_data={"workout_type": 1} if race else {},
                 splits_metric=[{"distance": 1000, "moving_time": 600, "elapsed_time": 630} for _ in range(int(km))] if splits else None)
    db.add(a)
    await db.flush()
    return a


async def _route(db: AsyncSession, user: User, race_date: str | None = "2026-10-02", sh: int | None = 21, sm: int | None = 0,
                 km: float = 30.0, sport: str = "trail", params: dict | None = None) -> Route:
    r = Route(user_id=user.id, name="r", total_distance_km=km, race_date=race_date, start_hour=sh, start_minute=sm, sport_type=sport, params_json=params)
    db.add(r)
    await db.flush()
    return r


async def _find(db, user, route):
    return await find_race_activity(db, user.id, route, today=TODAY)


# ── the rule ──

def test_race_start_is_read_in_paris_time():
    r = Route(user_id=1, name="r", total_distance_km=30, race_date="2026-10-02", start_hour=21, start_minute=0)
    assert race_start(r) == _utc(2026, 10, 2, 19, 0)  # CEST: 21:00 Paris = 19:00 UTC
    r.race_date, r.start_hour, r.start_minute = "2026-12-05", None, None
    assert race_start(r) == _utc(2026, 12, 5, 5, 0)  # 06:00 when unset; CET: 05:00 UTC
    r.race_date = "pas une date"
    assert race_start(r) is None


@pytest.mark.asyncio
async def test_window_edges_around_an_evening_ultra(db_session: AsyncSession, test_user: User):
    """Start 21:00 Paris (19:00 UTC): [16:00 UTC, +30 h = 2026-10-04 01:00 UTC]."""
    route = await _route(db_session, test_user)
    inside_early = await _act(db_session, test_user, _utc(2026, 10, 2, 16, 0), 30, name="watch started early")
    assert (await _find(db_session, test_user, route)).id == inside_early.id
    await _act(db_session, test_user, _utc(2026, 10, 2, 15, 59), 31, name="too early")  # longer, but outside
    assert (await _find(db_session, test_user, route)).id == inside_early.id
    late = await _act(db_session, test_user, _utc(2026, 10, 4, 1, 0), 31, name="recorded next day")  # the 30 h edge
    assert (await _find(db_session, test_user, route)).id == late.id
    await _act(db_session, test_user, _utc(2026, 10, 4, 1, 1), 32, name="too late")
    assert (await _find(db_session, test_user, route)).id == late.id


@pytest.mark.asyncio
async def test_paris_clock_not_utc_at_the_date_boundary(db_session: AsyncSession, test_user: User):
    # a race at 00:30 Paris on the 3rd starts on the 2nd in UTC (22:30): its activity has the day before's UTC date
    route = await _route(db_session, test_user, "2026-10-03", 0, 30)
    act = await _act(db_session, test_user, _utc(2026, 10, 2, 22, 40), 30)
    assert (await _find(db_session, test_user, route)).id == act.id
    # a 06:00 CEST start is 04:00 UTC: 01:30 UTC is 2 h 30 before (in), 00:30 UTC is 3 h 30 before (out)
    route = await _route(db_session, test_user, "2026-07-15", 6, 0)
    await _act(db_session, test_user, _utc(2026, 7, 15, 0, 30), 31)
    act = await _act(db_session, test_user, _utc(2026, 7, 15, 1, 30), 30)
    assert (await _find(db_session, test_user, route)).id == act.id


@pytest.mark.asyncio
async def test_distance_band_both_sides(db_session: AsyncSession, test_user: User):
    route = await _route(db_session, test_user, km=30.0)
    await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 25.3)  # −15.7 %
    await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 34.6)  # +15.3 %
    assert await _find(db_session, test_user, route) is None
    short = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 25.6)
    assert (await _find(db_session, test_user, route)).id == short.id
    long = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 34.4)
    assert (await _find(db_session, test_user, route)).id == long.id


@pytest.mark.asyncio
async def test_the_longest_wins_then_the_race_flag_then_splits(db_session: AsyncSession, test_user: User):
    route = await _route(db_session, test_user)
    await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 28, race=True, splits=True, name="warm-up")
    longest = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 30), 31, name="the race")
    assert (await _find(db_session, test_user, route)).id == longest.id
    # the same distance to the metre: Strava's race flag decides, then the splits
    with_splits = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 30), 31, splits=True, name="watch")
    assert (await _find(db_session, test_user, route)).id == with_splits.id
    flagged = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 30), 31, race=True, name="phone")
    assert (await _find(db_session, test_user, route)).id == flagged.id


@pytest.mark.asyncio
async def test_excluded_ids_sport_family_future_race_and_no_date(db_session: AsyncSession, test_user: User):
    a = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 31, name="a")
    b = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 29, name="b")
    await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 30, sport="Ride", name="a ride that day")
    route = await _route(db_session, test_user, params={"result_excluded": [a.id]})
    assert (await _find(db_session, test_user, route)).id == b.id
    route.params_json = {"result_excluded": [a.id, b.id]}
    assert await _find(db_session, test_user, route) is None
    assert await _find(db_session, test_user, await _route(db_session, test_user, "2026-10-10")) is None  # tomorrow: not run yet
    assert (await _find(db_session, test_user, await _route(db_session, test_user, "2026-10-09"))) is None  # today, nothing yet
    assert await _find(db_session, test_user, await _route(db_session, test_user, None)) is None
    assert await _find(db_session, test_user, await _route(db_session, test_user, "")) is None
    # a 100-miler on the bike family finds the ride
    ride = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 31, sport="GravelRide", name="gravel")
    assert (await _find(db_session, test_user, await _route(db_session, test_user, sport="bike"))).id == ride.id


# ── the card and « Mes courses » ──

@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


async def _saved(client: AsyncClient, race_date: str = "2026-10-02", **extra) -> int:
    r = await client.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS), "name": "Jeju test",
        "target_time_s": 5 * 3600, "race_date": race_date, "start_hour": 21, "start_minute": 0, "sport_type": "trail", "stop_minutes": 3, **extra,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _splits(n: int = 30) -> list[dict]:
    # 10:00/km moving, a 15-min stop at km 15: 5h00 moving, 5h15 elapsed
    return [{"distance": 1000, "moving_time": 600, "elapsed_time": 600 + (900 if k == 14 else 0)} for k in range(n)]


@pytest.mark.asyncio
async def test_the_card_links_the_race_by_itself(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _saved(as_user)
    act = Activity(user_id=test_user.id, sport_type="TrailRun", name="Jeju 2026", start_date=_utc(2026, 10, 2, 19, 5), distance=30200.0,
                   moving_time=18000, elapsed_time=18900, total_elevation_gain=800.0, raw_data={"workout_type": 1}, splits_metric=_splits())
    db_session.add(act)
    await db_session.flush()
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result")
    assert r.status_code == 200
    assert "D'après Jeju 2026" in r.text and "Réalisé <b>5h15</b>" in r.text and "Tronçon par tronçon" in r.text
    assert "Ce n'est pas la bonne activité" in r.text and "Changer / retirer" not in r.text
    assert "db-activity" not in r.text and "<select" not in r.text and "Comparer</button>" not in r.text
    rj = (await db_session.get(Route, route_id)).result_json
    assert rj["activity_id"] == act.id and rj["total_elapsed_s"] == 18900 and "fatigue_tilt" in rj


@pytest.mark.asyncio
async def test_mes_courses_shows_the_real_time_after_the_automatic_link(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _saved(as_user)
    db_session.add(Activity(user_id=test_user.id, sport_type="TrailRun", name="Jeju 2026", start_date=_utc(2026, 10, 2, 19, 5), distance=30000.0,
                            moving_time=18000, elapsed_time=18900, total_elevation_gain=800.0, raw_data={}, splits_metric=_splits()))
    await db_session.flush()
    page = (await as_user.get("/simulator")).text
    card = re.split(rf'id="route-(?:card|next)-{route_id}"', page)[1].split("</a>")[0]
    assert "<b>5h15</b>" in card and "+15 min vs plan" in card
    assert (await db_session.get(Route, route_id)).result_json["total_elapsed_s"] == 18900


@pytest.mark.asyncio
async def test_not_the_right_activity_sets_it_aside_and_looks_again(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _saved(as_user)
    big = Activity(user_id=test_user.id, sport_type="TrailRun", name="Grande boucle", start_date=_utc(2026, 10, 2, 19, 5), distance=31000.0,
                   moving_time=18600, elapsed_time=18600, total_elevation_gain=800.0, raw_data={}, splits_metric=_splits(31))
    small = Activity(user_id=test_user.id, sport_type="TrailRun", name="Jeju 2026", start_date=_utc(2026, 10, 2, 19, 5), distance=29000.0,
                     moving_time=17400, elapsed_time=18300, total_elevation_gain=800.0, raw_data={}, splits_metric=_splits(29))
    db_session.add_all([big, small])
    await db_session.flush()
    assert "D'après Grande boucle" in (await as_user.get(f"/api/simulator/routes/{route_id}/result")).text
    # not the right one: the next candidate is linked
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result/clear")
    assert "D'après Jeju 2026" in r.text
    assert (await db_session.get(Route, route_id)).params_json["result_excluded"] == [big.id]
    # not that one either: honest « not found », both set aside
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result/clear")
    assert "Aucune activité Strava trouvée le ven. 2 oct. 2026 autour de 30 km (2 activités écartées). Strava se synchronise tout seul" in r.text
    assert "D'après" not in r.text and "db-activity" not in r.text
    assert sorted((await db_session.get(Route, route_id)).params_json["result_excluded"]) == sorted([big.id, small.id])
    assert "2 activités écartées" in (await as_user.get(f"/api/simulator/routes/{route_id}/result")).text


@pytest.mark.asyncio
async def test_compare_from_an_activity_page_links_or_refuses(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _saved(as_user)
    footing = Activity(user_id=test_user.id, sport_type="Run", name="Footing", start_date=_utc(2026, 10, 5, 7, 0), distance=8000.0,
                       moving_time=2400, elapsed_time=2400, total_elevation_gain=50.0, raw_data={})
    # a month later and unflagged: never found by the rule, linked on purpose from its page
    chosen = Activity(user_id=test_user.id, sport_type="TrailRun", name="Même parcours en repérage", start_date=_utc(2026, 11, 5, 7, 0), distance=30500.0,
                      moving_time=18000, elapsed_time=18900, total_elevation_gain=800.0, raw_data={}, splits_metric=_splits())
    db_session.add_all([footing, chosen])
    await db_session.flush()
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result?preselect={footing.id}")
    assert "Cette activité fait 8 km, la course 30 km" in r.text and "pas le même parcours" in r.text and "D'après" not in r.text
    assert "Aucune activité Strava trouvée" in r.text and "db-activity" not in r.text
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result?preselect={chosen.id}")
    assert "D'après Même parcours en repérage" in r.text and "pf-tl-err" not in r.text
    assert (await db_session.get(Route, route_id)).result_json["activity_id"] == chosen.id
    # the page arriving from the activity carries the link
    page = (await as_user.get(f"/simulator/routes/{route_id}?compare={chosen.id}")).text
    assert f"/result?v=4&preselect={chosen.id}" in page


@pytest.mark.asyncio
async def test_unlinked_states_no_date_and_race_ahead(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    db_session.add(Activity(user_id=test_user.id, sport_type="TrailRun", name="Jeju 2026", start_date=_utc(2026, 10, 2, 19, 5), distance=30000.0,
                            moving_time=18000, elapsed_time=18900, total_elevation_gain=800.0, raw_data={}, splits_metric=_splits()))
    await db_session.flush()
    r = await as_user.get(f"/api/simulator/routes/{await _saved(as_user, race_date='')}/result")
    assert "Donne la date de ta course pour retrouver ton activité Strava." in r.text and ">Date et départ</button>" in r.text
    assert "switchRouteTab('plan')" in r.text and "openRaceSheet()" in r.text and "db-activity" not in r.text
    r = await as_user.get(f"/api/simulator/routes/{await _saved(as_user, race_date='2099-10-02')}/result")
    assert "Le débrief arrive après la course." in r.text and "D'après" not in r.text and "<button" not in r.text
    # no activity that day: the honest line, with the day in words and the course's distance
    r = await as_user.get(f"/api/simulator/routes/{await _saved(as_user, race_date='2026-09-20')}/result")
    assert "Aucune activité Strava trouvée le dim. 20 sept. 2026 autour de 30 km. Strava se synchronise tout seul : réessaie après la synchro." in r.text
    assert "écartée" not in r.text
