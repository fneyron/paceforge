"""The training load: one pipeline for every view (owner, 2026-10-14: scientific basis — the load was computed four
ways, with different heart-rate bounds, decays and duplicate handling: the Entraînement dial, the Charge accumulée,
the recovery's cumulative cap and Activités' 7 / 28 days).

- L, a session's load (sante_training.loads): its minutes, lap by lap, weighted x·0.64·e^(1.92x), x its share of the
  heart-rate reserve (Banister 1991; Morton, Fitz-Clarke & Banister 1990); one set of bounds a day for every
  session of every view (rest: the median sleeping HR of 60 days, else 50; max: the 98th percentile of the
  sessions' peaks over 12 months, else 190); a session without a readable HR, and strength, at the athlete's median
  load per minute of its sport family (« estimée »).
- D, the daily load: Σ L of the deduplicated sessions on the local day each ended.
- The views: Entraînement % (week_ratio): Σ D of the 7 days ÷ the mean weekly Σ D of the 11 weeks before them (8
  active weeks at least; « comme d'habitude » within the range of those active weeks, which an ordinary week leaves
  2/(n_a + 1) of the time); Charge accumulée (history,
  card): A and C, exponential averages of τ = 7 and 42 days, each day x ← x + α·(D − x) with α = 1 − e^(−1/τ)
  (the decay of the fitness-fatigue model: Morton, Fitz-Clarke & Banister 1990; Busso 2003 — Williams 2017's
  EWMA uses λ = 2/(N + 1), slightly faster), both started at the mean D of their first 28 days (started at 0,
  « habituelle » read 37 % too low at day 42); Activités' 7 j / 28 j (totals): the same sessions, the same D.

Descriptive: the time constants are smoothing constants, not individual recovery times, injury cutoffs or
performance predictions (Impellizzeri 2020). No future session participates in a day's load. All received sports
count.
"""
import json
import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

from app.services import sante_training as st, viz

ACUTE_TAU, CHRONIC_TAU = 7, 42  # (H) conventional smoothing constants (Banister 1991), not recovery times
INIT_DAYS = 28  # (H) the averages start at the mean daily load of their first 28 days
SHOWN_DAYS = 90  # the card's curves
WARMUP_DAYS = 180  # read before them: e^(−180/42) ≈ 1 % of the start remains in the chronic average
READY_DAYS, READY_WEEKS = 28, 4  # (H) the usual curve once 28 days of history and 4 active weeks exist
USUAL_WEEKS, USUAL_MIN_WEEKS = 11, 4  # (H) the usual week: the 11 weeks before the 7 days, 4 active at least; its
# range, from the lightest to the heaviest active week, is « comme d'habitude » (H)
# the Entraînement % and its word from 8 active weeks only (H): a week like the n_a active ones before it falls outside
# their range 2/(n_a + 1) of the time, so the word « plus » or « moins » would come with 40 % of ordinary weeks at 4
# active weeks, 22 % at 8, 17 % at 11 (the second statistics review, 2026-10-14)
USUAL_LABEL_WEEKS = 8  # (H)


def valid(sessions) -> list:
    """The sessions once each (by id, the first kept: an import order or a repeated row never doubles a load),
    with a finite, positive moving time."""
    seen, out = set(), []
    for s in sessions:
        if s.id not in seen and isinstance(s.minutes, (int, float)) and math.isfinite(s.minutes) and s.minutes > 0:
            seen.add(s.id)
            out.append(s)
    return out


def bounds(nights, sessions, today: date) -> tuple[float, float]:
    """The day's heart-rate bounds, the same for every session of every view: (rest, max)."""
    from app.services import nights as nt

    return nt.rest_hr(nights, today), st.hr_max(sessions, today)


def daily(sessions, load: dict, lo: date, hi: date) -> dict[date, float]:
    """{day: D} from `lo` to `hi`: Σ L of the sessions that ended that local day (a day without one: 0)."""
    out: dict[date, float] = defaultdict(float)
    for s in valid(sessions):
        if lo <= s.end_day <= hi and s.id in load:
            out[s.end_day] += load[s.id][0]
    return out


def _loads(sessions, today: date, load: dict | None, rest: float, peak: float | None) -> dict:
    if load is not None:
        return load
    return st.loads(sessions, today, rest, peak if peak is not None else st.hr_max(sessions, today))


def history(sessions, today: date, load: dict | None = None, rest: float = 50.0, peak: float | None = None) -> list:
    """[{day, recent (A), usual (C, None until 28 days of history and 4 active weeks), daily, measured,
    estimated}] from the first session (at most 270 days back: 180 of warm-up before the 90 drawn) to `today`.
    `load`: sante_training.loads of the day (else computed with `rest` and `peak`)."""
    sessions = [s for s in valid(sessions) if s.end_day <= today]
    if not sessions:
        return []
    load = _loads(sessions, today, load, rest, peak)
    first = min(s.end_day for s in sessions)
    start = max(first, today - timedelta(days=SHOWN_DAYS + WARMUP_DAYS - 1))
    per = daily(sessions, load, start, today)
    by = defaultdict(list)
    for s in sessions:
        if start <= s.end_day <= today:
            by[s.end_day].append(s)
    init_days = min(INIT_DAYS, (today - start).days + 1)
    recent = usual = sum(per.get(start + timedelta(days=k), 0.0) for k in range(init_days)) / init_days
    weeks = set()
    out = []
    d = start
    while d <= today:
        value = per.get(d, 0.0)
        if by[d]:
            weeks.add(d - timedelta(days=d.weekday()))
        recent += (value - recent) * (1 - math.exp(-1 / ACUTE_TAU))
        usual += (value - usual) * (1 - math.exp(-1 / CHRONIC_TAU))
        ready = (d - first).days >= READY_DAYS - 1 and len(weeks) >= READY_WEEKS
        out.append(dict(day=d, recent=recent, usual=usual if ready else None, daily=value,
                        measured=sum(1 for s in by[d] if load[s.id][1]),
                        estimated=sum(1 for s in by[d] if not load[s.id][1])))
        d += timedelta(days=1)
    return out


def card(sessions, today: date, load: dict | None = None, rest: float = 50.0, peak: float | None = None):
    rows = history(sessions, today, load, rest, peak)[-SHOWN_DAYS:]
    if not rows:
        return None
    chart = viz.lines([r['day'] for r in rows], [
        {'name': 'Récente', 'values': [r['recent'] for r in rows]},
        {'name': 'Habituelle', 'values': [r['usual'] for r in rows], 'cls': 'is-soft'},
    ], title='Charge accumulée des activités reçues sur 90 jours')
    readouts = [[viz.num(r['recent'], unit='u.a.'), 'charge récente',
                 viz.d_long(r['day']) + (' · habituelle : '+viz.num(r['usual'], unit='u.a.') if r['usual'] is not None else ' · repères en cours')]
                for r in rows]
    chart['read'] = readouts[-1]
    data = json.loads(chart['data'])
    data['r'] = readouts
    data['a'] = [' · '.join(r) for r in readouts]
    chart['aria_now'] = data['a'][-1]
    chart['data'] = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    chart['date_now'] = viz.d_short(today)
    chart['ready'] = rows[-1]['usual'] is not None
    chart['estimated'] = sum(r['estimated'] for r in rows)
    chart['recorded'] = sum(r['measured']+r['estimated'] for r in rows)
    return viz.rest(chart, ["", "Récente et habituelle" if chart["ready"] else "Charge récente", ""], chart["summary"])


def usual_weeks(sessions, since: date, value=None) -> list[float] | None:
    """The 11 weekly sums of `value(s)` (default: the moving minutes) just before the 7 days (the 77 days before
    `since`, their first day; each session on the day it ended), the weeks since the first session only, the latest
    first; None with fewer than 4 of those weeks holding an activity. Never the 7 days themselves (an « uncoupled »
    ratio: Lolli 2019; Windt & Gabbett 2019; owner, 2026-10-09: his Transjeju counted in both)."""
    sessions = valid(sessions)
    if not sessions:
        return None
    value = value or (lambda s: s.minutes)
    first = min(s.end_day for s in sessions)
    held, per = defaultdict(float), defaultdict(float)
    for s in sessions:
        k = (since - s.end_day).days - 1  # 0: the day before the 7 days
        if 0 <= k < 7 * USUAL_WEEKS:
            held[k // 7] += s.minutes
            per[k // 7] += value(s)
    weeks = [k for k in range(USUAL_WEEKS) if since - timedelta(days=7 * k + 1) >= first]
    if sum(1 for k in weeks if held.get(k, 0.0) > 0) < USUAL_MIN_WEEKS:
        return None
    return [per.get(k, 0.0) for k in weeks]


def week_ratio(sessions, load: dict, until: date) -> dict | None:
    """The Entraînement %: Σ D of the 7 days ending `until` ÷ the mean weekly Σ D of the 11 weeks before them;
    « comme d'habitude » while it lies between the lightest and the heaviest of those weeks that hold an activity
    (owner, 2026-10-14: scientific basis — it was ± 20 %, then the 25th–75th percentiles, which call half of an
    athlete's ordinary weeks « plus » or « moins »: a week like the n_a active ones before it falls outside their
    range 2/(n_a + 1) of the time, 17 % at 11). A usual week that weighs nothing (sessions at the resting HR) reads
    the minutes instead (`by`). {load7, usual (the mean week), lo, hi (the range of the active weeks), ratio, side
    (more, same, less), weeks, by}; None without a usual week (4 active weeks) and under 8 active weeks
    (USUAL_LABEL_WEEKS: at 4 the word came with 40 % of ordinary weeks; the second statistics review, 2026-10-14):
    the dial then says the 7 days' time, as without a usual week."""
    since = until - timedelta(days=6)
    sessions = valid(sessions)
    days7 = [s for s in sessions if since <= s.end_day <= until]
    weekly = usual_weeks(sessions, since, lambda s: load[s.id][0] if s.id in load else 0.0)
    if weekly is None:
        return None
    by = "load"
    load7 = sum(load[s.id][0] for s in days7 if s.id in load)
    if sum(1 for w in weekly if w > 0) < USUAL_MIN_WEEKS:
        weekly, load7, by = usual_weeks(sessions, since), sum(s.minutes for s in days7), "minutes"
    usual = statistics.fmean(weekly)
    active = [w for w in weekly if w > 0]  # a week without an activity is no lower bound
    if len(active) < USUAL_LABEL_WEEKS:
        return None
    lo, hi = min(active), max(active)
    side = "more" if load7 > hi else "less" if load7 < lo else "same"
    return {"load7": load7, "usual": usual, "lo": lo, "hi": hi, "ratio": load7 / usual, "side": side,
            "weeks": weekly, "by": by}


def totals(sessions, load: dict, today: date, days: int) -> dict:
    """Activités' last `days` local days up to `today` (the sessions that ended in them): their count, hours,
    kilometres (`km` every sport's, as Activités says them; per sport; `run_km` the running's alone) and the summed
    daily load."""
    window = [s for s in valid(sessions) if today - timedelta(days=days - 1) <= s.end_day <= today]
    km = defaultdict(float)
    for s in window:
        km[s.sport] += s.km
    return {"count": len(window), "hours": sum(s.minutes for s in window) / 60, "km": sum(km.values()),
            "km_by_sport": dict(km), "run_km": sum(s.km for s in window if s.sport in st.RUN_TYPES),
            "load": sum(load[s.id][0] for s in window if s.id in load)}
