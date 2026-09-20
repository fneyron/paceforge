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
from datetime import datetime, timedelta, timezone

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


def effort_km(distance_km: float, elevation_gain_m: float) -> float:
    return float(distance_km) + float(elevation_gain_m or 0) / 100.0


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


async def build_race_effort_model(db: AsyncSession, user_id: int, months: int = 30) -> dict | None:
    """Collect the athlete's races and fit the effort-km/h decay model.

    Returns {"a", "b", "n", "fitted", "races": [...]} or None when no race is
    usable (the caller then keeps the training-based prediction).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    result = await db.execute(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.sport_type.in_(RUN_TYPES),
            Activity.start_date >= cutoff,
            Activity.distance >= MIN_RACE_KM * 1000,
        )
        .order_by(Activity.start_date.desc())
    )
    acts = result.scalars().all()

    from app.services.activity_dedupe import find_duplicate_ids

    dups = find_duplicate_ids(acts)
    races, long_efforts = [], []
    for a in acts:
        if a.id in dups or not a.moving_time or a.moving_time < MIN_RACE_H * 3600:
            continue
        km = (a.distance or 0) / 1000
        hours = a.moving_time / 3600
        pt = {
            "id": a.id, "name": a.name, "date": a.start_date.strftime("%d/%m/%Y") if a.start_date else "",
            "km": round(km, 1), "dplus": int(round(a.total_elevation_gain or 0)),
            "hours": round(hours, 2), "ekm": round(effort_km(km, a.total_elevation_gain or 0), 1),
        }
        pt["ekm_h"] = round(pt["ekm"] / hours, 2)
        if _is_race(a):
            pt["weight"], pt["is_race"] = 1.0, True
            races.append(pt)
        elif km >= LONG_EFFORT_KM:
            pt["weight"], pt["is_race"] = 0.5, False
            long_efforts.append(pt)

    points = races if len(races) >= 2 else races + long_efforts
    if not points:
        return None
    used, ignored = select_best_efforts(points)
    model = fit_effort_model(used)
    if not model:
        return None
    model["races"] = sorted(used, key=lambda p: -p["hours"])[:8]
    model["ignored"] = sorted(ignored, key=lambda p: -p["hours"])[:6]
    model["n_races"] = len(races)
    model["n_used"] = len(used)
    return model


_NOT_A_RACE = ("hik", "rando", "randon", "marche", "walk", "trek", "balade", "recon", "reco ", "pacer", "accompagn", "dnf", "abandon")
BEST_BIN_FACTOR = 1.5   # duration bins: 3-4.5 h, 4.5-6.75 h, … one best effort each
OUTLIER_RATIO = 0.8     # a race more than 20 % under the curve of the others is not a performance


def select_best_efforts(points: list[dict]) -> tuple[list[dict], list[dict]]:
    """A race curve is fitted on what the athlete CAN do, not on the average
    of everything tagged « race » on Strava: a hike, a recce, a day spent
    pacing a friend, an abandon all sit far under the real curve and would
    drag the prediction down. Keep, per duration bin, the fastest effort;
    then drop what still sits well under the curve of the others."""
    kept, ignored = [], []
    for p in points:
        name = (p.get("name") or "").lower()
        if any(k in name for k in _NOT_A_RACE):
            ignored.append({**p, "why": "pas une course"})
        else:
            kept.append(p)
    if len(kept) < 2:
        kept, ignored = list(points), []
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


def apply_race_calibration(course, model: dict | None) -> dict | None:
    """Re-level the predicted segment times so the total matches the race
    model. Keeps the terrain shape. Returns a summary dict (or None if the
    model does not apply)."""
    if not model or not course.segments or not course.predicted_total_time_s:
        return None
    from app.services.race_simulator import format_time

    ekm = effort_km(course.total_distance_km, course.total_elevation_gain)
    total = predict_total_s(model, ekm)
    if not total or total <= 0:
        return None
    before = float(course.predicted_total_time_s)
    ratio = total / before
    # A wild ratio means the races don't describe this course (e.g. a 10 km
    # road race used to level a 100-miler): cap the correction.
    ratio = max(0.60, min(1.40, ratio))
    cum = 0.0
    for seg in course.segments:
        seg.predicted_time_s = round(seg.predicted_time_s * ratio, 1)
        seg.predicted_pace_s_per_km = round(seg.predicted_pace_s_per_km * ratio, 1)
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
