"""Coaching: a recorded run that is not the day's session (docs/coaching.md › « Une sortie qui n’est pas la séance
prévue »; the owner, 25 Nov 2026: « je n'ai pas fait ma séance du jour mais une vma club je fais quoi il se passe
quoi ? C'est recalculé automatiquement ? », then « ne fait pas en préventif pas d'actions utilisateurs quand tu vois
que ce n'est pas la séance attendu car tu ne peux pas la rapprocher alors tu met à jour »). Every reading of the plan
(the page, the follower's page, the daily round) reads such a run by itself — a hard session by its laps, its heart
rate or its pace on flat ground, else an easy outing — and updates the plan: a hard session applies the fixed
session's rules to that week only on the sessions nobody touched; an easy one rebuilds nothing and postpones a quality
session not done. One notice says what happened and what the plan did; one link corrects the reading and puts back
what it changed; a hard session the same weekday most weeks is kept there by itself as « Ma séance fixe » (the owner,
26 Nov 2026: « On ne sait pas si c'est une sortie club du coup je ne comprends pas la question … »: no question is
left), with one quiet way back; a reading is never applied twice; the follower sees no notice."""
import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.activity import Activity
from app.models.coaching import CoachingLink, CoachingPlan, CoachingSession
from app.services import coaching as co
from app.services import coaching_pace as pace
from app.services import coaching_plan as cp
from app.services import coaching_runs as cr
from app.services import coaching_start as cst
from app.services import coaching_view as cv
from tests.test_coaching import HTMX, _as, _plan, _rows, _run, people  # noqa: F401
from tests.test_coaching_start import MONDAY, _main, clock  # noqa: F401

TUE, WED, THU, FRI, SAT, SUN = (MONDAY + timedelta(days=i) for i in range(1, 7))
NNBSP, NBSP = " ", " "
INTERVALS = ([{"distance": 2000, "moving_time": 720}] + [{"distance": 300, "moving_time": 60},
                                                          {"distance": 200, "moving_time": 75}] * 8
             + [{"distance": 1500, "moving_time": 540}])  # 16 × 300 m at 5 m/s, jogged back at 2.7 m/s


def _club(user, day: date, n: int, minutes: int = 95, laps=None, hr=None) -> Activity:
    run = _run(user, day, minutes, n, name="VMA club")
    run.distance, run.average_speed, run.laps = 16500, 16500 / (minutes * 60), laps
    run.average_heartrate = hr
    return run


def _plain(structure):
    return {k: v for k, v in structure.items() if k != "answer"} if isinstance(structure, dict) else structure


def _state_of(rows, answers=True) -> list[tuple]:
    """Every live field of every session (not the calendar's counters); `answers`: with the reading each keeps."""
    return sorted((r.id, r.day, r.kind, r.minutes, r.structure if answers else _plain(r.structure), r.status,
                   r.activity_id, r.done_minutes, r.manual, r.ressenti, r.pinned, r.origin, r.auto_day)
                  for r in rows if answers or not cr.is_note(r))


def _card(text: str) -> str:
    return text.split('<section class="pf-coach-debrief pf-coach-runcard"')[1].split("</section>")[0]


def _held(sessions, today, free=True) -> list[cp.Held]:
    return [cp.Held(i, s.day, s.kind, s.minutes, s.structure, "planned", free and s.day >= today)
            for i, s in enumerate(sessions, start=1)]


def _week(days, base=300, intent=None, level=None):
    inp = cp.Inputs(days, 6 if 6 in days else max(days), base, MONDAY, cp.Goal(), {}, intent=intent, level=level)
    week = cp.build(inp, MONDAY)[0]
    return inp, week, cp.week_quality(week.monday, inp, {"target": week.target, "light": week.light, "easy": False},
                                      week.phase, week.index)


async def _before(db, athlete) -> list[tuple]:
    """The plan read as it stands without its runs read (what a correction must give back, the readings aside)."""
    state = await co.sync(db, await co.get_plan(db, athlete.id), athlete, settle=False)
    return _state_of(state.rows, answers=False)


# ── the rules (pure) ────────────────────────────────────────────────────────

def test_the_week_count_is_the_generator_s_own():
    """week_quality is the very count week_sessions writes: the sessions of quality the generator puts in a week."""
    for days, base, intent, level in (((1, 2, 4, 5, 6), 300, None, None), ((1, 3, 6), 180, None, None),
                                      ((1, 3, 6), 180, cp.PROGRESSER, cp.REGULIER),
                                      ((1, 2, 4, 5, 6), 300, cp.PROGRESSER, cp.ENTRAINE),
                                      ((1, 2, 4, 5, 6), 300, cp.PROGRESSER, cp.DEBUTANT), ((5, 6), 120, None, None)):
        inp = cp.Inputs(days, 6, base, MONDAY, cp.Goal(), {}, intent=intent, level=level)
        for week in cp.build(inp, MONDAY)[:9]:
            info = {"target": week.target, "light": week.light, "easy": False}
            written = sum(1 for s in week.sessions if s.kind in (cp.QUALITY, cp.HILLS))
            assert cp.week_quality(week.monday, inp, info, week.phase, week.index) == written, (days, week.index)


def test_a_hard_run_takes_a_quality_place_and_the_next_day_eases():
    """The owner's week (Tuesday, Wednesday, Friday, Saturday, Sunday; two quality sessions): a 1 h 35 club run on
    Tuesday's footing is one of the two — Friday's short hills stay beside it (BESIDE_FIXED: not short intervals at
    speed), Wednesday's fartlek, the day after, cannot stay (two hard days in a row) nor move (Saturday is before the
    long run): a récup of its duration."""
    _inp, week, wanted = _week((1, 2, 4, 5, 6))
    assert [(s.day, s.kind, (s.structure or {}).get("key")) for s in week.sessions if s.kind in (cp.QUALITY, cp.HILLS)] \
        == [(WED, cp.QUALITY, "fartlek"), (FRI, cp.HILLS, "cotes_courtes")] and wanted == 2
    held = _held([s for s in week.sessions if s.day != TUE], TUE)
    changes = cp.after_hard(TUE, 95, held, wanted, TUE, (1, 2, 4, 5, 6))
    wed = next(h for h in held if h.day == WED)
    assert changes == [cp.Change("recup", wed.id, WED, wed.minutes, cp.steady(wed.minutes, 1))]
    _inp, week, wanted = _week((1, 3, 6), 180)
    held = _held(week.sessions, WED)
    thu = next(h for h in held if h.day == THU)
    assert wanted == 1 and cp.after_hard(WED, 95, held, wanted, WED, (1, 3, 6)) == [
        cp.Change("recup", thu.id, THU, thu.minutes, cp.steady(thu.minutes, 1))]
    assert cp.after_hard(MONDAY, 95, held, wanted, MONDAY, (1, 3, 6)) == [
        cp.Change("eased", thu.id, THU, thu.minutes, cp.steady(thu.minutes, 2))]


def test_a_hard_session_the_day_after_moves_to_a_day_the_plan_may_use():
    _inp, week, wanted = _week((0, 1, 2, 3, 6))
    assert [(s.day, s.kind) for s in week.sessions] == [(MONDAY, cp.RECUP), (TUE, cp.FOOTING), (WED, cp.QUALITY),
                                                        (THU, cp.RECUP), (SUN, cp.LONG)] and wanted == 2
    held = _held([s for s in week.sessions if s.day != TUE], TUE)
    wed, thu = (next(h for h in held if h.day == d) for d in (WED, THU))
    assert cp.after_hard(TUE, 95, held, wanted, TUE, (0, 1, 2, 3, 6)) == [
        cp.Change("moved", wed.id, WED, wed.minutes, to=THU, swap=thu.id)]
    touched = _held([s for s in week.sessions if s.day != TUE], TUE, free=False)
    assert cp.after_hard(TUE, 95, touched, wanted, TUE, (0, 1, 2, 3, 6)) == []
    assert cp.after_hard(TUE, 95, held, None, TUE, (0, 1, 2, 3, 6))[0].what == "moved"


def test_the_hard_budget_keeps_fewer_repetitions_or_eases():
    _k, minutes, intervals = cp.quality_session("intervalles", 8)  # 8 × 3 min: 24 min of hard effort
    fewer = cp.fewer_reps(intervals, minutes, 15)
    assert fewer is not None and cp.hard_minutes(fewer) == 15 and sum(
        s.get("min", 0) + s.get("reps", 0) * (s.get("effort", 0) + s.get("recup", 0)) - s.get("recup", 0) * bool(
            s.get("reps")) for s in fewer["steps"]) == minutes, "five repetitions, the same duration"
    assert cp.fewer_reps(intervals, minutes, 11) is None, "under the fewest (4 × 3 min): no"
    session = cp.Held(9, FRI, cp.QUALITY, minutes, intervals, "planned", True)
    easy = cp.Held(8, THU, cp.FOOTING, 40, cp.steady(40, 2), "planned", True)
    changes = cp.after_hard(TUE, 200, [easy, session], 2, TUE, (1, 3, 4))
    assert [(c.what, c.id) for c in changes] == [("eased", 9)]
    changes = cp.after_hard(TUE, 90, [easy, session], 2, TUE, (1, 3, 4))
    assert [(c.what, c.id) for c in changes] == [("reps", 9)] and cp.hard_minutes(changes[0].structure) == 15


def test_a_long_run_the_day_after_that_cannot_move_stays_a_footing_of_its_duration():
    _inp, week, wanted = _week((0, 1, 2, 3), 240)
    assert [(s.day, s.kind) for s in week.sessions][-1] == (THU, cp.LONG)
    held = _held([s for s in week.sessions if s.day != WED], THU)
    long = next(h for h in held if h.day == THU)
    assert cp.after_hard(WED, 95, held, wanted, THU, (0, 1, 2, 3)) == [
        cp.Change("eased", long.id, THU, long.minutes, cp.steady(long.minutes, 2))]


def test_a_declared_run_is_the_fixed_session_s_shape():
    s = cp.declared_structure(95)
    assert cp.is_declared(s) and not cp.is_fixed(s) and cp.title(cp.QUALITY, s) == "Séance dure"
    assert cp.hard_minutes(s) == cp.fixed_hard(95) == 24 and cp.main_level(cp.QUALITY, s) == 4


def test_the_weekly_habit_reads_most_of_the_eight_weeks_before():
    day = WED
    runs = [SimpleNamespace(day=day - timedelta(weeks=w), minutes=95) for w in (1, 2, 3, 5, 7)]
    assert cst.runs_that_weekday(runs, day) == 5 and cst.weekly_habit(runs, day)
    assert not cst.weekly_habit(runs[:4], day), "four of eight: not most"
    assert not cst.weekly_habit([SimpleNamespace(day=day + timedelta(days=1) - timedelta(weeks=w), minutes=50)
                                 for w in range(1, 9)], day), "another weekday"
    assert not cst.weekly_habit([SimpleNamespace(day=day - timedelta(weeks=w), minutes=95) for w in range(9, 15)],
                                day), "older than eight weeks"


# ── the reading (pure): the plan's own definitions of intensity, one criterion at a time ──

def test_laps_of_repeated_efforts_read_hard_and_steady_laps_do_not():
    assert cr.looks_like_intervals(INTERVALS) and cr.reading(laps=INTERVALS) == (cr.HARD, "tours")
    steady = [{"distance": 1000, "moving_time": 330 + 5 * (i % 3)} for i in range(12)]
    assert not cr.looks_like_intervals(steady) and cr.reading(laps=steady) == (cr.EASY, None)
    finish = [{"distance": 1000, "moving_time": 330} for _ in range(10)] + [{"distance": 1000, "moving_time": 250}] * 2
    assert not cr.looks_like_intervals(finish), "one fast block is not repeated efforts"
    assert not cr.looks_like_intervals(INTERVALS[:5]) and not cr.looks_like_intervals(None)
    assert not cr.looks_like_intervals([{"distance": "x"}, "nope", {"distance": 50, "moving_time": 5}] * 4)
    assert cr.looks_like_intervals(INTERVALS + [{"distance": 3, "moving_time": 2}]), "a lap pressed twice: left out"


def test_the_average_heart_rate_reads_hard_from_the_soutenu_zone():
    """Level 3 (« soutenu ») is zone 4 of the app's zones (coaching_plan.HR_ZONES): from 80 % of a known maximum."""
    assert cp.HR_ZONES[cr.HR_LEVEL] == (4, 4)
    assert cr.hr_floor(190) == 152 and cr.hr_floor(None) is None
    assert cr.reading(hr=152, hr_max=190) == (cr.HARD, "cardio")
    assert cr.reading(hr=151, hr_max=190) == (cr.EASY, None), "zone 3: an easy outing"
    assert cr.reading(hr=175) == (cr.EASY, None), "no known maximum: the heart rate says nothing"


def test_the_pace_reads_hard_from_the_threshold_on_flat_ground_only():
    """The lower bound of the threshold paces (coaching_pace.THRESHOLD: 93 % of the critical speed), on a run of at
    most FLAT_CLIMB metres of climb per km."""
    cs = 4.0
    at = pace.THRESHOLD[0] * cs
    assert cr.reading(speed=at, km=10, dplus=50, cs=cs) == (cr.HARD, "allure")
    assert cr.reading(speed=at - 0.01, km=10, dplus=50, cs=cs) == (cr.EASY, None), "under the threshold"
    assert cr.reading(speed=at, km=10, dplus=10 * cr.FLAT_CLIMB + 1, cs=cs) == (cr.EASY, None), "hilly: no pace"
    assert cr.reading(speed=at, km=10, dplus=10 * cr.FLAT_CLIMB, cs=cs)[0] == cr.HARD, "the bound is flat"
    assert cr.reading(speed=at, km=10, dplus=0) == (cr.EASY, None), "no critical speed"
    assert cr.reading(speed=at, km=0, cs=cs) == (cr.EASY, None), "no distance"


def test_no_data_is_an_easy_outing():
    assert cr.reading() == (cr.EASY, None)
    assert cr.reading(laps=[], hr=None, hr_max=190, speed=None, km=0, dplus=None, cs=4.0) == (cr.EASY, None)


def test_the_notice_names_the_day_and_the_session():
    today = THU
    assert [cv.run_when(today - timedelta(days=k), today) for k in (0, 1, 2, 7)] == [
        "Aujourd’hui", "Hier", "Mardi", "Jeudi dernier"]
    assert cv._of_session({"kind": cp.FOOTING, "minutes": 40}) == "du footing de 40 min"
    assert cv._of_session({"kind": cp.LONG, "minutes": 105}) == "de la sortie longue de 1h45"
    assert cv._of_session({"kind": cp.HILLS, "minutes": 45, "structure": cp.quality_session("cotes_courtes", 6)[2]}) \
        == "des côtes courtes de 45 min"
    assert cv._of_session({"kind": cp.QUALITY, "minutes": 75, "structure": cp.fixed_structure(cp.Fixed(2, 75))}) \
        == "de ta séance fixe de 1h15"
    assert cv._of_session({"kind": cp.QUALITY, "minutes": 75, "structure": {"key": cp.FIXED_KEY,
                                                                              "name": "Séance club"}}) \
        == "de ta séance fixe de 1h15", "the name given by default before 26 Nov 2026 reads as today's"
    assert cv._of_session({"kind": cp.QUALITY, "minutes": 60, "structure": cp.fixed_structure(cp.Fixed(2, 60, "Piste"))}) \
        == "de ta séance « Piste » de 1h00"
    assert cv._your_session({"kind": cp.QUALITY, "structure": cp.quality_session("intervalles", 6)[2]}) == (
        "tes intervalles", "ils passent", "les ")
    assert cv._your_session({"kind": cp.QUALITY, "structure": cp.quality_session("fartlek", 6)[2]})[:2] == (
        "ton fartlek", "il passe")


# ── the page: read and applied at once ──────────────────────────────────────

async def _owner(client, db, people, clock, *, minutes=95, laps=INTERVALS, weeks_before=0):
    """The owner's week: Tuesday, Wednesday, Friday, Saturday, Sunday (two quality sessions: Wednesday's fartlek,
    Friday's short hills), made on Monday; on Tuesday a 1 h 35 club run instead of the 55-min footing."""
    athlete = people.athlete
    _as(client, athlete)
    plan = await _plan(client, db, athlete, days=("1", "2", "4", "5", "6"), base="300")
    for w in range(1, weeks_before + 1):  # Tuesdays of the weeks before
        db.add(_club(athlete, TUE - timedelta(weeks=w), 100 + w, minutes))
    clock.day = TUE
    db.add(_club(athlete, TUE, 1, minutes, laps))
    await db.flush()
    return athlete, plan


async def test_the_owner_s_club_run_is_read_hard_on_the_page_and_said(client, people, db_session, clock):
    athlete, plan = await _owner(client, db_session, people, clock)
    before = await _before(db_session, athlete)
    page = _main((await client.get("/coaching")).text)
    rows = {x.id: x for x in await _rows(db_session, athlete)}
    tue = next(r for r in rows.values() if r.day == TUE)
    wed = next(r for r in rows.values() if r.day == WED)
    fri = next(r for r in rows.values() if r.day == FRI)
    run_id = tue.activity_id
    # read by its laps, applied with no tap: the run is the day's hard session, the fartlek the day after a récup
    assert (tue.kind, tue.minutes, tue.status, tue.done_minutes) == (cp.QUALITY, 95, co.DONE, 95)
    assert cp.is_declared(tue.structure) and cr.answer_of(tue)["what"] == cr.HARD
    assert (wed.kind, wed.minutes, wed.status) == (cp.RECUP, 45, co.PLANNED) and co.marked(wed)
    assert fri.kind == cp.HILLS and not co.marked(fri), "Friday's short hills stay beside it"
    # one notice at the top, in the debrief's place: what happened, what the plan did, with the numbers
    card = _card(page)
    assert page.index("pf-coach-runcard") < page.index("pf-coach-calendar") and page.count("pf-coach-debrief ") == 1
    assert ' id="debrief" role="group" aria-labelledby="coach-run-h"' in page
    assert (f'<h2 id="coach-run-h" class="pf-coach-debrief-h" tabindex="-1"><b>Aujourd’hui, tu as couru 1h35 '
            f'(16,5{NNBSP}km) au lieu du footing de 55 min{NBSP}: comptée comme une séance dure.</b></h2>') in card
    assert (f'<p class="pf-coach-run-said" id="coach-run-said">Ta séance de mercredi devient une récup de 45 min{NBSP}; le '
            'reste ne bouge pas.</p>') in card
    # no question: the ressenti (the debrief for that run), one correction link, the quiet way out, « Fermer »
    assert "C’était quoi" not in page and "pf-coach-run-kind" not in card and "Annuler" not in card
    assert f'action="/coaching/sessions/{tue.id}/ressenti"' in card and card.count("data-feel") == 5
    assert f'action="/coaching/runs/{run_id}/corriger"' in card
    assert '>C’était une sortie tranquille</button>' in card and '>Ce n’est pas cette séance</button>' in card
    assert 'id="coach-run-close"' in card and "coach-run-yes" not in card and "Reporter" not in card
    assert "coach" not in re.sub(r"<[^>]+>", " ", card.split(">", 1)[1]).lower(), "never « coach » in the words"
    # the week says it, and Wednesday's ticket too
    assert (f'<p class="pf-coach-reason pf-coach-moves">Mercredi devient une récup{NBSP}: c’est le lendemain de la '
            'séance dure de mardi.</p>') in page
    wed_page = (await client.get("/coaching/calendar?d=2026-11-18", headers=HTMX)).text
    assert f'<p class="pf-coach-formline">Récup{NBSP}: lendemain de la séance dure de mardi.</p>' in wed_page

    # the ressenti, in the notice, stored as the debrief stores it; the notice stays (something changed)
    r = await client.post(f"/coaching/sessions/{tue.id}/ressenti",
                          data={"value": "4", "w": "2026-11-16", "back": "2026-11-17"}, headers=HTMX)
    assert (await db_session.get(CoachingSession, tue.id)).ressenti == 4
    assert 'id="coach-run-r-4" data-feel aria-pressed="true"' in r.text and "pf-coach-runcard" in r.text

    # idempotent: the page, the calendar, the daily round read the plan again — nothing applied twice or undone
    from app.tasks import coaching as task

    after = _state_of(await _rows(db_session, athlete))
    await client.get("/coaching/calendar?d=2026-11-17", headers=HTMX)
    await task.run(db_session)
    await client.get("/coaching")
    assert _state_of(await _rows(db_session, athlete)) == after

    # the follower: no notice, never; the sessions and their lines
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=people.coach.id))
    await db_session.flush()
    theirs = (await _as(client, people.coach).get(f"/coaching/athletes/{athlete.id}?d=2026-11-18")).text
    assert "pf-coach-runcard" not in theirs and "comptée comme" not in theirs
    assert "coach-run-" not in theirs.split("<script")[0] and "lendemain de la séance dure de mardi" in theirs
    assert (await client.post(f"/coaching/runs/{run_id}/corriger", data={})).status_code == 404, "the athlete's"
    _as(client, athlete)

    # « C’était une sortie tranquille »: what the reading changed put back, the other reading applied, the ressenti
    # kept; the notice says it, its link now says the other way
    r = await client.post(f"/coaching/runs/{run_id}/corriger", data={"w": "2026-11-16"}, headers=HTMX)
    rows = await _rows(db_session, athlete)
    assert _state_of(rows, answers=False) == [x if x[0] != tue.id else x[:9] + (4,) + x[10:] for x in before]
    tue = next(x for x in rows if x.id == tue.id)
    assert (tue.kind, tue.minutes, tue.ressenti, cr.answer_of(tue)["what"]) == (cp.FOOTING, 55, 4, cr.EASY)
    card = _card(r.text)
    assert (f'<b>Aujourd’hui, tu as couru 1h35 (16,5{NNBSP}km) au lieu du footing de 55 min{NBSP}: comptée comme une '
            f'sortie tranquille{NBSP}; rien d’autre ne change.</b>') in card
    assert '>C’était une séance dure</button>' in card and 'aria-pressed="true"' in card
    # … and back: the hard reading again, exactly as the first time
    await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    assert _state_of(await _rows(db_session, athlete), answers=False) == [
        x[:4] + (_plain(x[4]),) + x[5:] for x in after]
    # « Fermer »: the notice goes; the plan stays as it is, a reading never brings it back
    r = await client.post(f"/coaching/runs/{run_id}/fermer", data={}, headers=HTMX)
    assert "pf-coach-runcard" not in r.text and "pf-coach-runcard" not in (await client.get("/coaching")).text
    assert cp.is_declared((await db_session.get(CoachingSession, tue.id)).structure)


async def test_the_daily_round_reads_and_applies_without_the_page(client, people, db_session, clock):
    """A run synced at night: the daily round reads the plan (coaching.sync) — the run is read and the plan updated
    before the athlete opens anything; the page then says it."""
    from app.tasks import coaching as task

    athlete, _p = await _owner(client, db_session, people, clock)
    assert await task.run(db_session) >= 1
    rows = await _rows(db_session, athlete)
    assert cp.is_declared(next(r for r in rows if r.day == TUE).structure)
    assert next(r for r in rows if r.day == WED).kind == cp.RECUP
    first = _state_of(rows)
    await task.run(db_session)
    assert _state_of(await _rows(db_session, athlete)) == first, "idempotent"
    page = _main((await client.get("/coaching")).text)
    assert "comptée comme une séance dure" in _card(page)


async def test_a_hard_session_the_day_after_is_moved_and_put_back(client, people, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("0", "1", "2", "3", "6"), base="300")
    clock.day = TUE
    db_session.add(_club(athlete, TUE, 1, laps=INTERVALS))
    db_session.add(_run(athlete, MONDAY, 50, 2))  # Monday's récup, done
    await db_session.flush()
    before = await _before(db_session, athlete)
    page = _main((await client.get("/coaching")).text)
    rows = await _rows(db_session, athlete)
    run_id = next(r for r in rows if r.day == TUE).activity_id
    wed, thu = (next(r for r in rows if r.day == d) for d in (WED, THU))
    # the content moved, each session kept on its day (its calendar event follows its day)
    assert (wed.kind, wed.minutes, thu.kind, thu.minutes) == (cp.RECUP, 50, cp.QUALITY, 45)
    assert f"Ta séance de mercredi passe à jeudi{NBSP}; le reste ne bouge pas." in _card(page)
    assert f"Mercredi est le lendemain de la séance dure de mardi{NBSP}: sa séance passe à jeudi." in page
    thu_page = (await client.get("/coaching/calendar?d=2026-11-19", headers=HTMX)).text
    assert "Séance reportée de mercredi, lendemain de la séance dure de mardi." in thu_page
    assert "Séance déplacée de jeudi." in (await client.get("/coaching/calendar?d=2026-11-18", headers=HTMX)).text
    state = await co.sync(db_session, await co.get_plan(db_session, athlete.id), athlete)
    assert not cv.hard_neighbours(state.rows, TUE)
    await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    rows = await _rows(db_session, athlete)
    assert [x for x in _state_of(rows, answers=False) if x[1] != TUE] == [x for x in before if x[1] != TUE]


async def test_an_easy_run_instead_of_a_quality_session_postpones_it(client, people, db_session, clock):
    """An easy outing instead of the fartlek (no laps, no heart rate, no pace): nothing is rebuilt, the fartlek goes
    to the first day the move rules allow — at once, said in one sentence; corrected, it comes back."""
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "3", "4", "6"), base="200")  # Thursday's fartlek
    clock.day = THU
    db_session.add(_run(athlete, THU, 80, 1, name="Sortie avec Léa"))
    await db_session.flush()
    before = await _before(db_session, athlete)
    thu = next(r for r in await _rows(db_session, athlete) if r.day == THU)
    page = _main((await client.get("/coaching")).text)
    rows = {x.id: x for x in await _rows(db_session, athlete)}
    first = sorted(x[1:] for x in _state_of(rows.values(), answers=False))
    assert (rows[thu.id].kind, rows[thu.id].minutes, rows[thu.id].status) == (cp.FOOTING, 80, co.DONE)
    moved = next(x for x in rows.values() if x.day in (FRI, SAT) and x.status == co.PLANNED
                 and (x.structure or {}).get("key") == "fartlek")
    assert moved.kind == cp.QUALITY and co.marked(moved)
    to = cv.JOURS_L[moved.day.weekday()]
    card = _card(page)
    assert (f"<b>Aujourd’hui, tu as couru 1h20 (12{NNBSP}km) tranquille au lieu de ton fartlek{NBSP}: il passe à "
            f"{to}.</b>") in card
    assert "pf-coach-run-said" not in card and ">C’était une séance dure</button>" in card and "Reporter" not in card
    assert card.count("data-feel") == 5
    page = (await client.get(f"/coaching/calendar?d={moved.day.isoformat()}", headers=HTMX)).text
    assert "Séance reportée de jeudi." in page
    # corrected: the fartlek back on Thursday, then the run read hard there (it was the fartlek, harder)
    r = await client.post(f"/coaching/runs/{thu.activity_id}/corriger", data={}, headers=HTMX)
    rows = await _rows(db_session, athlete)
    t = next(x for x in rows if x.id == thu.id)
    assert cp.is_declared(t.structure) and not any(((x.structure or {}).get("why") or {}).get("what") == "postponed"
                                                   for x in rows if x.status != co.CANCELLED)
    assert "comptée comme une séance dure" in _card(r.text)
    await client.post(f"/coaching/runs/{thu.activity_id}/corriger", data={}, headers=HTMX)
    rows = await _rows(db_session, athlete)
    assert sorted(x[1:] for x in _state_of(rows, answers=False)) == first, "postponed again, as the first time"
    assert sorted(x[1:] for x in before) != first


async def test_with_no_day_the_quality_session_stays_not_done_and_it_is_said(client, people, db_session, clock,
                                                                            monkeypatch):
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "3", "4", "6"), base="200")
    clock.day = THU
    monkeypatch.setattr(cr, "report_day", lambda *a, **k: None)
    db_session.add(_run(athlete, THU, 80, 1, name="Sortie avec Léa"))
    await db_session.flush()
    before = await _before(db_session, athlete)
    page = _main((await client.get("/coaching")).text)
    assert (f"tu as couru 1h20 (12{NNBSP}km) tranquille au lieu de ton fartlek{NBSP}: aucun autre jour ne peut "
            "l’accueillir cette semaine.") in _card(page)
    rows = await _rows(db_session, athlete)
    assert _state_of(rows, answers=False) == before, "nothing moved"
    assert next(r for r in rows if r.day == THU).kind == cp.QUALITY


async def test_a_run_on_a_day_with_no_session_stays_outside_the_plan(client, people, db_session, clock):
    """Read easy: nothing changes, the run stays « hors plan » (its ways kept), the notice says it with the link
    that corrects it and « C’est ma séance de mardi »; read hard (corrected): a hard session that day."""
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")
    clock.day = WED
    db_session.add(_run(athlete, WED, 50, 1, name="Footing avec Léa"))
    await db_session.flush()
    before = await _before(db_session, athlete)
    page = _main((await client.get("/coaching")).text)
    run_id = (await db_session.execute(select(Activity.id).where(Activity.user_id == athlete.id))).scalar_one()
    card = _card(page)
    assert (f"<b>Aujourd’hui, tu as couru 50 min (7,5{NNBSP}km){NBSP}: rien n’était prévu. Comptée comme une sortie "
            f"tranquille{NBSP}; rien d’autre ne change.</b>") in card
    assert "data-feel" not in card and "Ce n’est pas cette séance" not in card, "no session, no ressenti"
    assert "C’est ma séance de mardi" in card and ">C’était une séance dure</button>" in card
    rows = await _rows(db_session, athlete)
    assert _state_of(rows, answers=False) == before and "Hors plan · Footing avec Léa" in page
    assert [cr.answer_of(r)["what"] for r in rows if cr.is_note(r)] == [cr.EASY]
    # corrected: a hard session on Wednesday; Thursday's fartlek (the week's one) a récup
    r = await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    rows = await _rows(db_session, athlete)
    made = next(x for x in rows if x.activity_id == run_id)
    thu = next(x for x in rows if x.day == THU)
    assert cp.is_declared(made.structure) and made.auto_day == WED and thu.kind == cp.RECUP
    assert not any(cr.is_note(x) for x in rows)
    assert f"Ta séance de jeudi devient une récup de 45 min{NBSP}; le reste ne bouge pas." in _card(r.text)
    await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    assert _state_of(await _rows(db_session, athlete), answers=False) == before
    # « C’est ma séance de mardi »: Tuesday's missed session moved to Wednesday, the run linked to it
    tue = next(x for x in await _rows(db_session, athlete) if x.day == TUE)
    r = await client.post(f"/coaching/runs/{run_id}/seance", data={"value": str(tue.id)}, headers=HTMX)
    await db_session.refresh(tue)
    assert (tue.day, tue.status, tue.activity_id) == (WED, co.DONE, run_id)
    assert not any(cr.is_note(x) for x in await _rows(db_session, athlete))


async def test_a_note_goes_when_the_run_is_held_onto_a_session_by_hand(client, people, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")
    clock.day = WED
    db_session.add(_run(athlete, WED, 50, 1, name="Footing avec Léa"))
    await db_session.flush()
    await client.get("/coaching")
    tue = next(x for x in await _rows(db_session, athlete) if x.day == TUE)
    await client.post(f"/coaching/sessions/{tue.id}/move", data={"to": WED.isoformat()}, headers=HTMX)
    rows = await _rows(db_session, athlete)
    assert not any(cr.is_note(x) for x in rows) and next(x for x in rows if x.id == tue.id).activity_id


async def test_not_this_session_takes_the_run_out_once(client, people, db_session, clock):
    athlete, _plan_ = await _owner(client, db_session, people, clock, laps=None)
    await client.get("/coaching")
    tue = next(r for r in await _rows(db_session, athlete) if r.day == TUE)
    run_id = tue.activity_id
    assert cr.answer_of(tue)["what"] == cr.EASY, "no laps, no heart rate: an easy outing"
    r = await client.post(f"/coaching/runs/{run_id}/pas-celle", data={}, headers=HTMX)
    await db_session.refresh(tue)
    assert (tue.status, tue.activity_id, tue.manual, tue.kind) == (co.MISSED, None, True, cp.FOOTING)
    assert "Hors plan · VMA club" in r.text and "pf-coach-runcard" not in r.text, "never read again"
    assert "pf-coach-runcard" not in _main((await client.get("/coaching")).text)


async def test_a_session_ticked_not_done_then_a_run_arrives(client, people, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "2", "4", "5", "6"), base="300")
    clock.day = TUE
    tue = next(r for r in await _rows(db_session, athlete) if r.day == TUE)
    await client.post(f"/coaching/sessions/{tue.id}/status", data={"value": "missed"}, headers=HTMX)
    db_session.add(_club(athlete, TUE, 1))
    await db_session.flush()
    before = await _before(db_session, athlete)
    page = _main((await client.get("/coaching")).text)
    assert (f"Aujourd’hui, tu as couru 1h35 (16,5{NNBSP}km) au lieu du footing de 55 min{NBSP}: comptée comme une "
            f"sortie tranquille{NBSP}; rien d’autre ne change.") in _card(page)
    run_id = (await db_session.execute(select(Activity.id).where(Activity.user_id == athlete.id))).scalar_one()
    await db_session.refresh(tue)
    assert (tue.status, tue.activity_id) == (co.MISSED, None), "easy: the session stays « Pas faite »"
    await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    await db_session.refresh(tue)
    assert (tue.status, tue.activity_id, tue.kind, tue.manual) == (co.DONE, run_id, cp.QUALITY, False)
    await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    assert _state_of(await _rows(db_session, athlete), answers=False) == before


@pytest.mark.parametrize("data,hard", [("laps", True), ("hr", True), ("pace", True), ("hilly", False), ("none", False)])
async def test_a_run_of_its_duration_on_an_easy_session_reads_by_each_criterion(data, hard, client, people,
                                                                                db_session, clock, monkeypatch):
    """A run linked to Tuesday's 55-min footing, its duration within 20 %: only a hard reading changes anything —
    by its laps alone, its heart rate alone, its pace on flat ground alone; hilly or without data: nothing, no
    notice, the day's debrief asks its ressenti."""
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "2", "4", "5", "6"), base="300")
    clock.day = TUE
    run = _run(athlete, TUE, 55, 1, name="Footing")  # 2.5 m/s, 8.25 km, 40 m of climb
    if data == "laps":
        run.laps = INTERVALS
    if data == "hr":
        athlete.max_heart_rate, run.average_heartrate = 190, 158
    if data in ("pace", "hilly"):
        async def critical(*a, **k):
            return 2.6  # its threshold from 2.42 m/s: the run's 2.5 reach it
        monkeypatch.setattr(cr, "_cs", critical)
    if data == "hilly":
        run.total_elevation_gain = 200
    db_session.add(run)
    await db_session.flush()
    page = _main((await client.get("/coaching")).text)
    tue = next(r for r in await _rows(db_session, athlete) if r.day == TUE)
    assert cp.is_declared(tue.structure) is hard and (tue.kind == cp.QUALITY) is hard
    if hard:
        assert (f"<b>Aujourd’hui, tu as couru 55 min (8,2{NNBSP}km), plus soutenu que le footing prévu{NBSP}: comptée "
                "comme une séance dure.</b>") in _card(page)
        assert next(r for r in await _rows(db_session, athlete) if r.day == WED).kind == cp.RECUP
    else:
        assert "pf-coach-runcard" not in page and "Ta sortie d’aujourd’hui est arrivée" in page
        assert cr.answer_of(tue)["open"] is False, "read once, never again"
    assert "C’était quoi" not in page


# ── the habit: a hard session the same weekday most weeks, kept there by itself ──

async def _tuesdays(client, db, people, clock, *, weeks=5, hard=3, laps=INTERVALS, coach=False):
    """The owner's week on a Tuesday, with a run on `weeks` of the 8 Tuesdays before — the first `hard` of them read
    hard by their laps (88, 93, 97 min: a median of 93, 95 to 5 min), the others an easy 50-min jog — and today's
    1 h 35 run instead of the 55-min footing (`laps`: read hard by its laps)."""
    athlete = people.athlete
    _as(client, athlete)
    plan = await _plan(client, db, athlete, days=("1", "2", "4", "5", "6"), base="300")
    hard_minutes = (88, 93, 97)
    for w in range(1, weeks + 1):
        minutes = hard_minutes[(w - 1) % 3] if w <= hard else 50
        db.add(_club(athlete, TUE - timedelta(weeks=w), 100 + w, minutes, INTERVALS if w <= hard else None))
    if coach:
        db.add(CoachingLink(athlete_id=athlete.id, coach_id=people.coach.id))
    clock.day = TUE
    db.add(_club(athlete, TUE, 1, 95, laps))
    await db.flush()
    return athlete, plan


async def _plan_of(db, athlete) -> CoachingPlan:
    return (await db.execute(select(CoachingPlan).where(CoachingPlan.user_id == athlete.id)
                             .execution_options(populate_existing=True))).scalar_one()


HABIT = (f"Tu fais une séance dure presque tous les mardis{NBSP}: le plan la garde ce jour-là chaque semaine et "
         "s’écrit autour.")
ONE_OFF = f"Ta séance de mercredi devient une récup de 45 min{NBSP}; le reste ne bouge pas."


async def test_a_hard_session_most_tuesdays_is_kept_there_by_itself(client, people, db_session, clock):
    """5 of the 8 Tuesdays before with a run, 3 of them read hard: « Ma séance fixe » on Tuesday by itself, as the
    option sets it — the median of the hard ones (93 min, not the 70 of all), to 5 min; the option's default name —
    the notice says so after its first sentence, with one quiet link; no question, no club."""
    athlete, _p = await _tuesdays(client, db_session, people, clock)
    page = _main((await client.get("/coaching")).text)
    plan = await _plan_of(db_session, athlete)
    assert co.fixed_of(plan) == cp.Fixed(1, 95, cp.FIXED_NAME) and cp.FIXED_NAME == "Séance fixe"
    card = _card(page)
    assert f'<p class="pf-coach-run-said" id="coach-run-said">{HABIT}</p>' in card
    assert card.index("coach-run-h") < card.index("coach-run-said") < card.index("coach-run-flip")
    assert '<button type="submit" id="coach-run-weekly" class="pf-coach-quietbtn">Pas tous les mardis</button>' in card
    assert 'action="/coaching/runs/' in card and '/pas-chaque-semaine"' in card
    assert "club" not in card.lower().replace("vma club", ""), "no sentence assumes a club"
    assert "coach-run-yes" not in card and "coach-run-offer" not in card and "Oui, tous les" not in card, "no question"
    rows = await _rows(db_session, athlete)
    tue = next(r for r in rows if r.day == TUE and r.status != co.CANCELLED)
    a = cr.answer_of(tue)
    assert a["recur"]["state"] == cr.YES and a["changes"] == [], "no one-off change: the fixed session's rules"
    assert len([r for r in rows if r.day == TUE and r.status != co.CANCELLED]) == 1, "today: the run's session alone"
    nxt = [r for r in rows if r.day == TUE + timedelta(weeks=1) and r.status != co.CANCELLED]
    assert len(nxt) == 1 and cp.is_fixed(nxt[0].structure) and nxt[0].minutes == 95
    assert cp.title(nxt[0].kind, nxt[0].structure) == "Séance fixe" and cv.short_title(nxt[0].kind, nxt[0].structure) \
        == "Séance" and nxt[0].structure["text"] == "Son contenu est le tien."
    # applied once: the page, the plan read again, the daily round, the follower's page write nothing more
    first, fixed = _state_of(await _rows(db_session, athlete)), plan.fixed
    await client.get("/coaching")
    await co.sync(db_session, await co.get_plan(db_session, athlete.id), athlete)
    from app.tasks import coaching as task
    assert await task.run(db_session) == 1
    assert _state_of(await _rows(db_session, athlete)) == first and (await _plan_of(db_session, athlete)).fixed == fixed
    # « Ma séance fixe » shows it as any fixed session
    tools = (await client.get("/coaching?outil=fixe")).text
    assert "Mardi · 1h35 · Séance fixe" in tools and 'id="coach-fixed-remove"' in tools


@pytest.mark.parametrize("weeks,hard,set_", [(4, 3, False), (5, 3, True), (5, 2, False), (8, 0, False)])
async def test_the_habit_at_its_edges(weeks, hard, set_, client, people, db_session, clock):
    """Five weeks of eight with a run on that weekday, not four; three of them read hard, not two; a weekly easy
    jog is no hard-session habit. Without the habit, the one-off reading of before."""
    athlete, _p = await _tuesdays(client, db_session, people, clock, weeks=weeks, hard=hard)
    card = _card(_main((await client.get("/coaching")).text))
    plan = await _plan_of(db_session, athlete)
    assert (co.fixed_of(plan) is not None) is set_ and (HABIT in card) is set_
    wed = next(r for r in await _rows(db_session, athlete) if r.day == WED)
    if not set_:
        assert plan.fixed is None and ONE_OFF in card and "coach-run-weekly" not in card
        assert wed.kind == cp.RECUP and co.marked(wed)


async def test_no_habit_with_a_fixed_session_or_on_a_declined_weekday(client, people, db_session, clock):
    athlete, plan = await _tuesdays(client, db_session, people, clock)
    await co.set_fixed(db_session, plan, athlete, cp.Fixed(4, 60))
    page = (await client.get("/coaching")).text
    assert "comptée comme une séance dure" in page and HABIT not in page
    assert co.fixed_of(await _plan_of(db_session, athlete)) == cp.Fixed(4, 60, cp.FIXED_NAME)


async def test_no_habit_on_a_declined_weekday(client, people, db_session, clock):
    athlete, plan = await _tuesdays(client, db_session, people, clock)
    plan.fixed = {"declined": [1]}
    await db_session.flush()
    card = _card(_main((await client.get("/coaching")).text))
    assert ONE_OFF in card and HABIT not in card
    assert (await _plan_of(db_session, athlete)).fixed == {"declined": [1]}


async def test_no_habit_where_the_option_would_refuse_it(client, people, db_session, clock):
    """Sunday, the long run's day: « Ma séance fixe » refuses it, so does the habit."""
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "2", "4", "5", "6"), base="300")
    for w in range(1, 6):
        db_session.add(_club(athlete, SUN - timedelta(weeks=w), 100 + w, 90, INTERVALS))
    clock.day = SUN
    db_session.add(_club(athlete, SUN, 1, 200, INTERVALS))
    await db_session.flush()
    await client.get("/coaching")
    assert (await _plan_of(db_session, athlete)).fixed is None


async def test_not_every_tuesday_puts_the_week_back_and_never_sets_it_again(client, people, db_session, clock):
    """« Pas tous les mardis »: the fixed session goes, the week as the one-off reading has it (Wednesday's
    fartlek a récup), Tuesday declined — a correction back and forth never sets it again."""
    athlete, _p = await _tuesdays(client, db_session, people, clock)
    await client.get("/coaching")
    run_id = next(r for r in await _rows(db_session, athlete) if r.day == TUE).activity_id
    r = await client.post(f"/coaching/runs/{run_id}/pas-chaque-semaine", data={}, headers=HTMX)
    plan = await _plan_of(db_session, athlete)
    assert plan.fixed == {"declined": [1]} and co.fixed_of(plan) is None
    card = _card(r.text)
    assert ONE_OFF in card and HABIT not in card and "coach-run-weekly" not in card
    rows = await _rows(db_session, athlete)
    wed = next(x for x in rows if x.day == WED)
    assert wed.kind == cp.RECUP and co.marked(wed)
    assert not [x for x in rows if cp.is_fixed(x.structure) and x.status != co.CANCELLED], "no fixed session left"
    after = _state_of(rows)
    await client.get("/coaching")
    assert _state_of(await _rows(db_session, athlete)) == after, "read again: nothing more"
    r = await client.post(f"/coaching/runs/{run_id}/pas-chaque-semaine", data={}, headers=HTMX)
    assert "Cette séance n’est plus fixée." in r.text
    await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    r = await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    assert ONE_OFF in _card(r.text) and (await _plan_of(db_session, athlete)).fixed == {"declined": [1]}


async def test_the_correction_takes_the_habit_with_it(client, people, db_session, clock):
    """« C’était une sortie tranquille »: the reading put back with the fixed session it set (no weekday declined:
    read hard again, the habit holds again)."""
    athlete, _p = await _tuesdays(client, db_session, people, clock)
    before = await _before(db_session, athlete)
    await client.get("/coaching")
    run_id = next(r for r in await _rows(db_session, athlete) if r.day == TUE).activity_id
    r = await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    plan = await _plan_of(db_session, athlete)
    assert plan.fixed is None
    assert "comptée comme une sortie tranquille" in _card(r.text) and "coach-run-weekly" not in r.text
    assert sorted(x[1:] for x in _state_of(await _rows(db_session, athlete), answers=False) if x[1] != TUE) == \
        sorted(x[1:] for x in before if x[1] != TUE), "every other session as it was (a regeneration may renumber)"
    r = await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    assert HABIT in _card(r.text) and co.fixed_of(await _plan_of(db_session, athlete)) is not None


async def test_removed_from_ma_seance_fixe_it_never_comes_back(client, people, db_session, clock):
    """« Retirer » in « Ma séance fixe »: as « Pas tous les mardis » — the week as the one-off reading has it, and
    the next Tuesday's hard run no longer sets it."""
    athlete, _p = await _tuesdays(client, db_session, people, clock)
    await client.get("/coaching")
    r = await client.post("/coaching/fixe/retirer")
    assert r.status_code == 303
    plan = await _plan_of(db_session, athlete)
    assert plan.fixed == {"declined": [1]}
    page = _main((await client.get("/coaching")).text)
    assert ONE_OFF in _card(page) and HABIT not in page
    assert next(x for x in await _rows(db_session, athlete) if x.day == WED).kind == cp.RECUP
    clock.day = TUE + timedelta(weeks=1)
    db_session.add(_club(athlete, clock.day, 2, 95, INTERVALS))
    await db_session.flush()
    page = _main((await client.get("/coaching")).text)
    assert "comptée comme une séance dure" in page and HABIT not in page
    assert (await _plan_of(db_session, athlete)).fixed == {"declined": [1]}


async def test_moved_in_ma_seance_fixe_the_day_it_leaves_is_declined(client, people, db_session, clock):
    athlete, _p = await _tuesdays(client, db_session, people, clock)
    await client.get("/coaching")
    await client.post("/coaching/fixe", data={"fixed_day": "3", "fixed_minutes": "60", "fixed_name": ""})
    plan = await _plan_of(db_session, athlete)
    assert co.fixed_of(plan) == cp.Fixed(3, 60, cp.FIXED_NAME) and co.declined_days(plan) == [1]
    assert HABIT not in _main((await client.get("/coaching")).text), "no longer its Tuesday: not said"


async def test_the_follower_sees_the_sessions_never_the_notice(client, people, db_session, clock):
    """The follower's page reads the plan first: the habit is set there too, once; the follower sees the fixed
    session, never the notice nor its link."""
    athlete, _p = await _tuesdays(client, db_session, people, clock, coach=True)
    _as(client, people.coach)
    page = (await client.get(f"/coaching/athletes/{athlete.id}?d={(TUE + timedelta(weeks=1)).isoformat()}")).text
    assert co.fixed_of(await _plan_of(db_session, athlete)) == cp.Fixed(1, 95, cp.FIXED_NAME)
    page = re.sub(r"<script.*?</script>", "", page, flags=re.S)  # the page's own scripts name the notice
    assert "pf-coach-runcard" not in page and "Pas tous les" not in page and "presque tous les" not in page
    assert "Séance fixe" in page
    first = _state_of(await _rows(db_session, athlete))
    _as(client, athlete)
    assert HABIT in _card(_main((await client.get("/coaching")).text))
    assert _state_of(await _rows(db_session, athlete)) == first


def test_a_legacy_question_reads_as_nothing():
    """An answer left by the question of before 26 Nov 2026 (« ask », « non »): nothing said of a habit."""
    plan = SimpleNamespace(fixed=None)
    assert not cr.habit_kept(plan, {"recur": {"day": 1, "minutes": 95, "state": "ask"}})
    assert not cr.worth_saying({"what": cr.EASY, "case": cr.BRISK, "recur": {"state": "ask"}})


async def test_no_notice_once_nothing_is_left_to_say(client, people, db_session, clock):
    """An easy outing on the day's footing, too long to be it: nothing changed — the notice holds only its ressenti
    and goes once it is said."""
    athlete, _p = await _owner(client, db_session, people, clock, laps=None)
    page = _main((await client.get("/coaching")).text)
    tue = next(r for r in await _rows(db_session, athlete) if r.day == TUE)
    assert (f"au lieu du footing de 55 min{NBSP}: comptée comme une sortie tranquille{NBSP}; rien d’autre ne change."
            in _card(page))
    r = await client.post(f"/coaching/sessions/{tue.id}/ressenti", data={"value": "2"}, headers=HTMX)
    assert "pf-coach-runcard" not in r.text and "pf-coach-debrief" not in r.text
    assert (await db_session.get(CoachingSession, tue.id)).ressenti == 2


async def test_the_notice_waits_its_turn_with_the_debrief_and_never_for_a_follower(client, people, db_session, clock):
    """One at a time, the most recent first: a debrief more recent than the run goes first; a run older than
    DEBRIEF_DAYS, or before the plan's first day, is not read."""
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")
    clock.day = THU
    rows = await _rows(db_session, athlete)
    thu = next(r for r in rows if r.day == THU)
    db_session.add_all([_run(athlete, WED, 50, 1, name="Footing avec Léa"), _run(athlete, THU, thu.minutes, 2),
                        _run(athlete, MONDAY - timedelta(days=1), 40, 3, name="Avant le plan")])
    await db_session.flush()
    page = _main((await client.get("/coaching")).text)
    assert "Ta sortie d’aujourd’hui est arrivée" in page and "pf-coach-runcard" not in page
    await client.post(f"/coaching/sessions/{thu.id}/ressenti", data={"value": "3"}, headers=HTMX)
    page = _main((await client.get("/coaching")).text)
    assert "Hier, tu as couru 50 min" in page and "Avant le plan" not in page.split("pf-coach-calendar")[0]
    state = await co.sync(db_session, await co.get_plan(db_session, athlete.id), athlete)
    assert [c.run.name for c in cr.notices(state)] == ["Footing avec Léa"] and not cr.candidates(state)
    assert cv.page(state, coach=True)["card"] is None and cv.page(state)["card"]["flip"] == "C’était une séance dure"
    clock.day = THU + timedelta(days=7)
    state = await co.sync(db_session, await co.get_plan(db_session, athlete.id), athlete)
    assert not cr.notices(state), "a week later: no longer said"


async def test_any_weekday_a_misfit_run_read_hard_changes_only_untouched_sessions(client, people, db_session,
                                                                                 same_today):
    """Whatever today's weekday (the suite runs on shifted weekdays): a run on today's session read hard never
    touches a session done, edited or behind, never writes two hard days in a row, is applied once and corrected."""
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=[str(d) for d in range(7) if d != (same_today.weekday() + 3) % 7],
                base="360")
    rows = await _rows(db_session, athlete)
    today = next(r for r in rows if r.day == same_today)
    later = [r for r in rows if r.day > same_today and r.day <= same_today + timedelta(days=6)]
    if later:  # a person's edit on one of the days to come: left alone whatever the rules say
        co.edit(later[-1], datetime.now(timezone.utc), kind=cp.QUALITY, minutes=50, origin="athlete")
    db_session.add(_club(athlete, same_today, 1, minutes=max(30, int(today.minutes * 2)), laps=INTERVALS))
    await db_session.flush()
    before = await _before(db_session, athlete)
    await client.get("/coaching")
    rows = await _rows(db_session, athlete)
    run_id = next(r for r in rows if r.day == same_today).activity_id
    assert cr.answer_of(next(r for r in rows if r.day == same_today))["what"] == cr.HARD
    was = {x[0]: x for x in before}
    for r in rows:
        if r.id in was and was[r.id][2:6] != (r.kind, r.minutes, _plain(r.structure), r.status) \
                and r.day != same_today:
            assert co.marked(r) and was[r.id][11] == "auto" and not was[r.id][10], "only sessions nobody touched"
    if later:
        assert (await db_session.get(CoachingSession, later[-1].id)).kind == cp.QUALITY
    state = await co.sync(db_session, await co.get_plan(db_session, athlete.id), athlete)
    marked = [r for r in state.rows if co.marked(r) and r.status == co.PLANNED]
    hard = {r.day for r in state.rows if r.status in (co.PLANNED, co.DONE) and r.kind in cp.HARD}
    assert not any(r.kind in cp.HARD and ({r.day - timedelta(days=1), r.day + timedelta(days=1)} & hard)
                   for r in marked), "never two hard days in a row of its making"
    first = _state_of(state.rows)
    await co.sync(db_session, state.plan, athlete)
    assert _state_of(await _rows(db_session, athlete)) == first
    await client.post(f"/coaching/runs/{run_id}/corriger", data={}, headers=HTMX)
    rows = await _rows(db_session, athlete)
    assert [x for x in _state_of(rows, answers=False) if x[1] != same_today] == [
        x for x in before if x[1] != same_today], "everything else as it was"


@pytest.mark.parametrize("action", ["reponse", "annuler", "reporter", "autre", "chaque-semaine"])
async def test_the_old_questions_are_gone_and_errors_change_nothing(action, client, people, db_session, clock):
    athlete, _p = await _owner(client, db_session, people, clock)
    await client.get("/coaching")
    run_id = next(r for r in await _rows(db_session, athlete) if r.day == TUE).activity_id
    after = _state_of(await _rows(db_session, athlete))
    assert (await client.post(f"/coaching/runs/{run_id}/{action}", data={"value": "dure"})).status_code == 404
    r = await client.post("/coaching/runs/999999/corriger", data={}, headers=HTMX)
    assert r.status_code == 200 and "Cette sortie n’a pas été lue." in r.text
    r = await client.post(f"/coaching/runs/{run_id}/seance", data={"value": "x"}, headers=HTMX)
    assert "Cette séance ne se déplace pas." in r.text
    assert _state_of(await _rows(db_session, athlete)) == after
    # without JavaScript: back to the page
    r = await client.post(f"/coaching/runs/{run_id}/fermer", data={"w": "2026-11-16"})
    assert r.status_code == 303 and r.headers["location"] == "/coaching?w=2026-11-16"
