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

The resulting total time re-levels the terrain-shaped prediction: the shape
(where the time goes) still comes from the gradient profile, the level (how
fast overall) from the races.
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
    return {
        "a": math.exp(-c / k), "b": 1 - 1 / k, "k": k, "c": c, "n": len(pts),
        "fitted": sxx >= 1e-6,
        # for the scenarios' spread: mean ln E of the races, the weighted count (Kish), Sxx
        "log_ekm_mean": mx, "n_eff": sw * sw / sum(w * w for w in ws), "sxx": sxx,
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
        heat = activity_heat(a)
        points.append(race_point(
            a.name, km, dplus, a.moving_time / 3600, is_race,
            day=a.start_date.date() if a.start_date else None, id=a.id, dplus_src=src,
            elapsed_h=round((a.elapsed_time or 0) / 3600, 2) or None, **({"heat": heat} if heat else {}),
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
    when two or more of them are real races (not a triathlon or relay leg, an
    abandon, a hike…), else the long untagged efforts help. Every race kept
    weighs its tag (race 1, long effort 0.5) × its recency."""
    races = [p for p in points if p.get("is_race")]
    real = [p for p in races if not_a_race_reason(p.get("name")) is None]
    pts = races if len(real) >= 2 else points
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
    model["stop_factor"] = stop_factor(used)
    return model


# ── the athlete's own stops: his races' (elapsed − moving) / elapsed against
# the field's at the same finish time ──
# The field's median share of the finish time spent stopped, 1 695 LiveTrail
# finishers of 40-180 km whose departures were timed at ≥ 60 % of the
# checkpoints (median regression): 2.69 % at 18 h, × (T / 18 h)^1.072. A
# race's ratio to it, averaged in ln over his races of 3 h or more, is shrunk
# halfway to the field (one race's share is noisy: ln SD 1.15) and bounded.
# Checked on 112 UTMB Live runners linked across two races timed in and out
# at the stations, the factor of one race applied to the other's median
# stops: total stops off by 21.8 min against 24.8 without it (−3.0 [−5.3,
# −0.6]), the clock after each station by 13.1 against 14.6 (−1.4 [−2.6,
# −0.2]; 95 % CI, bootstrap over the runners).
STOP_SHARE_18H, STOP_SHARE_EXP = 0.0269, 1.072
STOP_FACTOR_SHRINK = 0.5
STOP_FACTOR_RANGE = (0.5, 2.0)


def stop_factor(races: list[dict]) -> float:
    """The factor his planned stops take (1 = the field's), from his races'
    elapsed and moving hours; 1 without a race that says it."""
    logs, ws = [], []
    for p in races:
        el, mv = p.get("elapsed_h") or 0, p.get("hours") or 0
        if mv < 3 or el <= mv:  # short, or no stop recorded (a watch that stops its clock)
            continue
        field = STOP_SHARE_18H * (el / 18) ** STOP_SHARE_EXP
        logs.append(math.log(((el - mv) / el) / field))
        ws.append(p.get("weight", 1.0))
    if not logs:
        return 1.0
    mean = sum(w * x for w, x in zip(ws, logs, strict=True)) / sum(ws)
    lo, hi = STOP_FACTOR_RANGE
    return round(max(lo, min(hi, math.exp(STOP_FACTOR_SHRINK * mean))), 3)


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


_NOT_A_RACE = ("hik", "rando", "randon", "marche", "walk", "trek", "balade", "recon", "reco ", "pacer", "accompagn", "dnf", "abandon")
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
    low = (name or "").lower()
    if any(k in low for k in _NOT_A_RACE):
        return "pas une course"
    return None


# A race whose time sits this far from the curve of the athlete's other races
# (ln T, either way) is not his level: an injury, a race run with a friend, a
# course much longer or shorter than its name. ln 1.35 ≈ 3 × one race's noise
# (RACE_LOG_SD): symmetric, so the level is not pulled fast as the former
# « best effort per duration » selection did.
OUTLIER_LOG = math.log(1.35)


def select_races(points: list[dict]) -> tuple[list[dict], list[dict]]:
    """The races the level is read on: everything tagged « race » that
    measures a standalone running race (no triathlon run leg, relay leg or
    abandon; a hike, a recce, a day pacing a friend only when too little is
    left), then, from three races on, what sits far off the curve of the
    others (either way) is set aside."""
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

    dplus, dplus_src = course_dplus_src(course)
    ekm = effort_km(course.total_distance_km, dplus)
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
    n_eff = (model or {}).get("n_eff") or (model or {}).get("n") or 0
    if n_eff > 0:
        var += RACE_LOG_SD ** 2 / n_eff
        mx = (model or {}).get("log_ekm_mean")
        if mx is not None and course_ekm and course_ekm > 0:
            var += (K_PRIOR_SD * (math.log(course_ekm) - mx)) ** 2
    else:
        var += SPREAD_NO_RACE ** 2
    return math.sqrt(var)


def scenario_pcts(model: dict | None, course_ekm: float | None, known_course: bool = False) -> tuple[float, float]:
    """(Optimiste % faster, Sécurité % slower) on the moving time: the 20th and
    80th percentiles of the finish time, to 0.1 %."""
    sigma = finish_spread(model, course_ekm, known_course)
    return round((1 - math.exp(-Z80 * sigma)) * 100, 1), round((math.exp(Z80 * sigma) - 1) * 100, 1)


# ── personal fatigue tilt, measured on a matched race ──
# The tilt reshapes the population fatigue around 45 % of the distance (see
# race_simulator._fatigue_factor): above the neutral 0.22 the athlete starts
# faster and fades more, below he fades less. Measured on ALL the matched
# checkpoints: the tilt whose plan, run on the athlete's own finish time,
# best reproduces his cumulative passage times. Shrunk toward the neutral
# value with few checkpoints (one race adjusts, never dominates), clamped.
# Checked in-sample only (the 2026 Transjeju's own 11 checkpoints fit better,
# by construction); whether a tilt read on one race predicts the checkpoints of
# the next one better than the neutral value is not measured yet.
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
                                plan_moving_s=total_actual_s, fine_terrain=fine)
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
# cached empty (factor 1, not asked again).
HEAT_CACHE_KEY = "paceforge_heat"
HEAT_CACHE_VERSION = 1
HEAT_LOOKUPS_PER_PASS = 10


def activity_heat(a: Activity) -> float | None:
    """The race's mean heat factor when looked up (≥ 1), else None."""
    c = (a.raw_data or {}).get(HEAT_CACHE_KEY) or {}
    if c.get("v") == HEAT_CACHE_VERSION and c.get("h"):
        return float(c["h"])
    return None


def mean_heat(series: dict, start: datetime, hours: float) -> tuple[float, float] | None:
    """(mean heat factor, mean °C) over the hours of a race starting at ``start``
    (UTC) and lasting ``hours``, from {« YYYY-MM-DDTHH:00 » (GMT): (°C, % RH)}."""
    from app.services.weather import compute_heat_factor

    t0 = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    n = max(1, int(math.ceil(hours)))
    hs, ts = [], []
    for i in range(n + 1):
        v = series.get((t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00"))
        if v and v[0] is not None:
            hs.append(compute_heat_factor(v[0], v[1] if v[1] is not None else 60))
            ts.append(v[0])
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


async def _heat_pass(db: AsyncSession, user_id: int, limit: int, now: datetime) -> int:
    from app.services import weather
    from app.services.activity_env import start_point

    wt = Activity.raw_data["workout_type"].as_integer()
    cached_v = Activity.raw_data[(HEAT_CACHE_KEY, "v")].as_integer()
    q = candidate_query(user_id).with_only_columns(Activity.id, Activity.distance, wt, cached_v)
    rows = (await db.execute(q.order_by(Activity.start_date.desc()))).all()
    ids = [r[0] for r in rows
           if r[3] != HEAT_CACHE_VERSION and (r[2] == 1 or (r[1] or 0) >= LONG_EFFORT_KM * 1000)][:limit]
    if not ids:
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
                series = await weather.temperature_humidity(place[0], place[1], start.date(), last.date(), now.date())
            except weather.LookupUnavailable as e:
                logger.info("race heat backfill: Open-Meteo unavailable for user %d: %s", user_id, e)
                break
            got = mean_heat(series, start, hours)
            if got:
                entry = {"v": HEAT_CACHE_VERSION, "h": round(got[0], 3), "t": round(got[1], 1), "src": "open-meteo"}
        a.raw_data = {**raw, HEAT_CACHE_KEY: entry}
        done += 1
    if done:
        await db.flush()
    return done
