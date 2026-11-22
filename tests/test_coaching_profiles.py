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


# ── the level, read from the history (never asked) ──────────────────────────

def _history(weeks: dict[int, list[int]]) -> list:
    """Runs: {weeks before TODAY's week: [minutes of each run, one a day from Monday]}."""
    from types import SimpleNamespace

    return [SimpleNamespace(day=M0 - timedelta(weeks=w) + timedelta(days=i), minutes=m)
            for w, runs in weeks.items() for i, m in enumerate(runs)]


def test_without_any_run_the_level_comes_from_the_volume_announced_never_entraine():
    assert cp.read_level([], TODAY, 60) == cp.Level(cp.DEBUTANT, history=False, base=60)
    assert cp.read_level([], TODAY, 115).level == cp.DEBUTANT
    assert cp.read_level([], TODAY, cp.NOVICE_NO_HISTORY).level == cp.REGULIER
    assert cp.read_level([], TODAY, 900).level == cp.REGULIER, "never « entraîné » without recorded weeks"


def test_debutant_below_eight_weeks_with_a_run_or_a_median_week_under_75_min():
    assert cp.read_level(_history({w: [120] for w in range(1, 8)}), TODAY, 120).level == cp.DEBUTANT, "7 weeks"
    eight = cp.read_level(_history({w: [100] for w in range(1, 9)}), TODAY, 120)
    assert (eight.level, eight.weeks, eight.median) == (cp.REGULIER, 8, 100), "8 weeks, median of 12 with 4 at 0"
    assert cp.read_level(_history({w: [70] for w in range(1, 13)}), TODAY, 120).level == cp.DEBUTANT, "70 min"
    assert cp.read_level(_history({w: [75] for w in range(1, 13)}), TODAY, 120).level == cp.REGULIER, "75 min"
    # this week and the weeks before the twelve are not read; two runs the same day are one day of running
    assert cp.read_level(_history({0: [300] * 3, 13: [300] * 3}), TODAY, 60).history is False
    same_day = _history({w: [40] for w in range(1, 13)}) + _history({w: [40] for w in range(1, 13)})
    read = cp.read_level(same_day, TODAY, 120)
    assert (read.busy, read.median) == (0, 80)


def test_entraine_needs_ten_busy_weeks_four_hours_and_a_ninety_minute_run():
    def level(busy=10, minutes=80, longest=90):
        weeks = {w: ([minutes, minutes, minutes - 10, longest] if w <= busy else [longest, longest])
                 for w in range(1, 13)}
        return cp.read_level(_history(weeks), TODAY, 240)
    trained = level()
    assert (trained.level, trained.busy, trained.median, trained.longest) == (cp.ENTRAINE, 10, 320, 90)
    assert level(busy=9).level == cp.REGULIER, "9 weeks with 3 runs"
    assert level(minutes=50, longest=90).level == cp.REGULIER, "a median week under 4 h (230 min)"
    low = cp.read_level(_history({w: [80, 80, 80] for w in range(1, 13)}), TODAY, 240)
    assert (low.median, low.longest, low.level) == (240, 80, cp.REGULIER), "no run of 1 h 30"


def test_the_level_changes_the_rules_only_by_removing_load():
    days = (0, 1, 3, 4, 6)
    for intent, race, objective in ((cp.PROGRESSER, None, False), (cp.COURSE, RACE, True), (cp.ENTRETIEN, None, False)):
        beginner = cp.build(cp.Inputs(days, 6, 300, M0, _inp(days, 300, intent=intent, race=race,
                                                            objective=objective).goal, {}, intent=intent,
                                      level=cp.DEBUTANT), TODAY)
        for w in beginner:
            assert len({s.day.weekday() for s in w.sessions if s.kind != cp.RACE}) <= cp.NOVICE_MAX_DAYS
            assert len(_quality(w)) == 0 if w.index < cp.NOVICE_EASY_WEEKS else len(_quality(w)) <= 1
            for s in _quality(w):
                assert _reps(s) == cp.TEMPLATES[s.structure["key"]][4]
        assert any(_quality(w) for w in beginner if w.index >= cp.NOVICE_EASY_WEEKS), intent
    # « Progresser », entraîné: today's rules (two from 5 days and 4 h); régulier: one
    trained = cp.build(cp.Inputs(days, 6, 300, M0, cp.Goal(), {}, intent=cp.PROGRESSER, level=cp.ENTRAINE), TODAY)
    usual = cp.build(cp.Inputs(days, 6, 300, M0, cp.Goal(), {}, intent=cp.PROGRESSER, level=cp.REGULIER), TODAY)
    assert max(len(_quality(w)) for w in trained) == 2 and max(len(_quality(w)) for w in usual) == 1
    # a « débutant » prepares a race « pour finir », whatever its time aimed at
    goal = cp.Goal(RACE, 52, False, None, "", "objectif")
    assert cp.rule_set(cp.Inputs(days, 6, 300, M0, goal, {}, intent=cp.COURSE, level=cp.DEBUTANT)) == cp.ONE
    # the level never touches a plan made before the profiles
    assert not cp.novice(cp.Inputs(days, 6, 300, M0, cp.Goal(), {}, intent=None, level=cp.DEBUTANT))


def test_a_debutant_runs_on_four_of_the_days_spread_around_the_week_the_long_runs_first():
    def used(days, long_day):
        return cp.days_used(cp.Inputs(days, long_day, 120, M0, cp.Goal(), {}, intent=cp.PROGRESSER,
                                      level=cp.DEBUTANT))
    assert used((0, 1, 2, 4, 6), 6) == (0, 2, 4, 6)
    assert used((0, 1, 2, 3, 4, 5), 5) == (0, 1, 3, 5)
    assert used((1, 3, 5, 6), 6) == (1, 3, 5, 6), "four days or fewer: all of them"
    assert cp.days_used(cp.Inputs((0, 1, 2, 4, 6), 6, 120, M0, cp.Goal(), {}, intent=cp.PROGRESSER,
                                  level=cp.REGULIER)) == (0, 1, 2, 4, 6)


def test_the_level_line_says_the_level_and_why():
    line = cv.level_line(cp.Level(cp.DEBUTANT, 5, 0, 40, 50, True, 120))
    assert line["lead"] == "Tu débutes" and line["text"].startswith("huit semaines en aisance d")
    assert "12 dernières semaines" in line["why"] and "5 avec une sortie" in line["why"]
    later = cv.level_line(cp.Level(cp.DEBUTANT, 5, 0, 40, 50, True, 120), week=cp.NOVICE_EASY_WEEKS)
    assert "huit semaines" not in later["text"] and "une séance de qualité" in later["text"]
    none = cv.level_line(cp.Level(cp.DEBUTANT, history=False, base=60))
    assert "Aucune sortie enregistrée" in none["why"] and "1h00" in none["why"].replace(" ", "")
    usual = cv.level_line(cp.Level(cp.REGULIER, 11, 2, 130, 70, True, 120))
    assert usual["lead"] == "Tu cours régulièrement" and "11 avec une sortie" in usual["text"] and not usual["why"]
    trained = cv.level_line(cp.Level(cp.ENTRAINE, 12, 11, 300, 120, True, 300))
    assert trained["lead"] == "Tu t’entraînes beaucoup" and "11 avec 3 sorties ou plus" in trained["text"]
    assert cv.level_line(None) is None


async def test_the_level_is_read_when_the_plan_is_made_shown_to_the_athlete_never_to_a_follower(
        client, people, beta, db_session, clock):
    from app.models.coaching import CoachingLink
    from tests.test_coaching import _run

    athlete, coach = people.athlete, people.coach
    _as(client, athlete)
    data = {"intent": cp.PROGRESSER, "goal": "none", "days": ["0", "1", "3", "4", "6"], "long_day": "6", "base": "60"}
    pv = await client.get("/coaching/apercu", params=data, headers=HTMX)
    assert "<b>Tu débutes</b>" in pv.text and "Aucune sortie enregistrée" in pv.text
    assert (await client.post("/coaching/plan", data=data)).status_code == 303
    plan = (await db_session.execute(select(CoachingPlan))).scalar_one()
    assert (plan.level, plan.level_read["history"], plan.level_read["base"]) == (cp.DEBUTANT, False, 60)
    rows = await _rows(db_session, athlete)
    assert len({r.day.weekday() for r in rows}) <= cp.NOVICE_MAX_DAYS
    early = [r for r in rows if r.day < plan.start_monday + timedelta(weeks=cp.NOVICE_EASY_WEEKS)]
    assert early and not [r for r in early if r.kind in (cp.QUALITY, cp.HILLS)], "easy only, eight weeks"
    page = (await client.get("/coaching")).text
    assert '<p class="pf-coach-level"><b>Tu débutes</b>' in page
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    await db_session.flush()
    _as(client, coach)
    followed = (await client.get(f"/coaching/athletes/{athlete.id}")).text
    assert "Tu débutes" not in followed and "pf-coach-level" not in followed
    assert "Tu débutes" not in (await client.get("/coaching/athletes")).text
    # twelve weeks of running later, « Refaire le plan » reads the level again
    clock.day = clock.day + timedelta(weeks=13)
    m0 = cp.monday_of(clock.day)
    db_session.add_all([_run(athlete, m0 - timedelta(weeks=w) + timedelta(days=d), 45, 10 * w + d)
                        for w in range(1, 13) for d in (1, 3, 6)])
    await db_session.flush()
    _as(client, athlete)
    redo = (await client.get("/coaching?refaire=1")).text
    assert "<b>Tu cours régulièrement</b>" in redo
    assert (await client.post("/coaching/plan", data={**data, "base": "135"})).status_code == 303
    await db_session.refresh(plan)
    assert (plan.level, plan.level_read["weeks"], plan.level_read["median"]) == (cp.REGULIER, 12, 135)
