"""Coaching, « Ma séance fixe » (docs/coaching.md › « La séance fixe »): one weekly session the athlete brings — a
club's intervals — on its weekday, at its duration, around which the plan is written. The owner's case week by week,
the profiles beside it, a plan without one unchanged, the screens and the routes."""
import hashlib
import itertools
import json
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.coaching import CoachingPlan
from app.services import coaching as co
from app.services import coaching_plan as cp
from app.services import coaching_start as cst
from app.services import coaching_view as cv
from tests.test_coaching import HTMX, _as, _plan, _rows, _run, beta, people  # noqa: F401

M0 = date(2026, 11, 23)  # a Monday
TRAIL = date(2027, 5, 23)  # a Sunday, 26 weeks on
CLUB = cp.Fixed(2, 75)  # Wednesday, 1 h 15


def _owner(fixed=CLUB, **more) -> cp.Inputs:
    """The owner's case: Tuesday, Wednesday, Friday, Saturday, Sunday; the long run on Sunday; base 5 h; a trail
    race with a time aimed at (« pour performer »), 2 000 m of climb in 6 h."""
    goal = cp.Goal(TRAIL, 360.0, True, 1, "Trail", "objectif", 2000 / 6)
    more.setdefault("adaptations", {})
    return cp.Inputs((1, 2, 4, 5, 6), 6, 300, M0, goal, intent=cp.COURSE, level=cp.REGULIER, fixed=fixed, **more)


def _weeks(inp: cp.Inputs) -> list[cp.Week]:
    """Every week to the race: the plan writes 24 weeks ahead, the last ones are read three weeks on."""
    weeks = {w.monday: w for t in (M0, M0 + timedelta(weeks=3)) for w in cp.build(inp, t)}
    return [weeks[m] for m in sorted(weeks)]


def _fixed(week: cp.Week) -> list[cp.Planned]:
    return [s for s in week.sessions if cp.is_fixed(s.structure)]


def _own_quality(week: cp.Week) -> list[cp.Planned]:
    return [s for s in week.sessions if s.kind in (cp.QUALITY, cp.HILLS) and not cp.is_fixed(s.structure)]


def _hard_days(weeks: list[cp.Week]) -> list[date]:
    return sorted(s.day for w in weeks for s in w.sessions if s.kind in cp.HARD)


# ── the owner's case, week by week ──────────────────────────────────────────

def test_the_owners_club_wednesday_week_by_week():
    weeks = _weeks(_owner())
    assert [w.index for w in weeks] == list(range(26)) and weeks[-1].monday == cp.monday_of(TRAIL)
    race_week = weeks[-1]
    for w in weeks[:-1]:
        club = _fixed(w)
        assert [(s.day.weekday(), s.minutes, s.kind) for s in club] == [(2, 75, cp.QUALITY)], w.monday
        assert club[0].structure["name"] == "Séance fixe" and club[0].structure["text"].startswith(cp.FIXED_TEXT)
        assert "bloc" not in json.dumps(club[0].structure), "no repetition written: its content is the athlete's"
        # no hard session of the plan's on Tuesday or Thursday, beside the club's
        assert not [s for s in w.sessions if s.kind in cp.HARD and s.day.weekday() in (1, 3)], w.monday
        hard = sum(cp.hard_minutes(s.structure) for s in w.sessions)
        assert hard <= cp.HARD_SHARE * w.minutes + 1e-9, (w.monday, hard, w.minutes)
        own = _own_quality(w)
        if w.light or w.phase == "affutage":
            assert own == [] and club[0].structure["note"] == cp.FIXED_NOTE[
                "affutage" if w.phase == "affutage" else "light"]
            assert club[0].structure["hard"] == cp.fixed_hard(75, True) == 9
        else:  # the second quality session stays the plan's, on Friday, never short intervals at speed
            assert [s.day.weekday() for s in own] == [4] and club[0].structure["note"] is None
            assert club[0].structure["hard"] == cp.fixed_hard(75) == 19
            key = own[0].structure["key"]
            assert key == ("cotes_courtes" if w.phase == "base" else key) and key not in ("fartlek", "intervalles")
            if w.phase == "specifique":
                assert key in ("seuil", "cotes_longues")
    # the race week: the plan's reminder stands, the club's session is not written
    assert _fixed(race_week) == [] and [s.structure["key"] for s in _own_quality(race_week)] == ["rappels"]
    assert {s.kind for s in race_week.sessions if s.day == TRAIL} == {cp.RACE}
    # never two hard days side by side, across the weeks too
    days = _hard_days(weeks)
    assert all((b - a).days >= 2 for a, b in zip(days, days[1:], strict=False))
    assert {w.phase for w in weeks if w.light} == {"base", "specifique"}
    assert sorted({s.structure["key"] for w in weeks if w.phase == "specifique" for s in _own_quality(w)}) == [
        "cotes_longues", "seuil"]


def test_the_club_session_counts_for_the_days_ahead_and_is_never_shortened():
    # a short history: the plan's sessions are held to 10 % over the longest run of 30 days; the club's is not
    inp = _owner(recent=((M0 - timedelta(days=3), 40),))
    first = cp.build(inp, M0)[0]
    assert [s.minutes for s in _fixed(first)] == [75], "the athlete's duration"
    assert all(s.minutes <= first.limit for s in first.sessions if not cp.is_fixed(s.structure))
    second = cp.build(inp, M0)[1]
    assert second.limit == cp.session_limit(75), "the club's 75 min are a run of the 30 days before next week"


def test_the_second_quality_session_takes_what_is_left_of_the_hard_budget():
    without = {w.monday: w for w in _weeks(_owner(fixed=None))}
    for w in _weeks(_owner()):
        if _own_quality(w) and _own_quality(without[w.monday]):
            mine = sum(cp.hard_minutes(s.structure) for s in _own_quality(w))
            assert mine + cp.fixed_hard(75) <= cp.HARD_SHARE * w.minutes + 1e-9


# ── the profiles beside it ──────────────────────────────────────────────────

def test_two_days_a_week_with_the_club_session_and_a_long_run():
    inp = cp.Inputs((2, 6), 6, 150, M0, cp.Goal(), {}, intent=cp.PROGRESSER, level=cp.REGULIER,
                    fixed=cp.Fixed(2, 60, "Piste"))
    weeks = cp.build(inp, M0)
    assert cp.ceiling_of(inp, 150) == cp.long_cap_of(inp) + 60, "the week tops at the long run's cap and the club's"
    for w in weeks:
        assert [(s.day.weekday(), s.kind) for s in w.sessions] == [(2, cp.QUALITY), (6, cp.LONG)]
        assert cp.title(w.sessions[0].kind, w.sessions[0].structure) == "Piste"
    assert not cv.side_by_side(inp)
    # its weekday joins the days available when it was not among them
    joined = cp.Inputs((1, 6), 6, 150, M0, cp.Goal(), {}, intent=cp.PROGRESSER, level=cp.REGULIER,
                       fixed=cp.Fixed(3, 60))
    assert cp.days_used(joined) == (1, 3, 6)
    assert {s.day.weekday() for s in cp.build(joined, M0)[1].sessions} == {1, 3, 6}


def test_a_debutant_keeps_the_club_session_at_half_and_gets_no_quality_session_of_the_plans():
    inp = cp.Inputs((0, 1, 2, 4, 5, 6), 6, 90, M0, cp.Goal(), {}, intent=cp.PROGRESSER, level=cp.DEBUTANT,
                    fixed=cp.Fixed(2, 60))
    assert 2 in cp.days_used(inp) and len(cp.days_used(inp)) == cp.NOVICE_MAX_DAYS, "the club's day is kept"
    for w in cp.build(inp, M0):
        assert [s.structure["note"] for s in _fixed(w)] == [cp.FIXED_NOTE["debutant"]]
        assert _own_quality(w) == [], "the athlete decided the club session; the plan adds none"


def test_garder_la_forme_keeps_the_club_session_whole_and_writes_no_other():
    inp = cp.Inputs((1, 2, 4, 6), 6, 180, M0, cp.Goal(), {}, intent=cp.ENTRETIEN, level=cp.REGULIER,
                    fixed=CLUB)
    for w in cp.build(inp, M0):
        assert [s.structure["note"] for s in _fixed(w)] == [None] and _own_quality(w) == []
        assert w.minutes == 180 or w.minutes <= 180


def test_an_eased_week_keeps_the_club_session_with_its_line():
    eased = {(M0 + timedelta(weeks=1)).isoformat(): {"reason": "ressenti", "factor": cp.ADAPT_FACTOR}}
    w = cp.build(_owner(adaptations=eased), M0)[1]
    assert [s.structure["note"] for s in _fixed(w)] == [cp.FIXED_NOTE["light"]] and _own_quality(w) == []
    assert cv.short_title(cp.QUALITY, _fixed(w)[0].structure) == "Séance"


# ── a plan without a fixed session: unchanged ───────────────────────────────

# what the generator wrote before « Ma séance fixe », for 3 840 plans (every intent and level, 2 to 6 days, four
# volumes, no race, a road race, a trail race, with and without real runs and a week eased): the same, byte for byte
BEFORE_FIXED = (3840, "a747478f765e668428a0e14cbb65755d99f11c154f15fb4c50bfeb78dd65c46d")


def test_plans_without_a_fixed_session_write_exactly_what_they_wrote_before():
    today = date(2026, 11, 21)
    m0 = cp.monday_of(today)
    goals = [cp.Goal(), cp.Goal(m0 + timedelta(weeks=10, days=6), 55.0, False, None, "10 km", "objectif"),
             cp.Goal(m0 + timedelta(weeks=20, days=5), 420.0, True, 7, "Trail", "approx", 300.0)]
    profiles = [(None, None)] + [(i, lv) for i in cp.INTENTS for lv in (cp.DEBUTANT, cp.REGULIER, cp.ENTRAINE)]
    recent = ((today - timedelta(days=6), 70), (today - timedelta(days=2), 40))
    adapt = {(m0 + timedelta(weeks=3)).isoformat(): {"reason": "ressenti", "factor": 0.8}}
    h, n = hashlib.sha256(), 0
    for k in (2, 3, 4, 5, 6):
        for days in list(itertools.combinations(range(7), k))[::4]:
            for base in (60, 150, 300, 480):
                for goal in goals:
                    for intent, level in profiles:
                        if (intent == cp.COURSE) != (goal.race_day is not None) and intent is not None:
                            continue
                        for variant in (0, 1):
                            inp = cp.Inputs(days, cp.default_long_day(days), base, m0, goal,
                                            adapt if variant else {}, recent if variant else (),
                                            intent=intent, level=level)
                            for w in cp.build(inp, today):
                                h.update(repr((w.monday, w.index, w.phase, w.target, w.light, w.reason, w.limit,
                                               [(s.day, s.kind, s.minutes, json.dumps(s.structure, sort_keys=True))
                                                for s in w.sessions])).encode())
                            n += 1
    assert (n, h.hexdigest()) == BEFORE_FIXED


# ── the prefilled duration, read on the history ─────────────────────────────

class _Run:
    def __init__(self, day, minutes):
        self.day, self.minutes = day, minutes


def test_the_duration_proposed_is_the_median_of_that_weekdays_runs_over_eight_complete_weeks():
    today = date(2026, 11, 26)  # a Thursday: this week's Wednesday is not read
    wednesdays = [cp.monday_of(today) - timedelta(weeks=w) + timedelta(days=2) for w in range(1, 9)]
    runs = [_Run(d, m) for d, m in zip(wednesdays, (72, 78, 70, 74, 81, 69, 76, 300), strict=True)]
    runs += [_Run(cp.monday_of(today) + timedelta(days=2), 20),  # this week: not complete
             _Run(cp.monday_of(today) - timedelta(weeks=9, days=-2), 10),  # nine weeks back: not read
             _Run(wednesdays[0], 3)]  # two runs that day: one day, their minutes added
    assert cst.usual_minutes(runs, today, 2) == 75, "the median of 75, 78, 70, 74, 81, 69, 76, 300"
    props = cst.fixed_proposals(runs, today)
    assert props[2] == {"minutes": 75, "why": "Lu sur tes mercredis."}
    assert props[3] == {"minutes": cp.FIXED_DEFAULT, "why": "Sans sortie ce jour-là : 1h15 par défaut."}
    assert cst.usual_minutes([_Run(wednesdays[1], 400)], today, 2) == cp.FIXED_RANGE[1]


# ── the screens and the routes ──────────────────────────────────────────────

def test_clean_fixed_says_what_is_wrong():
    assert co.clean_fixed("2", "77", "  Club  du   jeudi ", (1, 3, 6), 6) == cp.Fixed(2, 75, "Club du jeudi")
    assert co.clean_fixed("2", "500", "", (1, 3, 6), 6) == cp.Fixed(2, 180, cp.FIXED_NAME)
    with pytest.raises(co.Invalid, match="sortie longue est ce jour-là"):
        co.clean_fixed("6", "75", "", (1, 3, 6), 6)
    with pytest.raises(co.Invalid, match="6 jours au plus"):
        co.clean_fixed("2", "75", "", (0, 1, 3, 4, 5, 6), 6)
    with pytest.raises(co.Invalid, match="le jour de ta séance fixe"):
        co.clean_fixed("", "75", "", (1, 3, 6), 6)
    assert co.fixed_warning(cp.Fixed(5, 75), 6) == "Ta séance fixe est la veille de ta sortie longue : deux jours " \
                                                    "durs de suite."
    assert co.fixed_warning(cp.Fixed(0, 75), 6).startswith("Ta séance fixe est le lendemain")
    assert co.fixed_warning(cp.Fixed(2, 75), 6) is None


def _on(rows, today, weekday):
    return [r for r in rows if r.day >= today and r.day.weekday() == weekday and r.status != co.CANCELLED]


async def test_the_option_is_set_changed_and_removed_through_its_row(client, people, beta, db_session, same_today):
    athlete = people.athlete
    plan = await _plan(client, db_session, athlete)  # Tuesday, Thursday, Saturday, Sunday; a plan made before
    before = [(r.day, r.kind, r.minutes, r.structure) for r in await _rows(db_session, athlete)]
    page = (await client.get("/coaching")).text
    assert 'id="coach-tool-fixed"' in page and "Ma séance fixe" in page and "Une séance qui revient chaque semaine" in page
    assert "club" not in page.split('id="coach-tool-fixed"')[1].split("</section>")[0]
    assert [(r.day, r.kind, r.minutes, r.structure) for r in await _rows(db_session, athlete)] == before, \
        "a plan without a fixed session: nothing moves at its next reading"
    # a Tuesday to come that the athlete edited stays as it is
    ahead = same_today + timedelta(days=7)
    edited = [r for r in _on(await _rows(db_session, athlete), same_today, 1) if r.day >= ahead][0]
    await client.post(f"/coaching/sessions/{edited.id}/edit", data={"kind": cp.FOOTING, "minutes": "40"})
    r = await client.post("/coaching/fixe", data={"fixed_day": "1", "fixed_minutes": "75", "fixed_name": ""})
    assert r.status_code == 303 and r.headers["location"] == "/coaching?outil=fixe#coach-tool-fixed"
    await db_session.refresh(plan)
    assert plan.fixed == {"day": 1, "minutes": 75, "name": "Séance fixe"}
    rows = await _rows(db_session, athlete)
    tuesdays = _on(rows, same_today, 1)
    club = [r for r in tuesdays if cp.is_fixed(r.structure)]
    assert club and all(r.minutes == 75 and r.kind == cp.QUALITY for r in club)
    assert (edited.kind, edited.minutes, edited.pinned) == (cp.FOOTING, 40, True), "a person's edit stays"
    assert {r.day for r in tuesdays if not cp.is_fixed(r.structure)} == {edited.day}, "every other Tuesday to come"
    assert all(r.day >= same_today for r in rows if cp.is_fixed(r.structure)), "written from today on"
    page = (await client.get("/coaching?outil=fixe")).text
    assert "Mardi · 1h15 · Séance fixe" in page and 'id="coach-fixed-remove"' in page
    assert '<section class="pf-coach-panel is-asked" id="coach-tool-fixed"' in page and 'data-open="fixed"' in page
    card = (await client.get(f"/coaching?d={club[0].day.isoformat()}")).text
    assert ">Séance fixe<span class=\"sr-only\"> 1h15</span></h4>" in card and "Son contenu est le tien." in card

    # changed: a new duration and a name
    await client.post("/coaching/fixe", data={"fixed_day": "1", "fixed_minutes": "65", "fixed_name": "Piste"})
    club = [r for r in _on(await _rows(db_session, athlete), same_today, 1) if cp.is_fixed(r.structure)]
    assert club and {(r.minutes, r.structure["name"]) for r in club} == {(65, "Piste")}
    # refused: the long run's day — said in its row, nothing written
    r = await client.post("/coaching/fixe", data={"fixed_day": "6", "fixed_minutes": "65"})
    assert r.status_code == 200 and "Ta sortie longue est ce jour-là" in r.text
    await db_session.refresh(plan)
    assert plan.fixed["day"] == 1
    # the long run's eve: kept, and said where the option is set
    await client.post("/coaching/fixe", data={"fixed_day": "5", "fixed_minutes": "65"})
    page = (await client.get("/coaching?outil=fixe")).text
    assert "Ta séance fixe est la veille de ta sortie longue : deux jours durs de suite." in page
    # removed: the plan places its own sessions again
    r = await client.post("/coaching/fixe/retirer")
    assert r.status_code == 303
    await db_session.refresh(plan)
    assert plan.fixed == {"declined": [1, 5]}, "moved off Tuesday, removed from Saturday: never set again by itself"
    assert co.fixed_of(plan) is None
    assert not [r for r in await _rows(db_session, athlete) if cp.is_fixed(r.structure) and r.day >= same_today]


async def test_the_start_screen_line_previews_and_saves_the_fixed_session(client, people, beta, db_session,
                                                                         same_today):
    athlete = people.athlete
    _as(client, athlete)
    m0 = cp.monday_of(same_today)
    db_session.add_all([_run(athlete, m0 - timedelta(weeks=w) + timedelta(days=2), 70 + w, w) for w in range(1, 9)])
    await db_session.flush()
    page = (await client.get("/coaching")).text
    assert "J’ai déjà une séance chaque semaine</span>" in page and 'name="fixed_asked"' in page
    assert re.search(r'name="fixed_on" value="1" id="coach-fixed-on" data-fixed-toggle>', page), "closed, unticked"
    assert not re.search(r'name="fixed_day" value="\d"[^>]*checked', page), "no weekday guessed"
    assert 'value="2" id="coach-fixed-day-2" aria-label="mercredi" data-minutes="75" data-why="Lu sur tes ' \
           'mercredis."' in page
    query = {"intent": cp.PROGRESSER, "goal": "none", "days": ["1", "3", "5", "6"], "long_day": "6", "base": "240"}
    fixed = {"fixed_on": "1", "fixed_day": "2", "fixed_minutes": "75", "fixed_name": ""}
    r = await client.get("/coaching/apercu", params={**query, **fixed}, headers=HTMX)
    pv = cv.first_week((1, 3, 5, 6), 6, 240, cp.Goal(), same_today, intent=cp.PROGRESSER,
                       level=cp.read_level([], same_today, 240), fixed=cp.Fixed(2, 75))
    assert r.status_code == 200 and "est ta séance de qualité de la semaine" not in r.text, "the rule: « Comment ça marche »"
    assert any(line.startswith("Ta séance fixe") for line in cv.METHOD)
    assert "mercredi : Séance fixe 1 heure 15" in pv["aria"]
    r = await client.get("/coaching/apercu", params={**query, **fixed, "fixed_day": "6"}, headers=HTMX)
    assert "Ta sortie longue est ce jour-là" in r.text
    r = await client.get("/coaching/apercu", params={**query, "fixed_day": "2"}, headers=HTMX)
    assert "Séance fixe est" not in r.text, "the box unticked: no fixed session"
    r = await client.post("/coaching/plan", data={**query, **fixed, "fixed_asked": "1"})
    assert r.status_code == 303
    plan = (await db_session.execute(select(CoachingPlan))).scalar_one()
    assert plan.fixed == {"day": 2, "minutes": 75, "name": "Séance fixe"}
    redo = (await client.get("/coaching?refaire=1")).text
    assert re.search(r'name="fixed_on" value="1" id="coach-fixed-on" data-fixed-toggle checked>', redo)
    assert re.search(r'name="fixed_day" value="2"[^>]*checked', redo)
    # a screen opened before it does not ask: the fixed session stays as it was
    await client.post("/coaching/plan", data=query)
    await db_session.refresh(plan)
    assert plan.fixed == {"day": 2, "minutes": 75, "name": "Séance fixe"}
    # unticked on a screen that asks: removed
    await client.post("/coaching/plan", data={**query, "fixed_asked": "1", "fixed_day": "2"})
    await db_session.refresh(plan)
    assert plan.fixed == {"declined": [2]}, "removed by hand: never set again by itself on that day"
    assert co.fixed_of(plan) is None


async def test_a_follower_the_feed_and_the_export_see_it_like_any_session(client, people, beta, db_session,
                                                                         same_today):
    from app.dependencies import get_current_user
    from app.models.coaching import CoachingLink
    from app.services import data_export

    athlete, coach = people.athlete, people.coach
    plan = await _plan(client, db_session, athlete)
    await client.post("/coaching/fixe", data={"fixed_day": "2", "fixed_minutes": "75", "fixed_name": "VMA club"})
    club = [r for r in _on(await _rows(db_session, athlete), same_today, 2) if cp.is_fixed(r.structure)][0]
    assert (await data_export.coaching(db_session, athlete))["plan"]["fixed"] == {"day": 2, "minutes": 75,
                                                                                  "name": "VMA club"}
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    await db_session.flush()
    _as(client, coach)
    followed = (await client.get(f"/coaching/athletes/{athlete.id}?d={club.day.isoformat()}")).text
    assert ">VMA club<span class=\"sr-only\"> 1h15</span></h4>" in followed and "Ma séance fixe" not in followed
    client._transport.app.dependency_overrides.pop(get_current_user)
    feed = (await client.get(f"/coaching/cal/{plan.feed_token}.ics")).text
    assert "SUMMARY:VMA club 1h15" in feed
    _as(client, athlete)
    await client.post("/coaching/stop")
    assert await db_session.scalar(select(CoachingPlan.id)) is None


async def test_a_low_morning_leaves_the_club_session_alone(client, people, beta, db_session, same_today,
                                                           monkeypatch):
    """The form turns the day's hard session into a footing on a low morning — never the club's: the athlete
    decided it (« Alléger aujourd'hui » stays theirs to tap)."""
    athlete = people.athlete
    plan = await _plan(client, db_session, athlete)
    await client.post("/coaching/fixe", data={"fixed_day": str(same_today.weekday()), "fixed_minutes": "75"})
    if plan.long_day == same_today.weekday() or len(set(plan.weekdays) | {same_today.weekday()}) > 6:
        pytest.skip("today is the long run's day")
    rows = await _rows(db_session, athlete)
    today_rows = [r for r in rows if r.day == same_today and cp.is_fixed(r.structure)]
    assert today_rows
    c = await co.clock(db_session, athlete)
    inp = co.inputs(plan, cp.Goal())
    co.lighten_today(db_session, plan, rows, inp, c)
    assert cp.is_fixed(today_rows[0].structure) and today_rows[0].kind == cp.QUALITY and not today_rows[0].form



def test_the_agenda_gives_the_club_session_no_time_and_offers_no_other():
    """The club's hour is the club's: « Mon agenda » places the plan's sessions and leaves the fixed one alone."""
    from types import SimpleNamespace

    from app.models.coaching import CoachingSession
    from app.services import coaching_agenda as ag
    from app.services import coaching_slots as cs

    today = date(2026, 11, 23)
    fixed = cp.Fixed(2, 75, "Séance club")

    def row(day, structure, kind):
        return CoachingSession(plan_id=1, user_id=1, day=day, auto_day=day, kind=kind, minutes=60, structure=structure,
                               status="planned", pinned=False, origin="auto", manual=False, seq=0, slot_chosen=False)
    club = row(today + timedelta(days=2), cp.fixed_structure(fixed), cp.QUALITY)
    footing = row(today + timedelta(days=1), cp.default_structure(cp.FOOTING, 60), cp.FOOTING)
    plan = SimpleNamespace(weekdays=(1, 2, 6), moments=None)
    ag.place([club, footing], cs.Agenda(), plan, today, 0, None, lambda r: True, lambda r: None)
    assert footing.slot_min is not None, "a session of the plan gets its time"
    assert (club.slot_min, club.slot_flag, club.day) == (None, None, today + timedelta(days=2))


def test_its_words_assume_no_club():
    """The owner, 26 Nov 2026: « On ne sait pas si c'est une sortie club ». The name given by default, its line,
    its board word say nothing of a club; a plan kept with the name of before (« Séance club ») reads as today's."""
    assert (cp.FIXED_NAME, cp.FIXED_TEXT) == ("Séance fixe", "Son contenu est le tien.")
    s = cp.fixed_structure(cp.Fixed(2, 75))
    assert cp.title(cp.QUALITY, s) == "Séance fixe" and cv.short_title(cp.QUALITY, s) == "Séance"
    assert cp.title(cp.QUALITY, {**s, "name": "Séance club"}) == "Séance fixe"
    plan = SimpleNamespace(fixed={"day": 2, "minutes": 75, "name": "Séance club"})
    assert co.fixed_of(plan) == cp.Fixed(2, 75, cp.FIXED_NAME)
    plan.fixed["name"] = "Piste du mercredi"
    assert co.fixed_of(plan).name == "Piste du mercredi" and cv.short_title(
        cp.QUALITY, cp.fixed_structure(co.fixed_of(plan))) == "Piste"
    for path in ("app/templates/partials/coaching_tools.html", "app/templates/coaching_onboarding.html",
                 "app/templates/partials/coaching_fixed_fields.html"):
        text = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
        assert "ton club" not in text and "(club)" not in text, path
