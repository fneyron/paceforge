"""The race level law the app used until 2026-10-14 (0f276af), frozen for one use only.

The plan page then saved its objective field at every save: the objective the
athlete typed, or, when he had not moved it, its own estimate — the moving
time at his level from the route's start, his level read with this law. To
tell one from the other in a time saved then (routers.simulator._objective_s),
that estimate is read again with this law: never to predict anything shown.

    ekm_h(T) = a · T^(−b)

fitted on the best effort per duration band (log-spaced, × 1.5), weighted by
recency (half-life 548 days), a race far under the curve of the others set
aside; one race alone: b = 0.08. Races read as they were run (no heat).
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone

DEFAULT_DECAY = 0.08
DECAY_MIN, DECAY_MAX = 0.02, 0.20
BEST_BIN_FACTOR = 1.5   # duration bins: 3-4.5 h, 4.5-6.75 h, … one best effort each
OUTLIER_RATIO = 0.8     # a race more than 20 % under the curve of the others is not a performance
RECENCY_HALF_LIFE_DAYS = 548.0

_NOT_A_RACE = ("hik", "rando", "randon", "marche", "walk", "trek", "balade", "recon", "reco ", "pacer", "accompagn", "dnf", "abandon")
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
    for pattern, why in _NOT_STANDALONE:
        if pattern.search(name or ""):
            return why
    low = (name or "").lower()
    if any(k in low for k in _NOT_A_RACE):
        return "pas une course"
    return None


def recency_weight(day: str | date | None, today: date | None = None) -> float:
    if not day:
        return 1.0
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day)
        except ValueError:
            return 1.0
    age = ((today or datetime.now(timezone.utc).date()) - day).days
    return 0.5 ** (max(0, age) / RECENCY_HALF_LIFE_DAYS)


def fit(points: list[dict]) -> dict | None:
    """a·T^(−b) on [{hours, ekm_h, weight}], weighted least squares in log-log."""
    pts = [p for p in points if p.get("hours", 0) > 0 and p.get("ekm_h", 0) > 0]
    if not pts:
        return None
    if len(pts) == 1:
        p = pts[0]
        return {"a": p["ekm_h"] * (p["hours"] ** DEFAULT_DECAY), "b": DEFAULT_DECAY, "n": 1, "fitted": False}
    xs = [math.log(p["hours"]) for p in pts]
    ys = [math.log(p["ekm_h"]) for p in pts]
    ws = [float(p.get("weight", 1.0)) for p in pts]
    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs, strict=True)) / sw
    my = sum(w * y for w, y in zip(ws, ys, strict=True)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs, strict=True))
    sxy = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys, strict=True))
    b = DEFAULT_DECAY if sxx < 1e-6 else -(sxy / sxx)
    b = max(DECAY_MIN, min(DECAY_MAX, b))
    return {"a": math.exp(my + b * mx), "b": b, "n": len(pts), "fitted": sxx >= 1e-6}


def select_best_efforts(points: list[dict]) -> list[dict]:
    kept, soft = [], []
    for p in points:
        why = not_a_race_reason(p.get("name"))
        if not why:
            kept.append(p)
        elif why == "pas une course":
            soft.append(p)
    if len(kept) < 2 and soft:
        kept = kept + soft
    best: dict[int, dict] = {}
    for p in kept:
        b = int(math.floor(math.log(max(p["hours"], 0.1)) / math.log(BEST_BIN_FACTOR)))
        if b not in best or p["ekm_h"] > best[b]["ekm_h"]:
            best[b] = p
    chosen = list(best.values())
    if len(chosen) >= 3:
        keep = []
        for p in chosen:
            m = fit([q for q in chosen if q is not p])
            pred = (m["a"] * (p["hours"] ** (-m["b"]))) if m else None
            if not (pred and p["ekm_h"] < OUTLIER_RATIO * pred):
                keep.append(p)
        if len(keep) >= 2:
            chosen = keep
    return chosen


def model_from_points(points: list[dict], today: date | None = None) -> dict | None:
    """The race model as that law read it ({a, b, n, fitted}: what race_simulator.predict_course takes), from the
    points race_calibration.collect_race_points gives: the tagged races when two or more are real races, else the
    long untagged efforts too."""
    races = [p for p in points if p.get("is_race")]
    real = [p for p in races if not_a_race_reason(p.get("name")) is None]
    pts = races if len(real) >= 2 else points
    used = select_best_efforts(pts) if pts else []
    if not used:
        return None
    return fit([{**p, "weight": p.get("weight", 1.0) * recency_weight(p.get("day"), today)} for p in used])


async def legacy_model(db, user_id: int) -> dict | None:
    """The athlete's race model by that law, on his races as they are read today (Strava, the cached D+)."""
    from app.services.race_calibration import collect_race_points

    return model_from_points(await collect_race_points(db, user_id))
