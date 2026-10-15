"""Race-based calibration: effort-km per hour on the athlete's real races.

The gradient↔pace profile learned on training splits gives *training* paces:
easy days, stops, social runs. Plans built on it come out pessimistic (a first
draft at 23h30 for a race that took 20h). What predicts a race is the pace the
athlete actually sustains when it counts: on their previous races, measured as
**effort-km per hour** (1 km + 100 m of climb = 1 effort-km) with a decay for
how long the effort lasts — the longer the race, the lower the sustainable rate.

    ekm_h(T) = a · T^(−b)          (T in hours; b = endurance decay)
    ⇔  ln T = c + k · ln E         (E = the race's effort-km, k = 1 / (1 − b))

Fitted on the athlete's races (Strava "race" tag, workout_type == 1) as a
regression of ln T on ln E: E is known exactly, T carries the race's noise
(the former fit regressed ln(E/T) on ln T, the noisy variable on both sides).
The exponent k is shrunk toward the population value (normal prior, Efron &
Morris 1975): with one race, or races of one length, it IS the population
value; races of different lengths move it toward the athlete's own. The level
c is the recency- and tag-weighted mean of the races (no « best effort »
selection: it read the athlete faster than he is).

Population k = 1.28 (b ≈ 0.22): at a fixed UTMB index, the finish time of
48 640 finishers of 170 UTMB Live races (21 events) grows as E^1.30
(leave-one-event-out 1.29-1.31; one festival, several distances: median
1.28). The former b = 0.08 (k = 1.087) put 140-200 km finishers 17 % too
fast and < 60 km ones 9.5 % too slow (leave-one-event-out finish MAE 12.9 %
vs 10.1 % at k = 1.28).

Checked by level (2026-10-14; level_benchmark.py k_by_level): at a fixed
index the exponent does not fall for strong runners: 1.29 [1.25, 1.32] under
index 550, 1.32 [1.26, 1.39] at 550-650, 1.32 [1.27, 1.37] at 650-750, 1.27
[1.25, 1.30] from 750 (jackknife over the 21 events). 242 runners followed
across 2 to 8 races (sex, birth year, index) show no spread of their own
exponent beyond the races' noise (SD 0.00, CI up to 0.05): the prior SD
stays 0.08. Each of their races predicted from the earlier ones: MAE 13.3 %
with the former law, 11.8 % with this one at SD 0.08, 0.12 or 0.15, 11.7-11.8
% with an exponent by level band. Every index band does better than with the
former law on the 48 640 finishers (MAE -20 to -26 %); on the followed
runners, the strongest (index 750+) about as well (9.2 vs 9.4 %).

The resulting total time re-levels the terrain-shaped prediction: the shape
(where the time goes) still comes from the gradient profile, the level (how
fast overall) from the races.
"""

from __future__ import annotations

import copy
import logging
import math
import re
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.route import Route

logger = logging.getLogger(__name__)

RUN_TYPES = ("Run", "TrailRun", "VirtualRun")
DEFAULT_DECAY = 0.22          # population endurance decay: T ∝ E^1.28 at a fixed level (see above)
DECAY_MIN, DECAY_MAX = 0.10, 0.35
K_PRIOR = 1.28                # population exponent k = 1 / (1 − b)
K_PRIOR_SD = 0.08             # its spread between athletes (prior SD)
RACE_LOG_SD = 0.10            # one race's ln T around the athlete's law: day 0.07 + course 0.07
MIN_RACE_KM = 15.0            # shorter "races" (park runs, 10 km) say little about an ultra
MIN_RACE_H = 1.0
LONG_EFFORT_KM = 40.0         # untagged very long efforts used as fallback (weight 0.5)
MODEL_MONTHS = 30             # races older than this are not read

# ── D+: one algorithm for the course and for the athlete's past races ──
# Strava's own total (another algorithm, on the watch's data) read 2,5-12 %
# above gpx.profile_elevation_gain on the same stream (median ~4,6 %) on six
# of the owner's races: the race rate and the course effort-km were not
# measured with the same ruler. A past race's D+ is recomputed from its
# altitude stream with gpx.profile_elevation_gain (fetched once from Strava,
# cached in raw_data); without a stream, Strava's total × this factor (median
# ratio profile_elevation_gain / Strava on those six races: 0,893-0,975).
STRAVA_DPLUS_FACTOR = 0.956
DPLUS_CACHE_KEY = "paceforge_dplus"
DPLUS_CACHE_VERSION = 1

# ── the fitted model per athlete, per worker ──
# A plan page asks for the athlete's profile two or three times (the route's context, its plan bundle, the older
# page's objective), and each fit read every race or long effort of 30 months as a full row, its 18-KiB raw_data
# included (594 ms a call, 36 MB decoded, on the audit's dataset; 2026-10-14). The candidates are read as columns
# (candidate_columns) and the fitted model is kept per worker for RACE_MODEL_TTL_S, keyed on the candidate set's
# count and last write: a sync that adds a race or rewrites one (its D+, its heat) replaces it at once; the TTL
# bounds what that key cannot see (the day's recency weights, a write outside the ORM).
RACE_MODEL_TTL_S = 300
RACE_MODEL_CACHE_SIZE = 256  # athletes kept per worker; the least recently read goes first
_RACE_MODELS: OrderedDict[int, tuple[float, tuple, dict | None]] = OrderedDict()  # user_id → (expires, key, model)


def effort_km(distance_km: float, elevation_gain_m: float) -> float:
    return float(distance_km) + float(elevation_gain_m or 0) / 100.0


def dplus_from_route_coords(coords: list | None, total_elevation_gain: float | None = None) -> float | None:
    """The effort D+ of a full-resolution trace ([lat, lon, km, ele] rows), or
    None when the trace carries no usable elevation."""
    rows = [c for c in coords or [] if len(c) > 3 and c[3] is not None]
    if len(rows) < 2:
        return None
    from app.services.gpx import profile_elevation_gain

    gain, _ = profile_elevation_gain([c[2] * 1000 for c in rows], [c[3] for c in rows])
    gain = float(round(gain, 0))
    if gain <= 0 and total_elevation_gain:
        return None  # a flat-lined trace: the displayed total says more
    return gain


def course_dplus_src(course) -> tuple[float, str]:
    """(D+, source) of the course for the race calibration: « effort » when
    measured with the races' algorithm, « total » for the displayed per-km
    total (a trace without elevations).

    Courses saved before carry no ``dplus_effort`` (migration v6e7f8a9b0c1
    stores it on the saved routes): computed from the trace and kept on this
    object, so the calls of one prediction pay it once."""
    dp = getattr(course, "dplus_effort", None)
    if dp is not None:
        return float(dp), "effort"
    gain = dplus_from_route_coords(getattr(course, "route_coords", None), course.total_elevation_gain)
    if gain is not None:
        try:
            course.dplus_effort = gain
        except Exception:  # an object without the field: just don't cache
            pass
        return gain, "effort"
    return float(course.total_elevation_gain or 0), "total"


def course_dplus(course) -> float:
    """The course's D+ for the race calibration (same algorithm as the races)."""
    return course_dplus_src(course)[0]


def dplus_from_streams(streams: dict | None, distance_m: float | None = None) -> float | None:
    """D+ of an activity from its Strava streams ({"distance": [...],
    "altitude": [...]}), or None when there is no usable altitude."""
    from app.services.gpx import profile_elevation_gain

    alt = (streams or {}).get("altitude") or []
    if len([a for a in alt if a is not None]) < 2:
        return None
    dist = (streams or {}).get("distance") or []
    if len(dist) != len(alt):
        if not distance_m or distance_m <= 0:
            return None
        n = len(alt)  # altitude alone: evenly spread over the activity's distance
        dist = [distance_m * i / (n - 1) for i in range(n)]
    gain, _ = profile_elevation_gain(dist, alt)
    return gain


def cached_dplus(a) -> float | None:
    """The D+ recomputed from the race's altitude stream and kept in raw_data (backfill_race_dplus), if any."""
    cached = (a.raw_data or {}).get(DPLUS_CACHE_KEY) or {}
    if cached.get("v") == DPLUS_CACHE_VERSION and cached.get("gain") is not None:
        return float(cached["gain"])
    return None


def activity_dplus(a) -> tuple[float, str]:
    """(D+, source) of a past race: « stream » when recomputed from its
    altitude stream, else « strava » (Strava's total × STRAVA_DPLUS_FACTOR)."""
    gain = cached_dplus(a)
    if gain is not None:
        return gain, "stream"
    if a.streams_data:
        try:
            g = dplus_from_streams(a.streams_data, a.distance)
        except Exception:
            g = None
        if g is not None:
            return g, "stream"
    return float(a.total_elevation_gain or 0) * STRAVA_DPLUS_FACTOR, "strava"


def _is_race(a) -> bool:
    wt = (a.raw_data or {}).get("workout_type")
    return wt == 1


def _ekm(p: dict) -> float:
    """A point's effort-km (older points carry only the rate)."""
    return float(p.get("ekm") or (p.get("ekm_h", 0) or 0) * (p.get("hours", 0) or 0))


def fit_hours(p: dict) -> float:
    """The hours a point weighs in the fit: its moving hours, heat taken out when
    its mean heat factor is known (a race run at 30 °C is not the athlete's
    level at 12 °C; the course's own forecast puts the heat back)."""
    heat = p.get("heat") or 1.0
    return float(p.get("hours", 0) or 0) / max(1.0, float(heat))


def fit_effort_model(points: list[dict]) -> dict | None:
    """Fit ln T = c + k·ln E on [{hours, ekm, weight}] (weighted), k shrunk
    toward the population: k̂ = (Sxy/σ² + k0/τ²) / (Sxx/σ² + 1/τ²), σ = one
    race's log-time noise, k0 ± τ the population exponent. With one race (or
    races of one length) Sxx = 0 and k̂ = k0. Returned as the a·T^(−b) model
    (b = 1 − 1/k, a = e^(−c/k)) plus what the scenarios' spread needs."""
    pts = [p for p in points if fit_hours(p) > 0 and _ekm(p) > 0]
    if not pts:
        return None
    xs = [math.log(_ekm(p)) for p in pts]
    ys = [math.log(fit_hours(p)) for p in pts]
    ws = [max(1e-6, float(p.get("weight", 1.0))) for p in pts]
    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs, strict=True)) / sw
    my = sum(w * y for w, y in zip(ws, ys, strict=True)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs, strict=True))
    sxy = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys, strict=True))
    s2, t2 = RACE_LOG_SD ** 2, K_PRIOR_SD ** 2
    k = (sxy / s2 + K_PRIOR / t2) / (sxx / s2 + 1 / t2)
    k = max(1 / (1 - DECAY_MIN), min(1 / (1 - DECAY_MAX), k))
    c = my - k * mx  # the level passes through the weighted mean of the races
    # the scenarios' spread counts real races only (Kish, with their weights): long training efforts
    # read without a race say no more than no race at all
    rw = [w for p, w in zip(pts, ws, strict=True) if p.get("is_race") and not_a_race_reason(p.get("name")) is None]
    return {
        "a": math.exp(-c / k), "b": 1 - 1 / k, "k": k, "c": c, "n": len(pts),
        "fitted": sxx >= 1e-6,
        # for the scenarios' spread: mean ln E of the races, the weighted count of real races (Kish), Sxx
        "log_ekm_mean": mx, "n_eff": sum(rw) ** 2 / sum(w * w for w in rw) if rw else 0.0, "sxx": sxx,
    }


def predict_total_s(model: dict, course_ekm: float) -> int | None:
    """Solve ekm / T = a·T^(−b)  →  T = (ekm / a)^(1 / (1 − b)) = e^c · ekm^k."""
    a, b = model.get("a") or 0, model.get("b") or 0
    if a <= 0 or course_ekm <= 0 or b >= 1:
        return None
    hours = (course_ekm / a) ** (1.0 / (1.0 - b))
    return int(round(hours * 3600))


def candidate_query(user_id: int, months: int = MODEL_MONTHS):
    """Runs long enough to be read as a race (or a long effort)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    return select(Activity).where(
        Activity.user_id == user_id,
        Activity.sport_type.in_(RUN_TYPES),
        Activity.start_date >= cutoff,
        Activity.distance >= MIN_RACE_KM * 1000,
        Activity.moving_time >= MIN_RACE_H * 3600,
    )


def candidate_columns(user_id: int, months: int = MODEL_MONTHS):
    """The candidates as the model reads them, newest first: a dozen scalars and three keys of raw_data, never
    raw_data itself nor the streams (asked apart, for the races whose D+ is not cached yet: collect_race_points)."""
    raw = Activity.raw_data
    return (
        candidate_query(user_id, months)
        .with_only_columns(
            Activity.id, Activity.name, Activity.sport_type, Activity.start_date, Activity.distance,
            Activity.moving_time, Activity.elapsed_time, Activity.total_elevation_gain, Activity.average_heartrate,
            raw["workout_type"].as_integer().label("workout_type"),
            raw[DPLUS_CACHE_KEY].label("dplus_cache"),
            raw[HEAT_CACHE_KEY].label("heat_cache"),
            Activity.splits_metric.is_not(None).label("has_splits"),  # the duplicate ranking's tie-breaker
        )
        .order_by(Activity.start_date.desc())
    )


class _Candidate:
    """A candidate read from candidate_columns: what activity_dplus, activity_heat and the duplicate ranking read
    on an Activity, from the columns alone (the streams come later, for the races that need them)."""

    __slots__ = ("id", "name", "sport_type", "start_date", "distance", "moving_time", "elapsed_time",
                 "total_elevation_gain", "average_heartrate", "splits_metric", "raw_data", "streams_data")

    def __init__(self, row):
        (self.id, self.name, self.sport_type, self.start_date, self.distance, self.moving_time, self.elapsed_time,
         self.total_elevation_gain, self.average_heartrate, workout_type, dplus, heat, has_splits) = row
        self.splits_metric = bool(has_splits)
        self.raw_data = {"workout_type": workout_type, DPLUS_CACHE_KEY: dplus, HEAT_CACHE_KEY: heat}
        self.streams_data = None


async def build_race_effort_model(db: AsyncSession, user_id: int, months: int = MODEL_MONTHS) -> dict | None:
    """Collect the athlete's races and fit the effort-km/h decay model.

    Returns {"a", "b", "n", "fitted", "races": [...]} or None when no race is
    usable (the caller then keeps the training-based prediction). Kept per
    worker while the candidate set is unchanged (its count and last write) and
    no result link moved (a route's link confirms an outing as a race and names
    it: activity_identity.confirmed_races), RACE_MODEL_TTL_S at most; every
    caller gets its own copy.
    """
    count, last_write = (await db.execute(
        candidate_query(user_id, months).with_only_columns(func.count(Activity.id), func.max(Activity.updated_at))
    )).one()
    links, last_link = (await db.execute(
        select(func.count(Route.result_activity_id), func.max(Route.updated_at)).where(Route.user_id == user_id)
    )).one()
    key = (months, count, last_write, links, last_link)
    now = time.monotonic()
    hit = _RACE_MODELS.get(user_id)
    if hit and hit[0] > now and hit[1] == key:
        _RACE_MODELS.move_to_end(user_id)
        return copy.deepcopy(hit[2])
    model = model_from_points(await collect_race_points(db, user_id, months))
    _remember_model(user_id, key, model, now + RACE_MODEL_TTL_S)
    return copy.deepcopy(model)


def _remember_model(user_id: int, key: tuple, model: dict | None, expires: float) -> None:
    _RACE_MODELS[user_id] = (expires, key, model)
    _RACE_MODELS.move_to_end(user_id)
    while len(_RACE_MODELS) > RACE_MODEL_CACHE_SIZE:
        _RACE_MODELS.popitem(last=False)


async def collect_race_points(db: AsyncSession, user_id: int, months: int = MODEL_MONTHS) -> list[dict]:
    """The athlete's races and long efforts as the model reads them (race_point; one per duplicate)."""
    acts = [_Candidate(row) for row in (await db.execute(candidate_columns(user_id, months))).all()]

    from app.services.activity_dedupe import duplicate_representatives
    from app.services.activity_identity import confirmed_races, races_on_retained_copies

    dups = duplicate_representatives(acts)
    confirmed = await confirmed_races(db, user_id, activity_ids=[a.id for a in acts])
    confirmed = races_on_retained_copies(confirmed, dups)
    kept = [a for a in acts
            if a.id not in dups and a.moving_time and a.moving_time >= MIN_RACE_H * 3600
            and (_is_race(a) or a.id in confirmed or (a.distance or 0) / 1000 >= LONG_EFFORT_KM)]
    # the altitude streams of the races whose D+ is not cached yet (backfill_race_dplus caches it once, out of
    # the request path; until then, or without a Strava stream, the row's own): those rows alone
    need = [a for a in kept if cached_dplus(a) is None]
    if need:
        streams = dict((await db.execute(
            select(Activity.id, Activity.streams_data)
            .where(Activity.id.in_([a.id for a in need]), Activity.streams_data.is_not(None))
        )).all())
        for a in need:
            a.streams_data = streams.get(a.id)
    points = []
    for a in kept:
        km = (a.distance or 0) / 1000
        is_race = _is_race(a) or a.id in confirmed
        dplus, src = activity_dplus(a)
        heat = activity_heat(a)
        points.append(race_point(
            confirmed.get(a.id) or a.name, km, dplus, a.moving_time / 3600, is_race,
            day=a.start_date.date() if a.start_date else None, id=a.id, dplus_src=src,
            **({"heat": heat} if heat else {}),
        ))
    return points


def race_point(name: str, km: float, dplus: float, hours: float, is_race: bool,
               day: date | None = None, **extra) -> dict:
    """One past effort as the model reads it (tagged races weigh 1, long
    untagged efforts 0.5)."""
    pt = {
        **extra, "name": name, "date": day.strftime("%d/%m/%Y") if day else "",
        "day": day.isoformat() if day else None,
        "km": round(km, 1), "dplus": int(round(dplus or 0)),
        "hours": round(hours, 2), "ekm": round(effort_km(km, dplus or 0), 1),
        "is_race": is_race, "weight": 1.0 if is_race else 0.5,
    }
    pt["ekm_h"] = round(pt["ekm"] / hours, 2) if hours > 0 else 0
    return pt


def model_from_points(points: list[dict], today: date | None = None) -> dict | None:
    """Selection + fit on prepared points (see race_point): the tagged races
    as soon as one of them is a real race (not a triathlon or relay leg, an
    abandon, a hike…); the long untagged efforts only without one (a race
    alone beats training runs: long runs read 10-40 % slower than the race
    pulled its level 5-40 % slow, and could set the race itself aside).
    Every race kept weighs its tag (race 1, long effort 0.5) × its recency."""
    races = [p for p in points if p.get("is_race")]
    real = [p for p in races if not_a_race_reason(p.get("name")) is None]
    pts = races if real else points
    if not pts:
        return None
    pts = [{**p, "weight": p.get("weight", 1.0) * recency_weight(p.get("day"), today)} for p in pts]
    used, ignored = select_races(pts)
    if not used:
        return None
    model = fit_effort_model(used)
    if not model:
        return None
    # every race's D+ measured like the course's (from its altitude stream)?
    model["dplus_all_stream"] = all(p.get("dplus_src") == "stream" for p in used)
    model["races"] = sorted(used, key=lambda p: -p["hours"])[:8]
    model["ignored"] = sorted(ignored, key=lambda p: -p["hours"])[:6]
    model["n_races"] = len(races)
    model["n_used"] = len(used)
    # the heat his races were run in (1 for a race not looked up): the level is read without it, so a course
    # without weather (no date, nothing fetched) takes it back, neutral against the level read with it
    sw = sum(p.get("weight", 1.0) for p in used)
    model["heat_mean"] = round(sum(p.get("weight", 1.0) * max(1.0, float(p.get("heat") or 1.0)) for p in used) / sw, 3) if sw else 1.0
    return model


# Recency: a race weighs 0.5 ** (age / half-life) in the fit (None = off): the
# athlete of two years ago is not today's. A prior, not a measured gain:
# replayed on the owner's six ultras (each predicted from the strictly earlier
# races), an 18-month half-life moved the median error by -0,15 point and the
# mean by -0,01 point, i.e. noise: the latest race 4 min closer, one 2 min
# closer, two 1-2 min further. To validate on more runners.
RECENCY_HALF_LIFE_DAYS: float | None = 548.0


def recency_weight(day: str | date | None, today: date | None = None) -> float:
    if not RECENCY_HALF_LIFE_DAYS or not day:
        return 1.0
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day)
        except ValueError:
            return 1.0
    age = ((today or datetime.now(timezone.utc).date()) - day).days
    return 0.5 ** (max(0, age) / RECENCY_HALF_LIFE_DAYS)


# Not a race whatever the tag: a hike, a walk, a recce, a day pacing a friend.
# Whole words only: « Shikoku Trail 100 », « Walker Ridge 50K », « Treklanders
# 60K » or « Trail des Marches de Bretagne » are races (a substring test read
# them as a hike, a walk, a trek, a « marche »).
_NOT_A_RACE = re.compile(
    r"\b(hik(e|es|ed|ing)|rando\w*|marche|walk(s|ing)?|trek(s|king)?|balade|reco(n|nnaissance)?|recce|pacer"
    r"|accompagn\w*)\b",
    re.IGNORECASE,
)
# Not a standalone running race of that distance, whatever the Strava tag: the
# run leg of a triathlon (run on legs tired by the swim and the bike), one leg
# of a relay, a race not finished. Word-bounded so that « Cap Corse Trail »,
# « Trail du Cap », « Imperial Trail » or « Relaxe Run » stay races; « CAP »
# only as the suffix of a multisport file (« Alpsman - CAP »); « IM » only in
# capitals before a distance or a year (« IM 70.3 », « IM 2025 »): the German
# « im » (« Trailrun im Allgäu ») is a race.
_NOT_STANDALONE = (
    (re.compile(
        r"\b70[.,]3\b|\biron ?man\b|\bhalf[ -]?im\b|(?-i:\bIM\s?\d)|\btriath?lon|\bduathlon|\bswim ?run\b"
        r"|[-–—:|]\s*c\.?a\.?p\.?\s*$",
        re.IGNORECASE,
    ), "course d'un triathlon"),
    (re.compile(r"\brelais\b|\brelay\b|\bekiden\b", re.IGNORECASE), "relais"),
    (re.compile(r"\bdnf\b|\babandon|\bdisqualifi", re.IGNORECASE), "abandon"),
)


def not_a_race_reason(name: str | None) -> str | None:
    """Why an effort tagged « race » does not measure the athlete's race level
    (None when it does)."""
    for pattern, why in _NOT_STANDALONE:
        if pattern.search(name or ""):
            return why
    if _NOT_A_RACE.search(name or ""):
        return "pas une course"
    return None


# A race whose time sits this far from the curve of the athlete's other races
# (ln T, either way) is not his level: an injury, a race run with a friend, a
# course much longer or shorter than its name. ln 1.35 ≈ 3 × one race's noise
# (RACE_LOG_SD): symmetric, so the level is not pulled fast as the former
# « best effort per duration » selection did.
OUTLIER_LOG = math.log(1.35)
# The outlier loop refits the curve once per race per pass (O(n³)): 20 points 4 ms, 80 points 200 ms, 205 points
# 2,9 s (measured 2026-10-14). The newest SELECT_MAX_POINTS of 30 months are read; an older one, past forty, would
# move the level by less than the day's noise. (H)
SELECT_MAX_POINTS = 40  # (H)


def select_races(points: list[dict]) -> tuple[list[dict], list[dict]]:
    """The races the level is read on: everything tagged « race » that
    measures a standalone running race (no triathlon run leg, relay leg or
    abandon; a hike, a recce, a day pacing a friend only when nothing else is
    left), the newest SELECT_MAX_POINTS of them, then, from three races on,
    what sits far off the curve of the others (either way) is set aside."""
    kept, ignored, soft = [], [], []
    for p in points:
        why = not_a_race_reason(p.get("name"))
        if why:
            ignored.append({**p, "why": why})
            if why == "pas une course":
                soft.append(p)
        else:
            kept.append(p)
    if not kept and soft:
        # nothing left: a hike or a recce still beats nothing (never a real race's company: it would read
        # him slow), but a triathlon leg, a relay leg or an abandon never comes back
        kept = kept + soft
        ignored = [p for p in ignored if p["why"] != "pas une course"]
    if len(kept) > SELECT_MAX_POINTS:
        kept = sorted(kept, key=lambda p: p.get("day") or "", reverse=True)[:SELECT_MAX_POINTS]
    # one at a time, the farthest first (a very fast race must not make the others look slow), while
    # three races or more are left
    while len(kept) >= 3:
        offs = []
        for p in kept:
            m = fit_effort_model([q for q in kept if q is not p])
            pred = predict_total_s(m, _ekm(p)) if m else None
            offs.append(math.log(fit_hours(p) * 3600 / pred) if pred and fit_hours(p) > 0 else 0.0)
        i = max(range(len(kept)), key=lambda j: abs(offs[j]))
        if abs(offs[i]) <= OUTLIER_LOG:
            break
        ignored.append({**kept[i], "why": "bien sous ta courbe" if offs[i] > 0 else "bien au-dessus de ta courbe"})
        kept = kept[:i] + kept[i + 1:]
    return kept, ignored


def race_level_total_s(course, model: dict | None, training_total_s: float, heat: float = 1.0) -> float | None:
    """The course's moving time at the level of the athlete's races, or None
    when the model does not apply. ``heat``: the heat it is read at (1: the
    level as fitted, heat-free; predict_course passes the races' mean heat,
    heat_mean, for a course without weather)."""
    if not model or not course.segments or not training_total_s or training_total_s <= 0:
        return None
    total = predict_total_s(model, effort_km(course.total_distance_km, course_dplus(course)))
    if not total or total <= 0:
        return None
    total *= heat or 1.0
    # A wild ratio means the races don't describe this course (e.g. a 10 km
    # road race used to level a 100-miler): cap the correction.
    return training_total_s * max(0.60, min(1.40, total / training_total_s))


def apply_race_calibration(course, model: dict | None, training_total_s: float | None = None,
                           heat: float = 1.0) -> dict | None:
    """Re-level the predicted segment times so the total matches the race
    model. Keeps the terrain shape. Returns a summary dict (or None if the
    model does not apply).

    ``training_total_s`` is the prediction at the training level when the
    course was already re-simulated on the race level's clock (predict_course):
    the cap and the « training alone » figure are read against it. ``heat``: as
    race_level_total_s."""
    if not model or not course.segments or not course.predicted_total_time_s:
        return None
    from app.services.race_simulator import format_time

    dplus, dplus_src = course_dplus_src(course)
    ekm = effort_km(course.total_distance_km, dplus)
    before = float(training_total_s or course.predicted_total_time_s)
    target = race_level_total_s(course, model, before, heat)
    if not target:
        return None
    ratio = target / before
    step = target / sum(seg.predicted_time_s for seg in course.segments)
    cum = 0.0
    for seg in course.segments:
        seg.predicted_time_s = round(seg.predicted_time_s * step, 1)
        seg.predicted_pace_s_per_km = round(seg.predicted_pace_s_per_km * step, 1)
        cum += seg.predicted_time_s
        seg.cumulative_time_s = round(cum, 1)
    course.predicted_total_time_s = int(cum)
    course.predicted_total_time_formatted = format_time(int(cum))
    return {
        "ekm": round(ekm, 1), "total_s": int(cum), "training_total_s": int(before),
        "ratio": round(ratio, 3), "a": round(model["a"], 2), "b": round(model["b"], 3),
        "n": model.get("n", 0), "n_races": model.get("n_races", 0), "fitted": model.get("fitted", False),
        "ekm_h": round(ekm / (cum / 3600), 2) if cum else None,
        "races": model.get("races", []),
        "ignored": model.get("ignored", []),
        "n_used": model.get("n_used", model.get("n", 0)),
        # the course and every race used have their D+ by the same algorithm
        "dplus_same_ruler": bool(model.get("dplus_all_stream")) and dplus_src == "effort",
    }


# ── how sure the finish time is: the Optimiste / Sécurité scenarios ──
# The finish time of an athlete on a course is the level of his races times
# the course's law, with what neither knows. In ln T, independent terms:
#   day        0.07   his own day-to-day spread (with the 0.07 of a level read on past
#                     races, the 0.10 SD of 48 640 finishers around their UTMB index)
#   edition    0.044  the same course from one edition to the next (39 courses, 104 editions)
#   course     0.07   how far this course's difficulty sits from its effort-km
#                     (0.04 on a course he has raced before)
#   level      0.10 / √n  the level read on n races (one race's noise / √n; Kish
#                     count with the recency weights); no race: 0.15 (H), the
#                     level then comes from training paces
#   reach      0.08 · ln(E / Ē)  the exponent's spread between athletes, read
#                     from the mean effort-km Ē of his races to this one's E
# Optimiste / Sécurité are the 20th / 80th percentiles: e^(∓0.84 σ) − 1. With
# a fixed −5 / +10 % only 33-55 % of finishers fell between the two (16-42 %
# with the former decay).
SPREAD_DAY, SPREAD_EDITION = 0.07, 0.044
SPREAD_COURSE, SPREAD_KNOWN_COURSE = 0.07, 0.04
SPREAD_NO_RACE = 0.15  # (H)
Z80 = 0.8416  # standard normal 80th percentile


def finish_spread(model: dict | None, course_ekm: float | None, known_course: bool = False) -> float:
    """σ of ln(finish time) for this athlete on this course (see above)."""
    var = SPREAD_DAY ** 2 + SPREAD_EDITION ** 2 + (SPREAD_KNOWN_COURSE if known_course else SPREAD_COURSE) ** 2
    n_eff = (model or {}).get("n_eff", (model or {}).get("n")) or 0  # 0: no real race, training efforts only
    if n_eff > 0:
        var += RACE_LOG_SD ** 2 / n_eff
        mx = (model or {}).get("log_ekm_mean")
        if mx is not None and course_ekm and course_ekm > 0:
            var += (K_PRIOR_SD * (math.log(course_ekm) - mx)) ** 2
    else:
        var += SPREAD_NO_RACE ** 2
    return math.sqrt(var)


def scenario_pcts(model: dict | None, course_ekm: float | None, known_course: bool = False) -> tuple[float, float]:
    """(Optimiste % faster, Sécurité % slower), to 0.1 %: the 20th and 80th
    percentiles of the finish time (read on finish times, stops included), so
    applied to the plan's elapsed time (race_simulator.build_scenarios)."""
    sigma = finish_spread(model, course_ekm, known_course)
    return round((1 - math.exp(-Z80 * sigma)) * 100, 1), round((math.exp(Z80 * sigma) - 1) * 100, 1)


# ── personal fatigue tilt, measured on a matched race ──
# The tilt reshapes the population fatigue around 45 % of the distance (see
# race_simulator._fatigue_factor): above the neutral 0.22 the athlete starts
# faster and fades more, below he fades less. Measured on ALL the matched
# checkpoints: the tilt whose plan, run on the athlete's own finish time,
# best reproduces his cumulative passage times (« raw », stored with the race).
# The plan uses the mean raw tilt of his latest races shrunk toward the
# neutral value by m / (m + 2), m = the races (race_simulator._personal_fatigue_tilt):
# one race moves it a third of the way. A race's 10 checkpoints say how he
# ran THAT day (split-half reliability within one race 0.97); what carries to
# the next race is much less: on 91 finishers linked across two races of the
# corpus (same sex, birth year and UTMB index, no names), the raw tilts
# correlate 0.14 (a lower bound: some links are wrong), and the next race's
# checkpoints came out 0.18 min closer than with the neutral tilt with m / (m + 2),
# 0.12 min further with the former n / (n + 4) on the checkpoints.
TILT_MIN, TILT_MAX = 0.05, 0.40
TILT_GRID_STEP = 0.01
TILT_PRIOR_RACES = 2      # weight of the measured tilts = m / (m + 2), m matched races
TILT_PRIOR_CPS = 4        # the former weight n / (n + 4) checkpoints: undoes the shrinkage of stored tilts
TILT_MAX_PROGRESS = 0.97  # a checkpoint at the finish says nothing (pinned there)


def tilt_weight(m: int) -> float:
    """How much m matched races move the tilt away from the neutral value."""
    return m / (m + TILT_PRIOR_RACES) if m > 0 else 0.0


def raw_tilt(result: dict) -> float | None:
    """A matched race's own tilt (before any shrinkage): stored as fatigue_tilt_raw; on races matched
    before, the stored tilt with the former n / (n + 4) shrinkage undone (n = fatigue_tilt_cps), or the
    tilt itself when it was read on the first checkpoint alone."""
    from app.services.race_simulator import DEFAULT_FATIGUE_TILT

    if result.get("fatigue_tilt_raw") is not None:
        return float(result["fatigue_tilt_raw"])
    tilt = result.get("fatigue_tilt")
    if tilt is None:
        return None
    n = result.get("fatigue_tilt_cps")
    if n:
        raw = DEFAULT_FATIGUE_TILT + (float(tilt) - DEFAULT_FATIGUE_TILT) * (n + TILT_PRIOR_CPS) / n
        return max(TILT_MIN, min(TILT_MAX, raw))
    return float(tilt)


def measure_fatigue_tilt(course_json: dict, profile, checkpoints: list[dict], actual: list[dict],
                         total_actual_s: float, start_hour: int = 6, start_minute: int = 0,
                         race_date=None, utc_offset_s: int | None = None) -> dict | None:
    """{"tilt", "raw", "n"}: the tilt one race alone gives, the best fit before
    shrinkage (stored: the plan combines races on it), the checkpoints used;
    None without a usable checkpoint.

    ``actual`` is [{km, time_s}] (cumulative moving time at each checkpoint,
    see race_simulator.actual_passage_times)."""
    from app.schemas.simulator import CourseProfile
    from app.services.race_simulator import (
        DEFAULT_FATIGUE_TILT,
        _fine_terrain,
        compute_passage_times,
        predict_course,
    )

    total_km = float(course_json.get("total_distance_km") or 0)
    if not total_actual_s or total_actual_s <= 0 or total_km <= 0:
        return None
    real = {
        round(float(a["km"]), 1): float(a["time_s"]) for a in actual or []
        if a.get("km") and a.get("time_s") and 0 < float(a["km"]) <= TILT_MAX_PROGRESS * total_km
    }
    if not real:
        return None

    # Parsed once (a long trace takes a while to validate) and copied per grid
    # step: predict_course writes into the segments only. The race level is
    # left out: the plan basis is set by the runner's own finish time. The
    # 25 m terrain does not depend on the tilt: computed once.
    base = CourseProfile(**course_json)
    base_profile = profile.model_copy(update={"race_model": None, "race_calibration": None})
    fine = _fine_terrain(base, base_profile)

    def gaps(tilt: float) -> list[float]:
        prof = base_profile.model_copy(update={"fatigue_tilt": tilt})
        course = base.model_copy(update={"segments": [s.model_copy() for s in base.segments]})
        course = predict_course(course, prof, start_hour=start_hour, start_minute=start_minute,
                                plan_moving_s=total_actual_s, fine_terrain=fine,
                                race_date=race_date, utc_offset_s=utc_offset_s)  # the plan's own night
        secs = compute_passage_times(course, checkpoints, int(total_actual_s), 1.0, start_hour, start_minute, None)
        # by km, not name: a loop course repeats names
        pred = {round(float(s["end_km"]), 1): s["adjusted_cumulative_time_s"] for s in secs[:-1]}
        return [real[k] - pred[k] for k in real if pred.get(k)]

    best, best_sse, n = None, None, 0
    steps = int(round((TILT_MAX - TILT_MIN) / TILT_GRID_STEP))
    for i in range(steps + 1):
        tilt = round(TILT_MIN + i * TILT_GRID_STEP, 3)
        g = gaps(tilt)
        if not g:
            return None
        sse = sum(x * x for x in g)
        if best_sse is None or sse < best_sse - 1e-9:
            best, best_sse, n = tilt, sse, len(g)
    # alone (one race), the tilt the plan would take: a third of the way from the neutral value
    tilt = DEFAULT_FATIGUE_TILT + tilt_weight(1) * (best - DEFAULT_FATIGUE_TILT)
    return {"tilt": round(max(TILT_MIN, min(tilt, TILT_MAX)), 3), "raw": best, "n": n}


# ── fetching the races' altitude streams (once per race, out of the request path) ──

async def backfill_race_dplus(db: AsyncSession, user, strava, limit: int = 20) -> int:
    """Recompute the D+ of the athlete's races from their Strava altitude
    stream and cache it in ``raw_data`` (one request per race, once).

    Only the races (and long efforts) the model reads and without a cached
    value. Stops at the first rate-limit answer (the next run carries on). A
    race without a usable stream is marked so it is not asked again; it keeps
    the Strava × STRAVA_DPLUS_FACTOR fallback. Returns the number of races
    updated. The races' heat (backfill_race_heat) is looked up after, in the
    same pass (it never raises)."""
    done = await _backfill_dplus(db, user, strava, limit)
    await backfill_race_heat(db, user.id, limit=min(limit, HEAT_LOOKUPS_PER_PASS))
    return done


async def _backfill_dplus(db: AsyncSession, user, strava, limit: int) -> int:
    from app.exceptions import StravaAPIError, StravaRateLimitError

    # light query first (this runs at every poll): ids, tag and cache marker only
    wt = Activity.raw_data["workout_type"].as_integer()
    cached_v = Activity.raw_data[(DPLUS_CACHE_KEY, "v")].as_integer()
    q = (candidate_query(user.id).where(Activity.strava_activity_id.is_not(None))  # streams come from Strava
         .with_only_columns(Activity.id, Activity.distance, wt, cached_v))
    rows = (await db.execute(q.order_by(Activity.start_date.desc()))).all()
    ids = [
        r[0] for r in rows
        if r[3] != DPLUS_CACHE_VERSION and (r[2] == 1 or (r[1] or 0) >= LONG_EFFORT_KM * 1000)
    ][:limit]
    if not ids:
        return 0
    todo = (await db.execute(select(Activity).where(Activity.id.in_(ids)))).scalars().all()
    done = 0
    for a in todo:
        try:
            streams = await strava.get_activity_streams(user, a.strava_activity_id, ["distance", "altitude"])
        except StravaRateLimitError:
            logger.info("race D+ backfill: Strava rate limit, %d done for user %d", done, user.id)
            break
        except StravaAPIError as exc:
            if exc.status_code not in (403, 404):
                logger.warning("race D+ backfill: activity %d: %s", a.id, exc)
                continue
            streams = None
        except Exception:
            logger.warning("race D+ backfill failed for activity %d", a.id, exc_info=True)
            continue
        try:
            gain = dplus_from_streams(streams, a.distance)
        except Exception:
            gain = None
        entry = {"v": DPLUS_CACHE_VERSION, "gain": round(gain, 1) if gain is not None else None,
                 "src": "stream" if gain is not None else "none"}
        # reassign (not mutate) so the JSON column is saved
        a.raw_data = {**(a.raw_data or {}), DPLUS_CACHE_KEY: entry}
        done += 1
    if done:
        await db.flush()
    return done


# ── the races' heat: a race run hot is not the athlete's level in the cool ──
# Each race's mean heat factor (weather.compute_heat_factor, the curve the
# course's own forecast goes through) over its hours, from Open-Meteo's hourly
# temperature and humidity at its start point (the forecast API's past days,
# else the ERA5 archive), cached in raw_data once: the fit then reads its
# hours divided by it (fit_hours), and the course's forecast puts the heat of
# race day back. A race without a position, or an answer without values, is
# cached empty (factor 1, not asked again). The temperature is read at the
# race's mean altitude, as the course's forecast is at each section's: the
# lapse rate from the elevation Open-Meteo's values are for to the middle of
# the race's lowest and highest points (Strava's elev_low / elev_high).
HEAT_CACHE_KEY = "paceforge_heat"
HEAT_CACHE_VERSION = 3  # 2: altitude-corrected, humidity from 15 °C; 3: humidity phased in to 20 °C
HEAT_LOOKUPS_PER_PASS = 10


def activity_heat(a) -> float | None:
    """The race's mean heat factor when looked up (≥ 1), else None."""
    c = (a.raw_data or {}).get(HEAT_CACHE_KEY) or {}
    if c.get("v") == HEAT_CACHE_VERSION and c.get("h"):
        return float(c["h"])
    return None


def mean_heat(series: dict, start: datetime, hours: float, shift_c: float = 0.0) -> tuple[float, float] | None:
    """(mean heat factor, mean °C) over the hours of a race starting at ``start``
    (UTC) and lasting ``hours``, from {« YYYY-MM-DDTHH:00 » (GMT): (°C, % RH)};
    ``shift_c`` moves every temperature to the race's altitude."""
    from app.services.weather import compute_heat_factor

    t0 = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    n = max(1, int(math.ceil(hours)))
    hs, ts = [], []
    for i in range(n + 1):
        v = series.get((t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00"))
        if v and v[0] is not None:
            hs.append(compute_heat_factor(v[0] + shift_c, v[1] if v[1] is not None else 60))
            ts.append(v[0] + shift_c)
    if not hs:
        return None
    return sum(hs) / len(hs), sum(ts) / len(ts)


async def backfill_race_heat(db: AsyncSession, user_id: int, limit: int = HEAT_LOOKUPS_PER_PASS,
                             now: datetime | None = None) -> int:
    """Look up the mean heat of the athlete's races not looked up yet (newest
    first, one Open-Meteo call each, ``limit`` at most). Returns the number of
    races updated; stops at the first outage; never raises."""
    from app.config import settings

    if not settings.ACTIVITY_ENV_FETCH:
        return 0
    try:
        async with db.begin_nested():
            return await _heat_pass(db, user_id, limit, now or datetime.now(timezone.utc))
    except Exception:
        logger.warning("race heat backfill failed for user %d", user_id, exc_info=True)
        return 0


def race_altitude(raw: dict) -> float | None:
    """The race's mean altitude (m), read as the middle of its lowest and highest points (Strava)."""
    try:
        lo, hi = float(raw["elev_low"]), float(raw["elev_high"])
    except (KeyError, TypeError, ValueError):
        return None
    return (lo + hi) / 2 if math.isfinite(lo) and math.isfinite(hi) and hi >= lo else None


async def _heat_pass(db: AsyncSession, user_id: int, limit: int, now: datetime) -> int:
    from app.services import weather
    from app.services.activity_env import start_point
    from app.services.race_simulator import _LAPSE_RATE_C_PER_M

    wt = Activity.raw_data["workout_type"].as_integer()
    cached_v = Activity.raw_data[(HEAT_CACHE_KEY, "v")].as_integer()
    q = candidate_query(user_id).with_only_columns(Activity.id, Activity.distance, wt, cached_v)
    rows = (await db.execute(q.order_by(Activity.start_date.desc()))).all()
    ids = [r[0] for r in rows
           if r[3] != HEAT_CACHE_VERSION and (r[2] == 1 or (r[1] or 0) >= LONG_EFFORT_KM * 1000)][:limit]
    if not ids or await weather.lookups_paused():  # nothing to read, or Open-Meteo failed lately: not now
        return 0
    done = 0
    for a in (await db.execute(select(Activity).where(Activity.id.in_(ids)))).scalars().all():
        raw = a.raw_data if isinstance(a.raw_data, dict) else {}
        place = start_point(raw)
        hours = (a.elapsed_time or a.moving_time or 0) / 3600
        entry = {"v": HEAT_CACHE_VERSION, "h": None, "src": "none"}
        if place and a.start_date and hours > 0:
            start = a.start_date if a.start_date.tzinfo else a.start_date.replace(tzinfo=timezone.utc)
            last = start + timedelta(hours=hours)
            try:
                series, at_m = await weather.temperature_humidity(place[0], place[1], start.date(), last.date(), now.date())
            except weather.LookupUnavailable as e:
                logger.info("race heat backfill: Open-Meteo unavailable for user %d: %s", user_id, e)
                await weather.pause_lookups()  # every worker stops asking for a while
                break
            race_m = race_altitude(raw)
            dz = race_m - at_m if race_m is not None and at_m is not None else 0.0
            got = mean_heat(series, start, hours, -_LAPSE_RATE_C_PER_M * dz)
            if got:
                entry = {"v": HEAT_CACHE_VERSION, "h": round(got[0], 3), "t": round(got[1], 1), "dz": round(dz),
                         "src": "open-meteo"}
        a.raw_data = {**raw, HEAT_CACHE_KEY: entry}
        done += 1
    if done:
        await db.flush()
    return done
