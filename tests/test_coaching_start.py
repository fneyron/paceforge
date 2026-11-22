"""Coaching, the screens (docs/coaching.md › « Les réponses de départ », « Les écrans »): the onboarding proposes a
plan before the athlete touches anything — the days they usually run (coaching_start, pure), the week these answers
make drawn by the generator itself (GET /coaching/apercu: truthful, writes nothing, answered 200) — and the page
opens on the day: one main action, the week as bars, the tools behind « Options du plan »."""
import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.coaching import CoachingPlan, CoachingSession
from app.services import coaching as co
from app.services import coaching_plan as cp
from app.services import coaching_start as cst
from app.services import coaching_view as cv
from app.services import sante_training
from app.services.viz import hm
from tests.test_coaching import HTMX, _as, _past_session, _plan, _rows, _run, beta, people  # noqa: F401
from tests.test_coaching_goal import _race

MONDAY = date(2026, 11, 16)
NNBSP = " "


@pytest.fixture
def clock(monkeypatch):
    """A « today » the test moves (the pages and the plan read sante_training.athlete_today)."""
    class Clock:
        day = MONDAY

    async def today(*a, **k):
        return Clock.day
    monkeypatch.setattr(sante_training, "athlete_today", today)
    return Clock


@pytest.fixture(autouse=True)
def no_kept_prediction():
    """The preview keeps a race's times per worker, race and day: each test reads its own."""
    co._PEEKED.clear()
    yield
    co._PEEKED.clear()


def _runs(*spec) -> list:
    """Runs as coaching_start reads them: (weeks ago, weekday, minutes), counted back from MONDAY's week."""
    return [SimpleNamespace(day=MONDAY - timedelta(weeks=w) + timedelta(days=wd), minutes=m) for w, wd, m in spec]


# ── the days the athlete usually runs (pure) ────────────────────────────────

def test_the_habitual_days_are_the_weekdays_run_most_often_and_the_usual_long_run_day():
    # eight weeks on Tuesday, Thursday and Saturday, the long run on Saturday
    steady = _runs(*[(w, wd, m) for w in range(1, 9) for wd, m in ((1, 45), (3, 50), (5, 95))])
    assert cst.habitual_days(steady, MONDAY) == cst.Proposal((1, 3, 5), 5, True)
    # a fourth day now and then does not make four days: the usual week has three
    assert cst.habitual_days(steady + _runs((2, 6, 30), (5, 0, 30)), MONDAY).days == (1, 3, 5)
    # five days a week, the long run on Sunday; a sixth day every other week is left out
    five = _runs(*[(w, wd, 40 + 20 * (wd == 6)) for w in range(1, 9) for wd in (0, 1, 3, 4, 6)])
    assert cst.habitual_days(five, MONDAY) == cst.Proposal((0, 1, 3, 4, 6), 6, True)
    # six or seven days a week: five are proposed (HABIT_DAYS), the ones with the most minutes on a tie
    daily = _runs(*[(w, wd, 30 + wd) for w in range(1, 9) for wd in range(7)])
    assert cst.habitual_days(daily, MONDAY) == cst.Proposal((2, 3, 4, 5, 6), 6, True)
    # twice a week on changing days: three days are proposed, the most run first (the most minutes on a tie)
    loose = _runs((1, 1, 40), (1, 5, 60), (2, 1, 40), (2, 6, 60), (3, 3, 40), (3, 5, 60), (4, 1, 40), (4, 5, 60))
    assert cst.habitual_days(loose, MONDAY) == cst.Proposal((1, 5, 6), 5, True)
    # only ever on two weekdays: those two (a plan takes two days)
    two = _runs(*[(w, wd, 50) for w in range(1, 5) for wd in (2, 6)])
    assert cst.habitual_days(two, MONDAY) == cst.Proposal((2, 6), 6, True)
    # the weekday that most often held the longest run (a Saturday, three weeks out of eight) is not one of the
    # three days proposed: Sunday when it is proposed, else the last day proposed
    def odd(days):
        return _runs(*[(w, wd, 45 if wd == days[w % 3] else 40) for w in range(1, 9) for wd in days],
                     (1, 5, 120), (2, 5, 120), (3, 5, 120))
    assert cst.habitual_days(odd((0, 2, 6)), MONDAY) == cst.Proposal((0, 2, 6), 6, True)
    assert cst.habitual_days(odd((0, 2, 3)), MONDAY) == cst.Proposal((0, 2, 3), 3, True)


def test_no_habit_to_read_gives_the_default_week():
    assert cst.habitual_days([], MONDAY) is None
    assert cst.habitual_days(_runs((1, 1, 40), (2, 3, 40), (3, 5, 40)), MONDAY) is None, "three runs are not a habit"
    assert cst.habitual_days(_runs(*[(w, 6, 60) for w in range(1, 9)]), MONDAY) is None, "one weekday only"
    # only the 8 weeks before today are read: older runs, today's and a run without a duration do not count
    old = _runs(*[(w, wd, 45) for w in range(9, 14) for wd in (0, 2, 4)])
    assert cst.habitual_days(old + _runs((0, 0, 45), (1, 1, 0)), MONDAY) is None
    # two runs the same day are one day of running
    assert cst.habitual_days(_runs((1, 1, 20), (1, 1, 20), (2, 1, 20), (2, 1, 20), (3, 1, 30)), MONDAY) is None
    assert cst.proposal([], MONDAY) == cst.Proposal((1, 3, 6), 6, False)
    assert cst.proposal([], MONDAY).days == cst.DEFAULT_DAYS and 2 <= len(cst.DEFAULT_DAYS) <= 6
    assert cst.DEFAULT_LONG in cst.DEFAULT_DAYS


# ── the week the onboarding draws (pure): the generator's own ───────────────

def _bars(pv) -> list[tuple[int, str, str]]:
    return [(i, b["cls"], b["duration"]) for i, c in enumerate(pv["cols"]) for b in c["bars"]]


def _as_bars(sessions) -> list[tuple[int, str, str]]:
    return [(s.day.weekday(), cv.KIND_CLASS[s.kind], "" if s.kind == cp.RACE else hm(s.minutes).replace(" min", NNBSP + "min"))
            for s in sorted(sessions, key=lambda s: s.day)]


@pytest.mark.parametrize("today", [MONDAY, MONDAY + timedelta(days=3), MONDAY + timedelta(days=6)])
@pytest.mark.parametrize("days,long_day,base", [((1, 3, 6), 6, 120), ((0, 1, 3, 4, 6), 6, 300), ((2, 5), 5, 60)])
def test_the_first_week_is_the_generators_own(today, days, long_day, base):
    pv = cv.first_week(days, long_day, base, cp.Goal(), today)
    weeks = cp.build(cp.Inputs(days, long_day, base, cp.monday_of(today), cp.Goal(), {}), today)
    week = weeks[0] if today.weekday() == 0 else weeks[1]  # the first complete week
    assert pv["label"] == cv.week_label(week.monday) and _bars(pv) == _as_bars(week.sessions)
    assert [c["wd"] for c in pv["cols"]] == ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
    assert all(cv.BAR_MIN <= b["h"] <= 100 for c in pv["cols"] for b in c["bars"])
    longest = max(week.sessions, key=lambda s: s.minutes)
    tall = [b for c in pv["cols"] for b in c["bars"] if b["h"] == max(x["h"] for k in pv["cols"] for x in k["bars"])]
    assert tall[0]["duration"] == hm(longest.minutes).replace(" min", NNBSP + "min"), "the tallest bar is the longest"
    # the first session to come is this week's, from today on (never a day already behind)
    ahead = [s for w in weeks for s in w.sessions if s.day >= today]
    when = "aujourd’hui" if ahead[0].day == today else "demain" if ahead[0].day == today + timedelta(days=1) else None
    assert pv["first"]["what"] == f"{cp.KINDS[ahead[0].kind]} {hm(ahead[0].minutes)}"
    assert when is None or pv["first"]["when"] == when
    assert f"{len(week.sessions)} séance" in pv["aria"] and pv["legend"] and pv["empty"] is None


def test_a_day_chosen_that_holds_no_session_is_said():
    # 2 h on four days: no session is shorter than 30 min, a day stays free — the preview says which
    pv = cv.first_week((1, 3, 5, 6), 6, 120, cp.Goal(), MONDAY)
    held = {i for i, c in enumerate(pv["cols"]) if c["bars"]}
    free = sorted({1, 3, 5, 6} - held)
    assert len(free) == 1 and pv["note"] == f"{cv.JOURS_L[free[0]].capitalize()} reste libre cette semaine-là."
    assert cv.first_week((1, 3, 6), 6, 120, cp.Goal(), MONDAY)["note"] is None


def test_a_race_this_week_previews_the_week_that_is_left():
    race = cp.Goal(MONDAY + timedelta(days=6), 300, True, 7, "Trail des Crêtes", co.OBJECTIVE, None)
    pv = cv.first_week((1, 3, 5), 5, 180, race, MONDAY + timedelta(days=2))
    assert pv["label"] == cv.week_label(MONDAY), "the only week of the plan"
    assert not pv["cols"][1]["bars"], "Tuesday is behind: never drawn"
    assert pv["cols"][6]["bars"] == [{"cls": "race", "h": 100, "duration": ""}]
    assert ("race", "Course") in pv["legend"]


# ── the onboarding: a plan already proposed ─────────────────────────────────

def _checked(page: str, name: str) -> list[str]:
    return re.findall(rf'<input type="(?:checkbox|radio)" name="{name}" value="(\w+)"[^>]*? checked>', page)


async def test_a_new_account_gets_a_plan_proposed_with_nothing_to_fill(client, people, db_session, clock):
    athlete = people.athlete
    clock.day = MONDAY + timedelta(days=3)  # a Thursday
    page = (await _as(client, athlete).get("/coaching")).text
    assert _checked(page, "days") == ["1", "3", "6"], "the default week: Tuesday, Thursday, Sunday"
    assert '<option value="6" selected>dimanche</option>' in page and page.count('name="long_day"') == 1
    assert _checked(page, "base") == ["120"] and page.count('type="radio" name="base"') == 5
    assert '<input type="hidden" name="goal" value="none">' in page and "Progresser, sans course" in page
    assert 'name="moments"' not in page and "Quand peux-tu courir" not in page, "asked in « Mon agenda », not here"
    # the week these answers make, drawn at once: the first complete week, one bar per session
    preview = page.split('id="coach-preview"')[1].split("</div>")[0]
    pv = cv.first_week((1, 3, 6), 6, 120, cp.Goal(), clock.day)
    assert preview.count("pf-coach-bar-s") == 3 == len(_bars(pv)) and "23–29 nov." in page
    for _i, _cls, duration in _bars(pv):
        assert f"<small>{duration}</small>" in preview
    assert f"Première séance {pv['first']['when']}" in page and pv["first"]["what"] in page
    assert "Créer mon plan" in page and "disabled" not in page.split('id="coach-go"')[1].split(">")[0]
    assert (await db_session.execute(select(func.count(CoachingPlan.id)))).scalar() == 0, "nothing exists yet"
    # the proposal posted as it is makes that plan
    r = await client.post("/coaching/plan", data={"goal": "none", "days": ["1", "3", "6"], "long_day": "6",
                                                  "base": "120"})
    assert r.status_code == 303
    rows = await _rows(db_session, athlete)
    week = [r for r in rows if MONDAY + timedelta(days=7) <= r.day < MONDAY + timedelta(days=14)]
    assert _as_bars(week) == _bars(pv), "the week drawn is the week made"
    assert f"{cp.KINDS[rows[0].kind]} {hm(rows[0].minutes)}" == pv["first"]["what"] and rows[0].day == clock.day


async def test_the_onboarding_ticks_the_days_the_athlete_runs_and_their_next_race(client, people, db_session, clock):
    athlete = people.athlete
    n = 0
    for week in range(1, 7):  # Monday, Wednesday, Saturday; the longest on Saturday
        for wd, minutes in ((0, 40), (2, 50), (5, 90)):
            n += 1
            db_session.add(_run(athlete, MONDAY - timedelta(weeks=week) + timedelta(days=wd), minutes, n))
    later = await _race(db_session, athlete, MONDAY + timedelta(days=120), course=False, name="Marathon du Lac")
    soon = await _race(db_session, athlete, MONDAY + timedelta(days=60), course=False, target=4 * 3600)
    page = (await _as(client, athlete).get("/coaching")).text
    assert _checked(page, "days") == ["0", "2", "5"]
    assert '<option value="5" selected>samedi</option>' in page
    assert _checked(page, "goal") == [str(soon.id)], "the next race"
    assert '<option value="180" selected>3h00</option>' in page and "4 dernières semaines" in page
    # opened from a race's page: that race
    assert _checked((await client.get(f"/coaching?course={later.id}")).text, "goal") == [str(later.id)]
    # « Refaire le plan » keeps the plan's own answers, never the habit's
    await _plan(client, db_session, athlete, days=("1", "4"), long_day="4", base="120", goal=str(later.id))
    page = (await client.get("/coaching?refaire=1")).text
    assert _checked(page, "days") == ["1", "4"] and '<option value="4" selected>vendredi</option>' in page
    assert _checked(page, "goal") == [str(later.id)] and "Refaire mon plan" in page


async def test_a_refused_form_keeps_the_answers_and_answers_200(client, people, db_session, clock):
    """The app's shell replaces any document answered 4xx by « Écran indisponible »: the message is shown in place."""
    _as(client, people.athlete)
    r = await client.post("/coaching/plan", data={"goal": "none", "days": ["2"], "long_day": "2", "base": "240"})
    assert r.status_code == 200 and "Choisis entre 2 et 6 jours." in r.text
    assert _checked(r.text, "days") == ["2"] and _checked(r.text, "base") == ["240"]
    assert "pf-coach-bar-s" not in r.text.split('id="coach-preview"')[1].split("</div>")[0], "no week to draw"
    assert await co.get_plan(db_session, people.athlete.id) is None


# ── GET /coaching/apercu: the week drawn again as a chip changes ────────────

async def _counts(db) -> tuple[int, int]:
    return ((await db.execute(select(func.count(CoachingPlan.id)))).scalar(),
            (await db.execute(select(func.count(CoachingSession.id)))).scalar())


async def test_the_preview_is_truthful_writes_nothing_and_answers_200(client, people, db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    clock.day = MONDAY + timedelta(days=1)
    query = "/coaching/apercu?goal=none&days=0&days=2&days=3&days=5&long_day=5&base=240"
    r = await client.get(query, headers=HTMX)
    assert r.status_code == 200 and "<html" not in r.text, "a partial"
    pv = cv.first_week((0, 2, 3, 5), 5, 240, cp.Goal(), clock.day)
    chart = r.text.split('id="coach-preview"')[1].split("</div>")[0]
    assert [d for d in re.findall(r"<small>(.*?)</small>", chart)] == [d for _i, _c, d in _bars(pv)]
    assert re.findall(r'pf-coach-bar-s is-(\w+)" style="--h: (\d+)"', chart) == [
        (b["cls"], str(b["h"])) for c in pv["cols"] for b in c["bars"]]
    # the two other parts follow out of band: the long run's day among the days chosen, the first session
    more = r.text.split('id="coach-preview-more"')[1]
    assert 'hx-swap-oob="true"' in more.split(">")[0]
    assert re.findall(r'<option value="(\d)"( selected)?>', more) == [("0", ""), ("2", ""), ("3", ""), ("5", " selected")]
    assert 'id="coach-preview-first"' in r.text and pv["first"]["what"] in r.text
    assert await _counts(db_session) == (0, 0), "nothing is written"
    # a long-run day that is not among the days: the plan's own default, as the saved plan would take
    r = await client.get("/coaching/apercu?goal=none&days=1&days=3&long_day=6&base=180", headers=HTMX)
    assert '<option value="3" selected>jeudi</option>' in r.text
    # answers that cannot make a plan: 200, said in the drawing's place (the days: by the line under them)
    for bad, said in (("days=1&base=180", None), ("days=0&days=1&days=2&days=3&days=4&days=5&days=6&base=180", None),
                      ("days=1&days=3&base=", "Indique ton volume de départ."),
                      ("goal=999999&days=1&days=3&base=180", "Cette course n’est plus disponible.")):
        r = await client.get(f"/coaching/apercu?{bad}", headers=HTMX)
        chart = r.text.split('id="coach-preview"')[1].split("</div>")[0]
        assert r.status_code == 200 and "pf-coach-bar-s" not in chart
        assert ("pf-coach-pv-msg" in chart) is (said is not None) and (said is None or said in chart)
    assert await _counts(db_session) == (0, 0)
    # an address opened by itself: the screen
    r = await client.get(query)
    assert r.status_code == 303 and r.headers["location"] == "/coaching"


async def test_the_preview_runs_on_the_race_as_the_plan_will(client, people, db_session, clock, monkeypatch):
    """The same race, the same times as the plan made a moment later (one source: the race page's reading), read
    once per race and day — and an existing plan is never touched."""
    from app.routers import simulator

    athlete = people.athlete
    _as(client, athlete)
    route = await _race(db_session, athlete, MONDAY + timedelta(days=75))
    calls = []
    real = simulator.race_times

    async def counted(*a, **k):
        calls.append(1)
        return await real(*a, **k)
    monkeypatch.setattr(simulator, "race_times", counted)
    query = f"/coaching/apercu?goal={route.id}&days=1&days=3&days=5&days=6&long_day=6&base=300"
    first = (await client.get(query, headers=HTMX)).text
    assert (await client.get(query, headers=HTMX)).text == first and len(calls) == 1, "read once"
    assert await _counts(db_session) == (0, 0)
    plan = await _plan(client, db_session, athlete, goal=str(route.id), days=("1", "3", "5", "6"), base="300")
    state = await co.sync(db_session, plan, athlete)
    assert state.goal.time_kind == co.ESTIMATE
    week = [r for r in state.rows if MONDAY <= r.day < MONDAY + timedelta(days=7)]
    chart = first.split('id="coach-preview"')[1].split("</div>")[0]
    assert re.findall(r"<small>(.*?)</small>", chart) == [d for _i, _c, d in _as_bars(week)]
    # « Refaire le plan »: the preview reads what the plan kept for the day, and leaves the plan as it is
    snap = (plan.days, plan.long_day, plan.base_minutes, dict(plan.goal_state), plan.generated_at)
    rows = [(r.id, r.day, r.kind, r.minutes, r.seq) for r in await _rows(db_session, athlete)]
    seen = len(calls)
    r = await client.get(f"/coaching/apercu?goal={route.id}&days=0&days=2&long_day=2&base=120", headers=HTMX)
    assert r.status_code == 200 and len(calls) == seen
    await db_session.refresh(plan)
    assert (plan.days, plan.long_day, plan.base_minutes, dict(plan.goal_state), plan.generated_at) == snap
    assert [(r.id, r.day, r.kind, r.minutes, r.seq) for r in await _rows(db_session, athlete)] == rows


async def test_the_preview_is_the_account_s_own_and_needs_coaching(client, people, db_session, clock, monkeypatch):
    from app.config import settings

    theirs = await _race(db_session, people.coach, MONDAY + timedelta(days=75), course=False)
    r = await _as(client, people.athlete).get(f"/coaching/apercu?goal={theirs.id}&days=1&days=3&base=180",
                                              headers=HTMX)
    assert r.status_code == 200 and "plus disponible" in r.text and "Trail des Crêtes" not in r.text
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", "off")
    assert (await client.get("/coaching/apercu?days=1&days=3&base=180", headers=HTMX)).status_code == 404


# ── the page opens on the day ───────────────────────────────────────────────

def _main(page: str) -> str:
    return page[page.index('id="coach-main"'):page.index('class="pf-coach-more"')]


async def test_the_page_opens_on_today_with_one_main_action(client, people, db_session, clock):
    athlete = people.athlete
    athlete.max_heart_rate = 186
    clock.day = MONDAY + timedelta(days=1)  # Tuesday, an available day
    plan = await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")
    today = next(r for r in await _rows(db_session, athlete) if r.day == clock.day)
    page = (await client.get("/coaching")).text
    main = _main(page)
    # the day's card first, then the week, then the plan
    assert main.index("pf-coach-daycard") < main.index("pf-coach-cal") < main.index("pf-coach-plan")
    assert "Aujourd’hui · mardi 17 novembre" in main and 'id="coach-today"' not in main
    assert f'<article class="pf-coach-session is-{cv.KIND_CLASS[today.kind]}"' in main
    # ONE main action; the others are quiet
    assert main.count("pf-btn-primary") == 2, "« C’est fait », and the save of the closed « Modifier » fold"
    done = re.search(rf'<button[^>]*id="coach-done-{today.id}"[^>]*>', main).group(0)
    assert 'value="done"' in done and "pf-btn-primary" in done and "pf-btn-block" in done and "C’est fait" in main
    for quiet in (rf'<button[^>]*id="coach-missed-{today.id}"[^>]*class="pf-coach-quietbtn">Pas faite<',
                  rf'<a id="coach-move-{today.id}" class="pf-coach-quietbtn"[^>]*>Déplacer<',
                  r'<summary class="pf-coach-quietbtn">Modifier<'):
        assert re.search(quiet, main), quiet
    # the week: seven columns of bars, each day's full name on its link, the total said once
    strip = main.split('class="pf-coach-strip"')[1].split("</div>")[0]
    assert strip.count('class="pf-coach-bars"') == 7 and strip.count("pf-coach-bar-s") == 3
    assert f'aria-label="mardi 17 novembre : {cp.KINDS[today.kind]} {viz_long(today.minutes)}, prévue (aujourd’hui)"' in strip
    assert "pf-coach-legend" not in main, "the day's card names the kind: no legend under the week"
    summary = cv.week_summary(await co.sync(db_session, plan, athlete), MONDAY, {})
    assert main.count(f">{summary['label']}<") == 1 and '<span class="pf-coach-phase">Progression</span>' in main
    # done by hand: no main action left, the ressenti on a scale of five, the note behind a fold
    r = await client.post(f"/coaching/sessions/{today.id}/status", data={"value": "done"}, headers=HTMX)
    assert r.status_code == 200 and "pf-btn-primary" not in r.text and "C’est fait" not in r.text
    assert r.text.count("data-feel") == 5 and "C’était comment" in r.text
    assert re.search(rf'id="coach-undo-{today.id}" class="pf-coach-quietbtn" aria-label="Annuler « fait »">Annuler<', r.text)
    assert '<summary class="pf-coach-quietbtn">Ajouter une note</summary>' in r.text
    assert f'is-{cv.KIND_CLASS[today.kind]} is-done' in r.text
    r = await client.post(f"/coaching/sessions/{today.id}/note", data={"note": "Bonnes jambes."}, headers=HTMX)
    assert '<p class="pf-coach-said is-note"><b>Ta note</b> Bonnes jambes.</p>' in r.text
    assert '<summary class="pf-coach-quietbtn">Modifier ma note</summary>' in r.text
    r = await client.post(f"/coaching/sessions/{today.id}/status", data={"value": "reset"}, headers=HTMX)
    assert "C’est fait" in r.text and (await db_session.get(CoachingSession, today.id)).status == "planned"
    # « Pas faite », by hand: the way back, and « C’est fait » no longer the main action
    r = await client.post(f"/coaching/sessions/{today.id}/status", data={"value": "missed"}, headers=HTMX)
    assert 'aria-label="Annuler « pas faite »"' in r.text and "pf-btn-primary" not in r.text.split("pf-coach-quiet")[0]
    assert "pf-btn-secondary" in re.search(rf'<button[^>]*id="coach-done-{today.id}"[^>]*>', r.text).group(0)


def viz_long(minutes: int) -> str:
    from app.services.viz import hm_long

    return hm_long(minutes)


async def test_a_steady_session_shows_a_gauge_and_a_structured_one_its_profile(client, people, db_session, clock):
    athlete = people.athlete
    await _plan(client, db_session, athlete, days=("1", "3", "5", "6"), base="240")
    rows = await _rows(db_session, athlete)
    easy = next(r for r in rows if r.kind == cp.FOOTING)
    hard = next(r for r in rows if r.kind in (cp.QUALITY, cp.HILLS))
    page = _main((await client.get(f"/coaching?d={easy.day}")).text)
    assert '<span class="pf-coach-gauge is-l2" role="img" aria-label="Effort facile : niveau 2 sur 4">' in page
    assert 'class="pf-effort"' not in page and "Facile\u00a0: tu peux tenir une conversation." in page
    page = _main((await client.get(f"/coaching?d={hard.day}")).text)
    assert 'class="pf-effort"' in page and "pf-coach-gauge" not in page
    state = await co.sync(db_session, await co.get_plan(db_session, athlete.id), athlete)
    recup = cv.page(state, d=easy.day)["sessions"][0]
    assert recup["gauge"]["level"] == 2 and recup["profile"] is None
    assert cv.page(state, d=hard.day)["sessions"][0]["gauge"] is None


async def test_a_day_without_a_session_names_the_next_one(client, people, db_session, clock):
    athlete = people.athlete
    await _plan(client, db_session, athlete, days=("3", "6"), long_day="6", base="120")
    nxt = next(r for r in await _rows(db_session, athlete))
    page = _main((await client.get("/coaching?d=2026-11-16")).text)  # today, a Monday without a session
    assert '<p class="pf-coach-rest">Repos</p>' in page and "Prochaine séance · jeudi 19 novembre" in page
    assert f'href="/coaching?d={nxt.day}"' in page and f'hx-get="/coaching/calendar?d={nxt.day}"' in page
    assert f"{cp.KINDS[nxt.kind]} " in page.split('id="coach-next-session"')[1]
    clock.day = MONDAY + timedelta(days=2)
    assert "Prochaine séance · demain" in _main((await client.get("/coaching")).text)
    # a rest day already behind says nothing of what comes next; another day than today has the way back
    page = _main((await client.get("/coaching?d=2026-11-16")).text)
    assert "Repos" in page and "Prochaine séance" not in page
    assert re.search(r'<a id="coach-today"[^>]*hx-get="/coaching/calendar\?d=2026-11-18"[^>]*>Aujourd’hui</a>', page)


async def test_the_tools_sit_behind_one_row_and_open_where_a_link_lands(client, people, db_session, clock):
    athlete = people.athlete
    await _plan(client, db_session, athlete)
    page = (await client.get("/coaching")).text
    tail = page[page.index('class="pf-coach-more"'):]
    assert tail.count("<details") >= 5 and "Options du plan" in tail
    assert re.search(r'<details class="pf-coach-tool pf-coach-opts" id="coach-options">', tail), "closed by default"
    inside = tail[tail.index('id="coach-options"'):]
    for option in ("Ajouter à mon calendrier", "Mon agenda", "Partager mon plan", "Suivre le plan de quelqu’un",
                   "Refaire le plan", "Arrêter le coaching"):
        assert option in inside, option
    assert tail.index("Comment ça marche") < tail.index("Options du plan")
    # a form of the options comes back with them open, on its own fold
    for outil, fold in (("agenda", 'id="coach-tool-agenda" open'), ("agenda-lu", 'id="coach-tool-agenda" open'),
                        ("forme", 'id="coach-tool-agenda">'), ("options", 'id="coach-tool-agenda">')):
        page = (await client.get(f"/coaching?outil={outil}")).text
        assert 'id="coach-options" open>' in page and fold in page, outil
    assert 'id="coach-options">' in (await client.get("/coaching?outil=nimporte")).text
    # … and the script opens them for an address that only carries a fragment (/coaching#coach-form)
    assert 'document.getElementById("coach-options")' in page and "location.hash" in page
    # a coach's page has none of it
    db_session.add(co.CoachingLink(athlete_id=athlete.id, coach_id=people.coach.id))
    await db_session.flush()
    theirs = (await _as(client, people.coach).get(f"/coaching/athletes/{athlete.id}")).text
    assert "coach-options" not in theirs and "Options du plan" not in theirs and "C’est fait" not in theirs
    assert "pf-coach-daycard" in theirs and "pf-coach-strip" in theirs


# ── the words: the plan is automatic, nobody needs a coach to have one ───────

def _read(page: str) -> str:
    """What a person reads of a page: its text, the labels a screen reader says, the placeholders, the questions a
    button asks before it acts — never a class, an id or an address."""
    body = page[page.index('id="main-content"'):]
    said = re.findall(r'(?:aria-label|placeholder|title|hx-confirm)="([^"]*)"', body)
    said += re.findall(r"var msg = '([^']*)'", body)
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S)
    return " ".join([re.sub(r"<[^>]+>", " ", body), *said])


async def test_the_pages_never_say_coach(client, people, db_session, clock):
    """Owner, 2026-11-22 (« décide ce qui est le mieux »): the feature stays « Coaching »; inside it, the word
    « coach » made a human coach look required. The person who follows a plan is named by what they do, or by their
    first name."""
    athlete, follower = people.athlete, people.coach
    _as(client, athlete)
    pages = [(await client.get("/coaching")).text]  # the onboarding
    plan = await _plan(client, db_session, athlete, days=("0", "3", "6"))
    today = next(r for r in await _rows(db_session, athlete) if r.day == MONDAY)
    today.coach_comment, today.origin, today.pinned = "Bien joué", "coach", True
    db_session.add(co.CoachingLink(athlete_id=athlete.id, coach_id=follower.id))
    await db_session.flush()
    page = (await client.get("/coaching?outil=options")).text
    assert "Partager mon plan" in page and "Suivre le plan de quelqu’un" in page
    assert "La personne à qui tu donnes le code voit ton plan et peut l’ajuster" in page
    assert "<b>Florent</b> suit ton plan" in page
    # one person follows the plan: a comment and a change are signed with that first name
    assert '<p class="pf-coach-said"><b>Florent</b> Bien joué</p>' in page
    assert '<p class="pf-coach-edited">Modifiée par Florent</p>' in page
    pages += [page, 'id="main-content"' + (await client.post("/coaching/share", headers=HTMX)).text]  # the code's fold
    # two people follow it: the author of a comment is not kept, no name is guessed
    db_session.add(co.CoachingLink(athlete_id=athlete.id, coach_id=people.outsider.id))
    await db_session.flush()
    page = (await client.get("/coaching")).text
    assert '<p class="pf-coach-said"><b>Commentaire</b> Bien joué</p>' in page
    assert '<p class="pf-coach-edited">Modifiée par la personne qui suit ton plan</p>' in page
    pages.append(page)
    # the follower's side: « Plans suivis », the athlete by her first name
    _as(client, follower)
    listing = (await client.get("/coaching/athletes")).text
    assert "Plans suivis" in listing and "Suivre le plan de quelqu’un" in listing and "Stella" in listing
    r = await client.post(f"/coaching/athletes/{athlete.id}/sessions/{today.id}/edit",
                          data={"kind": "footing", "minutes": "40"}, headers=HTMX)
    assert r.status_code == 200 and "Modifiée par toi" in r.text
    await _as(client, athlete).post(f"/coaching/sessions/{today.id}/edit", data={"kind": "footing", "minutes": "45"},
                                    headers=HTMX)
    theirs = (await _as(client, follower).get(f"/coaching/athletes/{athlete.id}")).text
    assert '<p class="pf-coach-edited">Modifiée par Stella</p>' in theirs and "Plans suivis" in theirs
    assert "Ton commentaire" in theirs
    pages += [listing, theirs, (await client.post("/coaching/athletes/join", data={"code": "ZZZZ-ZZZZ"})).text]
    for html in pages:
        read = _read(html)
        assert not re.search(r"\bcoachs?\b", read, re.I), re.search(r".{40}\bcoachs?\b.{40}", read, re.I | re.S).group(0)
        assert "athlète" not in read.replace("Athlète", ""), "the follower's pages name the person, not a role"
    assert plan.id and "Coaching" in _read(pages[1]), "the feature keeps its name"
