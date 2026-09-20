"""Checkpoint metadata: aid-station kind, crew / drop bag, cutoffs, autonomy.

An aid station is not a dot on a profile: whether it has food, whether you can
change socks there and by what time you must leave it decide the plan. The
distance between two *full* stations is a safety figure (how much you must
carry), so it is computed here, not eyeballed.
"""

from __future__ import annotations

KINDS: dict[str, str] = {
    "none": "Point",
    "water": "Eau",
    "full": "Ravito complet",
    "base": "Base vie",
}
# Kinds where you can refill fluid AND eat "real" food → they bound autonomy.
RESUPPLY_KINDS = {"full", "base"}

# A leg between two full stations longer than this is flagged (you carry
# everything for it): distance OR planned time.
AUTONOMY_ALERT_KM = 18.0
AUTONOMY_ALERT_S = 3 * 3600


def _clock_to_s(clock: str | None) -> int | None:
    if not clock:
        return None
    try:
        hh, mm = str(clock).strip().split(":")[:2]
        hh, mm = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh * 3600 + mm * 60


def normalize_checkpoint(cp: dict) -> dict:
    """Sanitize a checkpoint coming from the browser (JSON) or the DB."""
    kind = str(cp.get("kind") or "none").lower()
    if kind not in KINDS:
        kind = "none"
    cutoff = cp.get("cutoff_clock") or None
    if cutoff is not None and _clock_to_s(cutoff) is None:
        cutoff = None
    try:
        km = float(cp.get("distance_km") or 0)
    except (TypeError, ValueError):
        km = 0.0
    elev = cp.get("elevation")
    try:
        elev = float(elev) if elev is not None else None
    except (TypeError, ValueError):
        elev = None
    return {
        "name": str(cp.get("name") or "")[:100],
        "distance_km": km,
        "elevation": elev,
        "kind": kind,
        "crew": bool(cp.get("crew")),
        "drop_bag": bool(cp.get("drop_bag")),
        "cutoff_clock": (str(cutoff)[:5] if cutoff else None),
    }


def cutoff_elapsed_s(cutoff_clock: str | None, start_offset_s: int, min_elapsed_s: int = 0) -> int | None:
    """Seconds after the race start at which the cutoff falls.

    Cutoffs are published as clock times; a 100-miler crosses midnight (often
    twice), so the day is inferred: the first occurrence of that clock time
    that is not before ``min_elapsed_s`` (the earliest plausible passage).
    """
    c = _clock_to_s(cutoff_clock)
    if c is None:
        return None
    elapsed = c - int(start_offset_s)
    while elapsed < 0 or elapsed < min_elapsed_s - 6 * 3600:
        elapsed += 86400
    return int(elapsed)


def is_resupply(cp: dict) -> bool:
    return (cp.get("kind") in RESUPPLY_KINDS) or bool(cp.get("crew")) or bool(cp.get("drop_bag"))


def autonomy_legs(checkpoints: list[dict], total_km: float, sections: list[dict] | None = None) -> list[dict]:
    """Legs between two full resupply points (start → … → finish).

    Returns [{from_name, to_name, from_km, to_km, km, time_s, alert}], with the
    planned time taken from the passage sections when given (plan time if a
    target exists, prediction otherwise). Water-only points do not break a leg:
    you can refill flasks but not eat, so the food you carry must last.
    """
    cps = sorted((normalize_checkpoint(c) for c in checkpoints), key=lambda c: c["distance_km"])
    stops = [{"name": "Départ", "km": 0.0}]
    for cp in cps:
        if 0 < cp["distance_km"] < total_km and is_resupply(cp):
            stops.append({"name": cp["name"], "km": cp["distance_km"]})
    stops.append({"name": "Arrivée", "km": float(total_km)})

    def time_at(km: float) -> float | None:
        if not sections:
            return None
        prev_km, prev_t = 0.0, 0.0
        for s in sections:
            t = s.get("adjusted_cumulative_time_s")
            if t is None:
                t = s.get("cumulative_time_s")
            end_km = float(s.get("end_km") or 0)
            if end_km >= km - 1e-6:
                span = end_km - prev_km
                frac = (km - prev_km) / span if span > 0 else 1.0
                return prev_t + frac * (float(t or 0) - prev_t)
            prev_km, prev_t = end_km, float(t or 0)
        return prev_t

    legs = []
    for a, b in zip(stops, stops[1:], strict=False):
        km = round(b["km"] - a["km"], 1)
        ta, tb = time_at(a["km"]), time_at(b["km"])
        time_s = int(round(tb - ta)) if (ta is not None and tb is not None) else None
        legs.append({
            "from_name": a["name"], "to_name": b["name"],
            "from_km": round(a["km"], 1), "to_km": round(b["km"], 1),
            "km": km, "time_s": time_s,
            "alert": km >= AUTONOMY_ALERT_KM or (time_s is not None and time_s >= AUTONOMY_ALERT_S),
        })
    return legs


def annotate_cutoffs(sections: list[dict], checkpoints: list[dict], start_offset_s: int) -> list[dict]:
    """Add ``cutoff_elapsed_s`` / ``cutoff_margin_s`` to each section whose end
    checkpoint has a cutoff. Margin = cutoff − planned clock (negative = late)."""
    by_index = {i: normalize_checkpoint(cp) for i, cp in enumerate(checkpoints)}
    out = []
    for s in sections:
        s = dict(s)
        idx = s.get("end_checkpoint_index")
        cp = by_index.get(idx) if idx is not None else None
        planned_clock = s.get("adjusted_clock_time_s")
        if planned_clock is None:
            planned_clock = s.get("clock_time_s")
        if cp and cp.get("cutoff_clock") and planned_clock is not None:
            planned_elapsed = int(planned_clock) - int(start_offset_s)
            cut = cutoff_elapsed_s(cp["cutoff_clock"], start_offset_s, min_elapsed_s=planned_elapsed)
            s["cutoff_elapsed_s"] = cut
            s["cutoff_margin_s"] = (cut - planned_elapsed) if cut is not None else None
        for key in ("kind", "crew", "drop_bag", "cutoff_clock"):
            s[key] = cp.get(key) if cp else (None if key == "cutoff_clock" else ("none" if key == "kind" else False))
        out.append(s)
    return out
