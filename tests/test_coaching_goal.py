"""Coaching and its race (docs/coaching.md › « L’objectif et le temps de course »): the plan runs on the race page's
objective, else on the simulator's own estimate (one source: routers.simulator.race_times), said once in the header;
the race page links to the plan; a trail race gives the long runs a climb to aim at; a change on the race page is
followed; a race deleted or behind leaves a plan « Progresser, sans course » from the next week."""
import re
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.activity import Activity
from app.models.coaching import CoachingPlan, CoachingSession
from app.models.route import Route, RouteCheckpoint
from app.routers import simulator
from app.services import coaching as co
from app.services import coaching_ics as ics
from app.services import coaching_plan as cp
from app.services import coaching_view as cv
from app.services import sante_training
from app.services.viz import hm
from tests.test_coaching import HTMX, _as, _plan, _rows, beta, people  # noqa: F401
from tests.test_race_plan_services import CPS, _course

NBSP = " "


@pytest.fixture
def clock(monkeypatch):
    """A « today » the test moves: `clock.day = …` (the pages and the plan read sante_training.athlete_today)."""
    class Clock:
        day = datetime.now(timezone.utc).date()

    async def today(*a, **k):
        return Clock.day
    monkeypatch.setattr(sante_training, "athlete_today", today)
    return Clock


async def _race(db, user, day: date, *, course: bool = True, target: int | None = None, name="Trail des Crêtes",
                sport="trail") -> Route:
    """A saved race; `course`: with its GPX course and aid stations (the simulator then predicts it)."""
    c = _course()
    route = Route(user_id=user.id, name=name, course_json=c.model_dump() if course else None,
                  total_distance_km=c.total_distance_km, total_elevation_gain=c.total_elevation_gain,
                  total_elevation_loss=c.total_elevation_loss, target_time_s=target, start_hour=6, start_minute=0,
                  race_date=day.isoformat(), sport_type=sport,
                  params_json={"target_v": simulator.TARGET_VERSION} if target else None)
    db.add(route)
    await db.flush()
    if course:
        for point in CPS:
            db.add(RouteCheckpoint(route_id=route.id, **point))
        await db.flush()
    return route


PROGRESS = '<b class="pf-coach-lead">Progresser</b> <span class="pf-coach-race">sans course</span>'


def _goal_line(page: str) -> str:
    return re.search(r'<h2 class="pf-coach-goal" id="coach-road-h">(.*?)</h2>', page, re.S).group(1)


def _phase(page: str) -> str | None:
    """The phase of the week shown, said beside the week's total."""
    found = re.search(r'<span class="pf-coach-phase">(.*?)</span>', page)
    return found.group(1) if found else None


# ── the objective, else the simulator's estimate ────────────────────────────

async def test_the_plan_runs_on_the_race_pages_estimate_then_on_its_objective(client, people, db_session, clock):
    """No objective: the simulator's own estimate for this athlete, the very number the race page prints, said once
    in the header with the way to the race page. An objective set on the race page takes its place at the next
    reading, and the plan follows."""
    athlete = people.athlete
    _as(client, athlete)
    route = await _race(db_session, athlete, clock.day + timedelta(days=75))
    objective, estimate = await simulator.race_times(db_session, athlete.id, route.id)
    shown = (await simulator._build_route_context(route, db_session, athlete.id))
    assert objective is None and estimate == shown["estimate_s"] > 0, "the race page's own « Estimation »"
    assert abs(estimate - 3600 * (route.total_distance_km + route.total_elevation_gain / 100) / 7) > 600, \
        "not the rough guess from the distance and the D+"
    plan = await _plan(client, db_session, athlete, goal=str(route.id))
    page = (await client.get("/coaching")).text
    line = _goal_line(page)
    assert f'<a class="pf-coach-goal-time" href="/simulator/routes/{route.id}">Estimation{NBSP}{hm(estimate / 60)}</a>' in line
    assert page.count("Estimation") == 1 and "Objectif" not in line, "said once, in the header"
    race = next(r for r in await _rows(db_session, athlete) if r.kind == cp.RACE)
    assert race.minutes == round(estimate / 60)
    state = await co.sync(db_session, plan, athlete)
    assert (state.goal.time_kind, state.goal.race_minutes) == (co.ESTIMATE, estimate / 60)
    # the athlete sets an objective on the race page (today's page marks its saves): well under the estimate
    target = int(estimate * 0.6) // 60 * 60
    route.target_time_s, route.params_json = target, {"target_v": simulator.TARGET_VERSION}
    await db_session.flush()
    page = (await client.get("/coaching")).text
    assert f">Objectif{NBSP}{hm(target / 60)}</a>" in _goal_line(page) and "Estimation" not in _goal_line(page)
    await db_session.refresh(race)
    assert race.minutes == target // 60, "the race's own session follows"
    state = await co.sync(db_session, plan, athlete)
    assert state.goal.time_kind == co.OBJECTIVE and cp.ceiling(state.goal, 100) == cp.round5(150 + 0.7 * cp.rule_minutes(state.goal))
    # a coach reads the same words, without the link to a page that is not theirs
    assert cv.header(state, coach=True)["time"] == {"label": f"Objectif{NBSP}{hm(target / 60)}", "href": None}


async def test_without_a_prediction_the_plan_falls_back_to_the_saved_time_or_the_rough_guess(client, people,
                                                                                            db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    bare = await _race(db_session, athlete, clock.day + timedelta(days=60), course=False)
    assert await simulator.race_times(db_session, athlete.id, bare.id) is None, "no course: nothing to predict"
    await _plan(client, db_session, athlete, goal=str(bare.id))
    rough = round(3600 * (bare.total_distance_km + bare.total_elevation_gain / 100) / 7 / 60)
    line = _goal_line((await client.get("/coaching")).text)
    assert f"Environ{NBSP}{hm(rough)}" in line and "Estimation" not in line and "Objectif" not in line
    bare.target_time_s = 5 * 3600
    await db_session.flush()
    assert f"Objectif{NBSP}5h00" in _goal_line((await client.get("/coaching")).text)
    # the simulator fails: the page still answers, on the fallback
    full = await _race(db_session, athlete, clock.day + timedelta(days=90), name="Autre")

    async def broken(*a, **k):
        raise RuntimeError("no model")
    await _plan(client, db_session, athlete, goal=str(full.id))
    plan = await co.get_plan(db_session, athlete.id)
    plan.goal_state = None
    await db_session.flush()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(simulator, "race_times", broken)
        r = await client.get("/coaching")
    assert r.status_code == 200 and f"Environ{NBSP}" in _goal_line(r.text)
    assert await simulator.race_times(db_session, people.coach.id, full.id) is None, "never another account's race"


async def test_the_prediction_is_read_once_a_day_and_again_when_the_race_page_saves(client, people, db_session, clock,
                                                                                   monkeypatch):
    """A prediction reads the whole course: kept on the plan for the day, read again when the race changed."""
    athlete = people.athlete
    _as(client, athlete)
    route = await _race(db_session, athlete, clock.day + timedelta(days=75))
    calls = []
    real = simulator.race_times

    async def counted(db, user_id, route_id):
        calls.append(route_id)
        return await real(db, user_id, route_id)
    monkeypatch.setattr(simulator, "race_times", counted)
    plan = await _plan(client, db_session, athlete, goal=str(route.id))
    for _ in range(3):
        assert (await client.get("/coaching")).status_code == 200
    assert calls == [route.id], "one prediction for the day, whatever the readings"
    assert plan.goal_state["route"] == route.id and plan.goal_state["day"] == clock.day.isoformat()
    route.total_distance_km += 2  # the course re-imported on its page
    await db_session.flush()
    await client.get("/coaching")
    assert len(calls) == 2
    clock.day += timedelta(days=1)  # the next day: the athlete's level may have moved the estimate
    await client.get("/coaching")
    await client.get("/coaching")
    assert len(calls) == 3


# ── the race page's line ────────────────────────────────────────────────────

async def test_the_race_page_links_to_the_plan_or_offers_to_make_one(client, people, db_session, clock, monkeypatch):
    athlete = people.athlete
    _as(client, athlete)
    goal = await _race(db_session, athlete, clock.day + timedelta(days=75))
    other = await _race(db_session, athlete, clock.day + timedelta(days=120), name="Autre course")
    past = await _race(db_session, athlete, clock.day - timedelta(days=5), name="Passée")
    offer = f'href="/coaching?refaire=1&amp;course={other.id}">Préparer cette course avec Coaching ›</a>'
    assert offer in (await client.get(f"/simulator/routes/{other.id}")).text, "no plan yet: the way to make one"
    plan = await _plan(client, db_session, athlete, goal=str(goal.id))
    weeks = (cp.monday_of(clock.day + timedelta(days=75)) - cp.monday_of(clock.day)).days // 7 + 1
    page = (await client.get(f"/simulator/routes/{goal.id}")).text
    assert f'href="/coaching">Plan de coaching · semaine 1 sur {weeks} ›</a>' in page
    assert page.count("Plan de coaching") == 1 and "Préparer cette course avec Coaching" not in page
    plan.start_monday -= timedelta(weeks=4)
    await db_session.flush()
    assert f"Plan de coaching · semaine 5 sur {weeks + 4} ›" in (await client.get(f"/simulator/routes/{goal.id}")).text
    assert offer in (await client.get(f"/simulator/routes/{other.id}")).text
    assert "Coaching" not in (await client.get(f"/simulator/routes/{past.id}")).text.split('id="rpanel-plan"')[1] \
        .split('id="rpanel-tool"')[0], "a race behind: no line"
    # the link opens the onboarding with that race chosen
    form = (await client.get(f"/coaching?refaire=1&course={other.id}")).text
    assert f'name="goal" value="{other.id}" checked' in form and f'name="goal" value="{goal.id}" checked' not in form
    form = (await client.get(f"/coaching?refaire=1&course={past.id}")).text
    assert f'name="goal" value="{goal.id}" checked' in form, "a race that cannot be chosen: the plan's own stays"
    # Coaching switched off: the race page says nothing of it
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", "off")
    assert "oaching" not in (await client.get(f"/simulator/routes/{goal.id}")).text.split('id="rpanel-plan"')[1] \
        .split('id="rpanel-tool"')[0]


# ── a trail race: the climb to aim at ───────────────────────────────────────

async def test_a_trail_race_gives_its_long_runs_a_climb_said_on_the_card_in_the_feed_and_against_the_run(
        client, people, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    clock.day = cp.monday_of(clock.day)  # a Monday: the week's long run is ahead
    route = await _race(db_session, athlete, clock.day + timedelta(days=75), target=5 * 3600)
    plan = await _plan(client, db_session, athlete, goal=str(route.id), base="300")
    rows = await _rows(db_session, athlete)
    longs = [r for r in rows if r.kind == cp.LONG]
    rate = route.total_elevation_gain / 5
    assert longs and all(r.structure.get("climb") for r in longs[:6])
    first = longs[0]
    assert first.structure["climb"] == cp.climb_target(cp.Goal(clock.day, 300, True, climb_rate=rate), first.minutes,
                                                       cp.CLIMB_START)
    taper = cp.taper_start(date.fromisoformat(route.race_date))
    state = await co.sync(db_session, plan, athlete)
    shares = [cp.climb_share(cp.monday_of(r.day), state.inputs) for r in longs if r.day < taper]
    assert shares[0] == cp.CLIMB_START and shares == sorted(shares) and shares[-1] == 1.0, "progressive across the plan"
    for r, share in zip(longs, shares):  # each target is the race's D+ per hour × the week's share, to 50 m
        assert abs(r.structure["climb"] - rate * share * r.minutes / 60) <= cp.CLIMB_STEP / 2
    assert not [r for r in rows if r.kind in (cp.FOOTING, cp.RECUP, cp.RACE) and "climb" in (r.structure or {})]
    target = first.structure["climb"]
    said = f"+{target}{chr(0x202f)}m"
    page = (await client.get(f"/coaching?d={first.day}")).text
    assert f'<p class="pf-coach-s-text">Vise {said}</p>' in page and page.count(said) == 1
    # the calendar feed says it in the session's description
    feed = ics.render([first], clock.day, host="paceforge.example")
    assert f"DESCRIPTION:Vise {said} de dénivelé." in feed.replace("\r\n ", "")
    # done with a recorded run: the run's own climb against the target, each number once
    clock.day = first.day
    db_session.add(Activity(user_id=athlete.id, strava_activity_id=4242, name="Sortie longue", sport_type="TrailRun",
                            start_date=datetime.combine(first.day, time(7), timezone.utc), distance=14000,
                            moving_time=first.minutes * 60, elapsed_time=first.minutes * 60, average_speed=2.4,
                            total_elevation_gain=target - 130, raw_data={"utc_offset": 0}))
    await db_session.flush()
    page = (await client.get(f"/coaching?d={first.day}")).text
    assert f"+{target - 130}{chr(0x202f)}m sur {said} visés" in page and page.count(said) == 1
    # a road race: no climb anywhere
    road = await _race(db_session, athlete, clock.day + timedelta(days=80), target=4 * 3600, name="Marathon",
                       sport="road")
    road.total_elevation_gain = 120
    await db_session.flush()
    await _plan(client, db_session, athlete, goal=str(road.id), base="300")
    assert not [r for r in await _rows(db_session, athlete) if r.status == "planned" and "climb" in (r.structure or {})]
    assert plan.id


# ── a change on the race page is followed ───────────────────────────────────

async def test_a_change_of_date_or_objective_on_the_race_page_moves_the_sessions_nobody_touched(client, people,
                                                                                               db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    route = await _race(db_session, athlete, clock.day + timedelta(days=75), target=4 * 3600)
    plan = await _plan(client, db_session, athlete, goal=str(route.id), base="240")
    rows = await _rows(db_session, athlete)
    pinned = next(r for r in rows if r.day > clock.day + timedelta(days=14) and r.kind == cp.FOOTING)
    r = await client.post(f"/coaching/sessions/{pinned.id}/edit", data={"kind": "footing", "minutes": "95"}, headers=HTMX)
    assert r.status_code == 200
    before = {r.id: (r.day, r.kind, r.minutes) for r in await _rows(db_session, athlete)}
    assert [d for d, k, _ in before.values() if k == cp.RACE] == [clock.day + timedelta(days=75)]
    # on the race page: the race is two weeks later, and aimed at in 9 h instead of 4 h
    route.race_date, route.target_time_s = (clock.day + timedelta(days=89)).isoformat(), 9 * 3600
    await db_session.flush()
    from app.tasks import coaching as task

    assert await task.run(db_session) == 1, "the daily round regenerates (as the page does)"
    after = {r.id: (r.day, r.kind, r.minutes) for r in await _rows(db_session, athlete)}
    assert [(d, m) for d, k, m in after.values() if k == cp.RACE] == [(clock.day + timedelta(days=89), 540)]
    assert after[pinned.id] == (pinned.day, cp.FOOTING, 95), "what a person edited stays"
    assert max(d for d, _, _ in after.values()) == clock.day + timedelta(days=89), "two more weeks of plan"
    state = await co.sync(db_session, plan, athlete)
    assert cp.long_cap(state.goal) == 210 > 130, "the long run's cap follows the objective"
    assert await task.run(db_session) == 1
    assert {r.id: (r.day, r.kind, r.minutes) for r in await _rows(db_session, athlete)} == after, "idempotent"


# ── the race deleted, or behind ─────────────────────────────────────────────

async def test_a_deleted_race_leaves_a_plan_without_a_race_from_the_next_week(client, people, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    clock.day = cp.monday_of(clock.day) + timedelta(days=1)  # a Tuesday
    monday = cp.monday_of(clock.day)
    route = await _race(db_session, athlete, clock.day + timedelta(days=40), target=5 * 3600)
    plan = await _plan(client, db_session, athlete, goal=str(route.id), days=("1", "3", "5", "6"), base="240")
    plan.start_monday = monday - timedelta(weeks=3)
    await db_session.flush()
    await co.sync(db_session, plan, athlete)
    week = [(r.id, r.day, r.kind, r.minutes, r.seq) for r in await _rows(db_session, athlete) if r.day < monday + timedelta(days=7)]
    assert week and any(r.kind == cp.RACE for r in await _rows(db_session, athlete))
    await db_session.delete(route)  # « Supprimer » on the race page (the foreign key empties the plan's route)
    plan.route_id = None
    await db_session.flush()
    page = (await client.get("/coaching")).text
    assert PROGRESS in _goal_line(page) and "Trail des Crêtes" not in _goal_line(page)
    line = "Trail des Crêtes n’est plus dans tes courses : dès lundi, le plan continue sans course."
    assert f'<p class="pf-coach-reason pf-coach-ended">{line}</p>' in page and page.count("continue sans course") == 1
    rows = await _rows(db_session, athlete)
    assert [(r.id, r.day, r.kind, r.minutes, r.seq) for r in rows if r.day < monday + timedelta(days=7)] == week, \
        "this week stays as it was"
    assert not [r for r in rows if r.kind == cp.RACE], "the deleted race's session is gone"
    nxt = monday + timedelta(weeks=1)
    await db_session.refresh(plan)
    assert plan.start_monday == nxt and plan.route_id is None
    state = await co.sync(db_session, plan, athlete)
    assert [w.phase for w in state.weeks] == ["progression"] * cp.BLOCK_WEEKS and state.weeks[0].monday == nxt
    assert sum(r.minutes for r in rows if nxt <= r.day < nxt + timedelta(days=7)) == 240, "week 0 again, at the base"
    assert max(r.day for r in rows) >= nxt + timedelta(weeks=cp.BLOCK_WEEKS - 1)
    assert cv.header(state)["phase"] is None, "no phase said for a week that is no longer the plan's"
    # idempotent, then the line leaves once that first week is over
    snapshot = [(r.id, r.day, r.kind, r.minutes, r.seq) for r in rows]
    await client.get("/coaching")
    assert [(r.id, r.day, r.kind, r.minutes, r.seq) for r in await _rows(db_session, athlete)] == snapshot
    clock.day = nxt + timedelta(days=2)
    page = (await client.get("/coaching")).text
    assert "Trail des Crêtes n’est plus dans tes courses : le plan continue sans course." in page
    assert _phase(page) == "Progression"
    clock.day = nxt + timedelta(weeks=1)
    assert "continue sans course" not in (await client.get("/coaching")).text
    assert (await db_session.execute(select(CoachingPlan.goal_state))).scalar() is None


async def test_a_race_behind_leaves_a_plan_without_a_race_from_the_next_week(client, people, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    clock.day = cp.monday_of(clock.day) + timedelta(days=2)  # a Wednesday
    monday = cp.monday_of(clock.day)
    race_day = monday + timedelta(days=5)  # this Saturday
    route = await _race(db_session, athlete, race_day, target=3 * 3600)
    plan = await _plan(client, db_session, athlete, goal=str(route.id), days=("1", "3", "5", "6"), base="200")
    assert "J‑3</b> <span class=\"pf-coach-race\">Trail des Crêtes</span>" in _goal_line((await client.get("/coaching")).text)
    race = next(r for r in await _rows(db_session, athlete) if r.kind == cp.RACE)
    clock.day = race_day + timedelta(days=1)  # the Sunday after
    page = (await client.get("/coaching")).text
    assert PROGRESS in _goal_line(page)
    assert "Trail des Crêtes est derrière toi : dès lundi, le plan continue sans course." in page
    await db_session.refresh(plan)
    nxt = monday + timedelta(weeks=1)
    assert (plan.route_id, plan.start_monday, plan.goal_state["ended"]) == (None, nxt, co.PASSED)
    rows = await _rows(db_session, athlete)
    assert (await db_session.get(CoachingSession, race.id)).status == "missed", "the race's session stays, in the past"
    assert not [r for r in rows if clock.day <= r.day < nxt], "nothing added to the race's own week"
    ahead = [r for r in rows if r.day >= nxt]
    assert ahead and sum(r.minutes for r in ahead if r.day < nxt + timedelta(days=7)) == 200
    assert (await db_session.get(Route, route.id)) is not None, "the race itself is untouched"
    # the plan not opened for weeks after the race: it went on from the Monday after it
    clock.day = nxt + timedelta(days=3)
    page = (await client.get("/coaching")).text
    assert "est derrière toi : le plan continue sans course." in page and _phase(page) == "Progression"
