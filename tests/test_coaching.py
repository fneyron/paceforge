"""Coaching (beta, docs/coaching.md): access (every account by default; off → nothing exists), the onboarding, the matching with the recorded
activities, what a regeneration may rewrite, the ressenti's adaptation, the coach's link and its limits, the calendar
feed (parsed back), the daily round, the privacy row. The plan's rules themselves: tests/test_coaching_plan.py."""
import re
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text

from app import features
from app.config import settings
from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.coaching import CoachingInvite, CoachingLink, CoachingPlan, CoachingSession
from app.models.route import Route
from app.models.user import User
from app.services import coaching as co
from app.services import coaching_ics as ics
from app.services import coaching_plan as cp
from app.services import coaching_view as cv
from app.services import viz

ATHLETE, COACH, OUTSIDER = "stella@x.fr", "florent@x.fr", "autre@x.fr"
HTMX = {"HX-Request": "true"}
BS = chr(92)  # a backslash


@pytest.fixture
def beta(monkeypatch):
    """Coaching restricted to two listed accounts (the switch's list form; its default is every account)."""
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", f" {ATHLETE.upper()}, {COACH} ")
    monkeypatch.setattr(settings, "BASE_URL", "https://paceforge.example")


@pytest.fixture
async def people(db_session, monkeypatch):
    """The athlete and her coach (both in the list once `beta` restricts Coaching), and an account outside it."""
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    users = [User(email=email, email_verified=True, firstname=name, timezone="UTC")
             for email, name in ((ATHLETE, "Stella"), (COACH, "Florent"), (OUTSIDER, "Autre"))]
    db_session.add_all(users)
    await db_session.flush()
    return SimpleNamespace(athlete=users[0], coach=users[1], outsider=users[2])


def _as(client: AsyncClient, user: User) -> AsyncClient:
    client._transport.app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[attr-defined]
    return client


def _run(user, day: date, minutes: int, n: int, sport: str = "Run", name: str = "Sortie") -> Activity:
    return Activity(user_id=user.id, strava_activity_id=user.id * 100000 + n, name=name, sport_type=sport,
                    start_date=datetime.combine(day, time(7), timezone.utc), distance=minutes * 150,
                    moving_time=minutes * 60, elapsed_time=minutes * 60 + 30, total_elevation_gain=40,
                    average_speed=2.5, raw_data={"utc_offset": 0})


async def _plan(client, db, user, *, days=("1", "3", "5", "6"), goal="none", base="180", long_day="6"):
    r = await _as(client, user).post("/coaching/plan", data={"goal": goal, "days": list(days), "long_day": long_day,
                                                             "base": base})
    assert r.status_code == 303 and r.headers["location"] == "/coaching", r.text
    return (await db.execute(select(CoachingPlan).where(CoachingPlan.user_id == user.id))).scalar_one()


async def _rows(db, user) -> list[CoachingSession]:
    return list((await db.execute(select(CoachingSession).where(CoachingSession.user_id == user.id)
                                  .order_by(CoachingSession.day, CoachingSession.id)
                                  .execution_options(populate_existing=True))).scalars())


async def _past_session(db, plan, day, kind=cp.FOOTING, minutes=45, **more) -> CoachingSession:
    row = CoachingSession(plan_id=plan.id, user_id=plan.user_id, day=day, auto_day=day, kind=kind, minutes=minutes,
                          structure=cp.default_structure(kind, minutes), **more)
    db.add(row)
    await db.flush()
    return row


# ── access: every account by default; off, the feature does not exist ───────

PATHS = ("/coaching", "/coaching?refaire=1", "/coaching/calendar", "/coaching/athletes", "/coaching/athletes/1",
         "/coaching/athletes/1/calendar")
POSTS = ("/coaching/plan", "/coaching/stop", "/coaching/share", "/coaching/feed/regenerate", "/coaching/sessions",
         "/coaching/sessions/1/status", "/coaching/sessions/1/ressenti", "/coaching/sessions/1/note",
         "/coaching/sessions/1/move", "/coaching/sessions/1/edit", "/coaching/sessions/1/delete",
         "/coaching/athletes/join", "/coaching/athletes/1/leave", "/coaching/coaches/1/revoke",
         "/coaching/athletes/1/sessions/1/note", "/coaching/athletes/1/sessions")


async def test_by_default_every_account_has_coaching(client: AsyncClient, people, db_session, same_today):
    """Nothing set (production today): the switch reads « * », every signed-in account has the 4th entry and the
    onboarding, the privacy row shows, the daily round is scheduled, the pages stay out of the crawl."""
    from app.celery_app import celery_app
    from app.config import Settings
    from app.tasks import coaching as task

    assert Settings.model_fields["COACHING_BETA_USERS"].default == "*" and settings.COACHING_BETA_USERS == "*"
    assert features.coaching_enabled() and features.coaching_for_everyone() and not features.coaching_beta_emails()
    for user in (people.athlete, people.coach, people.outsider, User(email=None)):
        assert features.coaching_for(user)
    assert not features.coaching_for(None), "never a visitor"
    assert features.coaching_beat_schedule()["coaching-daily"]["task"] == "paceforge.coaching_daily"
    assert celery_app.conf.beat_schedule["coaching-daily"]["task"] == "paceforge.coaching_daily"
    _as(client, people.outsider)  # any account: no list names it
    page = (await client.get("/activities")).text
    top = page.split('class="pf-nav"')[1].split("</nav>")[0]
    bar = page.split('class="pf-tabbar is-four"')[1].split("</nav>")[0]
    for nav in (top, bar):
        assert re.findall(r"<span>([^<]+)</span>", nav) == ["Courses", "Activités", "Santé", "Coaching"]
    onboarding = await client.get("/coaching")
    assert onboarding.status_code == 200 and "Créer mon plan" in onboarding.text
    assert 'href="/coaching" aria-current="page"' in onboarding.text
    assert (await client.get("/coaching/athletes")).status_code == 200
    assert "Coaching (bêta)" in (await client.get("/privacy")).text
    assert "Disallow: /coaching\n" in (await client.get("/robots.txt")).text
    # an account that never makes a plan costs nothing: no row from looking, no work in the daily round
    assert (await db_session.execute(select(func.count()).select_from(CoachingPlan))).scalar() == 0
    assert (await db_session.execute(select(func.count()).select_from(CoachingSession))).scalar() == 0
    assert await task.run(db_session) == 0
    plan = await _plan(client, db_session, people.outsider)
    assert plan.user_id == people.outsider.id and await task.run(db_session) == 1


@pytest.mark.parametrize("value", ["", "  ", "*", "'*'", "-*", "tout le monde"])
def test_an_empty_or_unreadable_switch_means_everyone(value, monkeypatch):
    """Coolify kept the closed beta's empty default: an empty value (or anything that is neither an email nor an
    « off » word) must leave Coaching on for every account, never switch it off in silence."""
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", value)
    assert features.coaching_enabled() and features.coaching_for_everyone()
    assert features.coaching_for(User(email=OUTSIDER)) and features.coaching_beat_schedule()


@pytest.mark.parametrize("value", ["off", "OFF", "non, 0", " Off ,", "false"])
def test_the_switch_reads_everyone_a_list_or_nobody(value, monkeypatch):
    """« * »: every account, also beside emails; emails: those accounts; an « off » word: nobody."""
    stella, other = User(email=ATHLETE), User(email=OUTSIDER)
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", value)
    assert not features.coaching_enabled() and not features.coaching_for(stella)
    assert features.coaching_beat_schedule() == {}
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", f"{value},{ATHLETE.title()}")
    assert features.coaching_enabled() and not features.coaching_for_everyone()
    assert features.coaching_for(stella) and not features.coaching_for(other)
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", f"{value}, * ,{ATHLETE}")
    assert features.coaching_for_everyone() and features.coaching_for(other) and features.coaching_for(User(email=None))


def test_production_defaults_to_everyone_even_with_an_empty_variable():
    """docker-compose.prod.yml passes the variable as it is (no default Coolify could store once and keep): unset
    or left empty it reaches the app empty, which means every account; .env.example shows « * »."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert "  COACHING_BETA_USERS: ${COACHING_BETA_USERS:-}\n" in (root / "docker-compose.prod.yml").read_text()
    assert "\nCOACHING_BETA_USERS=*\n" in (root / ".env.example").read_text()


@pytest.mark.parametrize("off", ["off", "0"])
async def test_switched_off_nothing_exists(off, client: AsyncClient, people, db_session, same_today, monkeypatch):
    """Switched off (« off »): every route 404 (the feed too, even for a token that exists), no
    menu entry, no card, no privacy row, nothing scheduled."""
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", off)
    assert not features.coaching_enabled()
    assert not features.coaching_for(people.athlete) and features.coaching_beat_schedule() == {}
    plan = CoachingPlan(user_id=people.athlete.id, days="1,3,6", long_day=6, base_minutes=180,
                        start_monday=cp.monday_of(same_today), generated_at=datetime.now(timezone.utc))
    token = co.new_feed_token(plan)
    db_session.add(plan)
    await db_session.flush()
    _as(client, people.athlete)
    for path in PATHS:
        assert (await client.get(path)).status_code == 404, path
    for path in POSTS:
        assert (await client.post(path, data={"value": "done", "code": "ABCDEFGH"})).status_code == 404, path
    assert (await client.get(f"/coaching/cal/{token}.ics")).status_code == 404
    page = (await client.get("/activities")).text
    assert "Coaching" not in page and "/coaching" not in page and "is-four" not in page
    assert "Coaching" not in (await client.get("/privacy")).text
    assert "/coaching" not in (await client.get("/robots.txt")).text
    from app.celery_app import celery_app
    assert "paceforge.coaching_daily" in celery_app.tasks or "app.tasks.coaching" in celery_app.conf.include
    from app.tasks import coaching as task
    assert await task._run() == 0, "nothing while it is off (no database opened)"


async def test_only_the_accounts_of_the_list_have_it(client: AsyncClient, people, beta, same_today):
    assert features.coaching_beta_emails() == {ATHLETE, COACH}
    assert features.coaching_for(people.athlete) and features.coaching_for(people.coach)
    assert not features.coaching_for(people.outsider) and not features.coaching_for(User(email=None))
    schedule = features.coaching_beat_schedule()["coaching-daily"]
    assert schedule["task"] == "paceforge.coaching_daily"
    _as(client, people.outsider)
    for path in PATHS:
        assert (await client.get(path)).status_code == 404, path
    page = (await client.get("/activities")).text
    assert "/coaching" not in page
    _as(client, people.athlete)
    page = (await client.get("/activities")).text
    assert 'href="/coaching" class="pf-coach-entry pf-old-shell-only"' in page, "the entry card at the top of Activités"
    assert page.count('<a href="/coaching"') == 3 and "pf-tabbar is-four" in page, "the 4th entry, header and bar"
    assert page.index("pf-coach-entry") < page.index('id="sync-msg"') + 2000
    onboarding = await client.get("/coaching")
    assert onboarding.status_code == 200 and "Créer mon plan" in onboarding.text


async def test_the_card_and_the_way_back_are_for_an_app_older_than_its_coaching_tab(client, people, db_session,
                                                                                     same_today):
    """Rendered for every account, shown by the stylesheet only inside an app shell that does not announce the
    native Coaching tab (.pf-native without .pf-native-coaching, which mobile/src/navigation.ts adds): never on the
    site, never in an updated app. The calendar's webcal:// button is the reverse: hidden in that older shell only
    (it drops the link). What a browser then shows: tests/browser/coaching-visuals.cjs."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    css = (root / "app/static/css/interface.css").read_text()
    assert ".pf-old-shell-only { display: none !important; }" in css
    assert ".pf-native:not(.pf-native-coaching) .pf-old-shell-only { display: flex !important; }" in css
    assert ".pf-native:not(.pf-native-coaching) .pf-webcal { display: none !important; }" in css
    assert "classList.add('pf-native-coaching')" in (root / "mobile/src/navigation.ts").read_text()
    back = '<a href="/activities" class="pf-back pf-old-shell-only">'
    _as(client, people.athlete)
    activities = (await client.get("/activities")).text
    assert activities.count('<a href="/coaching" class="pf-coach-entry pf-old-shell-only">') == 1
    onboarding = (await client.get("/coaching")).text
    athletes = (await client.get("/coaching/athletes")).text
    assert back in onboarding and back in athletes
    await _plan(client, db_session, people.athlete)
    page = (await client.get("/coaching")).text
    assert back in page
    assert re.search(r'<a class="pf-btn pf-btn-primary pf-btn-sm pf-webcal" href="webcal://[^"/]+/coaching/cal/[\w-]{43}\.ics">',
                     page), "the subscription button, for a browser and an app that hands it to the system"
    assert 'id="coach-feed-url"' in page, "the address to copy stays, whatever the shell"
    athletes = (await client.get("/coaching/athletes")).text
    assert '<a href="/coaching" class="pf-back">' in athletes and back not in athletes, "with a plan: back to it"
    for html in (onboarding, page, athletes):
        assert "pf-native-only" not in html and "pf-browser-only" not in html


@pytest.mark.parametrize("on", ["*", ATHLETE])
async def test_the_privacy_row_shows_only_while_coaching_is_on(on, client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", "off")
    assert "Coaching" not in (await client.get("/privacy")).text
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", on)
    page = (await client.get("/privacy")).text
    row = page[page.index("Coaching (bêta)"):page.index("Sécurité</th>")]
    assert "ressenti" in row and "l’effort perçu, de 1 à 5" in row and "Contrat" in row and "coach que tu choisis" in row
    assert "suppression du compte" in row
    assert not re.search(r"douleur|blessure|maladie", row)


def test_no_pain_injury_or_illness_field_is_collected():
    columns = {c.name for t in (CoachingPlan, CoachingSession, CoachingLink, CoachingInvite) for c in t.__table__.c}
    assert not [c for c in columns if re.search(r"pain|injur|ill|sick|douleur|bless|malad", c)]
    assert cp.RESSENTI == ("Très facile", "Facile", "Bien", "Dur", "Très dur")


# ── the onboarding ──────────────────────────────────────────────────────────

async def test_the_onboarding_computes_the_starting_volume_from_four_complete_weeks(client, people, beta, db_session,
                                                                                   same_today):
    athlete, m0 = people.athlete, cp.monday_of(same_today)
    page = (await _as(client, athlete).get("/coaching")).text
    assert "Progresser, sans course" in page and 'name="days"' in page and 'name="long_day"' in page
    assert page.count('type="radio" name="base"') == 5, "no history: five coarse choices"
    # 4 complete weeks of 100, 200, 140, 160 min; this week's runs and a ride never count
    n = 0
    for weeks_ago, minutes in ((4, 100), (3, 200), (2, 140), (1, 160)):
        for part in (minutes // 2, minutes - minutes // 2):
            n += 1
            db_session.add(_run(athlete, m0 - timedelta(weeks=weeks_ago) + timedelta(days=n % 5), part, n,
                                sport="TrailRun" if n % 2 else "Run"))
    db_session.add(_run(athlete, m0 - timedelta(days=3), 600, 90, sport="Ride"))
    db_session.add(_run(athlete, m0, 30, 91))
    await db_session.flush()
    c = await co.clock(db_session, athlete)
    assert co.weekly_minutes(await co.runs_of(db_session, athlete.id, c), c.today) == [100, 200, 140, 160]
    assert await co.starting_volume(db_session, athlete, c) == 150
    page = (await client.get("/coaching")).text
    assert '<option value="150" selected>2h30</option>' in page and "4 dernières semaines" in page
    # made again: the volume is computed again, never the old plan's
    await _plan(client, db_session, athlete, base="300")
    page = (await client.get("/coaching?refaire=1")).text
    assert '<option value="150" selected>2h30</option>' in page and "Refaire mon plan" in page


async def test_a_plan_needs_two_to_six_days_and_a_race_of_the_athlete(client, people, beta, db_session, same_today):
    athlete = people.athlete
    _as(client, athlete)
    for days in (["2"], ["0", "1", "2", "3", "4", "5", "6"], []):
        r = await client.post("/coaching/plan", data={"goal": "none", "days": days, "long_day": "2", "base": "180"})
        assert r.status_code == 200 and "Choisis entre 2 et 6 jours." in r.text
    theirs = Route(user_id=people.coach.id, name="Pas la sienne", total_distance_km=20,
                   race_date=(same_today + timedelta(days=60)).isoformat())
    past = Route(user_id=athlete.id, name="Passée", total_distance_km=20,
                 race_date=(same_today - timedelta(days=3)).isoformat())
    mine = Route(user_id=athlete.id, name="Trail des Crêtes", total_distance_km=42, total_elevation_gain=2000,
                 target_time_s=6 * 3600, race_date=(same_today + timedelta(days=75)).isoformat())
    db_session.add_all([theirs, past, mine])
    await db_session.flush()
    page = (await client.get("/coaching")).text
    assert "Trail des Crêtes" in page and "Passée" not in page and "Pas la sienne" not in page
    for other in (theirs, past):
        r = await client.post("/coaching/plan", data={"goal": str(other.id), "days": ["1", "3", "6"], "base": "180"})
        assert r.status_code == 200 and "plus disponible" in r.text
    assert await co.get_plan(db_session, athlete.id) is None
    # the long run's day defaults to Sunday, else the last available day
    plan = await _plan(client, db_session, athlete, goal=str(mine.id), days=("1", "3", "5"), long_day="0")
    assert (plan.route_id, plan.weekdays, plan.long_day, plan.base_minutes) == (mine.id, (1, 3, 5), 5, 180)
    rows = await _rows(db_session, athlete)
    assert rows and min(r.day for r in rows) >= same_today, "nothing is written in the past"
    assert {r.day.weekday() for r in rows if r.kind != cp.RACE} <= {1, 3, 5}
    race = [r for r in rows if r.kind == cp.RACE]
    assert [(r.day, r.minutes) for r in race] == [(same_today + timedelta(days=75), 360)]
    page = (await client.get("/coaching")).text
    assert "J‑75 · Trail des Crêtes" in page and 'id="coach-main"' in page and "Comment ça marche" in page


# ── matching with the recorded activities (pure) ────────────────────────────

D = date(2026, 11, 20)


def _row(day, minutes, status="planned", manual=False, activity_id=None, rid=None, kind=cp.FOOTING):
    return SimpleNamespace(id=rid, day=day, minutes=minutes, status=status, manual=manual, activity_id=activity_id,
                           done_minutes=None, kind=kind, ressenti=None)


def _act(sid, day, minutes):
    return SimpleNamespace(id=sid, day=day, minutes=minutes)


def test_a_run_on_the_day_marks_the_session_done_the_closest_duration_first():
    rows = [_row(D, 45, rid=1), _row(D, 90, rid=2), _row(D - timedelta(days=1), 60, rid=3)]
    runs = [_act(10, D, 88.4), _act(11, D, 50), _act(12, D, 20), _act(13, D - timedelta(days=3), 60)]
    assert co.match(rows, runs, D) is True
    assert [(r.status, r.activity_id, r.done_minutes) for r in rows] == [
        ("done", 11, 50), ("done", 10, 88), ("missed", None, None)]
    assert [s.id for s in co.hors_plan(runs, rows)] == [12, 13], "the others are out of the plan"
    assert co.match(rows, runs, D) is False, "a second reading changes nothing"


def test_the_matching_respects_what_was_ticked_by_hand_and_undoes_a_link_whose_activity_is_gone():
    said_no = _row(D, 45, status="missed", manual=True, rid=1)
    said_yes = _row(D - timedelta(days=1), 45, status="done", manual=True, rid=2)
    said_yes.done_minutes = 45
    auto = _row(D - timedelta(days=2), 45, status="done", activity_id=99, rid=3)
    future = _row(D + timedelta(days=1), 45, rid=4)
    rows = [said_no, said_yes, auto, future]
    runs = [_act(10, D, 44), _act(11, D - timedelta(days=1), 52)]
    co.match(rows, runs, D)
    assert (said_no.status, said_no.activity_id) == ("missed", None), "« Pas faite » is never undone by a run"
    assert (said_yes.status, said_yes.activity_id, said_yes.done_minutes) == ("done", 11, 52), "« Fait » takes the run"
    assert (auto.status, auto.activity_id, auto.done_minutes) == ("missed", None, None), "its activity is gone"
    assert (future.status, future.activity_id) == ("planned", None)
    today_planned = _row(D, 30, rid=5)
    co.match([today_planned], [], D)
    assert today_planned.status == "planned", "today is not over"
    co.match([today_planned], [], D + timedelta(days=1))
    assert today_planned.status == "missed"


# ── the plan in the database ────────────────────────────────────────────────

async def test_an_activity_marks_its_session_done_and_another_is_out_of_the_plan(client, people, beta, db_session,
                                                                                 same_today):
    athlete = people.athlete
    days = [str((same_today + timedelta(days=i)).weekday()) for i in (0, 2)]  # today is an available day
    plan = await _plan(client, db_session, athlete, days=days, long_day=days[1])
    today_row = next(r for r in await _rows(db_session, athlete) if r.day == same_today)
    assert today_row.status == "planned"
    yesterday = await _past_session(db_session, plan, same_today - timedelta(days=1), minutes=40)
    before = await _past_session(db_session, plan, same_today - timedelta(days=2), minutes=40)
    db_session.add_all([_run(athlete, same_today, today_row.minutes + 4, 1, name="Footing du jour"),
                        _run(athlete, same_today, 15, 2, name="Retour à pied"),
                        _run(athlete, same_today - timedelta(days=2), 41, 3, sport="Ride"),
                        _run(athlete, same_today - timedelta(days=3), 33, 4, sport="TrailRun")])
    await db_session.flush()
    page = (await _as(client, athlete).get("/coaching")).text
    rows = {r.id: r for r in await _rows(db_session, athlete)}
    assert (rows[today_row.id].status, rows[today_row.id].done_minutes) == ("done", today_row.minutes + 4)
    assert rows[today_row.id].activity_id is not None and not rows[today_row.id].manual
    assert rows[yesterday.id].status == "missed" and rows[before.id].status == "missed", "a ride is not a run"
    assert "Footing du jour" in page and f'href="/activity/{rows[today_row.id].activity_id}"' in page
    assert "Hors plan · Retour à pied" in page and "C’était comment" in page
    state = await co.sync(db_session, plan, athlete)
    done = co.done_by_week(state.runs, state.rows)
    assert sum(done.values()) == today_row.minutes + 4 + 15 + 33, "every run counts in the week's done time"


async def test_a_regeneration_only_rewrites_future_sessions_nobody_touched(client, people, beta, db_session,
                                                                           same_today):
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete, days=[str(i) for i in range(6)], long_day="5", base="240")
    rows = [r for r in await _rows(db_session, athlete) if r.day > same_today + timedelta(days=7)]
    edited, moved, done, untouched = rows[0], rows[2], rows[3], rows[5]
    r = await client.post(f"/coaching/sessions/{edited.id}/edit", data={"kind": "recup", "minutes": "35"}, headers=HTMX)
    assert r.status_code == 200 and 'id="coach-main"' in r.text
    free = next(d for d in (moved.day + timedelta(days=i) for i in range(1, 9))
                if d.weekday() == 6)  # a Sunday: never an available day here
    r = await client.post(f"/coaching/sessions/{moved.id}/move", data={"to": free.isoformat()}, headers=HTMX)
    assert r.status_code == 200
    done.status, done.ressenti = "done", 3
    await db_session.flush()
    before = {r.id: (r.day, r.kind, r.minutes, r.status, r.pinned, r.origin, r.seq) for r in await _rows(db_session, athlete)}
    assert before[edited.id][1:] == ("recup", 35, "planned", True, "athlete", 1)
    assert before[moved.id][0] == free and before[moved.id][4:] == (True, "athlete", 1)
    # the plan is made again with other answers: half the volume
    await _plan(client, db_session, athlete, days=[str(i) for i in range(6)], long_day="5", base="120")
    after = {r.id: (r.day, r.kind, r.minutes, r.status, r.pinned, r.origin, r.seq) for r in await _rows(db_session, athlete)}
    for row in (edited, moved, done):
        assert after[row.id] == before[row.id], "pinned or done: untouched"
    kept = (await db_session.get(CoachingSession, untouched.id))
    if kept is not None and untouched.id in after:
        assert after[untouched.id][2] <= before[untouched.id][2] and after[untouched.id][0] == before[untouched.id][0]
    assert sum(v[2] for v in after.values()) < sum(v[2] for v in before.values()), "the rest follows the new base"
    assert not [r for r in await _rows(db_session, athlete) if r.auto_day == moved.auto_day and r.id != moved.id], \
        "the day a moved session left is not filled again"
    # a second reading writes nothing more
    again = {r.id: (r.day, r.kind, r.minutes, r.status, r.pinned, r.origin, r.seq) for r in await _rows(db_session, athlete)}
    await client.get("/coaching")
    assert {r.id: (r.day, r.kind, r.minutes, r.status, r.pinned, r.origin, r.seq)
            for r in await _rows(db_session, athlete)} == again


async def test_no_session_runs_ten_percent_past_the_longest_run_of_the_last_thirty_days(client, people, beta,
                                                                                         db_session, same_today):
    """Frandsen 2025 (docs/coaching.md › « Sources »). A plan that already exists meets the rule at its next reading:
    its sessions to come that nobody touched are shortened in place (same rows, same ids), a session a person edited
    and one already done stay as they are, and the plan grows back from the real runs."""
    athlete = people.athlete
    _as(client, athlete)
    plan = await _plan(client, db_session, athlete, days=("1", "3", "6"), base="180")  # no run recorded: no limit
    rows = await _rows(db_session, athlete)
    ahead = [r for r in rows if r.day > same_today]
    assert max(r.minutes for r in ahead) > 70, "long runs past an hour, as the plan was written"
    edited = next(r for r in ahead if r.kind == cp.LONG and r.day > same_today + timedelta(days=7))
    r = await client.post(f"/coaching/sessions/{edited.id}/edit", data={"kind": "sortie_longue", "minutes": "90"},
                          headers=HTMX)
    assert r.status_code == 200
    ids = {r.id for r in ahead}
    # her watch arrives: three runs this month, 40 min at most (one of 2 h, 45 days ago, is outside the 30 days)
    db_session.add_all([_run(athlete, same_today - timedelta(days=d), m, n)
                        for n, (d, m) in enumerate(((2, 40), (9, 36), (16, 38), (45, 120)), 1)])
    await db_session.flush()
    state = await co.sync(db_session, plan, athlete)
    assert state.inputs.recent == tuple(sorted((same_today - timedelta(days=d), m) for d, m in ((2, 40), (9, 36), (16, 38))))
    rows = await _rows(db_session, athlete)
    ahead = [r for r in rows if r.day >= same_today and r.status == "planned"]
    assert {r.id for r in ahead if r.day > same_today} == ids, "rewritten in place: the calendar feed keeps its events"
    m0 = cp.monday_of(same_today)
    this_week = [r for r in ahead if r.day < m0 + timedelta(days=7) and not r.pinned]
    assert all(r.minutes <= cp.session_limit(40) == 45 for r in this_week)
    assert (await db_session.get(CoachingSession, edited.id)).minutes == 90, "a session she edited is hers"
    by_week: dict = {}
    for row in ahead:
        if not row.pinned:
            by_week.setdefault(cp.monday_of(row.day), []).append(row.minutes)
    longest = [max(v) for _m, v in sorted(by_week.items())]
    assert longest[0] <= 45 and all(b <= cp.session_limit(a) for a, b in zip(longest, longest[1:])), longest
    assert longest[-1] > longest[0], "and it grows back, 10 % at most a week"
    assert [r for r in ahead if r.kind in (cp.QUALITY, cp.HILLS)], "the quality sessions keep their place"
    # a second reading writes nothing more
    seqs = {r.id: r.seq for r in rows}
    await co.sync(db_session, plan, athlete)
    assert {r.id: r.seq for r in await _rows(db_session, athlete)} == seqs
    # the athlete's fold says the rule, in her words
    page = (await client.get("/coaching")).text
    assert "Aucune séance ne dépasse de plus de 10 % ta plus longue sortie des 30 derniers jours" in page


async def test_moving_a_session_swaps_with_the_one_already_there(client, people, beta, db_session, same_today):
    athlete = people.athlete
    _as(client, athlete)
    await _plan(client, db_session, athlete)
    rows = [r for r in await _rows(db_session, athlete) if r.day > same_today]
    a, b = rows[0], rows[1]
    days = (a.day, b.day)
    r = await client.get(f"/coaching/calendar?d={a.day}&move={a.id}", headers=HTMX)
    assert "Choisis le nouveau jour" in r.text and f'action="/coaching/sessions/{a.id}/move"' in r.text
    r = await client.post(f"/coaching/sessions/{a.id}/move", data={"to": b.day.isoformat()}, headers=HTMX)
    assert r.status_code == 200
    got = {r.id: r for r in await _rows(db_session, athlete)}
    assert (got[a.id].day, got[b.id].day) == (days[1], days[0]) and got[a.id].pinned and got[b.id].pinned
    r = await client.post(f"/coaching/sessions/{a.id}/move", data={"to": (same_today - timedelta(days=1)).isoformat()},
                          headers=HTMX)
    assert "à partir d’aujourd’hui" in r.text and (await db_session.get(CoachingSession, a.id)).day == days[1]


async def test_fait_pas_faite_and_the_ressenti_are_saved_on_the_tap(client, people, beta, db_session, same_today):
    athlete = people.athlete
    _as(client, athlete)
    plan = await _plan(client, db_session, athlete)
    row = await _past_session(db_session, plan, same_today - timedelta(days=1), minutes=45)
    future = next(r for r in await _rows(db_session, athlete) if r.day > same_today)
    r = await client.post(f"/coaching/sessions/{row.id}/status", data={"value": "done"}, headers=HTMX)
    assert r.status_code == 200 and "C’était comment" in r.text and 'id="coach-main"' in r.text
    await db_session.refresh(row)
    assert (row.status, row.manual, row.done_minutes) == ("done", True, 45)
    r = await client.post(f"/coaching/sessions/{row.id}/status", data={"value": "done", "minutes": "52"}, headers=HTMX)
    await db_session.refresh(row)
    assert row.done_minutes == 50, "optional minutes, to 5 min"
    for level in (4, 2):  # changeable
        r = await client.post(f"/coaching/sessions/{row.id}/ressenti", data={"value": str(level)}, headers=HTMX)
        await db_session.refresh(row)
        assert row.ressenti == level and f'id="coach-r-{row.id}-{level}" data-feel aria-pressed="true"' in r.text
    r = await client.post(f"/coaching/sessions/{row.id}/ressenti", data={"value": "9"}, headers=HTMX)
    assert "Ressenti inconnu" in r.text
    r = await client.post(f"/coaching/sessions/{row.id}/note", data={"note": "  Bonnes   jambes " + "x" * 400},
                          headers=HTMX)
    await db_session.refresh(row)
    assert row.note.startswith("Bonnes jambes x") and len(row.note) == 280
    r = await client.post(f"/coaching/sessions/{row.id}/status", data={"value": "missed"}, headers=HTMX)
    await db_session.refresh(row)
    assert (row.status, row.manual, row.ressenti) == ("missed", True, None)
    r = await client.post(f"/coaching/sessions/{row.id}/status", data={"value": "reset"}, headers=HTMX)
    await db_session.refresh(row)
    assert (row.status, row.manual) == ("missed", False), "planned again, then read as missed: its day is over"
    r = await client.post(f"/coaching/sessions/{future.id}/status", data={"value": "done"}, headers=HTMX)
    assert "à venir" in r.text and (await db_session.get(CoachingSession, future.id)).status == "planned"
    # without JavaScript the same form comes back to the page
    r = await client.post(f"/coaching/sessions/{row.id}/status", data={"value": "done"})
    assert r.status_code == 303 and r.headers["location"] == f"/coaching?d={row.day.isoformat()}"
    # nobody else's session
    other = await _plan(client, db_session, people.coach)
    theirs = (await _rows(db_session, people.coach))[0]
    _as(client, athlete)
    assert other.user_id == people.coach.id
    assert (await client.post(f"/coaching/sessions/{theirs.id}/status", data={"value": "done"})).status_code == 404


async def test_the_ressenti_lightens_the_next_week_and_says_why(client, people, beta, db_session, same_today):
    athlete, m0 = people.athlete, cp.monday_of(same_today)
    _as(client, athlete)
    plan = await _plan(client, db_session, athlete, base="240")
    plan.start_monday = m0 - timedelta(weeks=2)  # a plan in its third week
    await db_session.flush()
    await co.sync(db_session, plan, athlete)
    nxt = m0 + timedelta(weeks=1)

    def week(rows, monday):
        return [r for r in rows if monday <= r.day < monday + timedelta(days=7) and r.status != "cancelled"]

    plain = week(await _rows(db_session, athlete), nxt)
    assert [r for r in plain if r.kind in (cp.QUALITY, cp.HILLS)]
    was = sum(r.minutes for r in plain)  # read now: the rows are rewritten in place
    # a footing done this week, felt « Très dur » (long enough to leave next week's sessions their length: no
    # session runs more than 10 % past the longest run of the last 30 days)
    day = m0 if same_today > m0 else same_today
    row = await _past_session(db_session, plan, day, minutes=100, status="done", manual=True, done_minutes=100)
    r = await client.post(f"/coaching/sessions/{row.id}/ressenti", data={"value": "5"}, headers=HTMX)
    assert r.status_code == 200
    await db_session.refresh(plan)
    assert plan.adaptations == {nxt.isoformat(): {"reason": "ressenti", "factor": 0.8}}
    light = week(await _rows(db_session, athlete), nxt)
    assert not [r for r in light if r.kind in (cp.QUALITY, cp.HILLS)], "its quality session became a footing"
    assert abs(sum(r.minutes for r in light) - was * 0.8) <= 5
    page = (await client.get(f"/coaching/calendar?w={nxt}", headers=HTMX)).text
    assert "Semaine allégée : tes séances faciles t’ont paru dures." in page
    assert page.count("Semaine allégée") == 1, "one short line"
    # the ressenti changed back: the planned progression is back
    await client.post(f"/coaching/sessions/{row.id}/ressenti", data={"value": "3"}, headers=HTMX)
    await db_session.refresh(plan)
    assert not plan.adaptations
    assert sum(r.minutes for r in week(await _rows(db_session, athlete), nxt)) == was


async def test_missed_weeks_repeat_then_restart_from_the_real_activities(people, beta, db_session, same_today):
    athlete, m0 = people.athlete, cp.monday_of(same_today)
    c = await co.clock(db_session, athlete)
    plan = await co.save_plan(db_session, athlete, c, route_id=None, days=[1, 3, 6], long_day=6, base=200)
    plan.start_monday = m0 - timedelta(weeks=3)
    for d in (1, 3, 6):  # last week: three sessions, none done
        await _past_session(db_session, plan, m0 - timedelta(weeks=1) + timedelta(days=d))
    state = await co.sync(db_session, plan, athlete)
    assert plan.adaptations[m0.isoformat()] == {"reason": "semaine_manquee"}
    targets = cp.week_targets(state.inputs, m0)
    assert targets[m0]["target"] == targets[m0 - timedelta(weeks=1)]["target"], "the same volume again"
    assert cv.week_note(state, m0)["reason"] == "Même volume que la semaine dernière, restée sans séance."
    for d in (1, 3, 6):  # and the week before it too
        await _past_session(db_session, plan, m0 - timedelta(weeks=2) + timedelta(days=d))
    db_session.add(_run(athlete, m0 - timedelta(weeks=4), 90, 1))  # the only run of the last 4 complete weeks
    await db_session.flush()
    state = await co.sync(db_session, plan, athlete)
    assert plan.adaptations[m0.isoformat()] == {"reason": "reprise", "base": 60}
    assert cp.week_targets(state.inputs, m0)[m0]["target"] == 60
    assert cv.week_note(state, m0)["reason"].startswith("Reprise en douceur")


# ── athlete → coach ─────────────────────────────────────────────────────────

async def _share(client, athlete) -> str:
    r = await _as(client, athlete).post("/coaching/share", headers=HTMX)
    assert r.status_code == 200
    return re.search(r'class="pf-coach-code"[^>]*>([A-Z0-9]{4}-[A-Z0-9]{4})<', r.text).group(1)


async def test_a_code_links_a_coach_once_within_seven_days(client, people, beta, db_session, same_today, monkeypatch):
    athlete, coach = people.athlete, people.coach
    await _plan(client, db_session, athlete)
    code = await _share(client, athlete)
    invite = (await db_session.execute(select(CoachingInvite))).scalar_one()
    assert invite.code_hash == co.code_hash(code) and code.replace("-", "") not in invite.code_hash
    assert timedelta(days=6, hours=23) < invite.expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc) \
        <= timedelta(days=7)
    assert code not in (await client.get("/coaching")).text, "shown once"
    # the athlete cannot follow herself; a wrong code tells nothing
    r = await client.post("/coaching/athletes/join", data={"code": code})
    assert r.status_code == 200 and "Code invalide ou expiré." in r.text
    _as(client, coach)
    assert (await client.get(f"/coaching/athletes/{athlete.id}")).status_code == 404, "not linked yet"
    assert (await client.post("/coaching/athletes/join", data={"code": "ZZZZ-ZZZZ"})).status_code == 200
    r = await client.post("/coaching/athletes/join", data={"code": f" {code.lower().replace('-', ' ')} "})
    assert r.status_code == 303 and r.headers["location"] == f"/coaching/athletes/{athlete.id}"
    assert (await db_session.execute(select(CoachingLink.athlete_id, CoachingLink.coach_id))).all() == [
        (athlete.id, coach.id)]
    # single use
    third = User(email="trois@x.fr", email_verified=True, firstname="Trois", timezone="UTC")
    db_session.add(third)
    await db_session.flush()
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", f"{ATHLETE},{COACH},trois@x.fr")
    r = await _as(client, third).post("/coaching/athletes/join", data={"code": code})
    assert r.status_code == 200 and (await db_session.execute(select(func.count()).select_from(CoachingLink))).scalar() == 1
    # expired
    code = await _share(client, athlete)
    invite = (await db_session.execute(select(CoachingInvite).where(CoachingInvite.used_at.is_(None)))).scalar_one()
    invite.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.flush()
    assert (await _as(client, third).post("/coaching/athletes/join", data={"code": code})).status_code == 200
    # a new code replaces the one not used yet
    old, new = await _share(client, athlete), await _share(client, athlete)
    assert (await _as(client, third).post("/coaching/athletes/join", data={"code": old})).status_code == 200
    assert (await client.post("/coaching/athletes/join", data={"code": new})).status_code == 303
    # an account outside the list cannot be a coach
    another = await _share(client, athlete)
    assert (await _as(client, people.outsider).post("/coaching/athletes/join", data={"code": another})).status_code == 404


async def test_the_coach_sees_the_plan_and_edits_it_never_more(client, people, beta, db_session, same_today):
    athlete, coach = people.athlete, people.coach
    plan = await _plan(client, db_session, athlete)
    past = await _past_session(db_session, plan, same_today - timedelta(days=1), minutes=45)
    db_session.add_all([_run(athlete, same_today - timedelta(days=1), 47, 1, name="Footing du matin"),
                        CoachingLink(athlete_id=athlete.id, coach_id=coach.id)])
    await db_session.flush()
    assert "Footing du matin" in (await _as(client, athlete).get(f"/coaching?d={past.day}")).text  # matched
    await client.post(f"/coaching/sessions/{past.id}/ressenti", data={"value": "4"}, headers=HTMX)
    await client.post(f"/coaching/sessions/{past.id}/note", data={"note": "Jambes lourdes"}, headers=HTMX)
    base = f"/coaching/athletes/{athlete.id}"
    _as(client, coach)
    listing = (await client.get("/coaching/athletes")).text
    assert "Stella" in listing and "Footing" in listing and ">Dur<" in listing and f'href="{base}"' in listing
    page = (await client.get(f"{base}?d={past.day}")).text
    assert "<h1" in page and "Stella" in page and "Footing du matin" in page and "Jambes lourdes" in page
    assert "Ressenti : Dur" in page and "/activity/" not in page, "the activity's summary, never its page"
    assert not re.search(r"VFC|Sommeil|sommeil|FC de nuit|/sante\?|Poids", page.split('id="main-content"')[1].split("pf-tabbar")[0])
    assert "Arrêter le coaching" not in page and "Ajouter à mon calendrier" not in page
    # the coach comments, adds, edits, moves, removes: pinned, origin coach
    r = await client.post(f"{base}/sessions/{past.id}/note", data={"note": "Bien joué"}, headers=HTMX)
    await db_session.refresh(past)
    assert r.status_code == 200 and (past.coach_comment, past.note) == ("Bien joué", "Jambes lourdes")
    target = same_today + timedelta(days=2)
    r = await client.post(f"{base}/sessions", data={"day": target.isoformat(), "kind": "seance", "minutes": "55"},
                          headers=HTMX)
    assert r.status_code == 200 and "3 × 8 min soutenu, récup 2 min" in r.text
    added = next(r for r in await _rows(db_session, athlete) if r.origin == "coach")
    assert (added.day, added.kind, added.minutes, added.pinned, added.auto_day) == (target, "seance", 55, True, None)
    generated = next(r for r in await _rows(db_session, athlete) if r.origin == "auto" and r.day > same_today)
    r = await client.post(f"{base}/sessions/{generated.id}/edit", data={"kind": "footing", "minutes": "40"}, headers=HTMX)
    await db_session.refresh(generated)
    assert (generated.minutes, generated.pinned, generated.origin) == (40, True, "coach")
    r = await client.post(f"{base}/sessions/{generated.id}/delete", headers=HTMX)
    await db_session.refresh(generated)
    assert generated.status == "cancelled", "kept, so that the regeneration does not make it again"
    await co.sync(db_session, plan, athlete)
    assert not [r for r in await _rows(db_session, athlete) if r.auto_day == generated.auto_day and r.status != "cancelled"]
    await client.post(f"{base}/sessions/{added.id}/delete", headers=HTMX)
    assert await db_session.get(CoachingSession, added.id) is None
    # what stays the athlete's: the status and the ressenti; what stays the coach's: add and delete
    assert (await client.post(f"{base}/sessions/{past.id}/status", data={"value": "missed"})).status_code == 404
    assert (await client.post(f"{base}/sessions/{past.id}/ressenti", data={"value": "1"})).status_code == 404
    _as(client, athlete)
    assert (await client.post(f"/coaching/sessions/{past.id}/delete")).status_code == 404
    assert (await client.post("/coaching/sessions", data={"day": target.isoformat(), "kind": "footing",
                                                          "minutes": "30"})).status_code == 404
    page = (await client.get(f"/coaching?d={past.day}")).text
    assert "Ton coach" in page and "Bien joué" in page


async def test_revoking_leaving_or_leaving_the_list_ends_the_access_at_once(client, people, beta, db_session,
                                                                            same_today, monkeypatch):
    athlete, coach = people.athlete, people.coach
    await _plan(client, db_session, athlete)
    base = f"/coaching/athletes/{athlete.id}"

    async def link():
        db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
        await db_session.flush()

    async def sees() -> list[int]:
        _as(client, coach)
        return [(await client.get(base)).status_code, (await client.get(f"{base}/calendar", headers=HTMX)).status_code,
                (await client.post(f"{base}/sessions", data={"day": same_today.isoformat(), "kind": "footing",
                                                             "minutes": "30"}, headers=HTMX)).status_code]

    assert await sees() == [404, 404, 404]
    await link()
    assert await sees() == [200, 200, 200]
    # the athlete revokes
    page = (await _as(client, athlete).get("/coaching")).text
    assert f'action="/coaching/coaches/{coach.id}/revoke"' in page and "Florent" in page
    r = await client.post(f"/coaching/coaches/{coach.id}/revoke", headers=HTMX)
    assert r.status_code == 200 and "Florent" not in r.text
    assert await sees() == [404, 404, 404]
    # the coach leaves
    await link()
    assert (await _as(client, coach).post(f"{base}/leave")).status_code == 303
    assert await sees() == [404, 404, 404]
    # the athlete leaves the list (the link stays, unseen), then the coach does
    await link()
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", COACH)
    assert await sees() == [404, 404, 404]
    assert "Stella" not in (await client.get("/coaching/athletes")).text
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", ATHLETE)
    assert await sees() == [404, 404, 404]
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", f"{ATHLETE},{COACH}")
    assert await sees() == [200, 200, 200]
    # a coach without a plan of their own lands on their athletes, and can still make one
    r = await _as(client, coach).get("/coaching")
    assert r.status_code == 303 and r.headers["location"] == "/coaching/athletes"
    assert "Créer mon plan" in (await client.get("/coaching?refaire=1")).text
    assert "Créer mon propre plan" in (await client.get("/coaching/athletes")).text
    # « Arrêter le coaching »: the plan, its sessions and its sharing go
    assert (await _as(client, athlete).post("/coaching/stop")).status_code == 303
    for model in (CoachingPlan, CoachingSession, CoachingLink, CoachingInvite):
        assert (await db_session.execute(select(func.count()).select_from(model))).scalar() == 0, model.__name__
    assert await sees() == [404, 404, 404]
    assert "Créer mon plan" in (await _as(client, athlete).get("/coaching")).text


# ── the calendar feed ───────────────────────────────────────────────────────

def _parse(text: str) -> tuple[dict, list[dict]]:
    """A feed parsed back: (the calendar's own properties, its events), lines unfolded, TEXT values unescaped."""
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", ""), "CRLF line ends only"
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, line
    lines = text.replace("\r\n ", "").split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR"
    head, events, event = {}, [], None
    for line in lines[1:-2]:
        if line == "BEGIN:VEVENT":
            event = {}
        elif line == "END:VEVENT":
            events.append(event)
            event = None
        else:
            name, value = line.split(":", 1)
            value = re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)
            (head if event is None else event)[name] = value
    return head, events


def test_the_feed_escapes_folds_and_parses_back():
    now = datetime(2026, 11, 21, 8, 30, tzinfo=timezone.utc)
    rows = [
        SimpleNamespace(id=7, day=date(2026, 11, 24), kind="seance", minutes=55, seq=3, updated_at=now,
                        status="planned", structure=cp.quality_session("seuil", 3)[2]),
        SimpleNamespace(id=8, day=date(2026, 11, 22), kind="sortie_longue", minutes=110, seq=0, updated_at=now,
                        status="done", structure=cp.steady(110, 2)),
        SimpleNamespace(id=9, day=date(2026, 11, 25), kind="footing", minutes=45, seq=0, updated_at=now,
                        status="cancelled", structure=cp.steady(45, 2)),
        SimpleNamespace(id=10, day=date(2026, 10, 1), kind="footing", minutes=45, seq=0, updated_at=now,
                        status="done", structure=cp.steady(45, 2)),
        SimpleNamespace(id=11, day=date(2026, 10, 22), kind="footing", minutes=45, seq=0, updated_at=None,
                        status="missed", structure=None),
    ]
    text = ics.render(rows, date(2026, 11, 21), host="paceforge.fr",
                      guidance=lambda r: "Soutenu ; quelques mots, pas des phrases. Été, à cœur : 149–167 bpm" + BS + "fin")
    head, events = _parse(text)
    assert head["VERSION"] == "2.0" and head["PRODID"] and head["X-WR-CALNAME"] == "PaceForge · Coaching"
    assert [e["UID"] for e in events] == ["coaching-11@paceforge.fr", "coaching-8@paceforge.fr",
                                         "coaching-7@paceforge.fr"], "30 days back, by day; cancelled left out"
    quality = events[2]
    assert quality["SUMMARY"] == "Séance 55 min" and events[1]["SUMMARY"] == "Sortie longue 1h50"
    assert quality["DTSTART;VALUE=DATE"] == "20261124" and quality["DTEND;VALUE=DATE"] == "20261125"
    assert quality["SEQUENCE"] == "3" and quality["DTSTAMP"] == quality["LAST-MODIFIED"] == "20261121T083000Z"
    assert quality["DESCRIPTION"] == ("3 × 8 min soutenu, récup 2 min\n"
                                      "Soutenu ; quelques mots, pas des phrases. Été, à cœur : 149–167 bpm" + BS + "fin")
    assert "DTSTART" not in " ".join(k for k in quality if "VALUE=DATE" not in k), "all-day events only"
    assert BS + ", récup" in text and "Soutenu " + BS + "; quelques" in text and "bpm" + BS * 2 + "fin" in text
    assert ics.fold("é" * 80).split("\r\n ")[0].encode("utf-8").__len__() <= 75
    assert ics.fold("a" * 75) == "a" * 75 and ics.fold("a" * 76) == "a" * 75 + "\r\n a"


async def test_the_feed_is_stable_follows_edits_and_dies_with_its_token(client, people, beta, db_session, same_today,
                                                                        monkeypatch):
    athlete = people.athlete
    plan = await _plan(client, db_session, athlete)
    old = await _past_session(db_session, plan, same_today - timedelta(days=45))
    recent = await _past_session(db_session, plan, same_today - timedelta(days=5))
    page = (await _as(client, athlete).get("/coaching")).text
    token = plan.feed_token
    assert len(token) >= 43 and plan.feed_token_hash == co.token_hash(token) and token not in plan.feed_token_hash
    assert f"webcal://paceforge.example/coaching/cal/{token}.ics" in page, "shown again to its owner"
    stored = (await db_session.execute(text("SELECT feed_token FROM coaching_plans"))).scalar()
    assert token not in stored and stored.startswith("gAAAA"), "encrypted at rest"
    client._transport.app.dependency_overrides.pop(get_current_user)  # no session: the token is the key
    r = await client.get(f"/coaching/cal/{token}.ics")
    assert r.status_code == 200 and r.headers["content-type"] == "text/calendar; charset=utf-8"
    head, events = _parse(r.text)
    uids = [e["UID"] for e in events]
    assert f"coaching-{recent.id}@paceforge.example" in uids and f"coaching-{old.id}@paceforge.example" not in uids
    rows = [r for r in await _rows(db_session, athlete) if r.day > same_today]
    assert len(events) == len(rows) + 1 + len([r for r in await _rows(db_session, athlete) if r.day == same_today])
    assert all("VALUE=DATE" in k for e in events for k in e if k.startswith("DT") and k not in ("DTSTAMP",))
    again = _parse((await client.get(f"/coaching/cal/{token}.ics")).text)[1]
    assert again == events, "the same feed twice: same UIDs, same stamps"
    # an edit, a move and a removal reach the feed
    edited, moved, removed = rows[0], rows[1], rows[2]
    _as(client, athlete)
    await client.post(f"/coaching/sessions/{edited.id}/edit", data={"kind": "footing", "minutes": "35"}, headers=HTMX)
    free = next(d for d in (moved.day + timedelta(days=i) for i in range(1, 9)) if d.weekday() == 0)
    await client.post(f"/coaching/sessions/{moved.id}/move", data={"to": free.isoformat()}, headers=HTMX)
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=people.coach.id))
    await db_session.flush()
    _as(client, people.coach)
    assert (await client.post(f"/coaching/athletes/{athlete.id}/sessions/{removed.id}/delete", headers=HTMX)).status_code == 200
    client._transport.app.dependency_overrides.pop(get_current_user)
    after = {e["UID"]: e for e in _parse((await client.get(f"/coaching/cal/{token}.ics")).text)[1]}
    before = {e["UID"]: e for e in events}
    uid = lambda row: f"coaching-{row.id}@paceforge.example"  # noqa: E731
    assert after[uid(edited)]["SUMMARY"] == "Footing 35 min"
    assert int(after[uid(edited)]["SEQUENCE"]) == int(before[uid(edited)]["SEQUENCE"]) + 1
    assert after[uid(moved)]["DTSTART;VALUE=DATE"] == free.strftime("%Y%m%d")
    assert int(after[uid(moved)]["SEQUENCE"]) > int(before[uid(moved)]["SEQUENCE"])
    assert uid(removed) not in after and uid(removed) in before
    untouched = [u for u in after if u not in (uid(edited), uid(moved))]
    assert untouched and all(after[u] == before[u] for u in untouched)
    # a wrong token, then the regenerated link: the old one stops working
    assert (await client.get("/coaching/cal/" + "x" * 43 + ".ics")).status_code == 404
    _as(client, athlete)
    r = await client.post("/coaching/feed/regenerate", headers=HTMX)
    await db_session.refresh(plan)
    assert r.status_code == 200 and plan.feed_token != token and plan.feed_token in r.text and token not in r.text
    client._transport.app.dependency_overrides.pop(get_current_user)
    assert (await client.get(f"/coaching/cal/{token}.ics")).status_code == 404
    assert (await client.get(f"/coaching/cal/{plan.feed_token}.ics")).status_code == 200
    # its owner is no longer in the list; then Coaching is off
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", COACH)
    assert (await client.get(f"/coaching/cal/{plan.feed_token}.ics")).status_code == 404
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", "off")
    assert (await client.get(f"/coaching/cal/{plan.feed_token}.ics")).status_code == 404


def test_the_feed_token_is_masked_in_the_access_log():
    from app.log_redaction import redact

    line = 'GET /coaching/cal/Kikqo8SpCo7sO8Rb_X1p5RlGL5HtHVQXf-lMJmevX_A.ics HTTP/1.1'
    assert redact(line) == "GET /coaching/cal/***.ics HTTP/1.1"
    assert redact("GET /coaching/calendar?d=2026-11-21 HTTP/1.1") == "GET /coaching/calendar?d=2026-11-21 HTTP/1.1"


# ── the daily round ─────────────────────────────────────────────────────────

async def test_the_daily_round_brings_every_beta_plan_up_to_date(client, people, beta, db_session, same_today,
                                                                 monkeypatch):
    from app.tasks import coaching as task

    athlete, coach = people.athlete, people.coach
    plan = await _plan(client, db_session, athlete)
    await _plan(client, db_session, coach)
    missed = await _past_session(db_session, plan, same_today - timedelta(days=1))
    done = await _past_session(db_session, plan, same_today - timedelta(days=2))
    db_session.add(_run(athlete, same_today - timedelta(days=2), 44, 1))
    await db_session.flush()
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", ATHLETE)  # the list no longer names the coach
    assert await task.run(db_session) == 1
    await db_session.refresh(missed)
    await db_session.refresh(done)
    assert missed.status == "missed" and (done.status, done.done_minutes) == ("done", 44)
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", "off")
    assert await task._run() == 0, "nothing while it is off (no database opened)"


# ── what the page draws ─────────────────────────────────────────────────────

def test_the_intensity_profile_draws_each_step_by_its_time_and_level():
    _kind, minutes, structure = cp.quality_session("intervalles", 6)
    p = viz.intensity_profile(structure)
    assert len(p["blocks"]) == 1 + 6 + 5 + 1, "warm-up, 6 efforts, 5 recoveries, cool-down"
    assert [b["part"] for b in p["blocks"]][:4] == ["echauffement", "effort", "recup", "effort"]
    widths = [b["w"] for b in p["blocks"]]
    assert widths[0] == pytest.approx(5 * widths[1], rel=0.02), "15 min of warm-up against a 3-min effort"
    efforts = [b for b in p["blocks"] if b["part"] == "effort"]
    assert all(b["h"] > p["blocks"][0]["h"] > p["blocks"][2]["h"] for b in efforts), "taller with the intensity"
    assert p["blocks"][-1]["x"] + p["blocks"][-1]["w"] == pytest.approx(p["W"], abs=0.5)
    assert "6 fois 3 minutes d’effort" in p["aria"] and "échauffement 15 minutes" in p["aria"]
    assert len(viz.intensity_profile(cp.steady(45, 2))["blocks"]) == 1
    assert viz.intensity_profile(None) is None and viz.intensity_profile({"steps": []}) is None


async def test_the_page_prints_each_number_once_and_explains_in_one_fold(client, people, beta, db_session, same_today):
    athlete = people.athlete
    athlete.max_heart_rate = 186
    plan = await _plan(client, db_session, athlete, base="240")
    await _past_session(db_session, plan, same_today - timedelta(days=1), minutes=45)
    nxt = next(r for r in await _rows(db_session, athlete) if r.day > same_today and r.kind in (cp.QUALITY, cp.HILLS))
    page = (await _as(client, athlete).get(f"/coaching?d={nxt.day}")).text
    main = page[page.index('id="coach-main"'):page.index("Comment ça marche")]
    main = re.sub(r'<script type="application/json".*?</script>', "", main, flags=re.S)  # the chart's own readouts
    assert main.count(cp.describe(nxt.kind, nxt.structure)) == 1
    assert 'class="pf-effort"' in main and 'role="img"' in main
    assert re.search(r"Efforts (soutenus|vifs)", main)
    summary = cv.week_summary(await co.sync(db_session, plan, athlete), cp.monday_of(nxt.day), {})
    assert main.count(summary["label"]) == 1, "the week's time, on its bar only"
    assert "<p" not in main[main.index('id="coach-plan-h"'):].split("</figure>")[1].split("</section>")[0].replace(
        '<p class="pf-viz-legend"', ""), "no sentence under the chart"
    assert page.count("Comment ça marche") == 1 and "10 %" in page.split("Comment ça marche")[1]
    assert not re.search(r"Bosquet|Mujika|Seiler|\(H\)", page), "sources and (H) stay in the docs and the code"
    # a heart-rate range once a maximum is known (the app's zones), none without
    easy = next(r for r in await _rows(db_session, athlete) if r.day > same_today and r.kind == cp.FOOTING)
    assert "112–148" in (await client.get(f"/coaching?d={easy.day}")).text
    athlete.max_heart_rate = None
    assert "bpm" not in (await client.get(f"/coaching?d={easy.day}")).text
    # the month view and a week further on
    month = (await client.get(f"/coaching/calendar?d={easy.day}&v=mois", headers=HTMX)).text
    assert "pf-coach-month" in month and 'aria-pressed="true">Mois' in month
    assert (await client.get("/coaching/calendar?d=nonsense", headers=HTMX)).status_code == 200
    assert (await client.get("/coaching/calendar?d=2026-11-21")).status_code == 303, "a partial opened by itself"
