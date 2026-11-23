"""Coaching: the plan as a calendar feed (RFC 5545), read by Apple Calendar, Google Calendar, Outlook…

One event per session, from 30 days back to the end of the plan: a timed one (DTSTART/DTEND in UTC, shown as busy)
when the session was given a time (« Mon agenda »), else an all-day one (DTSTART;VALUE=DATE, DTEND the day after,
shown as free). The UID does not change when a session gets or loses its time: the calendar updates it in place.
UID is the session's id (stable: a regeneration rewrites a row in place), SEQUENCE its count of changes, DTSTAMP and
LAST-MODIFIED its last change: an edit, a move or a new duration reaches the subscribed calendar at its next refresh.
A session removed (deleted, or cancelled by a coach) is simply no longer in the feed: a subscription drops it.
Lines are CRLF, folded at 75 octets (never inside a UTF-8 character); TEXT values escape « \\ ; , » and newlines.
"""
from datetime import date, datetime, time, timedelta, timezone

from app.services import coaching_plan as cp
from app.services.viz import dplus, hm

PAST_DAYS = 30
PRODID = "-//PaceForge//Coaching//FR"
NAME = "PaceForge · Coaching"
REFRESH = "PT6H"


def escape(text: str) -> str:
    """A TEXT value (RFC 5545 §3.3.11)."""
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def fold(line: str) -> str:
    """One content line folded at 75 octets: CRLF then a space, never inside a multi-byte character."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, chunk, size, limit = [], "", 0, 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            out.append(chunk)
            chunk, size, limit = "", 0, 74  # the continuation's leading space counts
        chunk += ch
        size += n
    out.append(chunk)
    return "\r\n ".join(out)


def stamp(moment: datetime | None) -> str:
    moment = moment or datetime(2026, 1, 1, tzinfo=timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def summary(row) -> str:
    """« Footing 45 min », « Sortie longue 1h50 »."""
    return f"{cp.title(row.kind, row.structure)} {hm(row.minutes)}"


def climb(row) -> str:
    """« Vise +600 m de dénivelé. » for a session that carries a climb target (a trail race's long run), else ''."""
    try:
        target = int((row.structure or {}).get("climb") or 0)
    except (TypeError, ValueError):
        target = 0
    return f"Vise {dplus(target)} de dénivelé." if target > 0 else ""


def description(row, guidance: str) -> str:
    parts = [p for p in (cp.describe(row.kind, row.structure), climb(row), guidance) if p]
    return "\n".join(parts)


def when(row, zone) -> list[str]:
    """The event's time: DTSTART/DTEND in UTC for a session with a time (`zone`: the athlete's clock, on which that
    time is written), else the whole day, shown as free."""
    slot = getattr(row, "slot_min", None)
    if slot is not None and zone is not None and row.kind != cp.RACE and 0 <= slot < 24 * 60:
        start = datetime.combine(row.day, time(slot // 60, slot % 60)).replace(tzinfo=zone).astimezone(timezone.utc)
        end = start + timedelta(minutes=int(row.minutes))
        return [f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}"]
    return [f"DTSTART;VALUE=DATE:{row.day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(row.day + timedelta(days=1)).strftime('%Y%m%d')}", "TRANSP:TRANSPARENT"]


def render(rows: list, today: date, *, host: str, guidance=lambda row: "", zone=None) -> str:
    """The feed's text. `rows`: the plan's sessions; `guidance(row)`: its line of advice; `zone`: the athlete's
    clock (None: every session is an all-day event)."""
    since = today - timedelta(days=PAST_DAYS)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{escape(NAME)}", f"NAME:{escape(NAME)}",
             f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}", f"X-PUBLISHED-TTL:{REFRESH}"]
    for row in sorted(rows, key=lambda r: (r.day, r.id or 0)):
        if row.status == "cancelled" or row.day < since:
            continue
        changed = stamp(row.updated_at)
        lines += [
            "BEGIN:VEVENT",
            f"UID:coaching-{row.id}@{host}",
            f"DTSTAMP:{changed}",
            f"LAST-MODIFIED:{changed}",
            f"SEQUENCE:{int(row.seq or 0)}",
            *when(row, zone),
            f"SUMMARY:{escape(summary(row))}",
        ]
        text = description(row, guidance(row))
        if text:
            lines.append(f"DESCRIPTION:{escape(text)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
