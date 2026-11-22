"""Coaching, the profiles (docs/coaching.md › « Les profils »): three intents chosen in one tap — « Garder la forme »,
« Progresser », « Préparer une course » (« pour finir » without a time aimed at, « pour performer » with one: read,
never asked). Each intent's rules, as properties over many generated plans; a plan made before the profiles keeps
the rules it was made with."""
import itertools
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.coaching import CoachingPlan
from app.services import coaching as co
from app.services import coaching_plan as cp
from app.services import coaching_view as cv
from app.services import sante_training
from tests.test_coaching import HTMX, _as, _rows, beta, people  # noqa: F401

TODAY = date(2026, 11, 21)  # a Saturday
M0 = cp.monday_of(TODAY)
RACE = M0 + timedelta(weeks=14, days=5)


def _inp(days, base, *, intent, race=None, objective=False, long_day=None, minutes=55.0):
    goal = cp.Goal(race, minutes, False, None, "10 km", "objectif" if objective else "approx") if race else cp.Goal()
    return cp.Inputs(tuple(days), cp.default_long_day(days) if long_day is None else long_day, base, M0, goal, {},
                     intent=intent)


def _quality(week):
    return [s for s in week.sessions if s.kind in (cp.QUALITY, cp.HILLS)]


def _reps(session):
    return session.structure["steps"][1]["reps"]


# ── which rules, read from the intent and the race ──────────────────────────

def test_a_race_is_prepared_to_finish_without_a_time_aimed_at_and_to_perform_with_one():
    assert cp.aim(cp.Goal()) is None
    assert cp.aim(cp.Goal(RACE, 55, False, None, "", "approx")) == cp.FINIR
    assert cp.aim(cp.Goal(RACE, 55, False, None, "", "estimation")) == cp.FINIR, "the simulator's estimate"
    assert cp.aim(cp.Goal(RACE, 52, False, None, "", "objectif")) == cp.PERFORMER
    assert cp.rule_set(_inp((1, 3, 6), 120, intent=None)) == cp.BEFORE, "a plan made before the profiles"
    assert cp.rule_set(_inp((1, 3, 6), 120, intent=cp.ENTRETIEN)) == cp.HOLD
    assert cp.rule_set(_inp((1, 3, 6), 120, intent=cp.PROGRESSER)) == cp.ONE
    assert cp.rule_set(_inp((1, 3, 6), 120, intent=cp.COURSE, race=RACE)) == cp.ONE
    assert cp.rule_set(_inp((1, 3, 6), 120, intent=cp.COURSE, race=RACE, objective=True)) == cp.TODAY
    assert cp.rule_set(_inp((1, 3, 6), 120, intent=cp.COURSE)) == cp.ONE, "its race behind: as « Progresser »"


# ── « Garder la forme » ─────────────────────────────────────────────────────

@pytest.mark.parametrize("days", [d for n in (2, 3, 4, 5) for d in itertools.combinations(range(7), n)][::3])
@pytest.mark.parametrize("base", (60, 90, 180, 300))
def test_garder_la_forme_holds_the_volume_without_a_lighter_week_and_one_short_session(days, base):
    inp = _inp(days, base, intent=cp.ENTRETIEN)
    weeks = cp.build(inp, TODAY)
    assert len(weeks) == cp.BLOCK_WEEKS and {w.phase for w in weeks} == {"entretien"}
    targets = [w.target for w in weeks]
    assert not any(w.light for w in weeks), "no lighter week: nothing rises, nothing to absorb"
    if base >= cp.FORME_FLOOR:
        assert set(targets) == {cp.round5(base)}, "the volume held, every week"
    else:  # +10 % a week up to the floor, then held
        assert targets[0] == base and targets == sorted(targets) and targets[-1] == cp.FORME_FLOOR
        assert all(b <= a * (1 + cp.GROWTH) + 5 for a, b in zip(targets, targets[1:]))
    for w in weeks:
        assert len(_quality(w)) <= 1
        for s in _quality(w):
            assert _reps(s) == cp.TEMPLATES[s.structure["key"]][4], "its fewest repetitions, never more"
        assert all(s.minutes <= cp.FORME_LONG_CAP for s in w.sessions if s.kind == cp.LONG)
        assert sum(cp.hard_minutes(s.structure) for s in w.sessions) <= cp.HARD_SHARE * w.minutes + 1e-9


def test_garder_la_forme_keeps_a_quality_session_each_week_at_three_days():
    weeks = cp.build(_inp((1, 3, 6), 150, intent=cp.ENTRETIEN), TODAY)
    keys = [s.structure["key"] for w in weeks for s in _quality(w)]
    assert len(keys) >= 10 and set(keys) <= {"fartlek", "cotes_courtes", "seuil"}


# ── « Progresser » and a race « pour finir »: one quality session at most ───

@pytest.mark.parametrize("intent,race,objective,most", [
    (cp.PROGRESSER, None, False, 1), (cp.COURSE, RACE, False, 1), (cp.COURSE, RACE, True, 2), (None, None, False, 2),
    (None, RACE, False, 2)])
def test_a_second_quality_session_is_kept_for_a_time_aimed_at(intent, race, objective, most):
    """Five days and a base of 4 h: today's rules give two quality sessions; « Progresser » and a race without a
    time aimed at keep one (no study compared them: the least supported addition goes first); a plan made before
    the profiles keeps its two."""
    weeks = cp.build(_inp((0, 1, 3, 4, 6), 300, intent=intent, race=race, objective=objective), TODAY)
    assert max(len(_quality(w)) for w in weeks) == most


# ── what the screens say ────────────────────────────────────────────────────

def test_each_intent_has_its_line_and_the_race_says_finir_or_performer():
    assert [k for k, *_ in cv.INTENT_CARDS] == list(cp.INTENTS)
    assert all(line.endswith(".") and len(line) < 120 for *_, line in cv.INTENT_CARDS)
    finish = cv.first_week((1, 3, 6), 6, 120, cp.Goal(RACE, 55, False, None, "10 km", "approx"), TODAY,
                           intent=cp.COURSE)
    assert cv.AIM_LINE[cp.FINIR] in finish["lines"]
    perform = cv.first_week((1, 3, 6), 6, 120, cp.Goal(RACE, 52, False, None, "10 km", "objectif"), TODAY,
                            intent=cp.COURSE)
    assert cv.AIM_LINE[cp.PERFORMER] in perform["lines"]


# ── the screens ─────────────────────────────────────────────────────────────

@pytest.fixture
def clock(monkeypatch):
    class Clock:
        day = date(2026, 11, 22)

    async def today(*a, **k):
        return Clock.day
    monkeypatch.setattr(sante_training, "athlete_today", today)
    return Clock


def _bars(text: str) -> list[str]:
    chart = text.split('id="coach-preview"')[1].split("</div>")[0]
    return re.findall(r"<small>(.*?)</small>", chart)


async def test_the_start_screen_offers_three_intents_in_one_tap_and_the_preview_follows_each(client, people, beta,
                                                                                          db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    page = (await client.get("/coaching")).text
    for key, title, _icon, line in cv.INTENT_CARDS:
        assert f'name="intent" value="{key}"' in page and title in page
    assert re.findall(r'name="intent" value="(\w+)"[^>]*? checked', page) == [cp.PROGRESSER]
    assert cv.INTENT_CARDS[1][3] in page.split('id="coach-intent-line"')[1].split("</p>")[0]
    query = {"days": ["1", "3", "5", "6"], "long_day": "6", "base": "300", "goal": "none"}
    for intent in cp.INTENTS[:2]:
        r = await client.get("/coaching/apercu", params={**query, "intent": intent}, headers=HTMX)
        pv = cv.first_week((1, 3, 5, 6), 6, 300, cp.Goal(), clock.day, intent=intent)
        assert r.status_code == 200 and _bars(r.text) == [b["duration"] for c in pv["cols"] for b in c["bars"]]
    r = await client.get("/coaching/apercu", params={**query, "intent": cp.COURSE}, headers=HTMX)
    assert r.status_code == 200 and "Choisis ta course, ou ajoute-la." in r.text
    assert await db_session.scalar(select(CoachingPlan.id)) is None


async def test_garder_la_forme_is_kept_on_the_plan_and_said_in_the_header(client, people, beta, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    r = await client.post("/coaching/plan", data={"intent": cp.ENTRETIEN, "goal": "new", "race_name": "Ignorée",
                                                  "race_date": "2027-04-24", "race_km": "10", "days": ["1", "3", "6"],
                                                  "long_day": "6", "base": "150"})
    assert r.status_code == 303
    plan = (await db_session.execute(select(CoachingPlan))).scalar_one()
    assert (plan.intent, plan.route_id, plan.race_name) == (cp.ENTRETIEN, None, None), "no race aimed at"
    page = (await client.get("/coaching")).text
    assert "<b>Garder la forme</b>" in page and "Entretien" in page
    rows = [r for r in await _rows(db_session, athlete) if r.kind != cp.RACE]
    weeks: dict = {}
    for row in rows:
        weeks[cp.monday_of(row.day)] = weeks.get(cp.monday_of(row.day), 0) + row.minutes
    full = [m for m in weeks if m > cp.monday_of(clock.day)]
    assert full and {weeks[m] for m in full} == {150}, "the same volume every week"
    redo = (await client.get("/coaching?refaire=1")).text
    assert re.findall(r'name="intent" value="(\w+)"[^>]*? checked', redo) == [cp.ENTRETIEN]


async def test_preparer_une_course_needs_a_race_and_an_unknown_intent_is_refused(client, people, beta, db_session,
                                                                                clock):
    _as(client, people.athlete)
    base = {"days": ["1", "3", "6"], "long_day": "6", "base": "150"}
    r = await client.post("/coaching/plan", data={**base, "intent": cp.COURSE, "goal": "none"})
    assert r.status_code == 200 and "Choisis ta course, ou ajoute-la." in r.text
    r = await client.post("/coaching/plan", data={**base, "intent": "marathon", "goal": "none"})
    assert r.status_code == 200 and "garder la forme, progresser ou préparer une course" in r.text
    assert await db_session.scalar(select(CoachingPlan.id)) is None


async def test_a_plan_made_before_the_profiles_keeps_its_rules(client, people, beta, db_session, clock):
    """A screen opened before the profiles sends no intent: the plan keeps today's rules (intent None) — and so does
    every plan made before this change, at its next reading: nothing of its sessions moves. « Refaire le plan »
    proposes the intent it already is."""
    athlete = people.athlete
    _as(client, athlete)
    data = {"goal": "none", "days": ["0", "1", "3", "4", "6"], "long_day": "6", "base": "300"}
    assert (await client.post("/coaching/plan", data=data)).status_code == 303
    plan = (await db_session.execute(select(CoachingPlan))).scalar_one()
    assert plan.intent is None
    before = [(r.day, r.kind, r.minutes, r.structure) for r in await _rows(db_session, athlete)]
    weeks = cp.build(cp.Inputs((0, 1, 3, 4, 6), 6, 300, plan.start_monday, cp.Goal(), {}), clock.day)
    assert max(len(_quality(w)) for w in weeks) == 2, "two quality sessions, as before"
    await client.get("/coaching")
    assert [(r.day, r.kind, r.minutes, r.structure) for r in await _rows(db_session, athlete)] == before
    redo = (await client.get("/coaching?refaire=1")).text
    assert re.findall(r'name="intent" value="(\w+)"[^>]*? checked', redo) == [cp.PROGRESSER]
    assert co.inputs(plan, cp.Goal()).intent is None
