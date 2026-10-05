"""Race-based calibration: effort-km per hour on the athlete's real races.

The gradient↔pace profile learned on training splits gives *training* paces:
easy days, stops, social runs. Plans built on it come out pessimistic (a first
draft at 23h30 for a race that took 20h). What predicts a race is the pace the
athlete actually sustains when it counts: on their previous races, measured as
**effort-km per hour** (1 km + 100 m of climb = 1 effort-km) with a decay for
how long the effort lasts — the longer the race, the lower the sustainable rate.

    ekm_h(T) = a · T^(−b)          (T in hours; b = endurance decay)

Fitted on the athlete's races (Strava "race" tag, workout_type == 1). With a
single race the decay is the generic ultra default; with two or more it is
their own. The resulting total time re-levels the terrain-shaped prediction:
the shape (where the time goes) still comes from the gradient profile, the
level (how fast overall) from the races.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity

logger = logging.getLogger(__name__)

RUN_TYPES = ("Run", "TrailRun", "VirtualRun")
DEFAULT_DECAY = 0.08          # generic endurance decay for trail ultras
DECAY_MIN, DECAY_MAX = 0.02, 0.20
MIN_RACE_KM = 15.0            # shorter "races" (park runs, 10 km) say little about an ultra
MIN_RACE_H = 1.0
LONG_EFFORT_KM = 40.0         # untagged very long efforts used as fallback (weight 0.5)
MODEL_MONTHS = 30             # races older than this are not read

# ── D+: one algorithm for the course and for the athlete's past races ──
# Strava's own total (another algorithm, on the watch's data) read 0,6-12 %
# above the course algorithm on the same stream (median ~2,5 %) on six of the
# owner's races: the race rate and the course effort-km were not measured with
# the same ruler. A past race's D+ is recomputed from its altitude stream with
# gpx.profile_elevation_gain (fetched once from Strava, cached in raw_data);
# without a stream, Strava's total × this factor (median of those six races).
STRAVA_DPLUS_FACTOR = 0.975
DPLUS_CACHE_KEY = "paceforge_dplus"
DPLUS_CACHE_VERSION = 1


def effort_km(distance_km: float, elevation_gain_m: float) -> float:
    return float(distance_km) + float(elevation_gain_m or 0) / 100.0


def course_dplus(course) -> float:
    """The course's D+ for the race calibration (same algorithm as the races).

    Courses saved before carry no ``dplus_effort``: computed once from the
    full-resolution trace ([lat, lon, km, ele] rows) and kept on the course;
    without elevations on the trace, the displayed total."""
    dp = getattr(course, "dplus_effort", None)
    if dp is not None:
        return float(dp)
    coords = getattr(course, "route_coords", None) or []
    rows = [c for c in coords if len(c) > 3 and c[3] is not None]
    if len(rows) >= 2:
        from app.services.gpx import profile_elevation_gain

        gain, _ = profile_elevation_gain([c[2] * 1000 for c in rows], [c[3] for c in rows])
        gain = float(round(gain, 0))
        if gain > 0 or not course.total_elevation_gain:
            try:
                course.dplus_effort = gain
            except Exception:  # an object without the field: just don't cache
                pass
            return gain
    return float(course.total_elevation_gain or 0)


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


def activity_dplus(a: Activity) -> tuple[float, str]:
    """(D+, source) of a past race: « stream » when recomputed from its
    altitude stream, else « strava » (Strava's total × STRAVA_DPLUS_FACTOR)."""
    cached = (a.raw_data or {}).get(DPLUS_CACHE_KEY) or {}
    if cached.get("v") == DPLUS_CACHE_VERSION and cached.get("gain") is not None:
        return float(cached["gain"]), "stream"
    if a.streams_data:
        try:
            g = dplus_from_streams(a.streams_data, a.distance)
        except Exception:
            g = None
        if g is not None:
            return g, "stream"
    return float(a.total_elevation_gain or 0) * STRAVA_DPLUS_FACTOR, "strava"


def _is_race(a: Activity) -> bool:
    wt = (a.raw_data or {}).get("workout_type")
    return wt == 1


def fit_effort_model(points: list[dict]) -> dict | None:
    """Fit a·T^(−b) on [{hours, ekm_h, weight}]. Weighted least squares in log-log."""
    pts = [p for p in points if p.get("hours", 0) > 0 and p.get("ekm_h", 0) > 0]
    if not pts:
        return None
    if len(pts) == 1:
        p = pts[0]
        b = DEFAULT_DECAY
        a = p["ekm_h"] * (p["hours"] ** b)
        return {"a": a, "b": b, "n": 1, "fitted": False}
    xs = [math.log(p["hours"]) for p in pts]
    ys = [math.log(p["ekm_h"]) for p in pts]
    ws = [float(p.get("weight", 1.0)) for p in pts]
    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs, strict=False)) / sw
    my = sum(w * y for w, y in zip(ws, ys, strict=False)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs, strict=False))
    sxy = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys, strict=False))
    if sxx < 1e-6:  # all races of the same duration → slope undefined
        b = DEFAULT_DECAY
    else:
        b = -(sxy / sxx)
    b = max(DECAY_MIN, min(DECAY_MAX, b))
    # intercept re-solved with the clamped slope so the fit passes through the cloud
    log_a = my + b * mx
    return {"a": math.exp(log_a), "b": b, "n": len(pts), "fitted": sxx >= 1e-6}


def predict_total_s(model: dict, course_ekm: float) -> int | None:
    """Solve ekm / T = a·T^(−b)  →  T = (ekm / a)^(1 / (1 − b))."""
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


async def build_race_effort_model(db: AsyncSession, user_id: int, months: int = MODEL_MONTHS) -> dict | None:
    """Collect the athlete's races and fit the effort-km/h decay model.

    Returns {"a", "b", "n", "fitted", "races": [...]} or None when no race is
    usable (the caller then keeps the training-based prediction).
    """
    result = await db.execute(candidate_query(user_id, months).order_by(Activity.start_date.desc()))
    acts = result.scalars().all()

    from app.services.activity_dedupe import find_duplicate_ids

    dups = find_duplicate_ids(acts)
    points = []
    for a in acts:
        if a.id in dups or not a.moving_time or a.moving_time < MIN_RACE_H * 3600:
            continue
        km = (a.distance or 0) / 1000
        is_race = _is_race(a)
        if not is_race and km < LONG_EFFORT_KM:
            continue
        dplus, src = activity_dplus(a)
        points.append(race_point(
            a.name, km, dplus, a.moving_time / 3600, is_race,
            day=a.start_date.date() if a.start_date else None, id=a.id, dplus_src=src,
        ))
    return model_from_points(points)


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
    when there are two or more, else the long untagged efforts help."""
    races = [p for p in points if p.get("is_race")]
    pts = races if len(races) >= 2 else points
    if not pts:
        return None
    used, ignored = select_best_efforts(pts)
    if not used:
        return None
    used = [{**p, "weight": p.get("weight", 1.0) * recency_weight(p.get("day"), today)} for p in used]
    model = fit_effort_model(used)
    if not model:
        return None
    model["races"] = sorted(used, key=lambda p: -p["hours"])[:8]
    model["ignored"] = sorted(ignored, key=lambda p: -p["hours"])[:6]
    model["n_races"] = len(races)
    model["n_used"] = len(used)
    return model


# Recency: a race weighs 0.5 ** (age / half-life) in the fit (None = off): the
# athlete of two years ago is not today's. Replayed on the owner's races (each
# ultra predicted from the strictly earlier ones, D+ fix and selection on), an
# 18-month half-life took the median error from 10.1 % to 9.9 % (mean 9.1 %
# either way) and his 2026 Transjeju replay from 17h05 to 16h52 (real 16h04):
# a small gain, kept because it never made a race worse by more than 3 min.
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


_NOT_A_RACE = ("hik", "rando", "randon", "marche", "walk", "trek", "balade", "recon", "reco ", "pacer", "accompagn", "dnf", "abandon")
# Not a standalone running race of that distance, whatever the Strava tag: the
# run leg of a triathlon (run on legs tired by the swim and the bike), one leg
# of a relay, a race not finished. Word-bounded so that « Cap Corse Trail »,
# « Trail du Cap », « Imperial Trail » or « Relaxe Run » stay races; « CAP »
# only as the suffix of a multisport file (« Alpsman - CAP »).
_NOT_STANDALONE = (
    (re.compile(
        r"\b70[.,]3\b|\biron ?man\b|\bhalf[ -]?im\b|\bim\b|\btriath?lon|\bduathlon|\bswim ?run\b"
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
    low = (name or "").lower()
    if any(k in low for k in _NOT_A_RACE):
        return "pas une course"
    return None


BEST_BIN_FACTOR = 1.5   # duration bins: 3-4.5 h, 4.5-6.75 h, … one best effort each
OUTLIER_RATIO = 0.8     # a race more than 20 % under the curve of the others is not a performance


def select_best_efforts(points: list[dict]) -> tuple[list[dict], list[dict]]:
    """A race curve is fitted on what the athlete CAN do, not on the average
    of everything tagged « race » on Strava: a hike, a recce, a day spent
    pacing a friend, an abandon, a triathlon's run leg, a relay leg all sit
    far under the real curve or measure something else. Keep, per duration
    bin, the fastest effort; then drop what still sits well under the curve
    of the others."""
    kept, ignored, soft = [], [], []
    for p in points:
        why = not_a_race_reason(p.get("name"))
        if why:
            ignored.append({**p, "why": why})
            if why == "pas une course":
                soft.append(p)
        else:
            kept.append(p)
    if len(kept) < 2 and soft:
        # too little left: a hike or a recce still beats nothing, but a
        # triathlon leg, a relay leg or an abandon never comes back
        kept = kept + soft
        ignored = [p for p in ignored if p["why"] != "pas une course"]
    # best effort per duration bin (log-spaced)
    best: dict[int, dict] = {}
    for p in kept:
        b = int(math.floor(math.log(max(p["hours"], 0.1)) / math.log(BEST_BIN_FACTOR)))
        if b not in best or p["ekm_h"] > best[b]["ekm_h"]:
            best[b] = p
    chosen = list(best.values())
    for p in kept:
        if p not in chosen:
            ignored.append({**p, "why": "moins bonne perf sur cette durée"})
    # second pass: a remaining point far under the curve of the others is not a performance
    if len(chosen) >= 3:
        keep2 = []
        for p in chosen:
            others = [q for q in chosen if q is not p]
            m = fit_effort_model(others)
            pred = (m["a"] * (p["hours"] ** (-m["b"]))) if m else None
            if pred and p["ekm_h"] < OUTLIER_RATIO * pred:
                ignored.append({**p, "why": "bien sous ta courbe"})
            else:
                keep2.append(p)
        if len(keep2) >= 2:
            chosen = keep2
    return chosen, ignored


def race_level_total_s(course, model: dict | None, training_total_s: float) -> float | None:
    """The course's moving time at the level of the athlete's races, or None
    when the model does not apply."""
    if not model or not course.segments or not training_total_s or training_total_s <= 0:
        return None
    total = predict_total_s(model, effort_km(course.total_distance_km, course_dplus(course)))
    if not total or total <= 0:
        return None
    # A wild ratio means the races don't describe this course (e.g. a 10 km
    # road race used to level a 100-miler): cap the correction.
    return training_total_s * max(0.60, min(1.40, total / training_total_s))


def apply_race_calibration(course, model: dict | None, training_total_s: float | None = None) -> dict | None:
    """Re-level the predicted segment times so the total matches the race
    model. Keeps the terrain shape. Returns a summary dict (or None if the
    model does not apply).

    ``training_total_s`` is the prediction at the training level when the
    course was already re-simulated on the race level's clock (predict_course):
    the cap and the « training alone » figure are read against it."""
    if not model or not course.segments or not course.predicted_total_time_s:
        return None
    from app.services.race_simulator import format_time

    ekm = effort_km(course.total_distance_km, course_dplus(course))
    before = float(training_total_s or course.predicted_total_time_s)
    target = race_level_total_s(course, model, before)
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
    }


# ── personal fatigue tilt, measured on a matched race ──
# The tilt reshapes the population fatigue around 45 % of the distance (see
# race_simulator._fatigue_factor): above the neutral 0.22 the athlete starts
# faster and fades more, below he fades less. Measured on ALL the matched
# checkpoints: the tilt whose plan, run on the athlete's own finish time,
# best reproduces his cumulative passage times. Shrunk toward the neutral
# value with few checkpoints (one race adjusts, never dominates), clamped.
TILT_MIN, TILT_MAX = 0.05, 0.40
TILT_GRID_STEP = 0.01
TILT_PRIOR_CPS = 4        # weight of the measured tilt = n / (n + 4) checkpoints
TILT_MAX_PROGRESS = 0.97  # a checkpoint at the finish says nothing (pinned there)


def measure_fatigue_tilt(course_json: dict, profile, checkpoints: list[dict], actual: list[dict],
                         total_actual_s: float, start_hour: int = 6, start_minute: int = 0) -> dict | None:
    """{"tilt", "raw", "n"}: the tilt to store, the best fit before shrinkage,
    the checkpoints used; None without a usable checkpoint.

    ``actual`` is [{km, time_s}] (cumulative moving time at each checkpoint,
    see race_simulator.actual_passage_times)."""
    from app.schemas.simulator import CourseProfile
    from app.services.race_simulator import (
        DEFAULT_FATIGUE_TILT,
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

    def gaps(tilt: float) -> list[float]:
        prof = profile.model_copy(update={"fatigue_tilt": tilt})
        course = predict_course(CourseProfile(**course_json), prof, start_hour=start_hour,
                                start_minute=start_minute, plan_moving_s=total_actual_s)
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
    w = n / (n + TILT_PRIOR_CPS)
    tilt = DEFAULT_FATIGUE_TILT + w * (best - DEFAULT_FATIGUE_TILT)
    return {"tilt": round(max(TILT_MIN, min(tilt, TILT_MAX)), 3), "raw": best, "n": n}


# ── fetching the races' altitude streams (once per race, out of the request path) ──

async def backfill_race_dplus(db: AsyncSession, user, strava, limit: int = 20) -> int:
    """Recompute the D+ of the athlete's races from their Strava altitude
    stream and cache it in ``raw_data`` (one request per race, once).

    Only the races (and long efforts) the model reads and without a cached
    value. Stops at the first rate-limit answer (the next run carries on). A
    race without a usable stream is marked so it is not asked again; it keeps
    the Strava × STRAVA_DPLUS_FACTOR fallback. Returns the number of races
    updated."""
    from app.exceptions import StravaAPIError, StravaRateLimitError

    # light query first (this runs at every poll): ids, tag and cache marker only
    wt = Activity.raw_data["workout_type"].as_integer()
    cached_v = Activity.raw_data[(DPLUS_CACHE_KEY, "v")].as_integer()
    q = candidate_query(user.id).with_only_columns(Activity.id, Activity.distance, wt, cached_v)
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
