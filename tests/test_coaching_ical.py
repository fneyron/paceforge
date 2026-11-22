"""services/coaching_ical: an iCalendar file read for the athlete's busy time, and nothing else (Coaching › « Mon
agenda », docs/coaching.md). Timed events in UTC, with a TZID (IANA or Windows) or floating; durations; repeated
events with their exceptions; what is ignored (free, cancelled, one-day all-day events, our own feed); several-day
all-day events as « away »."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services import coaching_ical as ical
from app.services import coaching_ics as ics

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 11, 23, 5, 0, tzinfo=timezone.utc)  # a Monday, 6:00 in Paris


def _cal(*events: str, prodid="-//Google Inc//Google Calendar 70.9054//EN") -> str:
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{prodid}", *events, "END:VCALENDAR", ""])


def _event(*lines: str, uid="e1") -> str:
    return "\r\n".join(["BEGIN:VEVENT", f"UID:{uid}", *lines, "END:VEVENT"])


def _read(*events: str, zone=PARIS, now=NOW, **kw):
    return [(b.start.astimezone(zone).strftime("%m-%d %H:%M"), b.end.astimezone(zone).strftime("%m-%d %H:%M"), b.away)
            for b in ical.parse(_cal(*events, **kw), zone, now)]


def test_a_timed_event_in_utc_with_a_zone_or_floating():
    assert _read(_event("DTSTART:20261124T130000Z", "DTEND:20261124T143000Z")) == [("11-24 14:00", "11-24 15:30", False)]
    assert _read(_event("DTSTART;TZID=Europe/Paris:20261124T090000", "DTEND;TZID=Europe/Paris:20261124T100000")) == [
        ("11-24 09:00", "11-24 10:00", False)]
    assert _read(_event("DTSTART;TZID=America/New_York:20261124T090000", "DTEND;TZID=America/New_York:20261124T100000")
                 ) == [("11-24 15:00", "11-24 16:00", False)]
    # Outlook names its zones as Windows does
    assert _read(_event('DTSTART;TZID="Romance Standard Time":20261124T090000',
                        'DTEND;TZID="Romance Standard Time":20261124T093000')) == [("11-24 09:00", "11-24 09:30", False)]
    assert _read(_event("DTSTART;TZID=GMT Standard Time:20261124T090000", "DURATION:PT1H")) == [
        ("11-24 10:00", "11-24 11:00", False)]
    # floating: the athlete's own clock; an unknown TZID too
    assert _read(_event("DTSTART:20261124T180000", "DTEND:20261124T190000")) == [("11-24 18:00", "11-24 19:00", False)]
    assert _read(_event("DTSTART;TZID=Mars/Olympus:20261124T180000", "DTEND;TZID=Mars/Olympus:20261124T190000")) == [
        ("11-24 18:00", "11-24 19:00", False)]
    tokyo = ZoneInfo("Asia/Tokyo")
    assert _read(_event("DTSTART:20261124T180000", "DTEND:20261124T190000"), zone=tokyo) == [
        ("11-24 18:00", "11-24 19:00", False)]


def test_a_duration_stands_for_the_end_and_an_event_without_length_is_nothing():
    assert _read(_event("DTSTART:20261124T130000Z", "DURATION:PT1H30M")) == [("11-24 14:00", "11-24 15:30", False)]
    assert _read(_event("DTSTART:20261124T130000Z", "DURATION:P1DT2H")) == [("11-24 14:00", "11-25 16:00", False)]
    assert _read(_event("DTSTART:20261124T130000Z")) == []
    assert _read(_event("DTSTART:20261124T130000Z", "DTEND:20261124T120000Z")) == []
    assert _read(_event("DTSTART:20261124T130000Z", "DURATION:nonsense")) == []
    assert _read(_event("DTSTART:garbage", "DTEND:20261124T120000Z")) == []


def test_what_is_free_or_cancelled_is_ignored():
    busy = _event("DTSTART:20261124T130000Z", "DTEND:20261124T140000Z")
    assert _read(busy, _event("DTSTART:20261125T130000Z", "DTEND:20261125T140000Z", "TRANSP:TRANSPARENT", uid="f"),
                 _event("DTSTART:20261126T130000Z", "DTEND:20261126T140000Z", "STATUS:CANCELLED", uid="c"),
                 _event("DTSTART:20261127T130000Z", "DTEND:20261127T140000Z", "TRANSP:OPAQUE", "STATUS:CONFIRMED",
                        uid="o")) == [("11-24 14:00", "11-24 15:00", False), ("11-27 14:00", "11-27 15:00", False)]


def test_a_one_day_all_day_event_is_ignored_and_a_longer_one_is_away():
    assert _read(_event("DTSTART;VALUE=DATE:20261126", "DTEND;VALUE=DATE:20261127"),  # a birthday
                 _event("DTSTART;VALUE=DATE:20261125", uid="r")) == []  # a reminder, no end
    assert _read(_event("DTSTART;VALUE=DATE:20261127", "DTEND;VALUE=DATE:20261130")) == [
        ("11-27 00:00", "11-30 00:00", True)], "three days away: the athlete's own midnights"
    assert _read(_event("DTSTART;VALUE=DATE:20261127", "DURATION:P2D")) == [("11-27 00:00", "11-29 00:00", True)]
    assert _read(_event("DTSTART;VALUE=DATE:20261127", "DTEND;VALUE=DATE:20261130", "TRANSP:TRANSPARENT")) == []
    assert ical.AWAY_DAYS == 2


def test_a_weekly_meeting_is_repeated_on_its_own_clock_with_its_exceptions():
    weekly = _event("DTSTART;TZID=Europe/Paris:20261005T090000", "DTEND;TZID=Europe/Paris:20261005T100000",
                    "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261202T080000Z",
                    "EXDATE;TZID=Europe/Paris:20261125T090000")
    assert _read(weekly) == [("11-23 09:00", "11-23 10:00", False), ("11-30 09:00", "11-30 10:00", False),
                             ("12-02 09:00", "12-02 10:00", False)], "until its last one, minus the excluded day"
    # a clock change in between (25 October): 9:00 stays 9:00
    assert _read(_event("DTSTART;TZID=Europe/Paris:20261005T090000", "DURATION:PT30M", "RRULE:FREQ=WEEKLY;COUNT=12")
                 )[:2] == [("11-23 09:00", "11-23 09:30", False), ("11-30 09:00", "11-30 09:30", False)]
    assert len(_read(_event("DTSTART:20261123T120000Z", "DURATION:PT1H", "RRULE:FREQ=DAILY;INTERVAL=2;COUNT=5"))) == 5
    assert len(_read(_event("DTSTART:20200101T120000Z", "DURATION:PT1H", "RRULE:FREQ=DAILY"))) == ical.HORIZON_DAYS
    assert _read(_event("DTSTART:20260115T170000Z", "DURATION:PT1H", "RRULE:FREQ=MONTHLY;BYMONTHDAY=1")) == [
        ("12-01 18:00", "12-01 19:00", False)]
    assert _read(_event("DTSTART;VALUE=DATE:20261102", "DTEND;VALUE=DATE:20261104", "RRULE:FREQ=WEEKLY;UNTIL=20261207")
                 ) == [("11-23 06:00", "11-25 00:00", True), ("11-30 00:00", "12-02 00:00", True),
                       ("12-07 00:00", "12-09 00:00", True)]


def test_an_override_replaces_its_occurrence_and_a_cancelled_one_frees_it():
    series = _event("DTSTART;TZID=Europe/Paris:20261102T090000", "DURATION:PT1H", "RRULE:FREQ=WEEKLY", uid="s")
    moved = _event("DTSTART;TZID=Europe/Paris:20261123T150000", "DURATION:PT1H",
                   "RECURRENCE-ID;TZID=Europe/Paris:20261123T090000", uid="s")
    gone = _event("DTSTART;TZID=Europe/Paris:20261130T090000", "DURATION:PT1H", "STATUS:CANCELLED",
                  "RECURRENCE-ID;TZID=Europe/Paris:20261130T090000", uid="s")
    assert _read(series, moved, gone) == [("11-23 15:00", "11-23 16:00", False), ("12-07 09:00", "12-07 10:00", False)]


@pytest.mark.parametrize("rule", ["FREQ=SECONDLY", "FREQ=MINUTELY;INTERVAL=1", "FREQ=HOURLY", "FREQ=FORTNIGHTLY",
                                  "nonsense", "FREQ=WEEKLY;BYDAY=XX", "FREQ=DAILY;UNTIL=whenever"])
def test_a_rule_this_reader_skips_leaves_the_event_itself(rule):
    """By the hour or finer (a flood), or unreadable: the event counts once, never thousands of times."""
    assert _read(_event("DTSTART:20261124T130000Z", "DURATION:PT1H", f"RRULE:{rule}")) == [
        ("11-24 14:00", "11-24 15:00", False)]


def test_only_the_next_21_days_merged_and_bounded():
    assert _read(_event("DTSTART:20261120T130000Z", "DTEND:20261120T140000Z"),  # before now
                 _event("DTSTART:20261215T130000Z", "DTEND:20261215T140000Z", uid="late")) == []  # after 21 days
    assert _read(_event("DTSTART:20261123T030000Z", "DTEND:20261123T070000Z")) == [("11-23 06:00", "11-23 08:00", False)]
    assert _read(_event("DTSTART:20261124T090000Z", "DTEND:20261124T100000Z"),
                 _event("DTSTART:20261124T093000Z", "DTEND:20261124T110000Z", uid="b"),
                 _event("DTSTART:20261124T110000Z", "DTEND:20261124T113000Z", uid="c")) == [
        ("11-24 10:00", "11-24 12:30", False)], "overlapping and touching meetings are one busy time"
    many = [_event(f"DTSTART:20261124T{h:02d}{m:02d}00Z", "DURATION:PT1M", uid=f"u{h}{m}") for h in range(24)
            for m in range(0, 60, 2)] * 3
    assert len(ical.parse(_cal(*many), PARIS, NOW)) <= ical.MAX_INTERVALS
    daily = _event("DTSTART:20200101T000000Z", "DURATION:PT1M", "RRULE:FREQ=DAILY;BYHOUR=0,1,2,3,4,5,6,7,8,9;BYMINUTE=0,10,20,30,40,50")
    assert len(ical.parse(_cal(daily), PARIS, NOW)) <= ical.MAX_OCCURRENCES


def test_folded_lines_alarms_and_other_components_do_not_confuse_the_reader():
    text = "\n".join([
        "﻿BEGIN:VCALENDAR", "PRODID:-//Apple Inc.//macOS 15//EN", "BEGIN:VTIMEZONE", "TZID:Europe/Paris",
        "BEGIN:STANDARD", "DTSTART:19961027T030000", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
        "END:VTIMEZONE", "BEGIN:VEVENT", "UID:x", "SUMMARY:Point très long, replié sur", "  deux lignes",
        "DTSTART;TZID=Europe/Par", " is:20261124T090000", "DTEND;TZID=Europe/Paris:20261124T100000", "BEGIN:VALARM",
        "TRIGGER:-PT15M", "DTSTART:20261125T090000Z", "END:VALARM", "END:VEVENT", "BEGIN:VTODO",
        "DTSTART:20261126T090000Z", "DUE:20261126T100000Z", "END:VTODO", "END:VCALENDAR"])
    assert [(b.start.astimezone(PARIS).strftime("%m-%d %H:%M"), b.away) for b in ical.parse(text, PARIS, NOW)] == [
        ("11-24 09:00", False)]


@pytest.mark.parametrize("text", ["", "<html><body>Sign in</body></html>", "BEGIN:VCARD\nEND:VCARD", "hello",
                                  '{"error": "not found"}', "\x00\x01\x02"])
def test_what_is_not_a_calendar_is_refused(text):
    with pytest.raises(ical.NotCalendar):
        ical.parse(text, PARIS, NOW)


def test_paceforges_own_feed_is_never_busy_time():
    """An athlete who subscribed to the Coaching feed in the very calendar they gave: no loop."""
    assert ics.PRODID == "-//PaceForge//Coaching//FR" and ical.OWN_PRODID in ics.PRODID
    row = type("Row", (), dict(id=12, day=NOW.date() + timedelta(days=1), kind="footing", minutes=45, structure=None,
                               status="planned", seq=0, updated_at=NOW, slot_min=735))
    feed = ics.render([row], NOW.date(), host="paceforge.example", zone=PARIS)
    assert "DTSTART:20261124T111500Z" in feed, "the feed's event is a timed one"
    with pytest.raises(ical.Own):
        ical.parse(feed, PARIS, NOW)
    # the same event copied into another calendar keeps our UID: skipped; the meeting beside it counts
    ours = _event("DTSTART:20261124T111500Z", "DTEND:20261124T120000Z", uid="coaching-12@paceforge.example")
    assert _read(ours, _event("DTSTART:20261124T140000Z", "DTEND:20261124T150000Z", uid="real")) == [
        ("11-24 15:00", "11-24 16:00", False)]
