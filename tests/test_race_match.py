"""The race's activity found by itself (app.services.race_match): the rule, then the débrief card
and « Mes courses » using it — no picker anywhere."""

import json
import re
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.route import Route
from app.models.user import User
from app.services.race_match import distance_mismatch, find_race_activity, race_start
from tests.test_race_plan_services import CPS, _course

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
KST = 9 * 3600  # Jeju


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


async def _act(db: AsyncSession, user: User, start: datetime, km: float, sport: str = "TrailRun", race: bool = False, splits: bool = False,
               name: str = "x", offset: int | None = None, flag: int | None = None) -> Activity:
    raw = {"workout_type": flag if flag is not None else 1} if (race or flag is not None) else {}
    if offset is not None:
        raw["utc_offset"] = offset
    a = Activity(user_id=user.id, sport_type=sport, name=name, start_date=start, distance=km * 1000, moving_time=int(km * 600),
                 elapsed_time=int(km * 630), total_elevation_gain=100.0, raw_data=raw,
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


async def _find(db, user, route, now=NOW):
    return await find_race_activity(db, user.id, route, now=now)


# ── the rule ──

def test_race_start_is_the_race_own_clock():
    r = Route(user_id=1, name="r", total_distance_km=30, race_date="2026-10-02", start_hour=21, start_minute=0)
    assert race_start(r) == datetime(2026, 10, 2, 21, 0)  # naive: wherever the race is
    r.race_date, r.start_hour, r.start_minute = "2026-12-05", None, None
    assert race_start(r) == datetime(2026, 12, 5, 6, 0)  # 06:00 when unset
    r.race_date = "pas une date"
    assert race_start(r) is None


@pytest.mark.asyncio
async def test_window_edges_around_an_evening_ultra(db_session: AsyncSession, test_user: User):
    """Start 21:00; activities without a stored offset are read on the athlete's clock, here Paris
    (nothing synced with an offset): 19:00 UTC, window [16:00 UTC, +30 h = 2026-10-04 01:00 UTC]."""
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
async def test_a_race_far_from_paris_is_read_on_its_own_clock(db_session: AsyncSession, test_user: User):
    """The sheet's 21:00 is Jeju's 21:00 (UTC+9 = 12:00 UTC), not Paris's: the activity's stored
    offset places it on race day; a 06:00 start there is still the day before in UTC."""
    hundred = await _route(db_session, test_user, "2026-10-02", 21, 0, km=100)
    jeju = await _act(db_session, test_user, _utc(2026, 10, 2, 12, 0), 101, race=True, offset=KST, name="Transjeju")
    assert (await _find(db_session, test_user, hundred)).id == jeju.id
    fifty = await _route(db_session, test_user, "2026-10-03", 6, 0, km=50)
    dawn = await _act(db_session, test_user, _utc(2026, 10, 2, 21, 0), 50, offset=KST, name="Jeju 50K")
    assert (await _find(db_session, test_user, fifty)).id == dawn.id
    # the same instants read in Paris (no offset stored, nothing synced with one): 14:00 and 23:00 Paris — not race time
    for a in (jeju, dawn):
        a.raw_data = {"workout_type": 1}
    await db_session.flush()
    assert await _find(db_session, test_user, hundred) is None
    assert await _find(db_session, test_user, fifty) is None
    # without its own offset, an activity is read on the athlete's latest one (the sessions synced since)
    await _act(db_session, test_user, _utc(2026, 10, 5, 0, 0), 8, offset=KST, name="shake-out, synced with its offset")
    assert (await _find(db_session, test_user, hundred)).id == jeju.id


@pytest.mark.asyncio
async def test_race_day_before_the_start_is_still_ahead(db_session: AsyncSession, test_user: User):
    route = await _route(db_session, test_user, "2026-10-09", 21, 0)
    act = await _act(db_session, test_user, _utc(2026, 10, 9, 18, 30), 30, name="early")  # 20:30 Paris: in the window
    assert await _find(db_session, test_user, route, now=_utc(2026, 10, 9, 18, 59)) is None  # 20:59 Paris: not started
    assert (await _find(db_session, test_user, route, now=_utc(2026, 10, 9, 19, 0))).id == act.id


@pytest.mark.asyncio
async def test_distance_band_both_sides_and_never_zero(db_session: AsyncSession, test_user: User):
    route = await _route(db_session, test_user, km=30.0)
    await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 25.3)  # −15.7 %
    await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 34.6)  # +15.3 %
    assert await _find(db_session, test_user, route) is None
    short = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 25.6)
    assert (await _find(db_session, test_user, route)).id == short.id
    long = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 34.4)
    assert (await _find(db_session, test_user, route)).id == long.id
    # a manual entry without distance (or a false start whose GPS never locked) is never the race
    route = await _route(db_session, test_user, "2026-09-06", 7, 0)
    zero = await _act(db_session, test_user, _utc(2026, 9, 6, 5, 2), 0, sport="Run", name="saisie manuelle")
    zero.elapsed_time = 19200
    await db_session.flush()
    assert await _find(db_session, test_user, route) is None
    assert distance_mismatch(route, zero) == "Cette activité n'a pas de distance : elle n'est pas associée."
    real = await _act(db_session, test_user, _utc(2026, 9, 6, 5, 5), 30.1, name="the race")
    assert (await _find(db_session, test_user, route)).id == real.id


@pytest.mark.asyncio
async def test_the_longest_wins_then_the_race_flag_then_splits_then_the_first_upload(db_session: AsyncSession, test_user: User):
    route = await _route(db_session, test_user)
    await _act(db_session, test_user, _utc(2026, 10, 2, 19, 0), 28, race=True, splits=True, name="warm-up")
    longest = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 30), 31, name="the race")
    assert (await _find(db_session, test_user, route)).id == longest.id
    # the same distance to the metre: the race flag decides, then the splits
    with_splits = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 30), 31, splits=True, name="watch")
    assert (await _find(db_session, test_user, route)).id == with_splits.id
    flagged = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 30), 31, race=True, name="phone")
    assert (await _find(db_session, test_user, route)).id == flagged.id
    # nothing left to tell them apart: the first upload
    await _act(db_session, test_user, _utc(2026, 10, 2, 19, 30), 31, race=True, name="phone again")
    assert (await _find(db_session, test_user, route)).id == flagged.id
    # on the bike, Strava's race flag is 10, not 1
    bike = await _route(db_session, test_user, km=120, sport="bike")
    await _act(db_session, test_user, _utc(2026, 10, 2, 19, 30), 120, sport="Ride", flag=1, name="a ride flagged 1")
    race_ride = await _act(db_session, test_user, _utc(2026, 10, 2, 19, 30), 120, sport="Ride", flag=10, name="the race")
    assert (await _find(db_session, test_user, bike)).id == race_ride.id


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
async def as_user(client: AsyncClient, test_user: User, db_session, monkeypatch):
    # Endpoint commits are replaced with flushes to keep each test rollback-isolated.
    monkeypatch.setattr(db_session, "commit", db_session.flush)
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


def _race(user: User, name: str = "Jeju 2026", start: datetime = _utc(2026, 10, 2, 19, 5), km: float = 30.0, n: int = 30, **raw) -> Activity:
    return Activity(user_id=user.id, sport_type="TrailRun", name=name, start_date=start, distance=km * 1000, moving_time=600 * n,
                    elapsed_time=600 * n + 900, total_elevation_gain=800.0, raw_data=raw, splits_metric=_splits(n))


@pytest.mark.asyncio
async def test_the_card_links_the_race_by_itself(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _saved(as_user)
    act = _race(test_user, km=30.2, workout_type=1)
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
async def test_the_card_reads_the_activity_on_its_own_clock(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """A 06:00 start in Jeju (21:00 UTC the day before): linked, and dated on race day."""
    route_id = await _saved(as_user, race_date="2026-10-03", start_hour=6, start_minute=0)
    db_session.add(_race(test_user, "Jeju 50K", _utc(2026, 10, 2, 21, 2), utc_offset=KST, workout_type=1))
    await db_session.flush()
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result")
    assert "D'après Jeju 50K" in r.text and "sam. 3 oct. 2026" in r.text


@pytest.mark.asyncio
async def test_mes_courses_shows_the_real_time_after_the_automatic_link(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _saved(as_user)
    db_session.add(_race(test_user))
    await db_session.flush()
    page = (await as_user.get("/simulator")).text
    card = re.split(rf'id="route-(?:card|next)-{route_id}"', page)[1].split("</a>")[0]
    assert "<b>5h15</b>" in card and "+15 min vs plan" in card
    assert (await db_session.get(Route, route_id)).result_json["total_elapsed_s"] == 18900


@pytest.mark.asyncio
async def test_mes_courses_builds_the_profile_once_and_survives_one_bad_link(as_user: AsyncClient, db_session: AsyncSession, test_user: User, monkeypatch):
    from app.services import race_match, race_simulator

    ids = [await _saved(as_user, race_date=d) for d in ("2026-10-02", "2026-09-25", "2026-09-18")]
    for d in ((2026, 10, 2), (2026, 9, 25), (2026, 9, 18)):
        db_session.add(_race(test_user, f"race {d[2]}", _utc(*d, 19, 5)))
    await db_session.flush()
    calls = []
    real = race_simulator.build_athlete_gradient_profile

    async def counting(*a, **kw):
        calls.append(1)
        return await real(*a, **kw)

    monkeypatch.setattr(race_simulator, "build_athlete_gradient_profile", counting)
    page = await as_user.get("/simulator")
    assert page.status_code == 200 and page.text.count(" vs plan<") == 3 and len(calls) == 1
    assert all([(await db_session.get(Route, i)).result_json for i in ids])
    # one activity the link chokes on never takes the page down
    late = await _saved(as_user, race_date="2026-09-11")
    db_session.add(_race(test_user, "odd", _utc(2026, 9, 11, 19, 5)))
    await db_session.flush()

    async def boom(*a, **kw):
        raise ValueError("odd splits")

    monkeypatch.setattr(race_match, "link_result", boom)
    page = await as_user.get("/simulator")
    assert page.status_code == 200 and (await db_session.get(Route, late)).result_json is None


@pytest.mark.asyncio
async def test_not_the_right_activity_sets_it_aside_and_looks_again(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _saved(as_user)
    big = _race(test_user, "Grande boucle", km=31, n=31)
    small = _race(test_user, "Jeju 2026", km=29, n=29)
    db_session.add_all([big, small])
    await db_session.flush()
    assert "D'après Grande boucle" in (await as_user.get(f"/api/simulator/routes/{route_id}/result")).text
    # not the right one: the next candidate is linked
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result/clear")
    assert "D'après Jeju 2026" in r.text
    assert (await db_session.get(Route, route_id)).params_json["result_excluded"] == [big.id]
    # not that one either: honest « not found », both set aside
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result/clear")
    assert "Aucune activité trouvée le ven. 2 oct. 2026 autour de 30 km (2 activités écartées). La synchro est automatique : réessaie un peu plus tard." in r.text
    assert "D'après" not in r.text and "db-activity" not in r.text and "Strava" not in r.text
    assert sorted((await db_session.get(Route, route_id)).params_json["result_excluded"]) == sorted([big.id, small.id])
    assert "2 activités écartées" in (await as_user.get(f"/api/simulator/routes/{route_id}/result")).text


@pytest.mark.asyncio
async def test_compare_from_an_activity_page_links_or_refuses(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _saved(as_user)
    footing = Activity(user_id=test_user.id, sport_type="Run", name="Footing", start_date=_utc(2026, 10, 5, 7, 0), distance=8000.0,
                       moving_time=2400, elapsed_time=2400, total_elevation_gain=50.0, raw_data={})
    # a month later and unflagged: never found by the rule, linked on purpose from its page
    chosen = _race(test_user, "Même parcours en repérage", _utc(2026, 11, 5, 7, 0), km=30.5)
    db_session.add_all([footing, chosen])
    await db_session.flush()
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result?preselect={footing.id}")
    assert "Cette activité fait 8 km, la course 30 km" in r.text and "pas le même parcours" in r.text and "D'après" not in r.text
    assert "db-activity" not in r.text
    # refused: the card says that alone — not « nothing found » (nothing was looked for), not another activity under it
    assert "Aucune activité" not in r.text and "pf-tl-lead" not in r.text
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result?preselect={chosen.id}")
    assert "D'après Même parcours en repérage" in r.text and "pf-tl-err" not in r.text
    assert (await db_session.get(Route, route_id)).result_json["activity_id"] == chosen.id
    # the page arriving from the activity carries the link once: the URL is rewritten to the path on load,
    # so a reload does not link the activity again after « Ce n'est pas la bonne activité »
    page = (await as_user.get(f"/simulator/routes/{route_id}?compare={chosen.id}")).text
    assert f"/result?v=4&preselect={chosen.id}" in page and "history.replaceState(null, '', location.pathname + (" in page
    r = await as_user.post(f"/api/simulator/routes/{route_id}/result/clear")
    assert "Aucune activité trouvée" in r.text and (await db_session.get(Route, route_id)).params_json["result_excluded"] == [chosen.id]
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result")
    assert "Aucune activité trouvée" in r.text and (await db_session.get(Route, route_id)).result_json is None


@pytest.mark.asyncio
async def test_a_refused_choice_does_not_link_the_race_day_activity_unasked(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    route_id = await _saved(as_user)
    footing = Activity(user_id=test_user.id, sport_type="Run", name="Footing", start_date=_utc(2026, 10, 2, 16, 0), distance=8000.0,
                       moving_time=2400, elapsed_time=2400, total_elevation_gain=50.0, raw_data={})
    race = _race(test_user, "the real race")
    db_session.add_all([footing, race])
    await db_session.flush()
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result?preselect={footing.id}")
    assert "pas le même parcours" in r.text and "D'après" not in r.text and "Aucune activité" not in r.text
    assert (await db_session.get(Route, route_id)).result_json is None
    # the next open (no choice carried) finds the race by itself
    assert "D'après the real race" in (await as_user.get(f"/api/simulator/routes/{route_id}/result")).text


@pytest.mark.asyncio
async def test_unlinked_states_no_date_and_race_ahead(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    db_session.add(_race(test_user))
    await db_session.flush()
    r = await as_user.get(f"/api/simulator/routes/{await _saved(as_user, race_date='')}/result")
    assert "Donne la date de ta course pour retrouver ton activité." in r.text and ">Date et départ</button>" in r.text
    assert "switchRouteTab('plan')" in r.text and "openRaceSheet()" in r.text and "db-activity" not in r.text
    r = await as_user.get(f"/api/simulator/routes/{await _saved(as_user, race_date='2099-10-02')}/result")
    assert "Le débrief arrive après la course." in r.text and "D'après" not in r.text and "<button" not in r.text
    # no activity that day: the honest line, with the day in words and the course's distance
    r = await as_user.get(f"/api/simulator/routes/{await _saved(as_user, race_date='2026-09-20')}/result")
    assert "Aucune activité trouvée le dim. 20 sept. 2026 autour de 30 km. La synchro est automatique : réessaie un peu plus tard." in r.text
    assert "écartée" not in r.text and "Strava" not in r.text


@pytest.mark.asyncio
async def test_race_day_before_the_start_says_the_debrief_comes_after(as_user: AsyncClient, db_session: AsyncSession, test_user: User, monkeypatch):
    from app.services import race_match

    route_id = await _saved(as_user, race_date="2026-10-09", start_hour=21, start_minute=0)
    db_session.add(_race(test_user, "early file", _utc(2026, 10, 9, 18, 30)))
    await db_session.flush()
    # one clock for the card: 14:00 Paris on race day, the start at 21:00
    monkeypatch.setattr(race_match, "not_run_yet", lambda route, now, offset: race_match.race_start(route) > datetime(2026, 10, 9, 14, 0))
    r = await as_user.get(f"/api/simulator/routes/{route_id}/result")
    assert "Le débrief arrive après la course." in r.text and "Aucune activité" not in r.text and "D'après" not in r.text
    monkeypatch.setattr(race_match, "not_run_yet", lambda route, now, offset: False)
    assert "D'après early file" in (await as_user.get(f"/api/simulator/routes/{route_id}/result")).text


@pytest.mark.asyncio
async def test_a_bike_plan_save_keeps_the_activities_set_aside(as_user: AsyncClient, db_session: AsyncSession, test_user: User, cycling_on):
    r = await as_user.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(CPS[:2]), "name": "Gran fondo",
        "race_date": "2026-10-02", "start_hour": 8, "start_minute": 0, "sport_type": "bike",
    })
    route_id = r.json()["id"]
    route = await db_session.get(Route, route_id)
    route.params_json = {**(route.params_json or {}), "result_excluded": [7]}
    await db_session.flush()
    r = await as_user.post(f"/api/simulator/routes/{route_id}/bike", data={
        "target_power_watts": 200, "rider_weight_kg": 70, "bike_weight_kg": 8, "cda": 0.32, "crr": 0.005,
        "race_date": "2026-10-02", "start_time": "08:30", "wind_mode": "none", "target_h": 1, "target_m": 20, "stop_minutes": 2,
    })
    assert r.status_code == 200
    db_session.expire_all()
    assert (await db_session.get(Route, route_id)).params_json["result_excluded"] == [7]
