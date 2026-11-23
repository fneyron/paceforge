"""Coaching › « Mon agenda » (docs/coaching.md): private calendar addresses, kept encrypted and shown masked, read by
a worker and never by a web request; only busy intervals are kept (never a title, a description, a place or an
attendee); each session gets a free time, a full day sends its session elsewhere; the feed emits timed events under
the same UID; a coach reads the times, never the calendar; without an address nothing changes."""
import io
import json
import re
import zipfile
from datetime import date, datetime, time, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select, text

from app.database import Base
from app.models.coaching import CoachingBusy, CoachingCalendar, CoachingLink, CoachingPlan, CoachingSession
from app.services import coaching as co
from app.services import coaching_agenda as ag
from app.services import coaching_plan as cp
from app.services import coaching_slots as cs
from app.services import safe_fetch as sf
from app.tasks import coaching as task
from tests.test_coaching import HTMX, _as, _plan, _rows, _run, beta, people  # noqa: F401
from tests.test_coaching_goal import clock  # noqa: F401

GOOGLE = "https://calendar.google.com/calendar/ical/stella%40x.fr/private-0a1b2c3d4e5f/basic.ics"
ICLOUD = "webcal://p12-caldav.icloud.com/published/2/MTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDEy"
SECRETS = ("Entretien confidentiel", "Dr Martin", "paul.durand@exemple.fr", "Salle Opéra", "Déjeuner avec Léa",
           "private-0a1b2c3d4e5f")
NNBSP = " "


def _ics(monday: date, *extra: str) -> str:
    """A work calendar for the week of `monday`: meetings with titles, places and attendees; Tuesday is full."""
    def ev(uid, start, end, *more):
        return "\r\n".join(["BEGIN:VEVENT", f"UID:{uid}@google.com", f"DTSTART:{start}", f"DTEND:{end}", *more, "END:VEVENT"])

    def at(d, hh, mm=0):
        return (monday + timedelta(days=d)).strftime("%Y%m%d") + f"T{hh:02d}{mm:02d}00Z"
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Google Inc//Google Calendar 70.9054//EN",
        ev("a", at(1, 5), at(1, 22), "SUMMARY:Entretien confidentiel", "LOCATION:Salle Opéra",
           "ATTENDEE;CN=Paul Durand:mailto:paul.durand@exemple.fr", "DESCRIPTION:Dr Martin\\, 3e étage"),
        ev("b", at(2, 12), at(2, 13), "SUMMARY:Déjeuner avec Léa"),
        ev("c", at(3, 6), at(3, 8, 30), "SUMMARY:Entretien confidentiel"),
        ev("d", at(3, 17), at(3, 19), "SUMMARY:Dr Martin"),
        *extra, "END:VCALENDAR", ""])


@pytest.fixture
def net(monkeypatch):
    """The resolver and the network of services/safe_fetch, faked: `net.body` is what every calendar answers."""
    class Net:
        body: bytes = b""
        status = 200
        requests: list = []

    async def resolve(host):
        return ["93.184.216.34"]

    def handler(request):
        Net.requests.append(request)
        return httpx.Response(Net.status, content=Net.body)
    Net.requests = []
    monkeypatch.setattr(sf, "_resolve", resolve)
    monkeypatch.setattr(sf, "_transport", httpx.MockTransport(handler))
    return Net


@pytest.fixture
def queued(monkeypatch):
    jobs = []
    monkeypatch.setattr(task.coaching_agenda, "apply_async", lambda args, **k: jobs.append(args[0]))
    return jobs


async def _monday_plan(client, db, athlete, clock, **kw):
    """A plan made on a Monday at 05:00 UTC: Tuesday, Thursday, Friday, Sunday."""
    clock.day = cp.monday_of(clock.day)
    kw.setdefault("days", ("1", "3", "4", "6"))
    return await _plan(client, db, athlete, long_day="6", base="240", **kw)


def _dawn(clock) -> datetime:
    return datetime.combine(clock.day, time(5), timezone.utc)


async def _read(db, athlete, clock, net, body: str) -> int:
    net.body = body.encode()
    return await task.read_agenda(db, athlete.id, _dawn(clock))


async def _everything(db) -> str:
    """Every row of every table, as text: what « never stored » is checked against."""
    out = []
    for table in Base.metadata.sorted_tables:
        for row in (await db.execute(text(f'SELECT * FROM "{table.name}"'))).all():  # as stored: no decryption
            out.append(repr(tuple(row)))
    return "\n".join(out)


# ── the addresses ───────────────────────────────────────────────────────────

async def test_an_address_is_checked_kept_encrypted_shown_masked_and_never_fetched_by_the_page(
        client, people, db_session, clock, queued, monkeypatch):
    athlete = people.athlete
    _as(client, athlete)
    await _monday_plan(client, db_session, athlete, clock)

    async def never(*a, **k):
        raise AssertionError("a web request fetched a user-typed address")
    monkeypatch.setattr(sf, "fetch", never)
    monkeypatch.setattr(sf, "_resolve", never)
    page = (await client.get("/coaching")).text
    assert 'id="coach-tool-agenda"' in page and "Où trouver l’adresse" in page and "Adresse secrète au format iCal" in page
    for bad, said in (("http://calendar.google.com/x.ics", "https://"), ("https://127.0.0.1/x.ics", "pas acceptée"),
                      ("https://169.254.169.254/latest", "pas acceptée"), ("https://localhost/x.ics", "pas acceptée"),
                      ("javascript:alert(1)", "https://"), ("", "https://"),
                      ("https://test/coaching/cal/abc.ics", "pas acceptée")):
        r = await client.post("/coaching/agenda", data={"url": bad})
        assert r.status_code == 200 and said in r.text and 'class="pf-coach-panel is-asked" id="coach-tool-agenda"' in r.text, bad
        assert "pf-coach-error" in r.text.split('id="coach-tool-agenda"')[1]
    assert queued == [] and (await db_session.execute(select(func.count()).select_from(CoachingCalendar))).scalar() == 0
    r = await client.post("/coaching/agenda", data={"url": GOOGLE})
    assert r.status_code == 303 and r.headers["location"] == "/coaching?outil=agenda#coach-tool-agenda"
    assert queued == [athlete.id], "read by a worker, once saved"
    (row,) = (await db_session.execute(select(CoachingCalendar))).scalars().all()
    assert row.url == GOOGLE and row.status is None and row.fetched_at is None
    raw = (await db_session.execute(text("SELECT url FROM coaching_calendars"))).scalar()
    assert "google" not in raw and "private-0a1b2c3d4e5f" not in raw and raw != GOOGLE, "encrypted at rest"
    page = (await client.get("/coaching?outil=agenda")).text
    assert "<b>calendar.google.com/….ics</b>" in page and "Lecture en cours…" in page
    assert "private-0a1b2c3d4e5f" not in page and "stella%40x.fr" not in page, "never shown whole again"
    # the same again, a webcal:// address, a third, and no room for a fourth
    assert "déjà dans ton agenda" in (await client.post("/coaching/agenda", data={"url": GOOGLE})).text
    assert (await client.post("/coaching/agenda", data={"url": ICLOUD})).status_code == 303
    assert (await client.post("/coaching/agenda", data={"url": "https://outlook.office365.com/owa/calendar/a/b/calendar.ics"})).status_code == 303
    urls = [c.url for c in await ag.calendars_of(db_session, athlete.id)]
    assert urls[1] == "https://p12-caldav.icloud.com/published/2/MTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDEy"
    page = (await client.get("/coaching?outil=agenda")).text
    assert 'id="coach-cal-url"' not in page, "three addresses: no field for a fourth"
    r = await client.post("/coaching/agenda", data={"url": "https://example.org/four.ics"})
    assert r.status_code == 200 and "3 adresses au plus" in r.text and len(queued) == 3
    # removable; another account's is not
    other = CoachingCalendar(user_id=people.coach.id, url="https://example.org/theirs.ics")
    db_session.add(other)
    await db_session.flush()
    assert (await client.post(f"/coaching/agenda/{other.id}/remove")).status_code == 404
    assert (await client.post(f"/coaching/agenda/{row.id}/remove")).status_code == 303
    assert len(await ag.calendars_of(db_session, athlete.id)) == 2 and await db_session.get(CoachingCalendar, other.id)


async def test_refresh_is_queued_once_a_minute_and_the_round_reads_only_plans_with_an_address(
        client, people, db_session, clock, queued, net):
    from app import features
    from app.services.auth import QUOTAS

    assert features.coaching_beat_schedule()["coaching-agenda"]["task"] == "paceforge.coaching_agenda_round"
    assert QUOTAS["coaching_agenda"] == (60, {"user": 1}) and QUOTAS["coaching_agenda_fetch"][1] == {"user": 30}
    athlete = people.athlete
    _as(client, athlete)
    await _monday_plan(client, db_session, athlete, clock)
    await _plan(client, db_session, people.outsider)  # a plan without an address
    db_session.add(CoachingCalendar(user_id=people.coach.id, url="https://example.org/noplan.ics"))  # no plan
    assert (await _as(client, athlete).post("/coaching/agenda", data={"url": GOOGLE})).status_code == 303
    r = await client.post("/coaching/agenda/refresh")
    assert r.status_code == 303 and r.headers["location"].startswith("/coaching?outil=agenda-lu")
    assert "Lecture demandée" in (await client.get("/coaching?outil=agenda-lu")).text
    r = await client.post("/coaching/agenda/refresh")
    assert r.status_code == 200 and "Une actualisation par minute" in r.text
    assert queued == [athlete.id, athlete.id], "the second press within the minute queues nothing"
    net.body = _ics(clock.day).encode()
    assert await task.run_agenda(db_session, _dawn(clock)) == 1, "only the athlete with a plan and an address"
    assert [str(r.url) for r in net.requests] == [
        "https://93.184.216.34/calendar/ical/stella%40x.fr/private-0a1b2c3d4e5f/basic.ics"]
    assert net.requests[0].headers["host"] == "calendar.google.com"


# ── what is kept of a calendar ──────────────────────────────────────────────

async def test_only_busy_intervals_are_kept_never_a_title_a_place_or_an_attendee(client, people, db_session, clock,
                                                                                queued, net):
    athlete = people.athlete
    _as(client, athlete)
    await _monday_plan(client, db_session, athlete, clock)
    await client.post("/coaching/agenda", data={"url": GOOGLE})
    assert await _read(db_session, athlete, clock, net, _ics(clock.day)) == 1
    rows = (await db_session.execute(select(CoachingBusy).order_by(CoachingBusy.start_at))).scalars().all()
    assert len(rows) == 4 and {c.name for c in CoachingBusy.__table__.columns} == {
        "id", "user_id", "calendar_id", "start_at", "end_at", "away"}, "a start, an end, away or not: nothing else"
    assert {c.name for c in CoachingCalendar.__table__.columns} == {
        "id", "user_id", "url", "status", "fetched_at", "checked_at", "created_at",
        # a connected Google Agenda or Outlook (test_coaching_accounts): its service and its encrypted tokens only
        "provider", "access_token", "refresh_token", "token_expires_at"}
    dump = await _everything(db_session)
    for secret in SECRETS:
        assert secret not in dump, f"« {secret} » was stored"
    for html in ((await client.get("/coaching?outil=agenda")).text, (await client.get("/coaching?v=mois")).text):
        for secret in SECRETS:
            assert secret not in html
    (cal,) = await ag.calendars_of(db_session, athlete.id)
    assert cal.status == "ok" and cal.fetched_at is not None
    assert "Lu aujourd’hui à 5" in (await client.get("/coaching?outil=agenda")).text
    # read again: the intervals are replaced, not piled up
    assert await _read(db_session, athlete, clock, net, _ics(clock.day)) == 1
    assert (await db_session.execute(select(func.count()).select_from(CoachingBusy))).scalar() == 4


@pytest.mark.parametrize("status,body,code,said", [
    (404, "<html>SECRET-INTERNAL not found</html>", "status", "a refusé la lecture"),
    (200, "<html><title>SECRET-INTERNAL sign in</title></html>", "format", "ne donne pas un calendrier iCal"),
    (200, "BEGIN:VCALENDAR\r\nPRODID:-//PaceForge//Coaching//FR\r\nEND:VCALENDAR\r\n", "own", "calendrier de PaceForge"),
    (200, "x" * (2 * 1024 * 1024 + 1), "too_big", "trop volumineux"),
])
async def test_a_failed_reading_says_why_in_its_own_words_and_keeps_the_last_good_one(
        status, body, code, said, client, people, db_session, clock, queued, net):
    athlete = people.athlete
    _as(client, athlete)
    await _monday_plan(client, db_session, athlete, clock)
    await client.post("/coaching/agenda", data={"url": GOOGLE})
    assert await _read(db_session, athlete, clock, net, _ics(clock.day)) == 1
    net.status = status
    assert await _read(db_session, athlete, clock, net, body) == 0
    (cal,) = await ag.calendars_of(db_session, athlete.id)
    assert cal.status == code
    kept = (await db_session.execute(select(func.count()).select_from(CoachingBusy))).scalar()
    assert kept == (0 if code == "own" else 4), "the last good reading stands (our own feed is never busy time)"
    page = (await client.get("/coaching?outil=agenda")).text
    assert said in page and "SECRET-INTERNAL" not in page and "SECRET-INTERNAL" not in await _everything(db_session)


async def test_each_outbound_read_is_counted_per_account(client, people, db_session, clock, queued, net, monkeypatch):
    from app.services import auth

    monkeypatch.setitem(auth.QUOTAS, "coaching_agenda_fetch", (3600, {"user": 2}))
    athlete = people.athlete
    _as(client, athlete)
    await _monday_plan(client, db_session, athlete, clock)
    await client.post("/coaching/agenda", data={"url": GOOGLE})
    for _ in range(2):
        assert await _read(db_session, athlete, clock, net, _ics(clock.day)) == 1
    assert await _read(db_session, athlete, clock, net, _ics(clock.day)) == 0 and len(net.requests) == 2
    assert (await ag.calendars_of(db_session, athlete.id))[0].status == "rate"
    assert "Trop de lectures" in (await client.get("/coaching?outil=agenda")).text


# ── the times ───────────────────────────────────────────────────────────────

async def test_sessions_land_in_free_slots_and_a_full_day_sends_its_session_elsewhere(client, people, db_session,
                                                                                    clock, queued, net):
    athlete, coach = people.athlete, people.coach
    _as(client, athlete)
    plan = await _monday_plan(client, db_session, athlete, clock)
    mon = clock.day
    before = {r.id: (r.day, r.kind, r.minutes, r.seq) for r in await _rows(db_session, athlete)}
    tue = next(r for r in await _rows(db_session, athlete) if r.day == mon + timedelta(days=1))
    thu = next(r for r in await _rows(db_session, athlete) if r.day == mon + timedelta(days=3))
    assert all(r.slot_min is None and not r.slot_flag for r in await _rows(db_session, athlete)), "no address: no time"
    await client.post("/coaching/agenda", data={"url": GOOGLE})
    state = await co.sync(db_session, plan, athlete, _dawn(clock))
    assert state.agenda is None and all(r.slot_min is None for r in state.rows), "not read yet: nothing is guessed"
    assert await _read(db_session, athlete, clock, net, _ics(mon)) == 1
    rows = {r.id: r for r in await _rows(db_session, athlete)}
    # Tuesday is full (05:00 → 22:00 UTC) and every other available day (Thu, Fri, Sun) holds a session: nowhere
    # to go, it stays, flagged
    assert (rows[tue.id].day, rows[tue.id].slot_min, rows[tue.id].slot_flag) == (tue.day, None, cs.FULL)
    # Thursday: busy 06:00–08:30 and 17:00–19:00 → the quality session (evening first) at 19:00
    assert thu.kind in (cp.QUALITY, cp.HILLS) and rows[thu.id].slot_min == 19 * 60
    assert cs.is_free(19 * 60, thu.minutes, [(360, 510), (1020, 1140)])
    later = [r for r in rows.values() if r.day > mon + timedelta(days=3) and r.day < mon + timedelta(days=21)
             and r.kind != cp.RACE]
    assert later and all(r.slot_min is not None for r in later), "every session of the next 21 days has a time"
    assert all(r.slot_min is None for r in rows.values() if r.day >= mon + timedelta(days=21)), "none further ahead"
    assert all(rows[i].day == d for i, (d, *_x) in before.items()), "no day changed"
    page = (await client.get(f"/coaching?d={thu.day}")).text
    assert f'<p class="pf-coach-slot-time">19{NNBSP}h{NNBSP}00 – {cv_time(19 * 60 + thu.minutes)}</p>' in page
    assert ">Autre créneau</button>" in page
    flagged = (await client.get(f"/coaching?d={tue.day}")).text
    assert '<p class="pf-coach-full">Journée chargée</p>' in flagged and "pf-coach-slot-time" not in flagged
    # Friday becomes an available day without a session (its own was removed by the coach): Tuesday's session
    # moves there, with its reason on the week
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    await db_session.flush()
    fri = next(r for r in rows.values() if r.day == mon + timedelta(days=4))
    r = await _as(client, coach).post(f"/coaching/athletes/{athlete.id}/sessions/{fri.id}/delete", headers=HTMX)
    assert r.status_code == 200
    _as(client, athlete)
    state = await co.sync(db_session, plan, athlete, _dawn(clock))
    moved = next(r for r in state.rows if r.id == tue.id)
    assert (moved.day, moved.auto_day, moved.slot_flag, moved.pinned) == (
        mon + timedelta(days=4), mon + timedelta(days=1), None, False)
    assert moved.slot_min == 6 * 60, "Friday: nothing in the calendar, the first moment"
    page = (await client.get("/coaching")).text
    assert '<p class="pf-coach-reason pf-coach-moves">Mardi est plein : séance déplacée à vendredi.</p>' in page
    hard = sorted(r.day for r in state.rows if r.kind in cp.HARD)
    assert all((b - a).days >= 2 for a, b in zip(hard, hard[1:]))
    # stable: reading the plan or the same calendar again changes nothing (no flapping, no new SEQUENCE)
    snap = [(r.id, r.day, r.slot_min, r.slot_flag, r.seq) for r in state.rows]
    await client.get("/coaching")
    assert await _read(db_session, athlete, clock, net, _ics(mon)) == 1
    assert [(r.id, r.day, r.slot_min, r.slot_flag, r.seq) for r in await _rows(db_session, athlete)] == snap
    # the coach reads the times, never the calendar
    _as(client, coach)
    theirs = (await client.get(f"/coaching/athletes/{athlete.id}?d={thu.day}")).text
    assert f"19{NNBSP}h{NNBSP}00 – " in theirs and "Autre créneau" not in theirs and "coach-tool-agenda" not in theirs
    week = (await client.get(f"/coaching/athletes/{athlete.id}")).text
    assert "Séance déplacée de mardi à vendredi." in week and "est plein" not in week and "Journée chargée" not in week
    assert "calendar.google.com" not in week and "Mon agenda" not in week
    assert (await client.post(f"/coaching/athletes/{athlete.id}/sessions/{thu.id}/slot")).status_code == 404
    for path in ("/coaching/agenda", "/coaching/agenda/refresh", "/coaching/agenda/moments"):
        assert (await client.post(path, data={"url": GOOGLE})).status_code == 404, "the coach has no plan: no agenda"


def cv_time(minutes: int) -> str:
    from app.services import coaching_view as cv

    return cv.clock_time(minutes)


async def test_another_slot_cycles_and_then_holds_whatever_the_calendar_says(client, people, db_session, clock,
                                                                           queued, net):
    athlete = people.athlete
    _as(client, athlete)
    plan = await _monday_plan(client, db_session, athlete, clock)
    await client.post("/coaching/agenda", data={"url": GOOGLE})
    await _read(db_session, athlete, clock, net, _ics(clock.day))
    fri = next(r for r in await _rows(db_session, athlete) if r.day == clock.day + timedelta(days=4))
    first, seq = fri.slot_min, fri.seq
    assert first == 6 * 60 and not fri.slot_chosen
    r = await client.post(f"/coaching/sessions/{fri.id}/slot", headers=HTMX)
    await db_session.refresh(fri)
    assert r.status_code == 200 and fri.slot_min == first + cs.CYCLE and fri.slot_chosen and fri.seq == seq + 1
    assert not fri.pinned and fri.day == clock.day + timedelta(days=4)
    assert f"6{NNBSP}h{NNBSP}30 – " in r.text
    # a meeting lands on the chosen time: it stands (the athlete chose it), the day too
    chosen = fri.slot_min
    clash = "\r\n".join(["BEGIN:VEVENT", "UID:z@google.com",
                         f"DTSTART:{fri.day.strftime('%Y%m%d')}T000000Z", f"DTEND:{fri.day.strftime('%Y%m%d')}T235900Z",
                         "END:VEVENT"])
    await _read(db_session, athlete, clock, net, _ics(clock.day, clash))
    await db_session.refresh(fri)
    assert (fri.day, fri.slot_min, fri.slot_flag) == (clock.day + timedelta(days=4), chosen, None)
    # a session moved by hand keeps its day and only gets a time
    await _read(db_session, athlete, clock, net, _ics(clock.day))
    sun = next(r for r in await _rows(db_session, athlete) if r.day == clock.day + timedelta(days=6))
    r = await client.post(f"/coaching/sessions/{sun.id}/move", data={"to": (clock.day + timedelta(days=5)).isoformat()},
                          headers=HTMX)
    await db_session.refresh(sun)
    assert r.status_code == 200 and sun.pinned and sun.day == clock.day + timedelta(days=5) and sun.slot_min == 6 * 60
    full = clash.replace(fri.day.strftime("%Y%m%d"), sun.day.strftime("%Y%m%d"))
    await _read(db_session, athlete, clock, net, _ics(clock.day, full))
    await db_session.refresh(sun)
    assert (sun.day, sun.slot_min, sun.slot_flag) == (clock.day + timedelta(days=5), None, cs.FULL), "never moved again"
    assert plan.id


async def test_the_feed_emits_timed_events_under_the_same_uid(client, people, db_session, clock, queued, net,
                                                              monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "BASE_URL", "https://paceforge.example")
    athlete = people.athlete
    _as(client, athlete)
    plan = await _monday_plan(client, db_session, athlete, clock)
    feed = (await client.get(f"/coaching/cal/{plan.feed_token}.ics")).text
    assert "DTSTART:" not in feed and feed.count("DTSTART;VALUE=DATE:") == feed.count("BEGIN:VEVENT") > 4
    assert feed.count("TRANSP:TRANSPARENT") == feed.count("BEGIN:VEVENT"), "without an address: all-day, shown as free"
    uids = re.findall(r"UID:(\S+)", feed)
    await client.post("/coaching/agenda", data={"url": GOOGLE})
    await _read(db_session, athlete, clock, net, _ics(clock.day))
    feed = (await client.get(f"/coaching/cal/{plan.feed_token}.ics")).text
    assert re.findall(r"UID:(\S+)", feed) == uids, "the same events: a calendar updates them in place"
    thu = next(r for r in await _rows(db_session, athlete) if r.day == clock.day + timedelta(days=3))
    event = feed.split(f"UID:coaching-{thu.id}@paceforge.example")[1].split("END:VEVENT")[0]
    start = datetime.combine(thu.day, time(19), timezone.utc)
    assert f"DTSTART:{start:%Y%m%dT%H%M%SZ}\r\n" in event and "VALUE=DATE" not in event and "TRANSP" not in event
    assert f"DTEND:{start + timedelta(minutes=thu.minutes):%Y%m%dT%H%M%SZ}\r\n" in event
    assert "SEQUENCE:1\r\n" in event, "the change is announced"
    tue = next(r for r in await _rows(db_session, athlete) if r.day == clock.day + timedelta(days=1))
    flagged = feed.split(f"UID:coaching-{tue.id}@paceforge.example")[1].split("END:VEVENT")[0]
    assert "DTSTART;VALUE=DATE:" in flagged and "TRANSP:TRANSPARENT" in flagged, "no time: still an all-day event"
    for secret in (*SECRETS, "chargée", "plein", "google"):
        assert secret not in feed
    # the athlete's zone: the same wall time, another instant
    athlete.timezone = "Europe/Paris"
    await db_session.flush()
    feed = (await client.get(f"/coaching/cal/{plan.feed_token}.ics")).text
    event = feed.split(f"UID:coaching-{thu.id}@paceforge.example")[1].split("END:VEVENT")[0]
    assert f"DTSTART:{start - timedelta(hours=1):%Y%m%dT%H%M%SZ}\r\n" in event


async def test_the_moments_the_athlete_chose_hold_the_times(client, people, db_session, clock, queued, net):
    athlete = people.athlete
    _as(client, athlete)
    # the onboarding no longer asks (« Mon agenda » does); a screen opened before the change still posts them
    assert 'name="moments"' not in (await client.get("/coaching")).text
    clock.day = cp.monday_of(clock.day)
    r = await client.post("/coaching/plan", data={"goal": "none", "days": ["1", "3", "4", "6"], "long_day": "6",
                                                  "base": "240", "moments_asked": "1", "moments": ["midi", "soir"]})
    assert r.status_code == 303
    plan = await co.get_plan(db_session, athlete.id)
    assert plan.moments == "midi,soir" and ag.moments_of(plan) == ("midi", "soir")
    await client.post("/coaching/agenda", data={"url": GOOGLE})
    await _read(db_session, athlete, clock, net, "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    placed = [r for r in await _rows(db_session, athlete) if r.slot_min is not None]
    assert placed and all(r.slot_min >= cs.WINDOWS["midi"][0] for r in placed), "never in the morning"
    fold = (await client.get("/coaching?outil=agenda")).text.split('id="coach-tool-agenda"')[1]
    assert 'value="matin" onchange' in fold and 'value="midi" checked' in fold and 'value="soir" checked' in fold
    r = await client.post("/coaching/agenda/moments", data={"moments": ["matin"]})
    assert r.status_code == 303
    await db_session.refresh(plan)
    assert plan.moments == "matin"
    assert all(r.slot_min < cs.WINDOWS["matin"][1] for r in await _rows(db_session, athlete) if r.slot_min is not None)
    await client.post("/coaching/agenda/moments", data={})
    await db_session.refresh(plan)
    assert plan.moments is None and ag.moments_of(plan) == cs.MOMENTS, "none ticked: all three"
    # « Refaire le plan » without the question (an older form) leaves them as they are
    plan.moments = "soir"
    await db_session.flush()
    await _plan(client, db_session, athlete)
    await db_session.refresh(plan)
    assert plan.moments == "soir"


async def test_the_usual_hour_of_the_runs_places_the_quality_session(client, people, db_session, clock, queued, net):
    athlete = people.athlete
    _as(client, athlete)
    await _monday_plan(client, db_session, athlete, clock)
    for n in range(4):  # the last weeks' runs started at 12:10
        run = _run(athlete, clock.day - timedelta(days=3 + 7 * n), 45, n + 1)
        run.start_date = run.start_date.replace(hour=12, minute=10)
        db_session.add(run)
    await db_session.flush()
    await client.post("/coaching/agenda", data={"url": GOOGLE})
    await _read(db_session, athlete, clock, net, "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    rows = await _rows(db_session, athlete)
    quality = [r for r in rows if r.kind in (cp.QUALITY, cp.HILLS) and r.slot_min is not None]
    assert quality and all(r.slot_min == cs.WINDOWS["midi"][0] for r in quality), "where the athlete really trains"
    assert all(r.slot_min == 360 for r in rows if r.kind == cp.LONG and r.slot_min is not None), "the long run: morning"


# ── without an address; removing; export; deletion ──────────────────────────

async def test_removing_the_last_address_undoes_the_times_and_the_moves(client, people, db_session, clock, queued, net):
    athlete = people.athlete
    _as(client, athlete)
    # 1 h 40 a week over five available days: three sessions, two available days without one
    clock.day = cp.monday_of(clock.day)
    plan = await _plan(client, db_session, athlete, days=("1", "2", "3", "4", "6"), long_day="6", base="100")
    plain = [(r.id, r.day) for r in await _rows(db_session, athlete)]
    wed = clock.day + timedelta(days=2)
    assert [d for _i, d in plain if d == wed], "the quality session is on Wednesday"
    full = "\r\n".join(["BEGIN:VEVENT", "UID:w@google.com", f"DTSTART:{wed:%Y%m%d}T000000Z",
                        f"DTEND:{wed:%Y%m%d}T235900Z", "END:VEVENT"])
    await client.post("/coaching/agenda", data={"url": GOOGLE})
    await _read(db_session, athlete, clock, net, _ics(clock.day, full))
    rows = await _rows(db_session, athlete)
    assert [r for r in rows if r.auto_day and r.day != r.auto_day] and [r for r in rows if r.slot_min is not None]
    (cal,) = await ag.calendars_of(db_session, athlete.id)
    assert (await client.post(f"/coaching/agenda/{cal.id}/remove")).status_code == 303
    rows = await _rows(db_session, athlete)
    assert all(r.slot_min is None and not r.slot_flag and not r.slot_chosen for r in rows)
    assert [(r.id, r.day) for r in sorted(rows, key=lambda r: r.id)] == sorted(plain), "every session back on its day"
    assert (await db_session.execute(select(func.count()).select_from(CoachingBusy))).scalar() == 0
    page = (await client.get("/coaching")).text
    assert "pf-coach-slot" not in page and "pf-coach-moves" not in page
    assert plan.id


async def test_the_export_and_stopping_cover_the_agenda(client, people, db_session, clock, queued, net):
    athlete, coach = people.athlete, people.coach
    _as(client, athlete)
    await _monday_plan(client, db_session, athlete, clock)
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    await client.post("/coaching/agenda", data={"url": GOOGLE})
    await _read(db_session, athlete, clock, net, _ics(clock.day))

    async def export(user) -> dict:
        r = await _as(client, user).get("/settings/export")
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            return {name: z.read(name).decode() for name in z.namelist()}
    mine = await export(athlete)
    data = json.loads(mine["coaching.json"])
    assert [c["hote"] for c in data["agenda"]] == ["calendar.google.com"] and data["agenda"][0]["etat"] == "ok"
    assert len(data["heures_occupees"]) == 4 and set(data["heures_occupees"][0]) == {"debut", "fin", "absence"}
    assert "private-0a1b2c3d4e5f" not in "".join(mine.values()), "the address itself is a secret: never exported"
    assert any(s["slot_min"] is not None for s in json.loads(mine["coaching_seances.json"]))
    theirs = "".join(v for k, v in (await export(coach)).items() if k.endswith(".json"))
    assert json.loads((await export(coach))["coaching.json"])["agenda"] == [] and "calendar.google.com" not in theirs
    assert "Coaching (bêta) · ton agenda" in (await client.get("/privacy")).text
    # « Arrêter le coaching » removes the addresses and the busy time
    assert (await _as(client, athlete).post("/coaching/stop")).status_code == 303
    for model in (CoachingPlan, CoachingSession, CoachingCalendar, CoachingBusy):
        assert (await db_session.execute(select(func.count()).select_from(model))).scalar() == 0, model.__tablename__


def test_an_address_is_masked_to_its_host_and_last_characters():
    assert ag.mask(GOOGLE) == "calendar.google.com/….ics"
    assert ag.mask("https://outlook.office365.com/owa/calendar/abc/def/reachcalendar.ics?x=secret") == \
        "outlook.office365.com/…cret"
    assert ag.mask(None) == "adresse illisible"


def test_an_interval_is_cut_at_the_athletes_midnights_and_an_absence_covers_its_days():
    from zoneinfo import ZoneInfo

    paris = ZoneInfo("Europe/Paris")
    d = date(2026, 11, 23)
    at = lambda day, h, m=0: datetime(2026, 11, day, h, m, tzinfo=timezone.utc)  # noqa: E731
    busy, away = ag.by_day([(at(23, 8), at(23, 9, 30), False),  # 9:00–10:30 in Paris
                            (at(23, 22), at(24, 1), False),  # across midnight (23:00 → 02:00)
                            (at(26, 23), at(28, 23), True)],  # away Friday and Saturday (Paris midnights)
                           paris, d)
    assert busy == {d: [(540, 630), (1380, 1440)], d + timedelta(days=1): [(0, 120)]}
    assert away == {date(2026, 11, 27), date(2026, 11, 28)}
