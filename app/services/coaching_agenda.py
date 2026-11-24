"""Coaching › « Mon agenda » (docs/coaching.md): where the athlete's busy time comes from — Google Agenda and Outlook
connected by OAuth (services/coaching_accounts), the iPhone's own calendar read on the phone (« iphone »), and the
private calendar addresses pasted before 24 Nov 2026 (« ical », still read: nobody can add one any more) —, the busy
time read from them, and the time each session is given. The reading itself is pure (coaching_ical), the placement
too (coaching_slots); the fetch is safe_fetch's, the only code that requests a user-typed address.

- an address pasted before is stored encrypted, shown masked; it is NEVER fetched in a web request:
  app.tasks.coaching reads it every 3 hours and on « Actualiser » (once a minute at most), each fetch counted per
  account (auth.QUOTAS); a connected account is read in the same round, under the same counts;
- the iPhone's calendar is never fetched by the server: the app reads it with EventKit and sends busy intervals
  alone, numbers (phone_intervals checks them), when the athlete connects it and when the Coaching page loads and
  the last reading is older than PHONE_STALE;
- what is kept of a calendar is its busy intervals for the next 21 days — a start, an end, away or not: never a
  title, a description, a place or an attendee (coaching_ical never reads them, the app never sends them);
- a coach is never shown any of it: only the time a session was given.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.coaching import CoachingBusy, CoachingCalendar, CoachingPlan, CoachingSession
from app.services import coaching_ical as ical
from app.services import coaching_plan as cp
from app.services import coaching_slots as cs
from app.services import safe_fetch

logger = logging.getLogger(__name__)

OK = "ok"
# why a calendar could not be read, as the page says it (never the response's own words)
STATUS = {
    "url": "Adresse non reconnue.",
    "host": "Cette adresse n’est pas acceptée.",
    "dns": "Adresse introuvable.",
    "redirect": "Trop de redirections.",
    "status": "Le calendrier a refusé la lecture : vérifie que l’adresse est toujours la bonne.",
    "too_big": "Calendrier trop volumineux (2 Mo au plus).",
    "timeout": "Le calendrier n’a pas répondu à temps.",
    "network": "Lecture impossible pour l’instant.",
    "format": "Cette adresse ne donne pas un calendrier iCal.",
    "own": "C’est le calendrier de PaceForge : rien à y lire.",
    "rate": "Trop de lectures pour l’instant : nouvel essai plus tard.",
    "reauth": "À reconnecter : l’accès a expiré ou a été retiré.",
    "off": "Connexion indisponible pour l’instant.",
    "phone": "À reconnecter : l’iPhone ne donne plus accès à son calendrier.",
}
ICAL = "ical"
IPHONE = "iphone"  # the iPhone's calendar, read on the phone (iCloud and every account the phone syncs)
PHONE_NAME = "Calendrier de l’iPhone"
PHONE_STALE = timedelta(hours=3)  # choix pratique: the app reads again once the last reading is older (the round's 3 h)
PHONE_MAX = ical.MAX_INTERVALS  # intervals in one reading, as an address's
PHONE_SKEW = timedelta(hours=1)  # choix pratique: a phone's clock may run a little off the server's
PENDING = "Lecture en cours…"


class Invalid(ValueError):
    """What the phone sent cannot be kept: `str(e)` is a code, never shown."""


def own_host() -> str:
    return (urlsplit(settings.BASE_URL).hostname or "paceforge.fr").lower()


def label(row: CoachingCalendar) -> str:
    """What the page calls a source: a connected account by its service's name, an address masked."""
    from app.services import coaching_accounts as accounts

    if row.provider == IPHONE:
        return PHONE_NAME
    return accounts.NAMES.get(row.provider) or mask(row.url)


def mask(url: str | None) -> str:
    """An address as the page shows it: its host and its last 4 characters, never the secret in between."""
    parts = urlsplit(url or "")
    tail = (parts.path + ("?" + parts.query if parts.query else ""))[-4:]
    return f"{parts.hostname or 'adresse'}/…{tail}" if parts.hostname else "adresse illisible"


def moments_of(plan: CoachingPlan) -> tuple[str, ...]:
    return cs.clean_moments((plan.moments or "").split(","))


def set_moments(plan: CoachingPlan, values) -> None:
    chosen = cs.clean_moments(values)
    plan.moments = None if chosen == cs.MOMENTS else ",".join(chosen)


async def calendars_of(db: AsyncSession, user_id: int) -> list[CoachingCalendar]:
    return list((await db.execute(select(CoachingCalendar).where(CoachingCalendar.user_id == user_id)
                                  .order_by(CoachingCalendar.id))).scalars().all())


async def connect_account(db: AsyncSession, user_id: int, provider: str, tokens: dict) -> CoachingCalendar:
    """A Google Agenda or Outlook account connected (one of each per athlete: connecting again renews its tokens),
    its tokens kept encrypted; read by a worker next."""
    from app.services import coaching_accounts as accounts

    row = next((c for c in await calendars_of(db, user_id) if c.provider == provider), None)
    if row is None:
        row = CoachingCalendar(user_id=user_id, provider=provider)
        db.add(row)
    accounts.keep_tokens(row, tokens)
    row.status = None
    await db.flush()
    return row


async def _let_go(row: CoachingCalendar) -> None:
    """A connected account's access withdrawn at the provider where it can be (best effort), its tokens forgotten."""
    from app.services import coaching_accounts as accounts

    if row.provider in accounts.PROVIDERS:
        await accounts.revoke(row)
        accounts.forget_tokens(row)


async def remove_calendar(db: AsyncSession, user_id: int, calendar_id: int) -> bool:
    row = await db.get(CoachingCalendar, calendar_id)
    if row is None or row.user_id != user_id:
        return False
    await _let_go(row)
    await db.execute(delete(CoachingBusy).where(CoachingBusy.calendar_id == row.id))
    await db.delete(row)
    await db.flush()
    return True


async def clear(db: AsyncSession, user_id: int) -> None:
    """« Arrêter le coaching »: the addresses, the connected accounts (revoked where it can be) and the busy time go."""
    for row in await calendars_of(db, user_id):
        await _let_go(row)
    await db.execute(delete(CoachingBusy).where(CoachingBusy.user_id == user_id))
    await db.execute(delete(CoachingCalendar).where(CoachingCalendar.user_id == user_id))


# ── reading the calendars (a Celery task: never a web request) ──────────────

async def read_calendar(db: AsyncSession, row: CoachingCalendar, zone: tzinfo, now: datetime) -> str:
    """One calendar fetched and read: its busy intervals replace the ones kept; its status says how it went. A
    failure keeps the intervals of the last good reading. The status is a code: nothing of the response is kept."""
    from app.services import coaching_accounts as accounts
    from app.services.auth import over_quota

    if row.provider == IPHONE:  # read on the phone (receive_phone): nothing for the server to fetch
        return row.status or OK
    row.checked_at = now
    try:
        if await over_quota("coaching_agenda_fetch", user=row.user_id):
            raise safe_fetch.Refused("rate")
        if row.provider != ICAL:  # a connected account: its busy intervals, nothing else (coaching_accounts)
            busy = await accounts.read(row, zone, now)
        else:
            body = await safe_fetch.fetch(row.url or "", own_host())
            busy = ical.parse(body.decode("utf-8", errors="replace"), zone, now)
    except accounts.Failed as e:
        row.status = e.code if e.code in STATUS else "network"
        return row.status
    except safe_fetch.Refused as e:
        row.status = e.code if e.code in STATUS else "network"
        return row.status
    except ical.Own:
        row.status = "own"
        await db.execute(delete(CoachingBusy).where(CoachingBusy.calendar_id == row.id))
        return row.status
    except ical.NotCalendar:
        row.status = "format"
        return row.status
    except Exception as e:  # the error's own words may quote the calendar: only its kind is logged
        logger.error("Coaching: calendar %d could not be read (%s)", row.id, type(e).__name__)
        row.status = "network"
        return row.status
    await db.execute(delete(CoachingBusy).where(CoachingBusy.calendar_id == row.id))
    db.add_all(CoachingBusy(user_id=row.user_id, calendar_id=row.id, start_at=b.start, end_at=b.end, away=b.away)
               for b in busy)
    row.status, row.fetched_at = OK, now
    return OK


async def read_all(db: AsyncSession, user_id: int, zone: tzinfo, now: datetime | None = None) -> dict[int, str]:
    """Every calendar of the athlete read; {calendar id: status}."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for row in await calendars_of(db, user_id):
        out[row.id] = await read_calendar(db, row, zone, now)
    await db.flush()
    return out


# ── the iPhone's calendar: busy intervals the app read on the phone ──────────

def phone_intervals(raw, zone: tzinfo, now: datetime, own: frozenset = frozenset()) -> list[ical.Busy]:
    """What the app sent (`raw`: a list of {"start": seconds, "end": seconds}, UTC epoch seconds) as busy intervals,
    or Invalid(code). Numbers only, nothing else in an interval; at most PHONE_MAX; each start before its end and
    within the horizon (now − PHONE_SKEW … now + 21 days + PHONE_SKEW), then cut to it. The rules of the other
    sources: an interval from one local midnight to another (the app sends an all-day event so) is « away » when it
    covers AWAY_DAYS days or more, ignored for a single day. `own`: (start, end) pairs of PaceForge's own timed
    sessions — an athlete subscribed to the plan's feed on the iPhone sees them there: never busy time."""
    from app.services import coaching_accounts as accounts

    if not isinstance(raw, list):
        raise Invalid("shape")
    if len(raw) > PHONE_MAX:
        raise Invalid("count")
    lo = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    hi = lo + timedelta(days=ical.HORIZON_DAYS)
    out = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"start", "end"}:
            raise Invalid("shape")
        a, b = item["start"], item["end"]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b)):
            raise Invalid("number")
        if not all(-1e11 < v < 1e11 for v in (a, b)):  # also NaN and infinities (no comparison holds for them)
            raise Invalid("number")
        start = datetime.fromtimestamp(a, timezone.utc)
        end = datetime.fromtimestamp(b, timezone.utc)
        if end <= start:
            raise Invalid("order")
        if start < lo - PHONE_SKEW or end > hi + PHONE_SKEW:
            raise Invalid("window")
        if (int(a), int(b)) in own:
            continue
        days = accounts._whole_days(start, end, zone)
        if days and days < ical.AWAY_DAYS:
            continue  # a single all-day event: a reminder, a birthday
        start, end = max(start, lo), min(end, hi)
        if end > start:
            out.append(ical.Busy(start, end, days >= ical.AWAY_DAYS))
    return ical.merge(out)[:PHONE_MAX]


def own_slots(rows: list[CoachingSession], zone: tzinfo) -> frozenset:
    """(start, end) in UTC epoch seconds of the plan's timed sessions, as the feed writes them (coaching_ics.when)."""
    out = set()
    for row in rows:
        if row.slot_min is None or row.status == "cancelled" or row.kind == cp.RACE or not 0 <= row.slot_min < 1440:
            continue
        start = datetime.combine(row.day, time(row.slot_min // 60, row.slot_min % 60), zone).astimezone(timezone.utc)
        out.add((int(start.timestamp()), int((start + timedelta(minutes=int(row.minutes))).timestamp())))
    return frozenset(out)


async def _phone_row(db: AsyncSession, user_id: int) -> CoachingCalendar:
    row = next((c for c in await calendars_of(db, user_id) if c.provider == IPHONE), None)
    if row is None:
        row = CoachingCalendar(user_id=user_id, provider=IPHONE)
        db.add(row)
        await db.flush()
    return row


async def receive_phone(db: AsyncSession, user_id: int, busy: list[ical.Busy], now: datetime) -> CoachingCalendar:
    """A reading of the iPhone's calendar (one source per athlete): its busy intervals replace the ones kept."""
    row = await _phone_row(db, user_id)
    await db.execute(delete(CoachingBusy).where(CoachingBusy.calendar_id == row.id))
    db.add_all(CoachingBusy(user_id=user_id, calendar_id=row.id, start_at=b.start, end_at=b.end, away=b.away)
               for b in busy)
    row.status, row.fetched_at, row.checked_at = OK, now, now
    await db.flush()
    return row


async def phone_refused(db: AsyncSession, user_id: int, now: datetime) -> CoachingCalendar:
    """The iPhone refused access to its calendar (at the prompt, or withdrawn since in its settings): « À
    reconnecter », with the way to the phone's settings. The intervals of the last reading stay until their horizon,
    as a connected account's (« Retirer » deletes them at once)."""
    row = await _phone_row(db, user_id)
    row.status, row.checked_at = "phone", now
    await db.flush()
    return row


def phone_stale(row: CoachingCalendar, now: datetime) -> bool:
    """Whether the app should read the iPhone's calendar again as the page loads: never read, refused (the athlete may
    have allowed it since in the phone's settings), or read more than PHONE_STALE ago."""
    return row.fetched_at is None or row.status != OK or now - _utc(row.fetched_at) >= PHONE_STALE


# ── the busy time, as the placement reads it ────────────────────────────────

def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def by_day(intervals: list[tuple[datetime, datetime, bool]], zone: tzinfo, today: date,
           days: int = cs.HORIZON_DAYS) -> tuple[dict[date, list[tuple[int, int]]], frozenset]:
    """({local day: [(start, end)] in minutes after midnight}, the « away » days) from UTC intervals: a timed one is
    cut at each local midnight; an away one covers every day whose local noon it holds."""
    busy: dict[date, list[tuple[int, int]]] = {}
    away = set()
    for k in range(days):
        d = today + timedelta(days=k)
        lo = datetime.combine(d, time(0), zone)
        hi = datetime.combine(d + timedelta(days=1), time(0), zone)
        noon = datetime.combine(d, time(12), zone)
        for start, end, is_away in intervals:
            start, end = _utc(start), _utc(end)
            if is_away:
                if start <= noon < end:
                    away.add(d)
            elif end > lo and start < hi:
                a = max(0, int((start - lo).total_seconds() // 60))
                b = min(24 * 60, int(-(-(end - lo).total_seconds() // 60)))
                if b > a:
                    busy.setdefault(d, []).append((a, b))
    return {d: sorted(v) for d, v in busy.items()}, frozenset(away)


async def agenda_of(db: AsyncSession, user_id: int, zone: tzinfo, today: date) -> tuple[cs.Agenda | None, bool]:
    """(the athlete's busy time, whether they have any source). None while there is nothing to place on: no
    source, or one not read yet (a time given on half the calendars would be a guess). An iPhone calendar the phone
    refused before it was ever read is no source yet: it waits for the athlete, the others place."""
    calendars = [c for c in await calendars_of(db, user_id)
                 if not (c.provider == IPHONE and c.fetched_at is None and c.status == "phone")]
    if not calendars:
        return None, False
    if any(c.fetched_at is None for c in calendars):
        return None, True
    rows = (await db.execute(select(CoachingBusy.start_at, CoachingBusy.end_at, CoachingBusy.away).where(
        CoachingBusy.user_id == user_id).order_by(CoachingBusy.start_at))).all()
    busy, away = by_day([(a, b, w) for a, b, w in rows], zone, today)
    # the busy time is known for 21 days from each reading: past the oldest one's, nothing is placed
    until = min(_utc(c.fetched_at).astimezone(zone).date() for c in calendars
                if c.fetched_at is not None) + timedelta(days=cs.HORIZON_DAYS)
    return cs.Agenda(busy, away, until), True


def usual_moment(runs: list, today: date) -> str | None:
    """The moment the athlete really trains most often: the starts of the last 8 weeks' runs, on their clock."""
    from app.services import sante_training as st

    since = today - timedelta(weeks=cs.USUAL_WEEKS)
    starts = []
    for run in runs:
        if since <= run.day <= today:
            local = st.local_start(run)
            starts.append(local.hour * 60 + local.minute)
    return cs.usual_moment(starts)


def _item(row: CoachingSession, today: date, movable) -> cs.Item:
    planned = row.status == "planned"
    return cs.Item(key=id(row), day=row.day, kind=row.kind, minutes=row.minutes, home=row.auto_day,
                   movable=bool(planned and row.auto_day is not None and not row.slot_chosen and movable(row)),
                   start=row.slot_min, chosen=bool(row.slot_chosen),
                   fixed=not planned or row.kind == cp.RACE)


def place(rows: list[CoachingSession], agenda: cs.Agenda, plan: CoachingPlan, today: date, now_min: int,
          usual: str | None, movable, touch) -> bool:
    """The sessions from today on given their time (coaching_slots.place) and, for a future day with no room, their
    day; `movable(row)`: whether the plan may rewrite the row (a generated session nobody touched); `touch(row)`:
    a change a calendar must see. True when a row changed."""
    # the fixed session (the club's) has its own hour: the agenda gives it none and never calls its day full
    live = [r for r in rows if r.status != "cancelled" and r.day >= today and not cp.is_fixed(r.structure)]
    items = {id(r): _item(r, today, movable) for r in live}
    placed = cs.place(list(items.values()), agenda, plan.weekdays, moments_of(plan), today, now_min, usual)
    changed = False
    for row in live:
        got = placed.get(id(row))
        if got is None:
            continue
        if (row.day, row.slot_min) != (got.day, got.start):
            row.day, row.slot_min = got.day, got.start
            touch(row)
            changed = True
        if row.slot_flag != got.flag:
            row.slot_flag, changed = got.flag, True
    return changed


def unplace(rows: list[CoachingSession], today: date, movable, touch) -> bool:
    """No address any more: the times go, and a session the calendar had sent to another day goes back to the
    plan's day when it is still free."""
    changed = False
    taken = {r.day for r in rows if r.status != "cancelled"}
    for row in rows:
        if row.day < today or row.status != "planned":
            continue
        if row.slot_min is not None or row.slot_flag or row.slot_chosen:
            had_time = row.slot_min is not None
            row.slot_min, row.slot_flag, row.slot_chosen, changed = None, None, False, True
            if had_time:
                touch(row)
        if (row.auto_day is not None and row.auto_day != row.day and row.auto_day >= today and movable(row)
                and row.auto_day not in taken):
            taken.discard(row.day)
            row.day = row.auto_day
            taken.add(row.day)
            touch(row)
            changed = True
    return changed


def next_slot(row: CoachingSession, agenda: cs.Agenda, plan: CoachingPlan, today: date, now_min: int,
              usual: str | None) -> int | None:
    """« Autre créneau »: the next free time of the session's day (coaching_slots.next_choice)."""
    return cs.next_choice(_item(row, today, lambda r: False), agenda, moments_of(plan), today, now_min, usual)
