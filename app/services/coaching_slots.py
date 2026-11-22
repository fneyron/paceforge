"""Coaching › « Mon agenda »: where a session goes in the day (docs/coaching.md). Pure functions: the week's
sessions, the athlete's busy time and preferences in, a time (or a day) for each session out. No database, no clock.

Every time is in minutes after midnight on the athlete's clock. All of it is (H), PaceForge's choices:
- the athlete runs in the morning, at midday or in the evening (WINDOWS), all three unless they say otherwise;
- a session needs its duration and MARGIN minutes more (to change, to shower) free of any busy time, inside one of
  those moments; a session longer than the moment itself starts at the moment's start and runs past its end;
- the moment is picked by a fixed order: the long run in the morning first; a quality session at the moment the
  athlete really trains most often (the last 8 weeks' runs, 3 at least), else in the evening; an easy session at
  the first moment that has room;
- a session keeps the time it was given while that time is still free (no flapping), and a time the athlete chose
  (« Autre créneau ») whatever the calendar says;
- a future day with no room (full, or an « away » day) sends its session to the nearest other available day of the
  same week that holds no session, has room and keeps « never two hard days in a row »; only a generated session
  nobody touched moves; none found: the day stays, flagged « Journée chargée », without a time;
- today's session is never sent to another day; it gets a time among those still ahead.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

MORNING, MIDDAY, EVENING = "matin", "midi", "soir"
WINDOWS = {MORNING: (6 * 60, 9 * 60), MIDDAY: (11 * 60 + 45, 14 * 60), EVENING: (17 * 60 + 30, 21 * 60)}  # (H)
MOMENTS = (MORNING, MIDDAY, EVENING)
MOMENT_WORDS = {MORNING: "Matin", MIDDAY: "Midi", EVENING: "Soir"}
MARGIN = 30  # (H) minutes after the session: to change, to shower
STEP = 15  # a time starts on a quarter of an hour
CYCLE = 30  # (H) « Autre créneau » offers a time every 30 minutes
USUAL_WEEKS = 8  # (H) the athlete's usual hour: the runs of the last 8 weeks…
USUAL_MIN_RUNS = 3  # (H) …3 of them at least
HORIZON_DAYS = 21  # the busy time known: the sessions further ahead get no time
FULL = "chargee"  # a session's flag: its day has no room and no other day would take it
LONG, HARD_KINDS = "sortie_longue", frozenset({"sortie_longue", "seance", "cotes", "course"})


@dataclass
class Item:
    """One session as the placement reads it."""
    key: int
    day: date
    kind: str
    minutes: int
    home: date | None = None  # the day the plan gave it (None: a session a person added)
    movable: bool = False  # generated, planned, nobody touched it: its day may change
    start: int | None = None  # the time it holds now
    chosen: bool = False  # the athlete chose that time (« Autre créneau »): kept as it is
    fixed: bool = False  # not to be placed at all (the race, a session done or missed): it only holds its day


@dataclass
class Placed:
    day: date
    start: int | None = None
    flag: str | None = None
    moved_from: date | None = None  # the plan's day, when the calendar sent the session elsewhere


@dataclass
class Agenda:
    """The athlete's busy time, by local day: [(start, end)] in minutes after midnight, and the « away » days."""
    busy: dict[date, list[tuple[int, int]]] = field(default_factory=dict)
    away: frozenset = frozenset()
    until: date | None = None  # the first day the busy time is no longer known


def clean_moments(values) -> tuple[str, ...]:
    """The moments chosen, in the day's order; none readable: all three."""
    chosen = tuple(m for m in MOMENTS if m in {str(v).strip().lower() for v in (values or ())})
    return chosen or MOMENTS


def usual_moment(start_minutes: list[int]) -> str | None:
    """The moment the athlete really trains most often, from their runs' start times (minutes after midnight):
    each run counts for the moment nearest its start; None under USUAL_MIN_RUNS runs."""
    if len(start_minutes) < USUAL_MIN_RUNS:
        return None

    def nearest(t: int) -> str:
        return min(MOMENTS, key=lambda m: (0 if WINDOWS[m][0] <= t <= WINDOWS[m][1] else
                                           min(abs(t - WINDOWS[m][0]), abs(t - WINDOWS[m][1])), MOMENTS.index(m)))
    counts = {m: 0 for m in MOMENTS}
    for t in start_minutes:
        counts[nearest(t)] += 1
    return max(MOMENTS, key=lambda m: (counts[m], -MOMENTS.index(m)))


def order(kind: str, moments: tuple[str, ...], usual: str | None = None) -> tuple[str, ...]:
    """The moments a session tries, in order (the module's rule), among those the athlete chose."""
    wanted: tuple[str, ...]
    if kind == LONG:
        wanted = (MORNING, MIDDAY, EVENING)
    elif kind in ("seance", "cotes"):
        first = usual if usual in MOMENTS else EVENING
        wanted = (first, *(m for m in (EVENING, MORNING, MIDDAY) if m != first))
    else:
        wanted = MOMENTS
    return tuple(m for m in wanted if m in moments)


def _up(t: int, step: int = STEP) -> int:
    return -(-t // step) * step


def is_free(start: int, minutes: int, busy: list[tuple[int, int]]) -> bool:
    """Whether the session and its margin, from `start`, touch no busy time."""
    end = start + minutes + MARGIN
    return all(b <= start or a >= end for a, b in busy)


def candidates(minutes: int, kind: str, busy: list[tuple[int, int]], moments: tuple[str, ...],
               usual: str | None = None, after: int = 0) -> list[int]:
    """The times a session may start at on one day, the best first: for each moment in the session's order, every
    CYCLE minutes from the first quarter of an hour that fits. `after`: nothing before that minute (today's clock)."""
    need, out = minutes + MARGIN, []
    for moment in order(kind, moments, usual):
        lo, hi = WINDOWS[moment]
        last = max(lo, hi - need)  # a session longer than the moment starts at its start
        found: list[int] = []
        t = _up(max(lo, after))
        while t <= last:
            if is_free(t, minutes, busy):
                if not found or t - found[-1] >= CYCLE:
                    found.append(t)
            t += STEP
        out += found
    return out


def _hard_clash(day: date, others: list[tuple[date, str]]) -> bool:
    return any(k in HARD_KINDS and abs((d - day).days) <= 1 for d, k in others)


def place(items: list[Item], agenda: Agenda, days: tuple[int, ...], moments: tuple[str, ...], today: date,
          now_min: int = 0, usual: str | None = None) -> dict[int, Placed]:
    """Each session's day and time. `items`: the live sessions from today on (those not to be placed, `fixed`, still
    hold their day); `days`: the athlete's available weekdays; `now_min`: today's clock."""
    out: dict[int, Placed] = {}
    where = {i.key: i.day for i in items}  # each session's day as the placement goes

    def known(d: date) -> bool:
        return agenda.until is None or d < agenda.until

    def room(i: Item, d: date) -> list[int]:
        if d in agenda.away or not known(d):
            return []
        return candidates(i.minutes, i.kind, agenda.busy.get(d, []), moments, usual, now_min if d == today else 0)

    # the hard sessions first (their days constrain the others), then by day
    for i in sorted((x for x in items if not x.fixed), key=lambda x: (x.kind not in HARD_KINDS, x.day, x.key)):
        if not known(i.day):
            out[i.key] = Placed(i.day)  # beyond the busy time known: no time, nothing said
            continue
        moved = i.home if i.movable and i.home is not None and i.home != i.day else None
        busy = agenda.busy.get(i.day, [])
        if i.chosen and i.start is not None:
            out[i.key] = Placed(i.day, i.start, moved_from=moved)  # the athlete's own choice stands
            continue
        if (i.start is not None and i.day not in agenda.away and is_free(i.start, i.minutes, busy)
                and any(WINDOWS[m][0] <= i.start <= WINDOWS[m][1] for m in moments)):
            out[i.key] = Placed(i.day, i.start, moved_from=moved)  # kept while it is still free, in a moment chosen
            continue
        others = [(where[x.key], x.kind) for x in items if x.key != i.key]
        taken = {d for d, _ in others}
        home = i.home if i.movable and i.home is not None else i.day
        # the plan's own day first (a session sent elsewhere comes back when its day has room again), then the
        # day it is on, then the nearest other available days of the week, the later on a tie
        tries = [home] if home >= today and (home == i.day or home not in taken) else []
        if i.day not in tries:
            tries.append(i.day)
        if i.movable and i.day > today:
            monday = home - timedelta(days=home.weekday())
            week = [monday + timedelta(days=k) for k in range(7)]
            tries += sorted((d for d in week if d > today and d not in tries and d.weekday() in days
                             and d not in taken), key=lambda d: (abs((d - home).days), -(d - home).days))
        placed = None
        for d in tries:
            if d != i.day and (i.kind in HARD_KINDS and _hard_clash(d, others)):
                continue
            if d != i.day and not i.movable:
                continue
            if d != i.day and d == today:
                continue
            slots = room(i, d)
            if slots:
                placed = Placed(d, slots[0], moved_from=home if d != home else None)
                break
        if placed is None:
            # no room anywhere: the day stays; flagged when the calendar is why (not when today's hours have passed)
            full = i.day in agenda.away or not candidates(i.minutes, i.kind, busy, moments, usual)
            placed = Placed(i.day, None, FULL if full else None, moved_from=moved)
        out[i.key] = placed
        where[i.key] = placed.day
    return out


def next_choice(item: Item, agenda: Agenda, moments: tuple[str, ...], today: date, now_min: int = 0,
                usual: str | None = None) -> int | None:
    """« Autre créneau »: the free time after the one the session holds, on its day, around to the first again;
    None when the day has no other."""
    if item.day in agenda.away:
        return None
    slots = candidates(item.minutes, item.kind, agenda.busy.get(item.day, []), moments, usual,
                       now_min if item.day == today else 0)
    if not slots or slots == [item.start]:
        return None
    if item.start in slots:
        return slots[(slots.index(item.start) + 1) % len(slots)]
    later = [t for t in slots if item.start is not None and t > item.start]
    return later[0] if later else slots[0]
