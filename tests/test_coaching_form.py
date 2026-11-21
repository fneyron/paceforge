"""Coaching and the form (docs/coaching.md › « La forme »): an explicit, separate option, off by default, that reads
Santé's own daily index — never a second score — and, on a low morning, turns the day's hard session into a footing
and moves it. With the option off nothing reads health data; a coach never sees a health value, a band or the
reason; the calendar feed says only the session as it now is; turning the option off, withdrawing the health consent
or deleting the plan clears what was kept."""
import io
import json
import math
import re
import zipfile
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.models.coaching import CoachingLink, CoachingPlan, CoachingSession
from app.models.health import HealthMetric
from app.services import coaching as co
from app.services import coaching_ics as ics
from app.services import coaching_plan as cp
from app.services import coaching_view as cv
from app.services import health_consent, sante
from app.services.health import HRV_METHOD
from tests.test_coaching import HTMX, _as, _plan, _rows, beta, people  # noqa: F401
from tests.test_coaching_goal import _race, clock  # noqa: F401

SWITCH = 'id="coach-form-switch"'
ATHLETE_LINE = "Séance allégée : récupération basse ce matin"
COACH_LINE = "Séance adaptée ce matin"
# what must never reach a coach's page, the calendar feed or another account's export
# (« récupération » alone is also a session's word: « 2 minutes de récupération » between two efforts)
HEALTH_WORDS = ("récupération basse", "Récupération", "basse", "VFC", "sommeil", "FC de repos", " forme", "/sante",
                "indice", "allégée", "form_state", "form_on_at")


async def _nights(db, user, today: date, *, low: bool = False, days: int = 110) -> None:
    """A watch's nights up to this morning: sleep, night heart rate, HRV; `low`: the last 7 nights' HRV far under
    its usual values (Santé's index then reads « à surveiller »)."""
    await db.execute(delete(HealthMetric).where(HealthMetric.user_id == user.id))
    for i in range(days):
        day = today - timedelta(days=i)
        hrv = 40 if low and i < 7 else 70 * math.exp(.08 * (i % 5 - 2))
        for metric, value, details in (
                ("sleep", 460, {"main_start": f"{day - timedelta(days=1)}T23:00", "main_end": f"{day}T07:00",
                                "timeline": False, "stages": {"awake": 20, "light": 280, "deep": 90, "rem": 90}}),
                ("hr_night", 44 + i % 3, {"method": "coros_sleep_summary"}),
                ("hrv", hrv, {"method": HRV_METHOD, "n": 24})):
            db.add(HealthMetric(user_id=user.id, date=day, metric=metric, value=value, source="COROS", details=details))
    await db.flush()


async def _consent(db, user) -> None:
    health_consent.record(user)
    await db.flush()


async def _wednesday(client, db, athlete, clock, *, low=True, on=True, days=("1", "2", "4", "6"), goal="none",
                     base="240"):
    """A plan made on a Monday (Tuesday, Wednesday, Friday, Sunday; the quality session on Wednesday, the long run on
    Sunday), read on its Wednesday, with a watch's nights and the health consent; `on`: the option turned on."""
    clock.day = cp.monday_of(clock.day)
    await _consent(db, athlete)
    plan = await _plan(client, db, athlete, days=days, long_day="6", base=base, goal=goal)
    clock.day += timedelta(days=2)
    await _nights(db, athlete, clock.day, low=low)
    if on:
        r = await _as(client, athlete).post("/coaching/form", data={"value": "on"})
        assert r.status_code == 303 and r.headers["location"] == "/coaching#coach-form"
    return plan


def _snap(rows):
    return [(r.id, r.day, r.kind, r.minutes, r.status, r.form, r.seq) for r in rows]


# ── the option: explicit, separate, off by default ──────────────────────────

async def test_the_option_is_offered_only_with_the_consent_and_an_index_and_is_off_by_default(client, people,
                                                                                             db_session, clock):
    athlete = people.athlete
    _as(client, athlete)
    plan = await _plan(client, db_session, athlete)
    assert plan.form_on_at is None and plan.form_state is None, "off by default"
    assert SWITCH not in (await client.get("/coaching")).text, "no health consent: not offered"
    await _nights(db_session, athlete, clock.day)
    assert SWITCH not in (await client.get("/coaching")).text, "health data without the consent: not offered"
    r = await client.post("/coaching/form", data={"value": "on"})
    await db_session.refresh(plan)
    assert r.status_code == 303 and plan.form_on_at is None, "nor can it be turned on behind the page's back"
    await _consent(db_session, athlete)
    await db_session.execute(delete(HealthMetric).where(HealthMetric.user_id == athlete.id))
    assert SWITCH not in (await client.get("/coaching")).text, "the consent without an index in Santé: not offered"
    await _nights(db_session, athlete, clock.day)
    page = (await client.get("/coaching")).text
    switch = re.search(r'<button[^>]*id="coach-form-switch"[^>]*>', page).group(0)
    assert 'role="switch"' in switch and 'aria-checked="false"' in switch and 'value="on"' in switch
    assert "Adapter mes séances à ma forme <small>VFC, sommeil, FC de repos</small>" in page
    assert "Activé le" not in page
    await db_session.refresh(plan)
    assert plan.form_on_at is None, "seeing the switch turns nothing on"
    before = datetime.now(timezone.utc) - timedelta(seconds=5)
    assert (await client.post("/coaching/form", data={"value": "on"})).status_code == 303
    await db_session.refresh(plan)
    at = plan.form_on_at if plan.form_on_at.tzinfo else plan.form_on_at.replace(tzinfo=timezone.utc)
    assert at >= before, "turning it on records when"
    page = (await client.get("/coaching")).text
    switch = re.search(r'<button[^>]*id="coach-form-switch"[^>]*>', page).group(0)
    assert 'aria-checked="true"' in switch and 'value="off"' in switch and "Activé le " in page
    # the privacy policy says it, while Coaching exists
    policy = (await client.get("/privacy")).text
    assert "Coaching (bêta) · ta forme" in policy and "jamais l’indice ni une mesure" in policy


async def test_with_the_option_off_nothing_in_coaching_reads_health_data(client, people, db_session, clock,
                                                                        monkeypatch):
    """The consent given, a low index in Santé, a hard session today — and the option off: the plan's readings (the
    page's taps, the coach's page, the daily and the morning rounds) never ask Santé for the index."""
    athlete = people.athlete
    await _wednesday(client, db_session, athlete, clock, low=True, on=False)
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=people.coach.id))
    await db_session.flush()
    asked = []

    async def spy(*a, **k):
        asked.append("index")
        raise AssertionError("Santé's index was read with the option off")
    monkeypatch.setattr(sante, "morning_index", spy)
    plan = await co.get_plan(db_session, athlete.id)
    await co.sync(db_session, plan, athlete)  # Tuesday's session, not done, is marked missed
    before = _snap(await _rows(db_session, athlete))
    await co.sync(db_session, plan, athlete)
    from app.tasks import coaching as task

    assert await task.run(db_session) == 1 and await task.run_morning(db_session) == 0
    assert (await client.get("/coaching/calendar", headers=HTMX)).status_code == 200
    assert (await _as(client, people.coach).get(f"/coaching/athletes/{athlete.id}")).status_code == 200
    assert asked == [] and _snap(await _rows(db_session, athlete)) == before
    today = [r for r in await _rows(db_session, athlete) if r.day == clock.day]
    assert [r.kind for r in today] == [cp.HILLS] or today[0].kind in co.HARD_KINDS, "today's hard session stands"
    assert plan.form_state is None


async def test_coaching_reads_santes_own_index_and_its_own_bands(people, db_session, clock):
    """Never a second score: the value and the state are the ones /sante computes for that day; « low » is Santé's
    state not being « ok » (its green band starts at 70: sante_score.HOLD)."""
    from app.services import sante_score

    athlete = people.athlete
    for low in (False, True):
        await _nights(db_session, athlete, clock.day, low=low)
        mine = await sante.morning_index(db_session, athlete.id, clock.day)
        page = await sante.health_page(db_session, athlete.id, today=clock.day)
        assert (mine["value"], mine["tone"]) == (page["score"]["value"], page["score"]["tone"])
        assert mine["low"] is low and (mine["tone"] != "ok") is low
        lo, hi = sante_score.HOLD[mine["tone"]]
        assert lo <= mine["value"] <= hi and (mine["value"] < sante_score.HOLD["ok"][0]) is low
        assert await sante.has_index(db_session, athlete.id, clock.day)
    await db_session.execute(delete(HealthMetric).where(HealthMetric.user_id == athlete.id,
                                                        HealthMetric.date == clock.day))
    await db_session.flush()
    assert await sante.morning_index(db_session, athlete.id, clock.day) is None, "no night this morning: no index"
    assert await sante.has_index(db_session, athlete.id, clock.day), "yesterday's still says Santé has one"
    await db_session.execute(delete(HealthMetric).where(HealthMetric.user_id == athlete.id))
    await db_session.flush()
    assert not await sante.has_index(db_session, athlete.id, clock.day)


# ── a low morning ───────────────────────────────────────────────────────────

async def test_a_low_morning_turns_the_hard_session_into_a_footing_and_moves_it(client, people, db_session, clock):
    athlete, coach = people.athlete, people.coach
    await _consent(db_session, athlete)
    clock.day = cp.monday_of(clock.day)
    plan = await _plan(client, db_session, athlete, days=("1", "2", "4", "6"), long_day="6", base="240")
    rows = await _rows(db_session, athlete)
    wed, fri, sun = (clock.day + timedelta(days=d) for d in (2, 4, 6))
    hard = next(r for r in rows if r.day == wed)
    easy = next(r for r in rows if r.day == fri)
    assert hard.kind in (cp.QUALITY, cp.HILLS) and easy.kind in (cp.FOOTING, cp.RECUP)
    was, week = (hard.kind, hard.minutes, hard.structure), sum(r.minutes for r in rows if r.day <= sun)
    clock.day = wed
    await _nights(db_session, athlete, wed, low=True)
    assert (await client.post("/coaching/form", data={"value": "on"})).status_code == 303
    rows = await _rows(db_session, athlete)
    today, moved = next(r for r in rows if r.day == wed), next(r for r in rows if r.day == fri)
    assert (today.id, today.kind, today.form) == (hard.id, cp.FOOTING, cp.LIGHTENED), "the same row: the feed's UID"
    assert today.minutes == min(cp.form_footing(was[1]), easy.minutes) <= cp.FORM_FOOTING_CAP and today.minutes < was[1]
    assert (moved.id, moved.kind, moved.minutes, moved.structure, moved.form) == (easy.id, *was, cp.MOVED)
    hard_days = sorted(r.day for r in rows if r.kind in cp.HARD)
    assert all((b - a).days >= 2 for a, b in zip(hard_days, hard_days[1:])), "never two hard days in a row"
    assert sum(r.minutes for r in rows if r.day <= sun) <= week, "a low morning never adds time"
    assert plan.form_state["low"] == [wed.isoformat()] and plan.form_state["seen"] is True
    assert set(plan.form_state) == {"day", "seen", "checked", "low"}, "dates only: no index, no measure, no reason"
    # idempotent: reading again writes nothing (the page, a tap, the daily round)
    snapshot = _snap(rows)
    page = (await client.get("/coaching")).text
    from app.tasks import coaching as task

    assert await task.run(db_session) == 1
    assert _snap(await _rows(db_session, athlete)) == snapshot
    # the athlete: one short reason on the day's card, the way to Santé, never the number
    index = await sante.morning_index(db_session, athlete.id, wed)
    assert f'<p class="pf-coach-formline"><a href="/sante">{ATHLETE_LINE}</a> <span>La séance prévue passe à vendredi.</span></p>' in page
    assert page.count("récupération basse") == 1 and f"{index['value']} %" not in page
    assert "Séance reportée de mercredi" in (await client.get(f"/coaching?d={fri}")).text
    # the coach: the session was adapted, never why
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    await db_session.flush()
    _as(client, coach)
    theirs = (await client.get(f"/coaching/athletes/{athlete.id}?d={wed}")).text
    assert f'<p class="pf-coach-formline">{COACH_LINE}</p>' in theirs
    pages = [theirs, (await client.get(f"/coaching/athletes/{athlete.id}?d={fri}")).text,
             (await client.get(f"/coaching/athletes/{athlete.id}?d={wed}&v=mois")).text,
             (await client.get(f"/coaching/athletes/{athlete.id}/calendar?d={wed}", headers=HTMX)).text,
             (await client.get("/coaching/athletes")).text]
    for html in pages:
        main = html.split('id="main-content"')[-1].split("<footer")[0].split('<nav aria-label="Navigation mobile"')[0]
        for word in HEALTH_WORDS:
            assert word not in main, word
        assert str(index["value"]) + " %" not in main
    # the calendar feed: the session as it now is, nothing more
    feed = ics.render(await _rows(db_session, athlete), wed, host="paceforge.example",
                      guidance=lambda row: cv.guidance(row.kind, row.structure, None))
    event = feed.split(f"UID:coaching-{today.id}@")[1].split("END:VEVENT")[0]
    assert f"SUMMARY:Footing {today.minutes} min" in event
    for word in (*HEALTH_WORDS, "adaptée", "basse"):
        assert word not in feed, word


async def test_no_index_this_morning_changes_nothing_until_the_night_arrives(client, people, db_session, clock):
    athlete = people.athlete
    plan = await _wednesday(client, db_session, athlete, clock, low=True, on=False)
    morning = (HealthMetric.user_id == athlete.id, HealthMetric.date == clock.day)
    kept = [(m.metric, m.value, m.source, m.details) for m in (await db_session.execute(
        select(HealthMetric).where(*morning))).scalars()]
    await db_session.execute(delete(HealthMetric).where(*morning))  # the watch has not synced this morning's night
    await db_session.flush()
    await co.sync(db_session, plan, athlete)  # the option still off: Tuesday's session, not done, is marked missed
    before = _snap(await _rows(db_session, athlete))
    now = datetime.combine(clock.day, time(6, 30), timezone.utc)
    plan.form_on_at = now
    await co.sync(db_session, plan, athlete, now)
    assert _snap(await _rows(db_session, athlete)) == before, "no index this morning: nothing changes"
    assert plan.form_state == {"day": clock.day.isoformat(), "seen": False, "checked": now.isoformat(), "low": []}
    for metric, value, source, details in kept:  # the night arrives at 7:10
        db_session.add(HealthMetric(user_id=athlete.id, date=clock.day, metric=metric, value=value, source=source,
                                    details=details))
    await db_session.flush()
    await co.sync(db_session, plan, athlete, now + timedelta(minutes=cp.FORM_RETRY_MIN - 1))
    assert _snap(await _rows(db_session, athlete)) == before, "asked again at most every 10 minutes"
    await co.sync(db_session, plan, athlete, now + timedelta(minutes=40))
    today = next(r for r in await _rows(db_session, athlete) if r.day == clock.day)
    assert (today.kind, today.form) == (cp.FOOTING, cp.LIGHTENED), "read again once the night is in"
    assert plan.form_state["seen"] is True and plan.form_state["low"] == [clock.day.isoformat()]
    # the past is never rewritten: yesterday stays as it was seen, whatever the nights say later
    clock.day += timedelta(days=1)
    await _nights(db_session, athlete, clock.day, low=False)
    await co.sync(db_session, plan, athlete)
    yesterday = next(r for r in await _rows(db_session, athlete) if r.day == clock.day - timedelta(days=1))
    assert (yesterday.kind, yesterday.form) == (cp.FOOTING, cp.LIGHTENED)
    assert plan.form_state["low"] == [(clock.day - timedelta(days=1)).isoformat()]


async def test_a_good_index_never_adds_load(client, people, db_session, clock):
    athlete = people.athlete
    clock.day = cp.monday_of(clock.day)
    await _consent(db_session, athlete)
    await _plan(client, db_session, athlete, days=("1", "2", "4", "6"), long_day="6", base="240")
    before = _snap(await _rows(db_session, athlete))
    clock.day += timedelta(days=2)
    await _nights(db_session, athlete, clock.day, low=False)
    assert (await client.post("/coaching/form", data={"value": "on"})).status_code == 303
    plan = await co.get_plan(db_session, athlete.id)
    assert plan.form_state["seen"] is True and plan.form_state["low"] == []
    after = _snap(await _rows(db_session, athlete))
    assert [x for x in after if x[1] >= clock.day] == [x for x in before if x[1] >= clock.day], "exactly the plan"
    page = (await client.get("/coaching")).text
    assert "pf-coach-formline" not in page and "pf-coach-suggest" not in page


async def test_a_long_run_with_no_day_left_in_the_week_is_dropped(client, people, db_session, clock):
    athlete = people.athlete
    clock.day = cp.monday_of(clock.day)
    await _consent(db_session, athlete)
    await _plan(client, db_session, athlete, days=("1", "2", "4", "6"), long_day="6", base="240")
    clock.day += timedelta(days=6)  # Sunday: the long run's day, the last of the week
    await _nights(db_session, athlete, clock.day, low=True)
    await client.post("/coaching/form", data={"value": "on"})
    rows = await _rows(db_session, athlete)
    today = next(r for r in rows if r.day == clock.day)
    assert (today.kind, today.form, today.minutes) == (cp.FOOTING, cp.LIGHTENED, cp.FORM_FOOTING_CAP)
    assert not [r for r in rows if r.form == cp.MOVED], "no day left this week: the long run is dropped"
    page = (await client.get("/coaching")).text
    assert f'<a href="/sante">{ATHLETE_LINE}</a></p>' in page and "passe à" not in page


async def test_a_pinned_or_coach_session_is_only_offered_to_be_lightened(client, people, db_session, clock):
    athlete, coach = people.athlete, people.coach
    plan = await _wednesday(client, db_session, athlete, clock, low=False, on=True)
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    today = next(r for r in await _rows(db_session, athlete) if r.day == clock.day)
    # the coach rewrites today's session: theirs now (pinned, origin « coach »)
    r = await _as(client, coach).post(f"/coaching/athletes/{athlete.id}/sessions/{today.id}/edit",
                                      data={"kind": "seance", "minutes": "60"}, headers=HTMX)
    assert r.status_code == 200
    await db_session.refresh(today)
    assert (today.kind, today.minutes, today.pinned, today.origin) == (cp.QUALITY, 60, True, "coach")
    # this morning's index turns out low (read once the night is in)
    await _nights(db_session, athlete, clock.day, low=True)
    plan.form_state = None
    await db_session.flush()
    page = (await _as(client, athlete).get("/coaching")).text
    await db_session.refresh(today)
    assert (today.kind, today.minutes, today.form) == (cp.QUALITY, 60, None), "never changed by itself"
    assert not [r for r in await _rows(db_session, athlete) if r.form]
    assert f'action="/coaching/sessions/{today.id}/lighten"' in page and "Alléger aujourd’hui</button>" in page
    assert '<p><a href="/sante">Récupération basse ce matin.</a></p>' in page
    theirs = (await _as(client, coach).get(f"/coaching/athletes/{athlete.id}")).text
    assert "Alléger" not in theirs and "lighten" not in theirs and "basse" not in theirs
    assert (await client.post(f"/coaching/athletes/{athlete.id}/sessions/{today.id}/lighten")).status_code == 404
    await db_session.refresh(today)
    assert today.kind == cp.QUALITY
    # one tap of the athlete's
    r = await _as(client, athlete).post(f"/coaching/sessions/{today.id}/lighten", headers=HTMX)
    assert r.status_code == 200 and "Alléger aujourd’hui" not in r.text and ATHLETE_LINE in r.text
    await db_session.refresh(today)
    assert (today.kind, today.minutes, today.form, today.pinned, today.origin) == (
        cp.FOOTING, cp.form_footing(60), cp.LIGHTENED, True, "athlete")
    assert COACH_LINE in (await _as(client, coach).get(f"/coaching/athletes/{athlete.id}")).text
    # on a morning that is not low there is nothing to lighten
    other = next(r for r in await _rows(db_session, athlete) if r.day > clock.day and r.kind in co.HARD_KINDS)
    r = await _as(client, athlete).post(f"/coaching/sessions/{other.id}/lighten", headers=HTMX)
    await db_session.refresh(other)
    assert r.status_code == 200 and other.kind in co.HARD_KINDS and not other.form


# ── three low mornings within five days ─────────────────────────────────────

async def test_three_low_mornings_in_five_days_lighten_the_next_week_once(client, people, db_session, clock):
    athlete, coach = people.athlete, people.coach
    plan = await _wednesday(client, db_session, athlete, clock, low=False, on=True)
    plan.start_monday -= timedelta(weeks=2)  # a plan in its third week
    await db_session.flush()
    await co.sync(db_session, plan, athlete)
    m0 = cp.monday_of(clock.day)
    nxt = m0 + timedelta(weeks=1)

    def week(rows):
        return [r for r in rows if nxt <= r.day < nxt + timedelta(days=7) and r.status != "cancelled"]

    plain = week(await _rows(db_session, athlete))
    was = sum(r.minutes for r in plain)
    assert [r for r in plain if r.kind in (cp.QUALITY, cp.HILLS)]
    plan.form_state = {**plan.form_state, "low": [(m0 + timedelta(days=d)).isoformat() for d in (0, 1)]}
    await db_session.flush()
    await co.sync(db_session, plan, athlete)
    assert not plan.adaptations, "two low mornings: nothing yet"
    # a third within five days (Monday, Tuesday, and this Wednesday read low)
    await _nights(db_session, athlete, clock.day, low=True)
    plan.form_state = {**plan.form_state, "seen": False, "checked": None}
    await db_session.flush()
    await co.sync(db_session, plan, athlete)
    assert plan.adaptations == {nxt.isoformat(): {"reason": "forme", "factor": cp.ADAPT_FACTOR}}
    light = week(await _rows(db_session, athlete))
    assert not [r for r in light if r.kind in (cp.QUALITY, cp.HILLS)], "its quality session became a footing"
    assert abs(sum(r.minutes for r in light) - was * cp.ADAPT_FACTOR) <= 5
    page = (await _as(client, athlete).get(f"/coaching/calendar?w={nxt}", headers=HTMX)).text
    assert "Semaine allégée : ta récupération est restée basse plusieurs matins." in page.replace(" : ", " : ")
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    await db_session.flush()
    theirs = (await _as(client, coach).get(f"/coaching/athletes/{athlete.id}/calendar?w={nxt}", headers=HTMX)).text
    assert '<p class="pf-coach-reason">Semaine allégée.</p>' in theirs and "récupération" not in theirs
    # the ressenti asks for a lighter week too: one reduction, never both (0.8, not 0.64)
    done = CoachingSession(plan_id=plan.id, user_id=athlete.id, day=m0, auto_day=m0, kind=cp.FOOTING, minutes=45,
                           structure=cp.default_structure(cp.FOOTING, 45), status="done", manual=True, done_minutes=45,
                           ressenti=5)
    db_session.add(done)
    await db_session.flush()
    await co.sync(db_session, plan, athlete)
    assert plan.adaptations == {nxt.isoformat(): {"reason": "ressenti", "factor": cp.ADAPT_FACTOR}}
    assert abs(sum(r.minutes for r in week(await _rows(db_session, athlete))) - was * cp.ADAPT_FACTOR) <= 5
    # the option turned off: the form's own lighter week goes with it
    done.ressenti = 3
    await db_session.flush()
    await co.sync(db_session, plan, athlete)
    assert plan.adaptations[nxt.isoformat()]["reason"] == "forme"
    assert (await _as(client, athlete).post("/coaching/form", data={"value": "off"})).status_code == 303
    await db_session.refresh(plan)
    assert not plan.adaptations
    assert sum(r.minutes for r in week(await _rows(db_session, athlete))) == was


async def test_in_a_taper_week_only_the_swap_to_a_footing(client, people, db_session, clock):
    """Affûtage: the reminder becomes a footing of the same duration, nothing moves, no volume is cut."""
    athlete = people.athlete
    clock.day = cp.monday_of(clock.day)
    await _consent(db_session, athlete)
    route = await _race(db_session, athlete, clock.day + timedelta(days=12), target=4 * 3600)  # next week's Saturday
    plan = await _plan(client, db_session, athlete, days=("1", "2", "4", "6"), long_day="6", base="240",
                       goal=str(route.id))
    rows = await _rows(db_session, athlete)
    hard = next(r for r in rows if r.kind in (cp.QUALITY, cp.HILLS) and r.day < clock.day + timedelta(days=7))
    total = sum(r.minutes for r in rows if r.day < clock.day + timedelta(days=7))
    assert cp.phase_of(clock.day, co.inputs(plan, co.goal_of(route))) == "affutage"
    clock.day = hard.day
    await _nights(db_session, athlete, clock.day, low=True)
    await client.post("/coaching/form", data={"value": "on"})
    rows = await _rows(db_session, athlete)
    today = next(r for r in rows if r.id == hard.id)
    assert (today.kind, today.minutes, today.form) == (cp.FOOTING, hard.minutes, cp.LIGHTENED)
    assert not [r for r in rows if r.form == cp.MOVED]
    assert sum(r.minutes for r in rows if r.day < cp.monday_of(clock.day) + timedelta(days=7)) == total


# ── clearing what was kept ──────────────────────────────────────────────────

@pytest.mark.parametrize("how", ["off", "withdraw", "stop"])
async def test_turning_it_off_withdrawing_the_consent_or_stopping_clears_the_form(how, client, people, db_session,
                                                                                 clock):
    athlete = people.athlete
    plan = await _wednesday(client, db_session, athlete, clock, low=True, on=True)
    rows = await _rows(db_session, athlete)
    assert plan.form_on_at and plan.form_state["low"] and {r.form for r in rows} >= {cp.LIGHTENED, cp.MOVED}
    plan.adaptations = {(cp.monday_of(clock.day) + timedelta(weeks=1)).isoformat(): {"reason": "forme", "factor": 0.8}}
    await db_session.flush()
    _as(client, athlete)
    uid = athlete.id
    if how == "off":
        assert (await client.post("/coaching/form", data={"value": "off"})).status_code == 303
    elif how == "withdraw":
        assert (await client.post("/settings/health-consent/withdraw")).status_code == 303
    else:
        assert (await client.post("/coaching/stop")).status_code == 303
        assert (await db_session.execute(select(CoachingPlan))).first() is None
        assert (await db_session.execute(select(CoachingSession))).first() is None
        return
    db_session.expire_all()
    athlete = await db_session.get(type(athlete), uid)
    plan = await co.get_plan(db_session, uid)
    assert plan.form_on_at is None and plan.form_state is None and not plan.adaptations
    assert not [r for r in await _rows(db_session, athlete) if r.form]
    if how == "withdraw":
        assert not health_consent.given(athlete)
        assert (await db_session.execute(select(HealthMetric).where(HealthMetric.user_id == uid))).first() is None
    # the plan is its own again at the next reading: today's quality session is back, the footing it displaced too
    await co.sync(db_session, plan, athlete)
    rows = await _rows(db_session, athlete)
    today = next(r for r in rows if r.day == clock.day)
    assert today.kind in (cp.QUALITY, cp.HILLS) and not [r for r in rows if r.form]
    page = (await _as(client, athlete).get("/coaching")).text
    assert "pf-coach-formline" not in page
    assert (SWITCH in page) is (how == "off")


async def test_a_consent_that_is_no_longer_the_current_one_stops_the_reading_and_clears(client, people, db_session,
                                                                                       clock, monkeypatch):
    """A new version of the consent text asks everyone again: until then nothing is read, and what was kept goes."""
    athlete = people.athlete
    plan = await _wednesday(client, db_session, athlete, clock, low=True, on=True)
    assert plan.form_state["low"]
    athlete.health_consent_version = "2020-01-01"
    await db_session.flush()

    async def spy(*a, **k):
        raise AssertionError("read without the consent")
    monkeypatch.setattr(sante, "morning_index", spy)
    state = await co.sync(db_session, plan, athlete)
    assert plan.form_on_at is None and plan.form_state is None and state.low == ()
    assert not [r for r in state.rows if r.form]


# ── export, deletion ────────────────────────────────────────────────────────

async def test_the_export_holds_the_form_for_its_owner_and_nothing_of_it_for_a_coach(client, people, db_session,
                                                                                    clock):
    athlete, coach = people.athlete, people.coach
    plan = await _wednesday(client, db_session, athlete, clock, low=True, on=True)
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    await db_session.flush()

    async def export(user) -> dict:
        r = await _as(client, user).get("/settings/export")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            return {name: z.read(name).decode() for name in z.namelist()}

    mine = await export(athlete)
    own = json.loads(mine["coaching.json"])["plan"]
    assert own["form_on_at"] and own["form_state"]["low"] == [clock.day.isoformat()]
    assert {s["form"] for s in json.loads(mine["coaching_seances.json"])} >= {cp.LIGHTENED, cp.MOVED}
    theirs = await export(coach)
    assert json.loads(theirs["coaching.json"])["athletes_suivis"] == [{"prenom": "Stella", "depuis": json.loads(
        theirs["coaching.json"])["athletes_suivis"][0]["depuis"]}]
    assert json.loads(theirs["coaching.json"])["plan"] is None and json.loads(theirs["coaching_seances.json"]) == []
    data = "".join(body for name, body in theirs.items() if name.endswith(".json"))
    for word in (cp.LIGHTENED, cp.MOVED, clock.day.isoformat() + '"]', '"low"'):
        assert word not in data, word
    assert plan.id


# ── the morning round ───────────────────────────────────────────────────────

async def test_the_morning_round_reads_only_the_plans_with_the_option_in_their_morning(client, people, db_session,
                                                                                      clock, monkeypatch):
    from app import features
    from app.tasks import coaching as task

    schedule = features.coaching_beat_schedule()
    assert schedule["coaching-morning"]["task"] == "paceforge.coaching_morning"
    assert schedule["coaching-daily"]["task"] == "paceforge.coaching_daily"
    athlete = people.athlete
    plan = await _wednesday(client, db_session, athlete, clock, low=True, on=False)
    await _plan(client, db_session, people.outsider)  # another plan, without the option: never read here
    night = datetime.combine(clock.day, time(3, 27), timezone.utc)
    assert await task.run_morning(db_session, night) == 0, "the option is off"
    plan.form_on_at = night
    await db_session.flush()
    assert await task.run_morning(db_session, night) == 0, "03:27 on the athlete's clock: not the morning yet"
    assert plan.form_state is None
    morning = datetime.combine(clock.day, time(7, 12), timezone.utc)
    assert await task.run_morning(db_session, morning) == 1
    today = next(r for r in await _rows(db_session, athlete) if r.day == clock.day)
    assert today.form == cp.LIGHTENED and plan.form_state["seen"] is True
    assert await task.run_morning(db_session, morning + timedelta(hours=1)) == 0, "this morning's index is read: done"
    assert await task.run_morning(db_session, datetime.combine(clock.day, time(13, 12), timezone.utc)) == 0
