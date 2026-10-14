"""Santé's day is a cycle, like WHOOP's (« from sleep onset to sleep onset », never midnight to midnight): before
noon (H), while this morning's night has not reached the page and last night's has, the page is still yesterday's;
from noon, or as soon as the night is in, today's. Owner, 2026-10-09, awake at 1 a.m. on a plane: « J'ai sommeil
vide »."""
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.services import sante
from app.services import sante_training as st
from app.services.sante import local_clock as _local_clock  # the real one (the suite moves sante.local_clock)
from tests.owner_v4 import D8, seed_owner_v4
from tests.test_nights import night_rows
from tests.test_sante import _seed_rows

D9 = date(2026, 10, 9)


def _at(monkeypatch, at: datetime | None, day: date = D9):
    """The athlete's calendar day and wall clock (None: no UTC offset known)."""
    async def today(*a, **k):
        return day

    async def clock(*a, **k):
        return at
    monkeypatch.setattr(sante, "athlete_today", today)
    monkeypatch.setattr(sante, "local_clock", clock)


async def test_awake_after_midnight_the_page_is_still_last_nights(db_session: AsyncSession, test_user: User,
                                                                  monkeypatch):
    """01:09 on the 9th, not slept yet (the owner's nights end on the morning of the 8th): the 8th's cycle — its
    date, last night's 8h36 (100 % of the 8-h need, « suffisant »), its state (none yet: his baselines are building)
    — never an empty Sommeil."""
    await seed_owner_v4(db_session, test_user)
    _at(monkeypatch, datetime(2026, 10, 9, 1, 9))
    page = await sante.health_page(db_session, test_user.id)
    assert page["today"] == D8 and page["day_label"] == "jeu. 8 oct."
    assert [(d["value"], d["sub"]) for d in page["dials"][:2]] == [("100", "suffisant"), ("—", "pas de score")]
    assert page["sleep"]["hero"]["label"] == "Nuit du mer. 7 au jeu. 8"
    # the night awaited is still the 9th's: the page keeps syncing sooner for it
    assert all(s["state"] != "received" for s in page["night_state"].values())


async def test_from_noon_a_night_that_never_came_reads_not_recorded(db_session: AsyncSession, test_user: User,
                                                                    monkeypatch):
    await seed_owner_v4(db_session, test_user)
    _at(monkeypatch, datetime(2026, 10, 9, 11, 59))
    assert (await sante.health_page(db_session, test_user.id))["today"] == D8
    _at(monkeypatch, datetime(2026, 10, 9, 12, 0))
    page = await sante.health_page(db_session, test_user.id)
    assert page["today"] == D9 and page["dials"][0]["value"] == "—"
    assert page["dials"][0]["sub"] == "pas de données"


async def test_this_mornings_night_turns_the_day(db_session: AsyncSession, test_user: User, monkeypatch):
    """07:30 on the 9th, the watch sent the night: the 9th, at once."""
    await seed_owner_v4(db_session, test_user)
    await _seed_rows(db_session, test_user, night_rows([0], today=D9, source="COROS",
                                                       hr_method="coros_sleep_summary"))
    _at(monkeypatch, datetime(2026, 10, 9, 7, 30))
    page = await sante.health_page(db_session, test_user.id)
    assert page["today"] == D9 and page["sleep"]["hero"]["label"] == "Cette nuit"
    assert page["dials"][0]["value"] != "—"


async def test_a_given_day_or_no_clock_stays_as_it_is(db_session: AsyncSession, test_user: User, monkeypatch):
    """A day given (the tests, the history) and an athlete without a known UTC offset: never moved."""
    await seed_owner_v4(db_session, test_user)
    _at(monkeypatch, datetime(2026, 10, 9, 1, 9))
    assert (await sante.health_page(db_session, test_user.id, today=D9))["today"] == D9
    _at(monkeypatch, None)
    assert (await sante.health_page(db_session, test_user.id))["today"] == D9


def test_cycle_day_needs_last_nights_sleep():
    """No night yesterday either (the watch left in a drawer): nothing to hold on to, the calendar day."""
    class N:
        def __init__(self, asleep=None, hr=None, hrv=None):
            self.asleep, self.hr, self.hrv = asleep, hr, hrv

    early = datetime(2026, 10, 9, 6, 0)
    assert sante.cycle_day({}, D9, early) == D9
    assert sante.cycle_day({D8: N(asleep=480)}, D9, early) == D8
    assert sante.cycle_day({D8: N(hrv=80.0)}, D9, early) == D8  # its heart values alone: a night too
    assert sante.cycle_day({D8: N(asleep=480), D9: N(asleep=300)}, D9, early) == D9
    assert sante.cycle_day({D8: N(asleep=480)}, D9, datetime(2026, 10, 9, 12, 0)) == D9
    assert sante.cycle_day({D8: N(asleep=480)}, D9, datetime(2026, 10, 10, 1, 0)) == D9  # a clock on another day


async def test_without_a_zone_the_day_is_the_latest_activity_s_clock(db_session: AsyncSession, test_user: User,
                                                                      monkeypatch):
    """users.timezone is NULL for every athlete who signed up before it (Strava's profile and the phone fill it in):
    until then their day, their cycle and an activity without its own offset read the UTC offset of their latest
    activity, as before; the zone once it is set (review, 2026-10-14: Europe/Paris in its place moved a Garmin
    athlete in Martinique, UTC−4, to tomorrow from 18:00 / 19:00 local and dropped the cycle's day). Neither: UTC
    and no wall clock (the calendar's day)."""
    monkeypatch.setattr(sante, "local_clock", _local_clock)
    d15 = date(2026, 10, 15)
    rows = night_rows(range(1, 30), today=d15, start=(22, 30), end=(6, 30))
    await _seed_rows(db_session, test_user, rows)
    db_session.add(Activity(user_id=test_user.id, garmin_activity_id=1, sport_type="Run", name="r",
                            start_date=datetime(2026, 10, 14, 21, tzinfo=timezone.utc), distance=8000,
                            moving_time=2700, elapsed_time=2700, total_elevation_gain=0, average_speed=3,
                            raw_data={"startTimeLocal": "2026-10-14 17:00:00",
                                      "startTimeGMT": "2026-10-14 21:00:00"}))
    db_session.add(Activity(user_id=test_user.id, strava_activity_id=2, sport_type="Run", name="no offset",
                            start_date=datetime(2026, 10, 13, 2, tzinfo=timezone.utc), distance=5000,
                            moving_time=1500, elapsed_time=1500, total_elevation_gain=0, average_speed=3.3,
                            raw_data={}))
    await db_session.flush()
    evening = datetime(2026, 10, 15, 1, 30, tzinfo=timezone.utc)  # 21:30 on the 14th there
    assert test_user.timezone is None
    assert await st.athlete_today(db_session, test_user.id, evening) == date(2026, 10, 14)
    assert await sante.local_clock(db_session, test_user.id, evening) == datetime(2026, 10, 14, 21, 30)
    page = await sante.health_page(db_session, test_user.id, now=evening)
    assert page["calendar_day"] == page["today"] == date(2026, 10, 14)
    morning = datetime(2026, 10, 15, 11, tzinfo=timezone.utc)  # 07:00 there, the night not in yet: the cycle
    page = await sante.health_page(db_session, test_user.id, now=morning)
    assert (page["calendar_day"], page["today"], page["cycle_pending"]) == (d15, date(2026, 10, 14), True)
    late = {s.name: s.day for s in await st.load_sessions(db_session, test_user.id, d15)}
    assert late["no offset"] == date(2026, 10, 12)  # 22:00 on the 12th at UTC−4 (02:00 on the 13th in UTC)
    test_user.timezone = "Europe/Paris"  # set: the zone, 03:30 on the 15th there
    await db_session.flush()
    assert await st.athlete_today(db_session, test_user.id, evening) == d15
    assert await sante.local_clock(db_session, test_user.id, evening) == datetime(2026, 10, 15, 3, 30)
    assert {s.name: s.day for s in await st.load_sessions(db_session, test_user.id, d15)}["no offset"] == \
        date(2026, 10, 13)
    other = User(firstname="Other")  # no zone, no activity: UTC, and no wall clock
    db_session.add(other)
    await db_session.flush()
    assert await st.athlete_today(db_session, other.id, evening) == d15
    assert await sante.local_clock(db_session, other.id, evening) is None
