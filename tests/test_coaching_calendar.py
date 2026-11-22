"""Coaching, the calendar at the centre (docs/coaching.md › « Les écrans », « Déplacer », « Le débrief »): the page is
a calendar — this week and the next, each day its sessions as blocks and the runs done outside the plan — whose day
opens in place; a session moves to any day (two hard days side by side are a warning, never a refusal; one missed
earlier this week can be planned again, or be the run done another day); a session done that waits for its ressenti
is asked about first, in one tap, and counted for a later notification."""
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.coaching import CoachingLink, CoachingSession
from app.services import coaching as co
from app.services import coaching_plan as cp
from app.services import coaching_view as cv
from app.services.viz import hm, hm_long
from tests.test_coaching import HTMX, _as, _plan, _rows, _run, beta, people  # noqa: F401
from tests.test_coaching_start import MONDAY, _main, clock  # noqa: F401

NOW = datetime(2026, 11, 16, 8, tzinfo=timezone.utc)
TUE, WED, THU, FRI, SAT, SUN = (MONDAY + timedelta(days=i) for i in range(1, 7))


def _row(day: date, kind: str = cp.FOOTING, status: str = co.PLANNED, **more) -> CoachingSession:
    return CoachingSession(**{"plan_id": 1, "user_id": 1, "day": day, "auto_day": day, "kind": kind, "minutes": 45,
                              "status": status, "pinned": False, "origin": "auto", "manual": False, "seq": 0, **more})


# ── « Déplacer »: any day, a warning rather than a refusal (pure) ───────────

def test_a_session_still_to_do_moves_to_any_day():
    today = WED
    quality = _row(THU, cp.QUALITY)
    rows = [quality, _row(SUN, cp.LONG)]
    # Saturday was never one of the plan's days, and sits next to the long run: accepted all the same
    co.move(rows, quality, SAT, today, NOW, "athlete")
    assert (quality.day, quality.pinned, quality.origin, quality.auto_day) == (SAT, True, "athlete", THU)
    assert cv.hard_neighbours(rows, today) == [(SAT, SUN)], "said by the calendar, never refused"
    co.move(rows, quality, MONDAY + timedelta(days=9), today, NOW, "coach")  # next week
    assert quality.day == MONDAY + timedelta(days=9) and quality.origin == "coach"
    assert cv.hard_neighbours(rows, today) == []
    # a planned session already on the day chosen takes the day left
    easy = _row(FRI)
    rows.append(easy)
    co.move(rows, quality, FRI, today, NOW, "athlete")
    assert (quality.day, easy.day, easy.pinned) == (FRI, MONDAY + timedelta(days=9), True)
    # never into the past, never the race, never a session already done
    with pytest.raises(co.Invalid, match="à partir d’aujourd’hui"):
        co.move(rows, quality, TUE, today, NOW, "athlete")
    for fixed in (_row(SUN + timedelta(days=7), cp.RACE), _row(TUE, status=co.DONE)):
        assert not co.movable(fixed, today)
        with pytest.raises(co.Invalid, match="ne se déplace pas"):
            co.move(rows, fixed, FRI, today, NOW, "athlete")


def test_a_session_missed_this_week_is_planned_again_or_is_the_run_done_another_day():
    today = THU
    missed = _row(TUE, status=co.MISSED, manual=True)
    planned = _row(FRI)
    rows = [missed, planned]
    assert co.movable(missed, today) and not co.movable(_row(MONDAY - timedelta(days=2), status=co.MISSED), today), \
        "this week's only: the week is the plan's unit"
    # to a day to come: planned again; the session already there stays (a day behind cannot be taken in exchange)
    co.move(rows, missed, FRI, today, NOW, "athlete")
    assert (missed.day, missed.status, missed.manual, missed.pinned) == (FRI, co.PLANNED, False, True)
    assert planned.day == FRI and not planned.pinned
    # back to a day behind: only one that holds a run no session took (the next reading links them)
    again = _row(TUE, status=co.MISSED)
    with pytest.raises(co.Invalid, match="à partir d’aujourd’hui"):
        co.move([again], again, WED, today, NOW, "athlete")
    co.move([again], again, WED, today, NOW, "athlete", free={WED})
    assert (again.day, again.status) == (WED, co.PLANNED)
    # … of this week: a run of last week is not a session of this one
    late = _row(FRI)
    with pytest.raises(co.Invalid):
        co.move([late], late, MONDAY - timedelta(days=3), today, NOW, "athlete", free={MONDAY - timedelta(days=3)})


# ── the debrief (pure) ──────────────────────────────────────────────────────

def test_the_sessions_done_wait_a_week_for_their_ressenti():
    today = SUN
    rows = [_row(SUN, status=co.DONE), _row(FRI, status=co.DONE, ressenti=3), _row(WED, status=co.DONE),
            _row(SUN - timedelta(days=7), status=co.DONE), _row(SUN - timedelta(days=8), status=co.DONE),
            _row(SAT, status=co.MISSED), _row(SUN + timedelta(days=1))]
    for i, r in enumerate(rows):
        r.id = i + 1
    assert [r.day for r in co.awaiting_debrief(rows, today)] == [SUN, WED, SUN - timedelta(days=7)], \
        "the most recent first; said, missed, to come and older than a week left out"
    assert co.DEBRIEF_DAYS == 7 and co.awaiting_debrief([], today) == []
    assert [cv.day_word(d, today) for d in (SUN, SAT, WED, SUN - timedelta(days=7))] == [
        "d’aujourd’hui", "d’hier", "de mercredi", "du dimanche 15 novembre"]


# ── the page is the calendar ────────────────────────────────────────────────

def _cells(main: str) -> list[str]:
    return re.findall(r'<(?:a|button)[^>]*id="coach-day-[^>]*>.*?</(?:a|button)>', main, re.S)


async def test_the_page_is_a_calendar_whose_day_opens_in_place(client, people, db_session, clock):
    athlete = people.athlete
    athlete.max_heart_rate = 186
    clock.day = TUE  # an available day
    plan = await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")
    rows = await _rows(db_session, athlete)
    today = next(r for r in rows if r.day == TUE)
    main = _main((await client.get("/coaching")).text)
    # the calendar first, the day opened in it, the plan's chart behind a fold
    assert (main.index("pf-coach-calendar") < main.index('class="pf-coach-grid"') < main.index("pf-coach-daycard")
            < main.index('<details class="pf-coach-tool pf-coach-planfold" id="coach-plan">') < main.index("pf-viz-svg"))
    assert 'id="debrief"' in main and "pf-coach-debrief" not in main, "nothing waits: the anchor is the day's"
    # this week and the next, each with its line; fourteen days, each a link with a full name
    assert main.count('<div class="pf-coach-week') == 2 and "16–29\u00a0nov." in main
    assert "<b>Cette semaine</b>" in main and "<b>Semaine prochaine</b>" in main
    state = await co.sync(db_session, plan, athlete)
    for monday in (MONDAY, MONDAY + timedelta(days=7)):
        assert main.count(f'>{cv.week_summary(state, monday, {})["label"]}<') >= 1
    assert main.count('<span class="pf-coach-phase">Progression</span>') == 1, "the phase, said again when it changes"
    cells = _cells(main)
    assert len(cells) == 14 and all(re.search(r'aria-label="(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche) \d+ '
                                              r'novembre : ', c) for c in cells)
    assert sum(c.count("pf-coach-bar-s") for c in cells) == len([r for r in rows if r.day < MONDAY + timedelta(days=14)])
    mine = cells[1]
    assert "is-today is-selected" in mine and 'aria-current="true"' in mine
    assert (f'aria-label="mardi 17 novembre : {cp.KINDS[today.kind]} {hm_long(today.minutes)}, prévue (aujourd’hui)"'
            in mine)
    # a day reads without a legend: its block (kind, status, height) and its word
    assert f'<i class="pf-coach-bar-s is-{cv.KIND_CLASS[today.kind]} is-planned" style="--h: ' in mine
    assert f'<span class="pf-coach-word" aria-hidden="true">{cv.KIND_SHORT[today.kind]}</span>' in mine
    assert "pf-coach-legend" not in main and "pf-coach-todo" not in main
    # the day opened: ONE main action, « Déplacer » beside it, the others quiet
    card = main[main.index("pf-coach-daycard"):main.index('id="coach-plan"')]
    assert "Aujourd’hui · mardi 17 novembre" in card and 'id="coach-today"' not in card
    assert card.count("pf-btn-primary") == 2, "« C’est fait », and the save of the closed « Modifier » fold"
    done = re.search(rf'<button[^>]*id="coach-done-{today.id}"[^>]*>', card).group(0)
    assert 'value="done"' in done and "pf-btn-primary" in done and "C’est fait" in card
    assert re.search(rf'<a id="coach-move-{today.id}" class="pf-btn pf-btn-secondary"[^>]*'
                     rf'hx-get="/coaching/calendar\?d=2026-11-17&amp;w=2026-11-16&move={today.id}"[^>]*>Déplacer</a>', card)
    for quiet in (rf'<button[^>]*id="coach-missed-{today.id}"[^>]*class="pf-coach-quietbtn">Pas faite<',
                  r'<summary class="pf-coach-quietbtn">Modifier<'):
        assert re.search(quiet, card), quiet
    # a tap on a day keeps the weeks shown; the arrows slide them; a far day asked alone opens its own week
    assert 'hx-get="/coaching/calendar?d=2026-11-26&amp;w=2026-11-16"' in main and 'id="coach-prev"' not in main
    nxt = (await client.get("/coaching/calendar?w=2026-11-23", headers=HTMX)).text
    assert "23\u00a0nov. – 6\u00a0déc." in nxt and "<b>Semaine prochaine</b>" in nxt and "<b>30\u00a0nov. – 6\u00a0déc.</b>" in nxt
    assert 'hx-get="/coaching/calendar?w=2026-11-16"' in nxt and 'id="coach-today"' in nxt
    kept = (await client.get("/coaching/calendar?d=2026-12-03&w=2026-11-23", headers=HTMX)).text
    assert "23\u00a0nov. – 6\u00a0déc." in kept and 'id="coach-day-2026-12-03"' in kept
    far = (await client.get("/coaching/calendar?d=2026-12-17", headers=HTMX)).text
    assert "14–27\u00a0déc." in far and far.count('<div class="pf-coach-week') == 2
    assert "16–29\u00a0nov." in (await client.get("/coaching/calendar?d=2026-11-26", headers=HTMX)).text
    # the month: the same days, more weeks
    month = (await client.get("/coaching/calendar?d=2026-11-17&v=mois", headers=HTMX)).text
    assert "pf-coach-calendar is-month" in month and "novembre 2026" in month and 'aria-pressed="true">Mois' in month
    assert month.count('<div class="pf-coach-week') == 6 and len(_cells(month)) == 42
    assert month.count("is-out") == 12 and "pf-coach-word" in month and "&amp;w=2026-11-01&amp;v=mois" in month


async def test_ticking_asks_the_ressenti_first_and_once(client, people, db_session, clock):
    athlete = people.athlete
    clock.day = TUE
    await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")
    today = next(r for r in await _rows(db_session, athlete) if r.day == TUE)
    keep = {"v": "semaine", "w": "2026-11-16"}
    r = await client.post(f"/coaching/sessions/{today.id}/status", data={"value": "done", **keep}, headers=HTMX)
    # done by hand: no main action left; the one question at the top, the day marked until it is answered
    assert r.status_code == 200 and "pf-btn-primary" not in r.text and "C’est fait" not in r.text
    assert '<section class="pf-coach-card pf-coach-debrief' in r.text and 'id="debrief"' in r.text
    assert "Ta séance d’aujourd’hui est faite\u00a0— c’était comment" in r.text
    assert r.text.count("data-feel") == 5 and r.text.count('id="coach-ask-') == 5, "asked once: not in the day too"
    assert r.text.index("pf-coach-debrief") < r.text.index("pf-coach-calendar")
    assert r.text.count("pf-coach-todo") == 1 and ", faite, à débriefer" in r.text
    assert f'is-{cv.KIND_CLASS[today.kind]} is-done' in r.text
    assert re.search(rf'id="coach-undo-{today.id}" class="pf-coach-quietbtn" aria-label="Annuler « fait »">Annuler<', r.text)
    assert '<summary class="pf-coach-quietbtn">Ajouter une note</summary>' in r.text
    state = await co.sync(db_session, await co.get_plan(db_session, athlete.id), athlete)
    assert cv.page(state)["waiting"] == 1 and cv.page(state, coach=True)["waiting"] == 0
    # answered at the top, from another day: the calendar stays on the day that was open
    r = await client.post(f"/coaching/sessions/{today.id}/ressenti",
                          data={"value": "4", "back": "2026-11-19", **keep}, headers=HTMX)
    assert (await db_session.get(CoachingSession, today.id)).ressenti == 4
    assert "pf-coach-debrief" not in r.text and "pf-coach-todo" not in r.text and "jeudi 19 novembre" in r.text
    assert re.search(r'id="coach-day-2026-11-19"[^>]*class="pf-coach-day is-selected" aria-current="true"', r.text)
    # … and the day itself now carries the scale, with the level said (it can be changed there)
    r = await client.get("/coaching/calendar?d=2026-11-17", headers=HTMX)
    assert f'id="coach-r-{today.id}-4" data-feel aria-pressed="true"' in r.text and r.text.count("data-feel") == 5
    r = await client.post(f"/coaching/sessions/{today.id}/note", data={"note": "Bonnes jambes.", **keep}, headers=HTMX)
    assert '<p class="pf-coach-said is-note"><b>Ta note</b> Bonnes jambes.</p>' in r.text
    assert '<summary class="pf-coach-quietbtn">Modifier ma note</summary>' in r.text
    r = await client.post(f"/coaching/sessions/{today.id}/status", data={"value": "reset", **keep}, headers=HTMX)
    assert "C’est fait" in r.text and (await db_session.get(CoachingSession, today.id)).status == "planned"
    # « Pas faite », by hand: the way back, « C’est fait » no longer the main action, « Reporter » beside it
    r = await client.post(f"/coaching/sessions/{today.id}/status", data={"value": "missed", **keep}, headers=HTMX)
    assert 'aria-label="Annuler « pas faite »"' in r.text
    assert "pf-btn-secondary" in re.search(rf'<button[^>]*id="coach-done-{today.id}"[^>]*>', r.text).group(0)
    assert re.search(rf'<a id="coach-move-{today.id}" class="pf-btn pf-btn-secondary"[^>]*>Reporter</a>', r.text)
    # without JavaScript the form comes back to the page, on the same weeks
    r = await client.post(f"/coaching/sessions/{today.id}/status", data={"value": "done", **keep})
    assert r.status_code == 303 and r.headers["location"] == "/coaching?d=2026-11-17&w=2026-11-16"


async def test_a_link_opens_the_calendar_on_a_day_s_debrief(client, people, db_session, clock):
    """/coaching?d=<day>#debrief: what a notification will open. The question is the day asked's when it waits."""
    athlete = people.athlete
    await _plan(client, db_session, athlete, days=("0", "2", "4"), base="180")
    clock.day = FRI
    db_session.add_all([_run(athlete, MONDAY, 42, 1, name="Footing du lundi"), _run(athlete, WED, 50, 2)])
    await db_session.flush()
    page = (await client.get("/coaching")).text
    main = _main(page)
    assert "Ta sortie de mercredi est arrivée\u00a0— c’était comment" in main, "the most recent first"
    assert "Puis 1 autre à débriefer." in main and main.count("pf-coach-todo") == 2
    wed = next(r for r in await _rows(db_session, athlete) if r.day == WED)
    assert f'action="/coaching/sessions/{wed.id}/ressenti"' in main and main.count('id="debrief"') == 1
    asked = _main((await client.get("/coaching?d=2026-11-16")).text)
    mon = next(r for r in await _rows(db_session, athlete) if r.day == MONDAY)
    assert "Ta sortie de lundi est arrivée" in asked and f'action="/coaching/sessions/{mon.id}/ressenti"' in asked
    assert "Footing du lundi" in asked and asked.count("data-feel") == 5, "said at the top, not twice"
    assert f"{cp.KINDS[mon.kind]} {hm(mon.minutes)} · " in asked
    state = await co.sync(db_session, await co.get_plan(db_session, athlete.id), athlete)
    assert cv.page(state)["waiting"] == 2 == len(co.awaiting_debrief(state.rows, FRI))
    # both answered: no question, the anchor is the day's own
    for row in (wed, mon):
        await client.post(f"/coaching/sessions/{row.id}/ressenti", data={"value": "3"}, headers=HTMX)
    main = _main((await client.get("/coaching?d=2026-11-16")).text)
    assert "pf-coach-debrief" not in main and '<div class="pf-coach-daycard" id="debrief"' in main
    # the follower: never asked, no mark, the same calendar
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=people.coach.id))
    mon.ressenti = None
    await db_session.flush()
    theirs = (await _as(client, people.coach).get(f"/coaching/athletes/{athlete.id}?d=2026-11-16")).text
    assert "pf-coach-calendar" in theirs and theirs.count('<div class="pf-coach-week') == 2
    assert "pf-coach-debrief" not in theirs and "pf-coach-todo" not in theirs and 'id="debrief"' not in theirs
    assert "c’était comment" not in theirs and "Ressenti : non renseigné" in theirs and "C’est fait" not in theirs


async def test_moving_is_one_tap_on_any_day_and_two_hard_days_are_said(client, people, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")  # Tuesday, Thursday, Sunday
    rows = await _rows(db_session, athlete)
    hard = next(r for r in rows if r.day == THU)
    assert hard.kind in (cp.QUALITY, cp.HILLS) and next(r for r in rows if r.day == SUN).kind == cp.LONG
    r = await client.get(f"/coaching/calendar?d=2026-11-19&w=2026-11-16&move={hard.id}", headers=HTMX)
    assert f"Où placer «&nbsp;{cp.KINDS[hard.kind]} {hm(hard.minutes)}&nbsp;»" in r.text
    assert "C’est fait" not in r.text and 'id="coach-move-cancel"' in r.text
    cells = {re.search(r'id="coach-day-([\d-]+)"', c).group(1): c for c in _cells(r.text)}
    assert len(cells) == 14 and all(c.startswith("<button") for c in cells.values())
    # every day from today on takes it — the plan's days or not; its own day does not
    targets = [day for day, c in cells.items() if "is-target" in c]
    assert targets == [f"2026-11-{d}" for d in (16, 17, 18, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29)]
    assert "disabled" in cells["2026-11-19"] and "disabled" not in cells["2026-11-21"]
    # … and the days next to another hard session carry a warning, not a refusal
    warned = [day for day, c in cells.items() if "is-warn" in c]
    assert "2026-11-21" in warned and "2026-11-23" in warned and "2026-11-20" not in warned
    assert 'aria-label="Déplacer au samedi 21 novembre : repos — deux jours durs de suite"' in cells["2026-11-21"]
    assert "tu peux le choisir quand même" in r.text
    r = await client.post(f"/coaching/sessions/{hard.id}/move", data={"to": "2026-11-21", "w": "2026-11-16"},
                          headers=HTMX)
    moved = await db_session.get(CoachingSession, hard.id)
    assert r.status_code == 200 and (moved.day, moved.pinned, moved.origin, moved.auto_day) == (SAT, True, "athlete", THU)
    assert ('<p class="pf-coach-reason pf-coach-hardpair">Samedi et dimanche\u00a0: deux jours durs de suite. '
            'Tu peux courir la séance en footing.</p>') in r.text, "a quality session: it may be run as a footing"
    assert "samedi 21 novembre" in r.text and "Modifiée par toi" in r.text
    # it stays there: a reading of the plan never rewrites a session a person moved, nor fills the day it left
    await client.get("/coaching")
    rows = await _rows(db_session, athlete)
    assert (await db_session.get(CoachingSession, hard.id)).day == SAT
    assert not [x for x in rows if x.day == THU], "Thursday stays free"


async def test_a_missed_session_is_reported_or_was_the_run_of_another_day(client, people, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")
    clock.day = THU
    db_session.add(_run(athlete, WED, 38, 1, name="Footing avec Léa"))
    await db_session.flush()
    page = _main((await client.get("/coaching?d=2026-11-18")).text)
    tue = next(r for r in await _rows(db_session, athlete) if r.day == TUE)
    assert tue.status == "missed"
    # Wednesday: a grey block in the calendar, the run offered as it is — and as the session it may have been
    cell = next(c for c in _cells(page) if 'id="coach-day-2026-11-18"' in c)
    assert 'is-extra is-done' in cell and ">Sortie</span>" in cell and "une sortie hors plan" in cell
    assert "Hors plan · Footing avec Léa" in page and "c’était comment" not in page, "nothing to answer"
    assert re.search(rf'<button type="submit" id="coach-attach-{tue.id}" class="pf-coach-quietbtn">'
                     rf'C’est ma séance de mardi <span>· {cp.KINDS[tue.kind]} {hm(tue.minutes)}</span></button>', page)
    assert f'action="/coaching/sessions/{tue.id}/move"' in page and '<input type="hidden" name="to" value="2026-11-18">' in page
    r = await client.post(f"/coaching/sessions/{tue.id}/move", data={"to": "2026-11-18", "w": "2026-11-16"},
                          headers=HTMX)
    await db_session.refresh(tue)
    assert (tue.day, tue.status, tue.done_minutes, tue.pinned) == (WED, "done", 38, True) and tue.activity_id
    assert "Hors plan" not in r.text and "Ta sortie d’hier est arrivée\u00a0— c’était comment" in r.text
    # a missed session left on its day: « Reporter », to any day to come
    await client.post(f"/coaching/sessions/{tue.id}/status", data={"value": "missed"}, headers=HTMX)
    r = await client.get("/coaching/calendar?d=2026-11-18", headers=HTMX)
    assert re.search(rf'<a id="coach-move-{tue.id}"[^>]*>Reporter</a>', r.text)
    r = await client.post(f"/coaching/sessions/{tue.id}/move", data={"to": "2026-11-20"}, headers=HTMX)
    await db_session.refresh(tue)
    assert (tue.day, tue.status, tue.manual) == (FRI, "planned", False) and "vendredi 20 novembre" in r.text
    # last week's is behind for good; a day behind without a run is refused, in place
    old = CoachingSession(plan_id=tue.plan_id, user_id=athlete.id, day=MONDAY - timedelta(days=3), kind=cp.FOOTING,
                          minutes=40, structure=cp.default_structure(cp.FOOTING, 40), status="missed")
    db_session.add(old)
    await db_session.flush()
    r = await client.post(f"/coaching/sessions/{old.id}/move", data={"to": "2026-11-20"}, headers=HTMX)
    assert r.status_code == 200 and "Cette séance ne se déplace pas." in r.text
    r = await client.post(f"/coaching/sessions/{tue.id}/move", data={"to": "2026-11-17"}, headers=HTMX)
    assert "à partir d’aujourd’hui" in r.text and (await db_session.get(CoachingSession, tue.id)).day == FRI


# ── a run that does not look like the session: « Fait autrement », and the way out ──
def test_a_run_far_from_the_planned_duration_is_a_misfit_never_the_race():
    from types import SimpleNamespace as Run
    row = _row(TUE)  # 45 min planned: within 20 % is the session
    assert [co.misfit(row, Run(minutes=m)) for m in (36, 45, 54, 35, 55, 197)] == [False] * 3 + [True] * 3
    assert not co.misfit(row, None) and not co.misfit(_row(TUE, cp.RACE), Run(minutes=197))


async def test_a_run_that_does_not_match_is_said_and_can_leave_the_session(client, people, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")
    clock.day = TUE
    tue = next(r for r in await _rows(db_session, athlete) if r.day == TUE)
    db_session.add(_run(athlete, TUE, 197, 2, name="Chantier"))
    await db_session.flush()
    page = _main((await client.get("/coaching")).text)
    await db_session.refresh(tue)
    assert tue.status == "done" and tue.activity_id and tue.done_minutes == 197, "the day's run is the day's load"
    assert '<span class="pf-pill pf-pill-soft">Fait autrement</span>' in page and ">Fait</span>" not in page
    assert f'<p class="pf-coach-s-guide pf-coach-misfit">{hm(197)} au lieu de {hm(tue.minutes)}</p>' in page
    assert re.search(rf'<button type="submit" name="value" value="missed" id="coach-unlink-{tue.id}" '
                     r'class="pf-coach-quietbtn">Ce n’est pas cette séance</button>', page)
    # the way out: the run goes back out of the plan, the session is not done and can be planned again
    r = await client.post(f"/coaching/sessions/{tue.id}/status", data={"value": "missed"}, headers=HTMX)
    await db_session.refresh(tue)
    assert (tue.status, tue.activity_id, tue.done_minutes, tue.manual) == ("missed", None, None, True)
    assert "Hors plan · Chantier" in r.text and "Fait autrement" not in r.text
    assert re.search(rf'<a id="coach-move-{tue.id}"[^>]*>Reporter</a>', r.text)
    r = await client.get("/coaching/calendar", headers=HTMX)  # the next reading leaves the choice alone
    await db_session.refresh(tue)
    assert tue.status == "missed" and tue.activity_id is None
    # a run close to the plan is simply « Fait », with the same way out
    thu = next(r for r in await _rows(db_session, athlete) if r.day == THU)
    db_session.add(_run(athlete, THU, thu.minutes + 3, 3, name="Footing"))
    await db_session.flush()
    clock.day = THU
    page = _main((await client.get("/coaching")).text)
    await db_session.refresh(thu)
    assert thu.status == "done" and thu.activity_id
    assert ">Fait</span>" in page and "Fait autrement" not in page and f'id="coach-unlink-{thu.id}"' in page
