"""Coaching, a race without a GPX file (docs/coaching.md › « Une course sans fichier »): « Ajouter une course » on the
start screen — a name, a date, a distance, optionally a time aimed at; nothing else — stored on the plan. The plan runs
on the time aimed at, else on race_prep.expected_s's rough guess (the fallback a Route without a prediction already
gets: no second estimator). The header, the race day, the calendar feed and the follower's view show it as they show
a Route; nothing links to a race page that does not exist. « Refaire le plan » offers it again beside a course imported
since; the account's export carries it."""
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.dependencies import get_current_user
from app.models.coaching import CoachingLink, CoachingPlan
from app.services import coaching as co
from app.services import coaching_plan as cp
from app.services import coaching_view as cv
from app.services import race_prep as rp
from app.services import sante_training
from tests.test_coaching import HTMX, _as, _rows, beta, people  # noqa: F401
from tests.test_coaching_goal import _race

TODAY = date(2026, 11, 22)  # a Sunday: the day the owner asked for it
ANNECY = date(2027, 4, 24)  # a Saturday
NAME = "10 km du lac d’Annecy"
NBSP = " "


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        day = TODAY

    async def today(*a, **k):
        return Clock.day
    monkeypatch.setattr(sante_training, "athlete_today", today)
    return Clock


def _story(**more) -> dict:
    """The owner's story: a new account, « 10 km du lac d’Annecy » on 24 April 2027, 10 km, no GPX file, two days."""
    return {"goal": "new", "race_name": NAME, "race_date": ANNECY.isoformat(), "race_km": "10", "race_h": "",
            "race_min": "", "days": ["1", "6"], "long_day": "6", "base": "120", **more}


async def _plan_of(db, user) -> CoachingPlan:
    return (await db.execute(select(CoachingPlan).where(CoachingPlan.user_id == user.id)
                             .execution_options(populate_existing=True))).scalar_one()


# ── what is typed (pure) ────────────────────────────────────────────────────

def test_a_race_needs_a_name_a_date_to_come_and_a_distance_and_takes_an_optional_time():
    assert co.clean_race(" 10 km  du lac ", "2027-04-24", "10", "", "", TODAY) == ("10 km du lac", ANNECY, 10.0, None)
    assert co.clean_race("Semi", ANNECY, "21,1", "1", "45", TODAY) == ("Semi", ANNECY, 21.1, 105 * 60)
    assert co.clean_race("10 km", ANNECY, "10", "", "52", TODAY)[3] == 52 * 60, "minutes alone"
    for args, said in ((("", ANNECY, "10", "", ""), "nom"), (("X", "", "10", "", ""), "date"),
                       (("X", "24/04/2027", "10", "", ""), "date"), (("X", TODAY - timedelta(days=1), "10", "", ""), "à venir"),
                       (("X", TODAY + timedelta(days=800), "10", "", ""), "deux ans"), (("X", ANNECY, "", "", ""), "distance"),
                       (("X", ANNECY, "dix", "", ""), "distance"), (("X", ANNECY, "0.5", "", ""), "entre 1 et 400"),
                       (("X", ANNECY, "10", "1", "75"), "heures et minutes"), (("X", ANNECY, "10", "", "5"), "10 minutes")):
        with pytest.raises(co.Invalid, match=said):
            co.clean_race(*args, TODAY)
    assert len(co.clean_race("x" * 200, ANNECY, "10", "", "", TODAY)[0]) == co.RACE_NAME_MAX
    assert co.clean_race("Aujourd’hui", TODAY, "5", "", "", TODAY)[1] == TODAY, "a race today is still to come"


def test_the_plan_runs_on_the_time_aimed_at_else_on_the_rough_guess_of_the_race_page():
    rough = co.custom_goal(NAME, ANNECY, 10.0, None)
    assert rough.race_day == ANNECY and rough.name == NAME and rough.route_id is None
    assert rough.time_kind == co.ROUGH and not rough.trail and rough.climb_rate is None, "no D+ known: no climb"
    stub = type("R", (), {"target_time_s": None, "total_distance_km": 10.0, "total_elevation_gain": 0,
                          "sport_type": "road"})()
    assert rough.race_minutes == rp.expected_s(stub) / 60 == pytest.approx(54.5, abs=0.1), "race_prep's 11 km/h"
    aimed = co.custom_goal(NAME, ANNECY, 10.0, 52 * 60)
    assert (aimed.race_minutes, aimed.time_kind) == (52, co.OBJECTIVE)
    assert cv.goal_time(rough) == {"label": "55 min", "word": "Environ", "href": None}, "no race page to link to"
    assert cv.goal_time(aimed) == {"label": "52 min", "word": "Objectif", "href": None}
    assert co.custom_goal(NAME, None, 10.0, None) == cp.Goal() == co.custom_goal(NAME, ANNECY, 0, None)


# ── the owner's story, end to end ───────────────────────────────────────────

async def test_a_new_account_adds_its_race_without_a_file_and_gets_a_plan_to_it(client, people, beta, db_session,
                                                                                 clock):
    athlete = people.athlete
    _as(client, athlete)
    page = (await client.get("/coaching")).text
    assert "Ajouter une course" in page and 'name="race_name"' in page and 'name="race_km"' in page
    assert 'type="date"' in page and f'min="{TODAY.isoformat()}"' in page
    # the week drawn as the race is typed (nothing written)
    pv = await client.get("/coaching/apercu", params=_story(), headers=HTMX)
    assert pv.status_code == 200 and "Ta première semaine" not in pv.text and "pf-coach-bar-s" in pv.text
    assert await db_session.scalar(select(CoachingPlan.id)) is None
    missing = await client.get("/coaching/apercu", params=_story(race_name=""), headers=HTMX)
    assert missing.status_code == 200 and "Donne un nom à ta course." in missing.text
    # made
    r = await client.post("/coaching/plan", data=_story())
    assert r.status_code == 303 and r.headers["location"] == "/coaching"
    plan = await _plan_of(db_session, athlete)
    assert (plan.route_id, plan.race_name, plan.race_date, plan.race_km, plan.race_target_s) == (
        None, NAME, ANNECY, 10.0, None)
    rows = await _rows(db_session, athlete)
    race = [r for r in rows if r.kind == cp.RACE]
    assert [(r.day, r.minutes, r.structure["text"]) for r in race] == [(ANNECY, 55, NAME)]
    assert max(r.day for r in rows) == ANNECY and {r.day.weekday() for r in rows if r.kind != cp.RACE} <= {1, 6}
    # the header: the race, its countdown, its time — no link to a page that does not exist
    page = (await client.get("/coaching")).text
    goal = re.search(r'<h2 class="pf-coach-goal" id="coach-road-h">(.*?)</h2>', page, re.S).group(1)
    assert NAME.replace("’", "&#39;") in goal or NAME in goal
    assert '<span class="sr-only">Environ </span>55 min' in goal and "/simulator/routes/" not in page
    day = (await client.get(f"/coaching?d={ANNECY.isoformat()}")).text
    assert "Course" in day and "Voir le plan de course" not in day


async def test_the_race_day_shows_in_the_feed_and_on_the_followers_page(client, people, beta, db_session, clock):
    athlete, coach = people.athlete, people.coach
    _as(client, athlete)
    await client.post("/coaching/plan", data=_story(race_h="0", race_min="52"))
    plan = await _plan_of(db_session, athlete)
    assert plan.race_target_s == 52 * 60
    client._transport.app.dependency_overrides.pop(get_current_user)
    feed = (await client.get(f"/coaching/cal/{plan.feed_token}.ics")).text
    event = feed.split("DTSTART;VALUE=DATE:20270424")[1].split("END:VEVENT")[0]
    assert "SUMMARY:Course 52 min" in event and "10 km du lac d’Annecy" in event
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    await db_session.flush()
    _as(client, coach)
    page = (await client.get(f"/coaching/athletes/{athlete.id}")).text
    assert "10 km du lac d" in page and '<span class="sr-only">Objectif </span>52 min' in page and "/simulator/routes/" not in page
    listing = (await client.get("/coaching/athletes")).text
    assert "10 km du lac d" in listing


async def test_a_course_imported_later_is_offered_by_refaire_le_plan(client, people, beta, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    await client.post("/coaching/plan", data=_story())
    redo = (await client.get("/coaching?refaire=1")).text
    assert re.search(r'value="race" checked>.*?10 km du lac d', redo, re.S), "its own race, chosen again"
    route = await _race(db_session, athlete, ANNECY, course=False, name="10 km du lac d’Annecy (parcours)",
                        sport="road")
    redo = (await client.get("/coaching?refaire=1")).text
    assert f'value="{route.id}"' in redo and 'value="race"' in redo
    r = await client.post("/coaching/plan", data={"goal": str(route.id), "days": ["1", "6"], "long_day": "6",
                                                  "base": "120"})
    assert r.status_code == 303
    plan = await _plan_of(db_session, athlete)
    assert plan.route_id == route.id and (plan.race_name, plan.race_date, plan.race_km) == (None, None, None)
    race = [r for r in await _rows(db_session, athlete) if r.kind == cp.RACE]
    assert len(race) == 1 and race[0].structure["text"] == route.name


async def test_a_refused_race_answers_200_with_what_was_typed(client, people, beta, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    r = await client.post("/coaching/plan", data=_story(race_km="", race_name="Mon 10 km"))
    assert r.status_code == 200 and "Indique la distance de ta course, en km." in r.text
    assert 'value="Mon 10 km"' in r.text and 'value="new" id="coach-goal-new" checked' in r.text
    assert await db_session.scalar(select(CoachingPlan.id)) is None
    r = await client.post("/coaching/plan", data={**_story(), "goal": "race"})
    assert r.status_code == 200 and "Cette course n’est plus disponible." in r.text


async def test_once_its_day_is_behind_the_plan_goes_on_without_it(client, people, beta, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    await client.post("/coaching/plan", data=_story(race_date=(TODAY + timedelta(days=20)).isoformat()))
    clock.day = TODAY + timedelta(days=23)
    page = (await client.get("/coaching")).text
    plan = await _plan_of(db_session, athlete)
    assert (plan.race_name, plan.race_date) == (None, None) and plan.goal_state["ended"] == co.PASSED
    assert "10 km du lac d" in page and "le plan continue sans course" in page


async def test_the_export_carries_the_race(client, people, beta, db_session, clock):
    from app.services import data_export

    athlete = people.athlete
    _as(client, athlete)
    await client.post("/coaching/plan", data=_story(race_min="58"))
    plan = (await data_export.coaching(db_session, athlete))["plan"]
    assert (plan["race_name"], plan["race_date"], plan["race_km"], plan["race_target_s"]) == (
        NAME, "2027-04-24", 10.0, 58 * 60)
