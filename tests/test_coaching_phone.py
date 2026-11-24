"""Coaching › « Mon agenda » › « Calendrier de l’iPhone » (docs/coaching.md): the app reads the phone's calendars on
the phone and sends busy intervals alone — numbers, checked, bounded, within the horizon —, kept as the « iphone »
source and placed around as the other sources are; the server never fetches it; a refusal reads « À reconnecter »
with the way to the settings; « Retirer » deletes the intervals at once; the person who follows the plan sees none of
it. And « Dans mon calendrier »: three one-tap links, no address shown."""
import io
import json
import zipfile
from datetime import datetime, time, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select

from app.models.coaching import CoachingBusy, CoachingCalendar, CoachingLink
from app.services import coaching as co
from app.services import coaching_agenda as ag
from app.services import coaching_slots as cs
from app.tasks import coaching as task
from tests.test_coaching import _as, _plan, _rows, beta, people  # noqa: F401
from tests.test_coaching_agenda import GOOGLE, _address, net, queued  # noqa: F401
from tests.test_coaching_goal import clock  # noqa: F401

PHONE = "/coaching/agenda/iphone"


def _s(moment: datetime) -> int:
    return int(moment.timestamp())


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _source(db, athlete) -> CoachingCalendar | None:
    return next((c for c in await ag.calendars_of(db, athlete.id) if c.provider == ag.IPHONE), None)


async def _busy(db, row) -> list:
    return (await db.execute(select(CoachingBusy.start_at, CoachingBusy.end_at, CoachingBusy.away).where(
        CoachingBusy.calendar_id == row.id).order_by(CoachingBusy.start_at))).all()


def _fold(page: str) -> str:
    return page.split('id="coach-tool-agenda"')[1].split('id="coach-tool-share"')[0]


# ── the screen ──────────────────────────────────────────────────────────────

async def test_the_button_is_the_apps_and_elsewhere_one_quiet_line(client, people, db_session, clock):
    athlete = people.athlete
    await _plan(client, db_session, athlete)
    page = (await _as(client, athlete).get("/coaching?outil=agenda")).text
    fold = _fold(page)
    assert '<button type="button" class="pf-btn pf-btn-secondary pf-btn-sm pf-phone-calendar" id="coach-cal-connect-iphone" ' \
           'data-phone-calendar="connect">Calendrier de l’iPhone</button>' in fold
    assert 'data-phone-calendar-state="none"' in fold
    assert '<span class="pf-icloud-web">iCloud se relie depuis l’app iPhone.</span>' in fold
    assert "coaching-agenda.js" in page
    css = open("app/static/css/interface.css").read()
    assert "html:not(.pf-native-calendar) .pf-phone-calendar" in css, "hidden on the site and in an app before it"
    assert ".pf-native-calendar .pf-coach-icloud-note { display: none !important; }" in css
    js = open("app/static/js/coaching-agenda.js").read()
    assert "paceforgeNativeCalendar" in js and '"/coaching/agenda/iphone"' in js and "phone-calendar" in js
    nav = open("mobile/src/phone-calendar.ts").read()
    assert "window.paceforgeNativeCalendar = 1;" in nav and "classList.add('pf-native-calendar')" in nav


# ── a reading ───────────────────────────────────────────────────────────────

async def test_busy_intervals_are_kept_and_the_sessions_placed_around_them(client, people, db_session, clock, queued,
                                                                           net):
    athlete = people.athlete
    plan = await _plan(client, db_session, athlete, days=("0", "1", "2", "3", "4", "6"), base="300")
    now = _now()
    target = next(r for r in await _rows(db_session, athlete) if r.day >= clock.day + timedelta(days=2)
                  and r.kind != "course")
    # the morning and noon of that day busy: the session goes to the evening
    lo = datetime.combine(target.day, time(5), timezone.utc)
    hi = datetime.combine(target.day, time(17), timezone.utc)
    r = await _as(client, athlete).post(PHONE, json={"intervals": [{"start": _s(lo), "end": _s(hi)}]})
    assert r.status_code == 200 and r.json() == {"ok": True, "state": "read"}
    assert r.headers["cache-control"] == "no-store"
    row = await _source(db_session, athlete)
    assert row is not None and row.status == ag.OK and row.fetched_at is not None and row.url is None
    assert row.access_token is None and row.refresh_token is None, "nothing but intervals on the row"
    assert [(a.replace(tzinfo=timezone.utc) if a.tzinfo is None else a, w) for a, _b, w in await _busy(db_session, row)] \
        == [(lo, False)]
    assert queued == [], "nothing for a worker: the phone read it"
    placed = next(x for x in await _rows(db_session, athlete) if x.id == target.id)
    assert placed.slot_min is not None and placed.slot_min >= 17 * 60
    assert cs.is_free(placed.slot_min, placed.minutes, [(300, 1020)])
    page = (await client.get("/coaching?outil=agenda")).text
    fold = _fold(page)
    assert "<b>Calendrier de l’iPhone</b>" in fold and "Lu aujourd’hui à" in fold
    assert 'id="coach-cal-connect-iphone"' not in fold and "pf-coach-icloud-note" not in fold
    assert 'data-phone-calendar-state="fresh"' in fold
    # the worker's round never fetches it
    assert await task.read_agenda(db_session, athlete.id) == 1 and net.requests == []
    # older than PHONE_STALE: the page asks the app for a silent reading
    row.fetched_at = now - ag.PHONE_STALE - timedelta(minutes=1)
    await db_session.flush()
    assert 'data-phone-calendar-state="stale"' in (await client.get("/coaching")).text
    # read again: replaced, never piled up
    r = await client.post(PHONE, json={"intervals": []})
    assert r.status_code == 200 and await _busy(db_session, row) == []
    assert plan.id


async def test_an_all_day_absence_is_away_a_single_day_is_ignored_and_our_own_sessions_are_never_busy(
        client, people, db_session, clock):
    athlete = people.athlete
    await _plan(client, db_session, athlete)
    now = _now()
    day = now.date() + timedelta(days=3)
    mid = lambda k: datetime.combine(day + timedelta(days=k), time(0), timezone.utc)  # noqa: E731
    got = ag.phone_intervals([{"start": _s(mid(0)), "end": _s(mid(2))}, {"start": _s(mid(5)), "end": _s(mid(6))},
                              {"start": _s(mid(7) + timedelta(hours=9)), "end": _s(mid(7) + timedelta(hours=10))}],
                             timezone.utc, now)
    assert [(b.start, b.end, b.away) for b in got] == [
        (mid(0), mid(2), True), (mid(7) + timedelta(hours=9), mid(7) + timedelta(hours=10), False)]
    own = frozenset({(_s(mid(7) + timedelta(hours=9)), _s(mid(7) + timedelta(hours=10)))})
    assert ag.phone_intervals([{"start": _s(mid(7) + timedelta(hours=9)), "end": _s(mid(7) + timedelta(hours=10))}],
                              timezone.utc, now, own=own) == [], "the plan's own session, seen through the feed"
    # an interval begun just before now (the phone's clock): cut to now
    (b,) = ag.phone_intervals([{"start": _s(now - timedelta(minutes=30)), "end": _s(now + timedelta(hours=1))}],
                              timezone.utc, now)
    assert b.start == now.replace(microsecond=0) or abs((b.start - now).total_seconds()) < 1
    # the plan's timed sessions, as the feed writes them
    rows = await _rows(db_session, athlete)
    rows[1].slot_min = 18 * 60
    start = datetime.combine(rows[1].day, time(18), timezone.utc)
    assert (_s(start), _s(start + timedelta(minutes=rows[1].minutes))) in ag.own_slots(rows, timezone.utc)


@pytest.mark.parametrize("body,code", [
    ({"intervals": [{"start": "1", "end": 2}]}, "number"),
    ({"intervals": [{"start": True, "end": 2}]}, "number"),
    ({"intervals": [{"start": 1, "end": 2, "title": "Dr Martin"}]}, "shape"),
    ({"intervals": [[1, 2]]}, "shape"),
    ({"intervals": "x"}, "shape"),
    ({"intervals": [], "calendar": "Travail"}, "shape"),
    ({"refused": False}, "shape"),
    ([], "shape"),
    ({}, "shape"),
])
async def test_anything_but_numbers_is_refused(body, code, client, people, db_session, clock):
    athlete = people.athlete
    await _plan(client, db_session, athlete)
    r = await _as(client, athlete).post(PHONE, json=body)
    assert r.status_code == 422 and r.json() == {"error": code}
    assert await _source(db_session, athlete) is None and \
        (await db_session.execute(select(func.count()).select_from(CoachingBusy))).scalar() == 0


async def test_the_window_the_order_the_count_and_the_size_are_checked(client, people, db_session, clock,
                                                                         monkeypatch):
    from app.services import auth

    # seven refusals below are counted once their shape is read (the body too big and the text that is not JSON are
    # refused before); the eighth reading goes through, the ninth is over the hourly bound
    monkeypatch.setitem(auth.QUOTAS, "coaching_agenda_phone", (3600, {"user": 8}))
    athlete = people.athlete
    await _plan(client, db_session, athlete)
    _as(client, athlete)
    now = _now()
    hour = timedelta(hours=1)

    async def code(intervals) -> tuple[int, str | None]:
        r = await client.post(PHONE, json={"intervals": intervals})
        return r.status_code, r.json().get("error")
    assert await code([{"start": _s(now + 2 * hour), "end": _s(now + hour)}]) == (422, "order")
    assert await code([{"start": _s(now + hour), "end": _s(now + hour)}]) == (422, "order")
    assert await code([{"start": _s(now - 3 * hour), "end": _s(now + hour)}]) == (422, "window"), "the past"
    assert await code([{"start": _s(now + timedelta(days=22, hours=2)), "end": _s(now + timedelta(days=22, hours=3))}]) \
        == (422, "window"), "beyond the 21 days"
    assert await code([{"start": 1e300, "end": 1e301}]) == (422, "number")
    r = await client.post(PHONE, content=b'{"intervals": [{"start": NaN, "end": 1}]}',
                          headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"] == "number"
    many = [{"start": _s(now + hour + timedelta(minutes=2 * k)), "end": _s(now + hour + timedelta(minutes=2 * k + 1))}
            for k in range(ag.PHONE_MAX + 1)]
    assert await code(many) == (422, "count")
    r = await client.post(PHONE, content=b"{" + b" " * (200 * 1024) + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    r = await client.post(PHONE, content=b"not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert await _source(db_session, athlete) is None
    # a reading within bounds goes through; the hourly bound holds
    assert await code([{"start": _s(now + hour), "end": _s(now + 2 * hour)}]) == (200, None)
    assert await code([]) == (429, "rate")


# ── refused, removed ────────────────────────────────────────────────────────

async def test_a_refusal_reads_reconnect_with_the_settings_and_never_blocks_the_other_sources(
        client, people, db_session, clock, net):
    athlete = people.athlete
    plan = await _plan(client, db_session, athlete)
    _as(client, athlete)
    google = await _address(db_session, athlete)
    net.body = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    await task.read_agenda(db_session, athlete.id)
    r = await client.post(PHONE, json={"refused": True})
    assert r.status_code == 200 and r.json() == {"ok": True, "state": "refused"}
    row = await _source(db_session, athlete)
    assert row.status == "phone" and row.fetched_at is None
    fold = _fold((await client.get("/coaching?outil=agenda")).text)
    assert "À reconnecter : l’iPhone ne donne plus accès à son calendrier." in fold
    assert f'id="coach-cal-settings-{row.id}" data-phone-calendar="settings">Ouvrir les réglages</button>' in fold
    assert 'data-phone-calendar-state="refused"' in fold
    state = await co.sync(db_session, plan, athlete)
    assert state.agenda is not None, "refused before any reading: the address still places the sessions"
    # allowed again later: a reading clears it
    now = _now()
    r = await client.post(PHONE, json={"intervals": [{"start": _s(now + timedelta(hours=2)),
                                                       "end": _s(now + timedelta(hours=3))}]})
    assert r.status_code == 200 and (await _source(db_session, athlete)).status == ag.OK
    # withdrawn after a reading: the intervals of that reading stay (as a connected account's)
    await client.post(PHONE, json={"refused": True})
    assert len(await _busy(db_session, row)) == 1 and (await _source(db_session, athlete)).status == "phone"
    assert google.id


async def test_removing_deletes_the_intervals_at_once(client, people, db_session, clock):
    athlete = people.athlete
    await _plan(client, db_session, athlete)
    _as(client, athlete)
    now = _now()
    await client.post(PHONE, json={"intervals": [{"start": _s(now + timedelta(hours=k * 24 + 2)),
                                                   "end": _s(now + timedelta(hours=k * 24 + 3))} for k in range(5)]})
    row = await _source(db_session, athlete)
    assert len(await _busy(db_session, row)) == 5
    r = await client.post(f"/coaching/agenda/{row.id}/remove")
    assert r.status_code == 303
    assert await _source(db_session, athlete) is None
    assert (await db_session.execute(select(func.count()).select_from(CoachingBusy))).scalar() == 0
    assert all(x.slot_min is None for x in await _rows(db_session, athlete)), "no source: no time"


# ── the follower, the export ────────────────────────────────────────────────

async def test_the_follower_never_sees_the_busy_time_nor_the_source(client, people, db_session, clock):
    athlete, coach = people.athlete, people.coach
    await _plan(client, db_session, athlete)
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    await db_session.flush()
    now = _now()
    await _as(client, athlete).post(PHONE, json={"intervals": [{"start": _s(now + timedelta(hours=26)),
                                                                 "end": _s(now + timedelta(hours=30))}]})
    theirs = (await _as(client, coach).get(f"/coaching/athletes/{athlete.id}")).text
    for absent in ("iPhone", "data-phone-calendar", "coach-tool-agenda", "coaching-agenda.js", "iCloud"):
        assert absent not in theirs, absent
    assert (await client.post(PHONE, json={"intervals": []})).status_code == 404, "the follower has no plan here"
    assert (await client.post(f"/coaching/athletes/{athlete.id}/agenda/iphone", json={"intervals": []})).status_code \
        in (404, 405)

    async def export(user) -> dict:
        r = await _as(client, user).get("/settings/export")
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            return json.loads(z.read("coaching.json"))
    mine = await export(athlete)
    assert [c["service"] for c in mine["agenda"]] == ["Calendrier de l’iPhone"] and len(mine["heures_occupees"]) == 1
    assert (await export(coach))["agenda"] == []


# ── « Dans mon calendrier » ─────────────────────────────────────────────────

async def test_the_feed_is_added_in_one_tap_to_apple_google_and_outlook(client, people, db_session, clock,
                                                                         monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "BASE_URL", "https://paceforge.example")
    athlete = people.athlete
    plan = await _plan(client, db_session, athlete)
    page = (await _as(client, athlete).get("/coaching?outil=calendrier")).text
    feed = page.split('id="coach-tool-feed"')[1].split("</section>")[0]
    webcal = f"webcal://paceforge.example/coaching/cal/{plan.feed_token}.ics"
    https = f"https://paceforge.example/coaching/cal/{plan.feed_token}.ics"
    assert f'id="coach-feed-apple" href="{webcal}">Calendrier Apple</a>' in feed
    google = feed.split('id="coach-feed-google" href="')[1].split('"')[0].replace("&amp;", "&")
    outlook = feed.split('id="coach-feed-outlook" href="')[1].split('"')[0].replace("&amp;", "&")
    g, o = urlsplit(google), urlsplit(outlook)
    assert (g.scheme, g.netloc, g.path) == ("https", "calendar.google.com", "/calendar/render")
    assert parse_qs(g.query) == {"cid": [webcal]}
    assert (o.scheme, o.netloc, o.path) == ("https", "outlook.live.com", "/calendar/0/addfromweb/")
    assert parse_qs(o.query) == {"url": [webcal], "name": ["PaceForge · Coaching"]}
    assert ">Google Agenda</a>" in feed and ">Outlook</a>" in feed
    assert f'id="coach-feed-copy" class="pf-coach-textbtn" data-copy-value="{https}">Copier l’adresse</button>' in feed
    assert "<input" not in feed and f">{https}<" not in feed, "the address is never shown"
    assert 'data-feed-provider' in feed and 'target="_blank"' not in feed, "a new tab on the site only (the script)"
    js = open("app/static/js/coaching-agenda.js").read()
    assert 'link.target = "_blank"' in js and "!window.ReactNativeWebView" in js
    # the app hands Google and Outlook to the system's browser: they are not this site
    app = open("mobile/App.tsx").read()
    assert "if (request.url.startsWith('https://')) Linking.openURL(request.url)" in app
    assert GOOGLE
