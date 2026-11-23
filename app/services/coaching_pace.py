"""Coaching › la cible: the athlete's critical speed, read from their own runs, and the paces it gives the plan's
sessions (docs/coaching.md › « L’intensité »). Pure functions that take plain lists, and one loader.

Critical speed (CS) is the boundary between the heavy and the severe intensity domains: below it a steady state can
be reached, above it it cannot (Jones & Vanhatalo 2017). It is computed as Smyth & Muniz-Pumares 2020 compute it
from raw training data: the fastest time recorded over target distances (400, 800, 1000, 1500, 3000 and 5000 m)
in the 16 weeks before, for runners who logged at least 24 runs in them; CS is the slope of the line distance =
D′ + CS × time (D′: the distance that can be run above CS), and the model with 400, 800 and 5000 m predicted best.
Where PaceForge departs from it (each marked (H) on its constant):
- the efforts are Strava's best efforts (400 m, 1/2 mile, 1 km, 1 mile, 2 miles, 5 km: their own distances are kept
  in the fit), else the fastest rolling efforts computed from a run's distance and time streams — the paper reads
  every 100 m of a grade-adjusted pace. No grade adjustment here: an effort run downhill (a net descent over it, when
  the altitude is known) is left out instead; uphill only slows an effort, which can only lower CS;
- without the three distances of the best model, all the target distances found, three at least, from 1000 m or
  less to 3000 m or more;
- efforts faster than the world record of their distance, a stream with a gap or a jump inside the effort, a fit
  whose D′ is not positive or beyond D_PRIME_MAX, a CS outside CS_RANGE: nothing (never a default pace).
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

# ── Smyth & Muniz-Pumares 2020 ───────────────────────────────────────────────
WINDOW_DAYS = 16 * 7  # the 16 weeks before (Smyth & Muniz-Pumares 2020)
MIN_RUNS = 24  # runs logged in that window, at least (their inclusion criterion)
TARGETS = (400, 800, 1000, 1500, 3000, 5000)  # metres (theirs)
BEST_MODEL = (400, 800, 5000)  # the model that predicted the marathon best (theirs)
# ── PaceForge's (H) ──────────────────────────────────────────────────────────
RUN_SPORTS = ("Run", "TrailRun")  # (H) choix pratique: a treadmill run's distance (VirtualRun) is the machine's or a pod's
BUCKET_TOLERANCE = 0.1  # (H) a Strava distance counts for the target within 10 % of it (805 m → 800, 1609 m → 1500)
SHORT_MAX, LONG_MIN = 1000, 3000  # (H) without the best model: the fit spans 1000 m or less to 3000 m or more
DOWNHILL = 0.01  # (H) an effort whose net descent is 1 % of its distance or more is left out (no grade adjustment)
MAX_GAP_S = 10  # (H) a stream's effort: no gap between two samples longer than this …
MAX_STEP_SPEED = 10.5  # (H) … and no step faster than this (m/s, beyond any sprinter's average: a GPS jump)
D_PRIME_MAX = 400.0  # (H) metres: D′ in Smyth & Muniz-Pumares 2020 was 136 ± 39 m; a fit beyond is not trusted
CS_RANGE = (1.5, 6.5)  # (H) m/s: 11:07 to 2:34 per km; outside, a fit of garbage
# choix pratique: an effort faster than the men's world record over its distance (seconds) is a GPS error
RECORD_S = {400: 43.0, 800: 100.9, 1000: 131.9, 1500: 206.0, 3000: 437.5, 5000: 755.3}

# ── the paces, as a share of CS (docs/coaching.md › « L’intensité ») ─────────
# easy and very easy: not faster than the first threshold, found at 82.3 % of CS on average (Hunter 2024,
# meta-analysis of 26 studies, 527 runners) — an upper bound only
EASY_CAP = 0.823
# threshold efforts (« soutenu », 8 min): just under CS — the maximal lactate steady state ran at 15.2 km/h against a
# CS of 16.4 (92.7 %), V̇O2 still steady 0.5 km/h under CS (97 %) (Nixon 2021, 10 well-trained runners); the bounds
# rounded to 93–97 % are (H)
THRESHOLD = (0.93, 0.97)
# « vite » efforts of 3 min or more: the speed of V̇O2max, held 404 ± 101 s (Billat 1994, 8 sub-elite runners) — on
# the athlete's own line, CS + D′ / t for t from 505 s (the slow end) to 303 s (the fast end). Intervals at that speed
# lasting 60 % of that time improved V̇O2max and 3000 m (Esfarjani 2007): a 3-min effort is 60 % of 5 min. Taking
# Billat's mean ± 1 SD for everyone is (H).
VO2MAX_TIME_S = (505, 303)


@dataclass(frozen=True)
class Effort:
    distance: float  # m
    seconds: float

    @property
    def speed(self) -> float:
        return self.distance / self.seconds


@dataclass(frozen=True)
class Run:
    """One run of the window, as the estimate reads it: its sport and its efforts."""
    sport: str
    efforts: tuple[Effort, ...] = ()


@dataclass(frozen=True)
class Critical:
    cs: float  # m/s
    d_prime: float  # m
    points: tuple[Effort, ...]  # the efforts of the fit
    runs: int  # the runs of the window


def bucket(distance: float) -> int | None:
    """The target distance a distance counts for (within BUCKET_TOLERANCE), else None."""
    for target in TARGETS:
        if abs(distance - target) <= BUCKET_TOLERANCE * target:
            return target
    return None


def plausible(effort: Effort) -> bool:
    """A positive time, and no faster than the world record over its target distance."""
    target = bucket(effort.distance)
    return (target is not None and effort.seconds > 0 and effort.distance > 0
            and effort.speed <= target / RECORD_S[target])


def downhill(start_alt, end_alt, distance: float) -> bool:
    """Whether the net descent from `start_alt` to `end_alt` is DOWNHILL of the distance or more (unknown: no)."""
    if start_alt is None or end_alt is None or distance <= 0:
        return False
    try:
        return float(start_alt) - float(end_alt) >= DOWNHILL * distance
    except (TypeError, ValueError):
        return False


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN


def strava_efforts(best_efforts, streams: dict | None = None) -> list[Effort]:
    """Strava's best efforts of one run (its list as the detail gives it) over the target distances; one run downhill
    is left out when the run's altitude stream says so (the effort's start and end indices)."""
    altitude = (streams or {}).get("altitude") or []
    out = []
    for item in best_efforts if isinstance(best_efforts, list) else []:
        if not isinstance(item, dict):
            continue
        distance, seconds = _number(item.get("distance")), _number(item.get("elapsed_time"))
        if distance is None or seconds is None:
            continue
        effort = Effort(distance, seconds)
        if not plausible(effort):
            continue
        i, j = item.get("start_index"), item.get("end_index")
        if isinstance(i, int) and isinstance(j, int) and 0 <= i < j < len(altitude) \
                and downhill(altitude[i], altitude[j], distance):
            continue
        out.append(effort)
    return out


def stream_efforts(times, distances, altitudes=None, targets=TARGETS) -> list[Effort]:
    """The fastest effort over each target distance in one run's streams (seconds and metres from the start, one
    sample each): the shortest window that covers the distance, without a gap, a jump or a net descent inside it."""
    n = min(len(times or []), len(distances or []))
    raw_t = [_number(x) for x in (times or [])[:n]]
    raw_d = [_number(x) for x in (distances or [])[:n]]
    if n < 2 or None in raw_t or None in raw_d:
        return []
    t = [float(x or 0) for x in raw_t]
    d = [float(x or 0) for x in raw_d]
    alt = list(altitudes or [])
    alt = alt if len(alt) >= n else []
    # a bad step (a gap, a jump, a distance going back) makes every window across it unusable: prefix counts
    bad = [0]
    for k in range(1, n):
        dt, dd = t[k] - t[k - 1], d[k] - d[k - 1]
        bad.append(bad[-1] + (dt <= 0 or dt > MAX_GAP_S or dd < 0 or dd > MAX_STEP_SPEED * dt))
    out = []
    for target in targets:
        best, i = None, 0
        for j in range(1, n):
            if d[j] - d[0] < target:
                continue
            while d[j] - d[i + 1] >= target:
                i += 1
            if bad[j] - bad[i]:
                continue
            covered = d[j] - d[i]
            seconds = (t[j] - t[i]) * target / covered  # the window's pace over the target distance
            if alt and downhill(alt[i], alt[j], covered):
                continue
            if best is None or seconds < best:
                best = seconds
        if best is not None:
            effort = Effort(float(target), best)
            if plausible(effort):
                out.append(effort)
    return out


def fit(points) -> tuple[float, float] | None:
    """(CS, D′): the least-squares line distance = D′ + CS × time through the points; None for fewer than two
    distinct times."""
    pts = list(points)
    if len(pts) < 2:
        return None
    mt = sum(p.seconds for p in pts) / len(pts)
    md = sum(p.distance for p in pts) / len(pts)
    var = sum((p.seconds - mt) ** 2 for p in pts)
    if var <= 0:
        return None
    cs = sum((p.seconds - mt) * (p.distance - md) for p in pts) / var
    return cs, md - cs * mt


def best_by_target(efforts) -> dict[int, Effort]:
    """The fastest effort for each target distance (the fastest speed: a 1/2 mile and an 800 m count alike)."""
    out: dict[int, Effort] = {}
    for effort in efforts:
        target = bucket(effort.distance)
        if target is not None and plausible(effort) and (target not in out or effort.speed > out[target].speed):
            out[target] = effort
    return out


def estimate(runs) -> Critical | None:
    """The athlete's critical speed from the runs of the window, or None: fewer than MIN_RUNS runs, not the
    distances the model needs, or a fit that is not plausible."""
    runs = [r for r in runs if r.sport in RUN_SPORTS]
    if len(runs) < MIN_RUNS:
        return None
    best = best_by_target(e for r in runs for e in r.efforts)
    if all(t in best for t in BEST_MODEL):
        chosen = [best[t] for t in BEST_MODEL]
    else:
        chosen = [best[t] for t in TARGETS if t in best]
        if len(chosen) < 3 or min(bucket(e.distance) or 0 for e in chosen) > SHORT_MAX \
                or max(bucket(e.distance) or 0 for e in chosen) < LONG_MIN:
            return None
    line = fit(chosen)
    if line is None:
        return None
    cs, d_prime = line
    if not CS_RANGE[0] <= cs <= CS_RANGE[1] or not 0 < d_prime <= D_PRIME_MAX:
        return None
    return Critical(cs, d_prime, tuple(chosen), len(runs))


# ── the paces ───────────────────────────────────────────────────────────────

def pace_s(speed: float) -> float:
    """Seconds per km."""
    return 1000 / speed


def easy_cap(c: Critical) -> float:
    """The slowest « pas plus vite que » of easy running, in s/km."""
    return pace_s(EASY_CAP * c.cs)


def threshold(c: Critical) -> tuple[float, float]:
    """(fast, slow) s/km of a threshold effort."""
    return pace_s(THRESHOLD[1] * c.cs), pace_s(THRESHOLD[0] * c.cs)


def vo2max(c: Critical) -> tuple[float, float]:
    """(fast, slow) s/km of a « vite » effort of 3 min or more: the speeds the athlete's line holds for
    VO2MAX_TIME_S."""
    slow_t, fast_t = VO2MAX_TIME_S
    return pace_s(c.cs + c.d_prime / fast_t), pace_s(c.cs + c.d_prime / slow_t)


def race_pace(km: float | None, minutes: float | None) -> float | None:
    """The average pace of a race (s/km): plain arithmetic."""
    if not km or not minutes or km <= 0 or minutes <= 0:
        return None
    return minutes * 60 / km


# ── the loader ──────────────────────────────────────────────────────────────

STREAM_BATCH = 12  # choix pratique: the streams are read a few runs at a time (each is a large JSON)
CACHE_MAX = 512  # choix pratique: the estimates kept in this process, one per athlete and state of their runs
_seen: dict[tuple, "Critical | None"] = {}


def forget() -> None:
    """Empties the estimates kept in this process (the tests)."""
    _seen.clear()


def _winners(efforts: dict[int, list[Effort]]) -> set[int]:
    """The runs that hold the fastest effort of a target distance."""
    best: dict[int, tuple[float, int]] = {}
    for run_id, items in efforts.items():
        for effort in items:
            target = bucket(effort.distance)
            if target is not None and plausible(effort) and (target not in best or effort.speed > best[target][0]):
                best[target] = (effort.speed, run_id)
    return {run_id for _, run_id in best.values()}


async def for_athlete(db, user_id: int, today: date, *, streams: bool = True) -> Critical | None:
    """The athlete's critical speed from their runs of the last WINDOW_DAYS, once per page. `streams`: also read the
    stored streams (a run without best efforts, a best effort's descent); the public calendar feed does not.
    A stream is a large JSON: only those the estimate needs are read — the runs without best efforts, and the runs
    that hold a fastest effort (to leave out one run downhill, then the next fastest is checked in turn) — and the
    result is kept in this process for as long as the athlete's runs of the window do not change."""
    from sqlalchemy import select

    from app.models.activity import Activity

    since = datetime.combine(today - timedelta(days=WINDOW_DAYS), time(), timezone.utc)
    until = datetime.combine(today + timedelta(days=1), time(), timezone.utc)
    where = (Activity.user_id == user_id, Activity.sport_type.in_(RUN_SPORTS), Activity.start_date >= since,
             Activity.start_date < until)
    rows = (await db.execute(select(Activity.id, Activity.sport_type, Activity.best_efforts, Activity.updated_at,
                                    Activity.streams_data.is_not(None).label("stream")).where(*where))).all()
    if len(rows) < MIN_RUNS:
        return None
    key = (user_id, today.toordinal(), streams, len(rows), max(r.id for r in rows),
           max((r.updated_at for r in rows if r.updated_at is not None), default=None),
           hash(repr(sorted((r.id, repr(r.best_efforts), bool(r.stream)) for r in rows))))
    if key in _seen:
        return _seen[key]

    async def read(ids: list[int]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for at in range(0, len(ids), STREAM_BATCH):
            found = await db.execute(select(Activity.id, Activity.streams_data).where(
                Activity.id.in_(ids[at:at + STREAM_BATCH])))
            out.update({r.id: r.streams_data for r in found if isinstance(r.streams_data, dict)})
        return out

    efforts = {r.id: strava_efforts(r.best_efforts) if r.best_efforts else [] for r in rows}
    if streams:
        with_stream = {r.id for r in rows if r.stream}
        # a run without best efforts: its fastest rolling efforts
        bare = [r.id for r in rows if not efforts[r.id] and r.id in with_stream]
        for run_id, stream in (await read(bare)).items():
            efforts[run_id] = stream_efforts(stream.get("time"), stream.get("distance"), stream.get("altitude"))
        # Strava's best efforts: the altitude of the runs that hold a fastest one says whether it was run downhill
        raw = {r.id: r.best_efforts for r in rows}
        checked = set(bare)
        for _ in range(len(rows)):
            todo = sorted((_winners(efforts) & with_stream) - checked)
            if not todo:
                break
            checked.update(todo)
            for run_id, stream in (await read(todo)).items():
                efforts[run_id] = strava_efforts(raw[run_id], stream)
    result = estimate([Run(r.sport_type, tuple(efforts[r.id])) for r in rows])
    if len(_seen) >= CACHE_MAX:
        _seen.pop(next(iter(_seen)))
    _seen[key] = result
    return result
