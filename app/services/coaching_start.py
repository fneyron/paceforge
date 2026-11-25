"""Coaching: what the onboarding proposes before the athlete touches anything (docs/coaching.md › « Les réponses de
départ »). Pure functions: the days the athlete usually runs and the day of their usual longest run, read from their
recent runs; a default week without a history. Nothing here is a training rule: the plan is still made by
coaching_plan from the days the athlete confirms.
"""
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from app.services import coaching_plan as cp

HABIT_WEEKS = 8  # (H) the runs read: the 8 weeks before today
HABIT_MIN_RUNS = 4  # (H) fewer days of running than this is not a habit: the default week is proposed
HABIT_DAYS = (3, 5)  # (H) between 3 and 5 days are proposed (fewer when the athlete ran on fewer weekdays)
DEFAULT_DAYS = (1, 3, 6)  # (H) without a habit: Tuesday, Thursday, Sunday — a session each at 2 h a week
DEFAULT_LONG = 6  # … the long run on Sunday


@dataclass(frozen=True)
class Proposal:
    days: tuple[int, ...]  # weekdays, 0 = Monday
    long_day: int
    habit: bool  # read from the athlete's runs (False: the default week)


def _long_default(days: tuple[int, ...]) -> int:
    """Sunday when it is proposed, else the last day proposed (the weekend's when there is one)."""
    return 6 if 6 in days else max(days)


def habitual_days(runs, today: date) -> Proposal | None:
    """The weekdays an athlete usually runs, from `runs` (anything with a `.day` and its `.minutes`) of the
    HABIT_WEEKS weeks before `today`; None without a habit to read (fewer than HABIT_MIN_RUNS days of running, or
    one weekday only).

    How many: the athlete's usual number of running days in a week (the median of the weeks they ran), held to
    HABIT_DAYS. Which: the weekdays they ran most often (the most minutes on a tie, then the earliest). The long
    run: the weekday that most often held the week's longest run, when it is among them; else Sunday, else the last
    day proposed."""
    since = today - timedelta(weeks=HABIT_WEEKS)
    by_day: dict[date, float] = {}
    for run in runs:
        if since <= run.day < today and run.minutes > 0:
            by_day[run.day] = by_day.get(run.day, 0.0) + run.minutes
    if len(by_day) < HABIT_MIN_RUNS:
        return None
    count, minutes = [0] * 7, [0.0] * 7
    weeks: dict[date, list[date]] = {}
    for day, spent in by_day.items():
        count[day.weekday()] += 1
        minutes[day.weekday()] += spent
        weeks.setdefault(day - timedelta(days=day.weekday()), []).append(day)
    usual = statistics.median(len(days) for days in weeks.values())
    wanted = int(min(HABIT_DAYS[1], max(HABIT_DAYS[0], int(usual + 0.5))))
    ranked = sorted((wd for wd in range(7) if count[wd]), key=lambda wd: (-count[wd], -minutes[wd], wd))
    days = tuple(sorted(ranked[:wanted]))
    if len(days) < 2:
        return None
    # the weekday of each week's longest run; the one that comes back most often (the longest runs on a tie)
    held, spent_long = [0] * 7, [0.0] * 7
    for week in weeks.values():
        longest = max(week, key=lambda d: (by_day[d], d))
        held[longest.weekday()] += 1
        spent_long[longest.weekday()] += by_day[longest]
    best = max(range(7), key=lambda wd: (held[wd], spent_long[wd], wd))
    return Proposal(days, best if best in days else _long_default(days), True)


def proposal(runs, today: date) -> Proposal:
    """What the onboarding ticks by itself: the athlete's habit, else the default week."""
    return habitual_days(runs, today) or Proposal(DEFAULT_DAYS, DEFAULT_LONG, False)


# ── « Ma séance fixe »: its duration proposed from the history (docs/coaching.md › « La séance fixe ») ──
FIXED_WEEKS = 8  # (H) the runs read: the 8 complete weeks (Monday to Sunday) before this one, as HABIT_WEEKS
WEEKDAYS_PLURAL = ("lundis", "mardis", "mercredis", "jeudis", "vendredis", "samedis", "dimanches")


def usual_minutes(runs, today: date, weekday: int) -> int | None:
    """The median duration of the athlete's runs on `weekday` over the FIXED_WEEKS complete weeks before `today`'s
    (two runs the same day: one day, their minutes added), to 5 min and held to FIXED_RANGE; None without any."""
    m0 = today - timedelta(days=today.weekday())
    first = m0 - timedelta(weeks=FIXED_WEEKS)
    by_day: dict[date, float] = {}
    for run in runs:
        if first <= run.day < m0 and run.day.weekday() == weekday and run.minutes > 0:
            by_day[run.day] = by_day.get(run.day, 0.0) + run.minutes
    if not by_day:
        return None
    return int(cp.clamp(cp.round5(statistics.median(by_day.values())), *cp.FIXED_RANGE))


def fixed_proposals(runs, today: date) -> list[dict]:
    """For each weekday (0 = Monday), the duration the screen proposes for a fixed session on it, and why in a few
    words: « lu sur tes mercredis », else FIXED_DEFAULT (« sans sortie ce jour-là »). The weekday itself is never
    guessed: the athlete chooses it."""
    out, default = [], f"{cp.FIXED_DEFAULT // 60}h{cp.FIXED_DEFAULT % 60:02d}"
    for wd in range(7):
        read = usual_minutes(runs, today, wd)
        out.append({"minutes": read or cp.FIXED_DEFAULT,
                    "why": f"Lu sur tes {WEEKDAYS_PLURAL[wd]}." if read
                    else f"Sans sortie ce jour-là : {default} par défaut."})
    return out


# ── « Tous les mercredis ? »: a run declared « une séance dure » on a weekday the athlete runs most weeks ──
RECUR_FROM = 5  # choix pratique: a run on that weekday in 5 of the FIXED_WEEKS complete weeks before the run's week (most of them) — a habit worth asking about, never set by itself


def runs_that_weekday(runs, day: date) -> int:
    """How many of the FIXED_WEEKS complete weeks before `day`'s week hold a run on `day`'s weekday."""
    m0 = day - timedelta(days=day.weekday())
    first = m0 - timedelta(weeks=FIXED_WEEKS)
    return len({run.day for run in runs if first <= run.day < m0 and run.day.weekday() == day.weekday()
                and run.minutes > 0})


def weekly_habit(runs, day: date) -> bool:
    """Whether the athlete runs on `day`'s weekday in most recent weeks (RECUR_FROM of FIXED_WEEKS): « Tu cours tous
    les mercredis avec ton club ? » may be asked."""
    return runs_that_weekday(runs, day) >= RECUR_FROM
