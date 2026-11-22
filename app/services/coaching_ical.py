"""Coaching › « Mon agenda »: an iCalendar file read for ONE thing — when the athlete is busy (docs/coaching.md).

Pure: text in, busy intervals out. Only DTSTART, DTEND, DURATION, RRULE, EXDATE, RECURRENCE-ID, TRANSP, STATUS and UID
are read; a title, a description, a place or an attendee is never looked at, let alone kept.

- a timed event is busy from its start to its end (a UTC time, a TZID — an IANA name, or one of Outlook's Windows
  names, WINDOWS_ZONES — or a floating time read on the athlete's clock);
- TRANSP:TRANSPARENT (shown as free) and STATUS:CANCELLED events are ignored;
- an all-day event is ignored (a birthday, a reminder), unless it lasts AWAY_DAYS days or more: the athlete is then
  « away » those days (H) and no session is placed on them;
- a repeated event (RRULE) is expanded with python-dateutil (already installed: celery needs it) for FREQ=DAILY,
  WEEKLY, MONTHLY and YEARLY, on its own wall clock (a weekly 9:00 stays at 9:00 across a clock change), minus its
  EXDATEs and the occurrences an override (RECURRENCE-ID) replaces; a rule by the hour, minute or second, and RDATE,
  are skipped;
- PaceForge's own feed is never busy time: a calendar whose PRODID is ours is refused whole (Own), an event whose
  UID is one of our sessions' is skipped (an athlete who subscribed to the Coaching feed in the calendar they gave).
"""
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

AWAY_DAYS = 2  # (H) an all-day event of 2 days or more: away
HORIZON_DAYS = 21  # the busy time kept: the next 21 days
MAX_OCCURRENCES = 400  # per repeated event, within the window
MAX_INTERVALS = 1500  # kept per calendar, once merged
FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
OWN_PRODID = "//PaceForge//"
OWN_UID = re.compile(r"^coaching-\d+@", re.I)
# Outlook's published calendars name their zones as Windows does (CLDR's windowsZones, the common ones)
WINDOWS_ZONES = {
    "Romance Standard Time": "Europe/Paris", "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest", "Central European Standard Time": "Europe/Warsaw",
    "GMT Standard Time": "Europe/London", "Greenwich Standard Time": "Atlantic/Reykjavik", "UTC": "UTC",
    "E. Europe Standard Time": "Europe/Chisinau", "FLE Standard Time": "Europe/Kiev",
    "GTB Standard Time": "Europe/Bucharest", "Russian Standard Time": "Europe/Moscow",
    "Turkey Standard Time": "Europe/Istanbul", "Israel Standard Time": "Asia/Jerusalem",
    "Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver", "Pacific Standard Time": "America/Los_Angeles",
    "SA Pacific Standard Time": "America/Bogota", "E. South America Standard Time": "America/Sao_Paulo",
    "Morocco Standard Time": "Africa/Casablanca", "South Africa Standard Time": "Africa/Johannesburg",
    "Arabian Standard Time": "Asia/Dubai", "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai", "Singapore Standard Time": "Asia/Singapore",
    "Tokyo Standard Time": "Asia/Tokyo", "Korea Standard Time": "Asia/Seoul",
    "AUS Eastern Standard Time": "Australia/Sydney", "New Zealand Standard Time": "Pacific/Auckland",
}


class NotCalendar(ValueError):
    """The text is not an iCalendar file."""


class Own(ValueError):
    """The calendar is PaceForge's own Coaching feed: nothing to read in it."""


@dataclass(frozen=True)
class Busy:
    start: datetime  # UTC
    end: datetime  # UTC
    away: bool = False  # an all-day absence of several days: no session those days


@dataclass
class _Event:
    uid: str = ""
    start: datetime | None = None  # naive, on its own clock (`zone`); midnight for an all-day event
    end: datetime | None = None
    all_day: bool = False
    zone: tzinfo = timezone.utc
    end_zone: tzinfo | None = None
    duration: timedelta | None = None
    rrule: str | None = None
    exdates: tuple = ()
    recurrence: datetime | None = None  # RECURRENCE-ID, as a UTC instant (an all-day one: its midnight, « UTC »)
    skip: bool = False  # cancelled, shown as free, or ours


def unfold(text: str) -> list[str]:
    """The content lines: CRLF (or LF) ends a line, a following space or tab continues it (RFC 5545 §3.1)."""
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    return out


def _split(line: str) -> tuple[str, dict[str, str], str]:
    """« NAME;PARAM=value;PARAM="quoted:value":VALUE » → (NAME, {PARAM: value}, VALUE)."""
    quoted, cut = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            cut = i
            break
    if cut < 0:
        return line.upper(), {}, ""
    head, value = line[:cut], line[cut + 1:]
    name, *rest = head.split(";")
    params = {}
    for part in rest:
        key, _, val = part.partition("=")
        params[key.upper()] = val.strip('"')
    return name.upper().rsplit(".", 1)[-1], params, value.strip()


def zone_of(tzid: str | None, default: tzinfo) -> tzinfo:
    """A TZID's zone: an IANA name, one of Windows' common names, else the athlete's clock."""
    if not tzid:
        return default
    name = WINDOWS_ZONES.get(tzid, tzid).lstrip("/")
    for candidate in (name, name.split("/", 2)[-1] if name.count("/") >= 2 else name):
        try:
            return ZoneInfo(candidate)
        except (KeyError, ValueError, OSError):
            continue
    return default


def _moment(value: str, params: dict, default: tzinfo) -> tuple[datetime, bool, tzinfo] | None:
    """A DATE or DATE-TIME value → (naive time on its own clock, all-day, that clock)."""
    value = value.strip()
    try:
        if params.get("VALUE", "").upper() == "DATE" or (len(value) == 8 and value.isdigit()):
            return datetime.strptime(value[:8], "%Y%m%d"), True, default
        utc = value.endswith(("Z", "z"))
        naive = datetime.strptime(value.rstrip("Zz")[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    return naive, False, timezone.utc if utc else zone_of(params.get("TZID"), default)


_DURATION = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _duration(value: str) -> timedelta | None:
    m = _DURATION.match(value.strip().upper())
    if not m or value.strip().upper() in ("P", "PT"):
        return None
    sign, w, d, h, mi, s = m.groups()
    span = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -span if sign == "-" else span


def _aware(naive: datetime, zone: tzinfo) -> datetime:
    return naive.replace(tzinfo=zone).astimezone(timezone.utc)


def _events(lines: list[str], default: tzinfo) -> list[_Event]:
    events, current, depth = [], None, 0
    for line in lines:
        name, params, value = _split(line)
        if name == "BEGIN":
            if value.upper() == "VEVENT" and current is None:
                current, depth = _Event(), 0
            elif current is not None:
                depth += 1  # a VALARM inside the event: its lines are not the event's
            continue
        if name == "END":
            if current is not None and depth:
                depth -= 1
            elif current is not None and value.upper() == "VEVENT":
                events.append(current)
                current = None
            continue
        if current is None or depth:
            continue
        if name == "UID":
            current.uid = value
            if OWN_UID.match(value):
                current.skip = True
        elif name == "DTSTART":
            got = _moment(value, params, default)
            if got:
                current.start, current.all_day, current.zone = got
        elif name == "DTEND":
            got = _moment(value, params, default)
            if got:
                current.end, _all, current.end_zone = got
        elif name == "DURATION":
            current.duration = _duration(value)
        elif name == "RRULE":
            current.rrule = value
        elif name == "EXDATE":
            dates = [_moment(v, params, default) for v in value.split(",")]
            current.exdates += tuple(d for d in dates if d)
        elif name == "RECURRENCE-ID":
            got = _moment(value, params, default)
            if got:
                current.recurrence = _aware(got[0], timezone.utc if got[1] else got[2])
        elif name == "TRANSP" and value.upper() == "TRANSPARENT":
            current.skip = True
        elif name == "STATUS" and value.upper() == "CANCELLED":
            current.skip = True
    return events


def _rule(text: str, start: datetime, zone: tzinfo):
    """The RRULE on the event's own wall clock (dateutil), its UNTIL brought to that clock; None for a rule this
    reader skips (by the hour or finer, or one dateutil refuses)."""
    from dateutil.rrule import rrulestr

    parts = {}
    for part in text.strip().split(";"):
        key, _, val = part.partition("=")
        if key and val:
            parts[key.strip().upper()] = val.strip()
    if parts.get("FREQ", "").upper() not in FREQS:
        return None
    until = parts.get("UNTIL")
    if until:
        try:
            if len(until) == 8:
                local = datetime.combine(datetime.strptime(until, "%Y%m%d").date(), time(23, 59, 59))
            elif until.endswith(("Z", "z")):
                local = datetime.strptime(until.rstrip("Zz")[:15], "%Y%m%dT%H%M%S").replace(
                    tzinfo=timezone.utc).astimezone(zone).replace(tzinfo=None)
            else:
                local = datetime.strptime(until[:15], "%Y%m%dT%H%M%S")
        except ValueError:
            return None
        parts["UNTIL"] = local.strftime("%Y%m%dT%H%M%S")
    try:
        return rrulestr(";".join(f"{k}={v}" for k, v in parts.items()), dtstart=start)
    except (ValueError, TypeError):
        return None


def _starts(event: _Event, begin: datetime, lo: datetime, hi: datetime, span: timedelta,
            replaced: set[datetime]) -> list[datetime]:
    """The event's occurrences (naive starts on its own clock, the first one `begin`) that may touch the window
    [lo, hi] (UTC)."""
    zone: tzinfo = timezone.utc if event.all_day else event.zone
    first, last = (lo - span).astimezone(zone).replace(tzinfo=None) - timedelta(days=1), hi.astimezone(zone).replace(
        tzinfo=None) + timedelta(days=1)
    if not event.rrule:
        return [begin] if first <= begin <= last else []
    rule = _rule(event.rrule, begin, zone)
    if rule is None:
        return [begin] if first <= begin <= last else []
    excluded = set()
    for naive, all_day, ex_zone in event.exdates:
        if all_day:
            excluded.add(naive.date())
        else:
            excluded.add(naive.replace(tzinfo=ex_zone).astimezone(zone).replace(tzinfo=None))
    out = []
    for moment in rule.xafter(first, count=MAX_OCCURRENCES, inc=True):
        if moment > last:
            break
        if moment in excluded or moment.date() in excluded or _aware(moment, zone) in replaced:
            continue
        out.append(moment)
    return out


def parse(text: str, zone: tzinfo, now: datetime, days: int = HORIZON_DAYS) -> list[Busy]:
    """The busy intervals of the next `days` days, merged and sorted. `zone`: the athlete's clock (floating times,
    all-day absences). NotCalendar when the text is not an iCalendar file, Own when it is PaceForge's own feed."""
    lines = unfold(text.lstrip("﻿ \t\r\n"))
    if not lines or lines[0].strip().upper() != "BEGIN:VCALENDAR":
        raise NotCalendar("not a calendar")
    if any(line.upper().startswith("PRODID") and OWN_PRODID.lower() in line.lower() for line in lines[:40]):
        raise Own("our own feed")
    lo = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    hi = lo + timedelta(days=days)
    events = [e for e in _events(lines, zone) if e.start is not None]
    replaced: dict[str, set[datetime]] = {}
    for e in events:
        if e.recurrence is not None and e.uid:
            replaced.setdefault(e.uid, set()).add(e.recurrence)
    out: list[Busy] = []
    for e in events:
        begin = e.start
        if e.skip or begin is None:
            continue
        if e.all_day:
            span = (e.end - begin) if e.end and e.end > begin else (e.duration or timedelta(days=1))
            if span < timedelta(days=AWAY_DAYS):
                continue  # a one-day all-day event: a reminder, a birthday
        elif e.end is not None:
            span = _aware(e.end, e.end_zone or e.zone) - _aware(begin, e.zone)
        else:
            span = e.duration or timedelta(0)
        if span <= timedelta(0):
            continue
        skip = replaced.get(e.uid, set()) if e.recurrence is None else set()
        for start in _starts(e, begin, lo, hi, span, skip):
            if e.all_day:  # the athlete's own days, midnight to midnight on their clock
                a = _aware(start, zone)
                b = _aware(start + span, zone)
            else:
                a = _aware(start, e.zone)
                b = a + span
            if b > lo and a < hi:
                out.append(Busy(max(a, lo), min(b, hi), e.all_day))
    return merge(out)[:MAX_INTERVALS]


def merge(intervals: list[Busy]) -> list[Busy]:
    """Overlapping or touching intervals of one kind as one, by start."""
    out: list[Busy] = []
    for kind in (False, True):
        last: Busy | None = None
        for b in sorted((x for x in intervals if x.away is kind), key=lambda x: (x.start, x.end)):
            if last is not None and b.start <= last.end:
                last = Busy(last.start, max(last.end, b.end), kind)
                out[-1] = last
            else:
                last = b
                out.append(b)
    return sorted(out, key=lambda x: (x.start, x.end, x.away))
