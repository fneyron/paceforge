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


# ── the page: the road, the board and its next week, the day as a ticket ────

def _cells(main: str) -> list[str]:
    return re.findall(r'<(?:a|button)[^>]*id="coach-day-[^>]*>.*?</(?:a|button)>', main, re.S)


def _tiles(cell: str) -> list[tuple[str, str, int | None]]:
    """A day's tiles: (kind, status, height in % of the slot — None in the next week's small row)."""
    return [(k, st, int(h) if h else None) for k, st, h in
            re.findall(r'<i class="pf-coach-tile is-(\w+) is-(\w+)"(?: style="--h: (\d+)")?', cell)]


def test_a_tile_is_as_tall_as_its_session_on_one_scale_for_every_week():
    assert [cv.tile_height(m) for m in (20, 30, 75, 120, 210, 300)] == [28, 28, 45, 62, 95, 95]
    assert [cv.tile_label(m) for m in (40, 60, 115, 120)] == ["40′", "1h", "1h55", "2h"]
    assert cv.big_duration(40) == {"h": None, "m": "40"} and cv.big_duration(65) == {"h": "1", "m": "05"}
    assert cv.big_duration(120) == {"h": "2", "m": ""}


async def test_the_page_is_the_road_the_board_and_the_day_s_ticket(client, people, db_session, clock):
    athlete = people.athlete
    athlete.max_heart_rate = 186
    clock.day = TUE  # an available day
    plan = await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")
    rows = await _rows(db_session, athlete)
    today = next(r for r in rows if r.day == TUE)
    main = _main((await client.get("/coaching")).text)
    # the road, then the board and the next week's row, the day's ticket last; no chart, no « Mois »
    assert (main.index('class="pf-coach-road"') < main.index('class="pf-coach-calendar"')
            < main.index('class="pf-coach-board"') < main.index("pf-coach-week is-next") < main.index("pf-coach-daycard"))
    assert "pf-viz" not in main and ">Mois<" not in main and 'id="coach-plan"' not in main
    assert 'id="debrief"' in main and "pf-coach-debrief" not in main, "nothing waits: the anchor is the day's"
    # the road: every week of the plan a bar, as tall as the time it plans, the current one shown, its address
    state = await co.sync(db_session, plan, athlete)
    road = cv.page(state)["road"]
    bars = re.findall(r'<a class="pf-coach-wbar is-(\w+)( is-current)?( is-shown)?( is-race)?" style="--h: (\d+)" '
                      r'href="/coaching\?w=([\d-]+)" hx-get="/coaching/calendar\?w=\6"[^>]*tabindex="(-?\d)"', main)
    assert len(bars) == len(road["bars"]) >= 8 and bars[0][5] == MONDAY.isoformat(), "from the plan's first week"
    assert [b[5] for b in bars if b[1]] == [b[5] for b in bars if b[2]] == [MONDAY.isoformat()]
    assert [b[6] for b in bars].count("0") == 1 and bars[0][6] == "0", "one bar in the tab order: the arrows move"
    planned: dict = {}
    for r in rows:
        if r.kind != cp.RACE:
            planned[cp.monday_of(r.day)] = planned.get(cp.monday_of(r.day), 0) + r.minutes
    peak = max(planned.values())
    assert [int(b[4]) for b in bars] == [round(22 + 78 * planned.get(date.fromisoformat(b[5]), 0) / peak) if
                                         planned.get(date.fromisoformat(b[5])) else 6 for b in bars]
    assert {b[0] for b in bars} == {"progression"} and not road["race"], "no race: one phase, no flag"
    assert f'data-label="16–22\u00a0nov. · {hm(planned[MONDAY])} prévues · Progression"' in main
    assert '<b class="pf-coach-lead">Progresser</b> <span class="pf-coach-race">sans course</span>' in main
    assert f">Semaine 1 sur {len(road['bars'])}<" in main
    # the board: this week, its line and phase; then next week as a row — fourteen days, each a link with a full name
    assert main.count('<div class="pf-coach-week is-') == 2 and '<h2 id="coach-cal-h" class="pf-coach-period">Cette semaine</h2>' in main
    assert '<span id="coach-next-h">Semaine prochaine</span>' in main
    for monday in (MONDAY, MONDAY + timedelta(days=7)):
        assert main.count(f'>{cv.week_summary(state, monday, {})["line"]}<') == 1
    assert main.count('<span class="pf-coach-phase">Progression</span>') == 1, "the board's week says its phase"
    cells = _cells(main)
    assert len(cells) == 14 and all(re.search(r'aria-label="(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche) \d+ '
                                              r'novembre : ', c) for c in cells)
    assert sum(len(_tiles(c)) for c in cells) == len([r for r in rows if r.day < MONDAY + timedelta(days=14)])
    mine = cells[1]
    assert "is-today is-selected" in mine and 'aria-current="true"' in mine
    assert (f'aria-label="mardi 17 novembre : {cp.KINDS[today.kind]} {hm_long(today.minutes)}, prévue (aujourd’hui)"'
            in mine)
    # a tile reads without a legend: its kind, its status, a height that follows its duration, its word under it
    for cell in cells[:7]:
        day = re.search(r'id="coach-day-([\d-]+)"', cell).group(1)
        on = [r for r in rows if r.day.isoformat() == day]
        assert _tiles(cell) == [(cv.KIND_CLASS[r.kind], "planned", 100 if r.kind == cp.RACE else cv.tile_height(r.minutes))
                                for r in on], day
    assert f'<span class="pf-coach-word" aria-hidden="true">{cv.KIND_SHORT[today.kind]}</span>' in mine
    assert f">{cv.tile_label(today.minutes)}</span></i>" in mine
    # the next week's row: small tiles of no height, no word, each day a link to its week
    assert all(" is-mini" in c and "pf-coach-word" not in c and all(h is None for *_, h in _tiles(c)) for c in cells[7:])
    assert 'hx-get="/coaching/calendar?d=2026-11-26&w=2026-11-23"' in main
    assert "pf-coach-legend" not in main and "pf-coach-todo" not in main
    # the day: its ticket — the title, the duration large, the effort drawn —, ONE main action, the others quiet
    card = main[main.index("pf-coach-daycard"):]
    assert '<h3 id="coach-day-h" class="pf-coach-date"><span>Aujourd’hui</span> <small>mardi 17 novembre</small></h3>' in card
    assert 'id="coach-today"' not in main, "today's week: no way back to ask for"
    assert f'<article class="pf-coach-session is-{cv.KIND_CLASS[today.kind]} is-planned"' in card
    big = cv.big_duration(today.minutes)
    assert f'<p class="pf-coach-t-dur" aria-hidden="true">{big["m"]}<small>min</small></p>' in card or big["h"]
    assert ('class="pf-effort"' in card) != ('class="pf-coach-gauge' in card), "a profile or a gauge, in the ticket"
    assert card.count("pf-btn-primary") == 2, "« C’est fait », and the save of the closed « Modifier » fold"
    done = re.search(rf'<button[^>]*id="coach-done-{today.id}"[^>]*>', card).group(0)
    assert 'value="done"' in done and "pf-btn-primary" in done and "C’est fait" in card
    assert re.search(rf'<button type="submit" name="value" value="missed" id="coach-missed-{today.id}" '
                     r'class="pf-coach-quietbtn">Pas faite<', card), "quiet, beside it"
    assert re.search(rf'<a id="coach-move-{today.id}" class="pf-coach-quietbtn"[^>]*'
                     rf'hx-get="/coaching/calendar\?d=2026-11-17&amp;w=2026-11-16&move={today.id}"[^>]*>Déplacer</a>', card)
    assert re.search(r'<summary class="pf-coach-quietbtn">Modifier<', card)
    # a tap on a day keeps the week shown; the arrows slide it; a day asked outside it opens its own week
    assert 'hx-get="/coaching/calendar?d=2026-11-19&w=2026-11-16"' in main and 'id="coach-prev"' not in main
    nxt = (await client.get("/coaching/calendar?w=2026-11-23", headers=HTMX)).text
    assert '<h2 id="coach-cal-h" class="pf-coach-period">Semaine prochaine</h2>' in nxt
    assert '<span id="coach-next-h">Semaine suivante</span>' in nxt and 'id="coach-day-2026-12-06"' in nxt
    assert 'hx-get="/coaching/calendar?w=2026-11-16"' in nxt and 'id="coach-today"' in nxt
    assert 'class="pf-coach-wbar is-progression is-shown"' in nxt, "the road shows the week the board shows"
    kept = (await client.get("/coaching/calendar?d=2026-12-03&w=2026-11-23", headers=HTMX)).text
    assert ">30\u00a0nov. – 6\u00a0déc.</h2>" in kept and 'class="pf-coach-day is-selected" aria-current' in kept
    far = (await client.get("/coaching/calendar?d=2026-12-17", headers=HTMX)).text
    assert ">14–20\u00a0déc.</h2>" in far and far.count('<div class="pf-coach-week is-') == 2
    assert ">Cette semaine</h2>" in (await client.get("/coaching/calendar?d=2026-11-19", headers=HTMX)).text
    # the address of the « Mois » view of before: the week board of its day (the road took its place)
    month = (await client.get("/coaching/calendar?d=2026-11-17&v=mois", headers=HTMX)).text
    assert "is-month" not in month and len(_cells(month)) == 14 and ">Cette semaine</h2>" in month


async def test_ticking_asks_the_ressenti_first_and_once(client, people, db_session, clock):
    athlete = people.athlete
    clock.day = TUE
    await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")
    today = next(r for r in await _rows(db_session, athlete) if r.day == TUE)
    keep = {"v": "semaine", "w": "2026-11-16"}
    r = await client.post(f"/coaching/sessions/{today.id}/status", data={"value": "done", **keep}, headers=HTMX)
    # done by hand: no main action left; the one question at the top, the day marked until it is answered
    assert r.status_code == 200 and "pf-btn-primary" not in r.text and "C’est fait" not in r.text
    assert '<section class="pf-coach-debrief' in r.text and 'id="debrief"' in r.text
    assert "<b>Ta séance d’aujourd’hui est faite.</b> C’était comment" in r.text
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
    assert re.search(rf'<a id="coach-move-{today.id}" class="pf-coach-quietbtn"[^>]*>Reporter</a>', r.text)
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
    assert "<b>Ta sortie de mercredi est arrivée.</b> C’était comment" in main, "the most recent first"
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
    assert "pf-coach-calendar" in theirs and theirs.count('<div class="pf-coach-week is-') == 2 and "pf-coach-wbar" in theirs
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


async def test_by_hand_the_calendar_carries_what_moving_answers(client, people, db_session, clock):
    """A session held and dropped on another day (coaching-calendar.js) posts the move « Déplacer » posts: its block
    carries the days that take it and those that warn, the very answers of the moving mode; the follower picks up
    what « Déplacer » gives them, never the run outside the plan."""
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")  # Tuesday, Thursday, Sunday
    clock.day = WED
    db_session.add(_run(athlete, WED, 38, 1, name="Footing avec Léa"))
    await db_session.flush()
    main = _main((await client.get("/coaching")).text)
    rows = await _rows(db_session, athlete)
    hard = next(r for r in rows if r.kind == cp.HILLS)  # next Thursday's
    block = re.search(rf'<i class="pf-coach-tile [^"]*" data-move="/coaching/sessions/{hard.id}/move"'
                      r' data-to="([\d -]*)" data-warn="([\d -]*)" data-what="([^"]+)"><span>', main)
    assert block, "next week's, in the small row: a drop source and a drop target too"
    assert block.group(3) == f"{cv.KIND_SHORT[hard.kind]} {hm(hard.minutes)}"
    moving = (await client.get(f"/coaching/calendar?d=2026-11-18&w=2026-11-16&move={hard.id}", headers=HTMX)).text
    assert 'is-target' in moving
    cells = {re.search(r'id="coach-day-([\d-]+)"', c).group(1): c for c in _cells(moving)}
    assert block.group(1).split() == [d for d, c in cells.items() if "is-target" in c], "the same days as « Déplacer »"
    assert block.group(2).split() == [d for d, c in cells.items() if "is-warn" in c] and "2026-11-23" in block.group(2)
    assert "data-move" not in moving and 'id="coach-drag-hint"' not in moving, "none while the days are targets"
    # only what « Déplacer » takes (a session done is never held); the run of this week may be dropped on a session
    # (« C’est ma séance de … »); each day says its date to the script
    tue = next(r for r in rows if r.day == TUE)
    assert tue.status == co.MISSED and f"/sessions/{tue.id}/move" in main, "missed this week: « Reporter », by hand too"
    done = _row(MONDAY, status=co.DONE, plan_id=tue.plan_id, user_id=athlete.id, manual=True, done_minutes=45)
    db_session.add(done)
    await db_session.flush()
    main = _main((await client.get("/coaching")).text)
    assert f"/sessions/{done.id}/move" not in main
    assert f'<i class="pf-coach-tile is-extra is-done" style="--h: {cv.tile_height(38)}" data-run=' in main
    assert 'data-run="2026-11-18" data-what="Sortie"' in main
    assert 'id="coach-day-2026-11-19" data-day="2026-11-19" hx-get=' in main
    assert ('<p class="pf-coach-drag-hint" id="coach-drag-hint" aria-hidden="true" hidden>'
            'Maintiens une séance pour la déplacer</p>') in main
    assert '<div class="pf-coach-grid" data-w="2026-11-16">' in main
    # the follower: the same sessions, never the run outside the plan
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=people.coach.id))
    await db_session.flush()
    theirs = (await _as(client, people.coach).get(f"/coaching/athletes/{athlete.id}")).text
    assert f'data-move="/coaching/athletes/{athlete.id}/sessions/{hard.id}/move" data-to="{block.group(1)}"' in theirs
    assert "data-run" not in theirs

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
    assert "Hors plan" not in r.text and "<b>Ta sortie d’hier est arrivée.</b> C’était comment" in r.text
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
    assert '<span class="pf-coach-status">Fait autrement</span>' in page and ">Fait</span>" not in page
    assert f'<p class="pf-coach-t-dur" aria-hidden="true">3<small>h</small>17 <span>prévu {hm(tue.minutes)}</span></p>' \
        in page, "the ticket says the time run, large, and the time planned beside it"
    assert f'<i class="pf-coach-tile is-{cv.KIND_CLASS[tue.kind]} is-done" style="--h: {cv.tile_height(197)}">' in page
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


# ── the road to the race ────────────────────────────────────────────────────

async def test_the_road_runs_to_the_race_its_phases_named_and_its_flag_on_the_last_bar(client, people, db_session,
                                                                                        clock):
    from app.models.route import Route

    athlete = people.athlete
    clock.day = TUE
    route = Route(user_id=athlete.id, name="Trail des Crêtes", total_distance_km=42, total_elevation_gain=2000,
                  target_time_s=6 * 3600, race_date=(TUE + timedelta(days=75)).isoformat())
    db_session.add(route)
    await db_session.flush()
    plan = await _plan(client, db_session, athlete, goal=str(route.id), days=("1", "3", "6"))
    state = await co.sync(db_session, plan, athlete)
    road = cv.page(state)["road"]
    race_week = cp.monday_of(TUE + timedelta(days=75))
    bars = road["bars"]
    # every week from the plan's first to the race's, the race's last, full, flagged; the current week first here
    assert bars[0]["monday"] == MONDAY.isoformat() and bars[-1]["monday"] == race_week.isoformat()
    assert [b["race"] for b in bars] == [False] * (len(bars) - 1) + [True] and bars[-1]["h"] == 100 and road["race"]
    assert bars[0]["current"] and bars[0]["shown"] and sum(b["current"] for b in bars) == 1
    assert all(6 <= b["h"] <= 100 for b in bars)
    # the phases in order, each named once with its length in weeks
    assert [p["key"] for p in road["phases"]] == ["base", "specifique", "affutage"]
    assert sum(p["weeks"] for p in road["phases"]) == len(bars)
    assert [p["label"] for p in road["phases"]] == ["Base", "Spécifique", "Affûtage"]
    assert bars[-1]["aria"].startswith("Semaine de la course, ") and bars[0]["aria"].startswith("Semaine en cours, ")
    assert road["tip"] == f"Semaine 1 sur {len(bars)}" and road["aria"].endswith("jusqu’à la course")
    # the heading: « J‑75 », the race, its time with the way to its page (a follower: the words only)
    head = cv.page(state)["head"]
    assert (head["lead"], head["race"]) == ("J‑75", "Trail des Crêtes") and head["time"]["href"]
    assert cv.page(state, coach=True)["head"]["time"]["href"] is None
    # the board on the race's week: no next week's row (the plan ends there), « Semaine x sur x »
    last = cv.page(state, w=race_week)
    assert len(last["cal"]["weeks"]) == 1 and last["road"]["tip"] == f"Semaine {len(bars)} sur {len(bars)}"
    assert last["road"]["bars"][-1]["shown"] and not last["cal"]["next"]
    page = (await client.get(f"/coaching?w={race_week.isoformat()}")).text
    assert 'class="pf-coach-week is-next"' not in page and 'class="pf-coach-wbar is-affutage is-shown is-race"' in page


def test_a_long_plan_keeps_thirty_bars_the_current_week_among_them():
    from types import SimpleNamespace as NS

    start = MONDAY - timedelta(weeks=20)
    rows = [_row(start + timedelta(weeks=i, days=1)) for i in range(45)]
    for r in rows:
        r.minutes = 60
    inputs = cp.Inputs((1,), 1, 180, start, cp.Goal(None, None, None), {}, intent=cp.PROGRESSER)
    state = NS(rows=rows, plan=NS(start_monday=start, adaptations={}), weeks=[], goal=cp.Goal(None, None, None),
               inputs=inputs, clock=NS(today=TUE))
    road = cv.road(state, MONDAY)
    assert len(road["bars"]) == cv.ROAD_WEEKS and any(b["current"] for b in road["bars"])
    last = start + timedelta(weeks=44)
    assert road["bars"][-1]["monday"] == last.isoformat(), "the plan's end stays on the road"
    assert road["bars"][0]["monday"] == (last - timedelta(weeks=29)).isoformat(), "its first weeks give way first"
    late = cv.road(NS(**{**vars(state), "clock": NS(today=last - timedelta(weeks=2))}), MONDAY)
    assert late["bars"][-1]["monday"] == last.isoformat() and late["tip"] == "Semaine 21 sur 45"
    assert not road["race"] and {b["h"] for b in road["bars"]} == {100}
