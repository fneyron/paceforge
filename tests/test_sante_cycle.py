"""Santé's day is a cycle, like WHOOP's (« from sleep onset to sleep onset », never midnight to midnight): before
noon (H), while this morning's night has not reached the page and last night's has, the page is still yesterday's;
from noon, or as soon as the night is in, today's. Owner, 2026-10-09, awake at 1 a.m. on a plane: « J'ai sommeil
vide »."""
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services import sante
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
    date, last night's 8h36 (96 % of its 9-h need: 8 h and 1 h owed, « suffisant »), its score — never an empty
    Sommeil."""
    await seed_owner_v4(db_session, test_user)
    _at(monkeypatch, datetime(2026, 10, 9, 1, 9))
    page = await sante.health_page(db_session, test_user.id)
    assert page["today"] == D8 and page["day_label"] == "jeu. 8 oct."
    assert [(d["value"], d["sub"]) for d in page["dials"][:2]] == [("96", "suffisant"), ("65", "en cours")]
    assert page["sleep"]["hero"]["label"] == "Cette nuit"
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
    assert page["dials"][0]["sub"] == "pas enregistré"


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
