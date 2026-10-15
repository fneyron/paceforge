"""Data the plan page draws on the elevation profile: where the night falls
along the course, the pacing terrain bands, checkpoint clocks and cutoffs.
Computed once per plan refresh, sent as JSON next to the table."""

from __future__ import annotations

from app.services.plan_clock import PlanClock

NIGHT_START_H = 21.0
NIGHT_END_H = 6.0


def _km_elapsed_curve(sections: list[dict], clock: PlanClock, use_target: bool) -> list[tuple[float, float]]:
    """(km, seconds after the start) at the start and at each section's end, stops included."""
    pts = [(0.0, 0.0)]
    for s in sections:
        t = clock.elapsed_of(s, use_target and s.get("adjusted_clock_time_s") is not None)
        if t is None:
            continue
        pts.append((float(s["end_km"]), float(t)))
    return pts


def _km_at(curve: list[tuple[float, float]], t: float) -> float:
    """The km reached ``t`` seconds after the start, linear inside a section."""
    for (k0, t0), (k1, t1) in zip(curve, curve[1:], strict=False):
        if t1 >= t:
            span = t1 - t0
            return k0 + (t - t0) / span * (k1 - k0) if span > 0 else k1
    return curve[-1][0]


def _dark_bands(curve: list[tuple[float, float]], dark) -> list[dict]:
    """night_bands_km in the dark mode: the time walked every DARK_STEP_S, km linear between points."""
    bands, open_km = [], None
    t, end = curve[0][1], curve[-1][1]
    while True:
        km = _km_at(curve, t)
        d = dark(t, km)
        if d and open_km is None:
            open_km = km
        elif not d and open_km is not None:
            bands.append({"start_km": round(open_km, 2), "end_km": round(km, 2)})
            open_km = None
        if t >= end:
            break
        t = min(end, t + DARK_STEP_S)
    if open_km is not None:
        bands.append({"start_km": round(open_km, 2), "end_km": round(curve[-1][0], 2)})
    return bands


DARK_STEP_S = 300  # night by the sun: the clock walked every 5 min


def night_bands_km(sections: list[dict], start_offset_s: int, use_target: bool, dark=None,
                   clock: PlanClock | None = None) -> list[dict]:
    """[{start_km, end_km}] where the athlete is on the course at night: in the
    dark at his place and time when ``dark`` (elapsed s, km → bool, see
    race_simulator.SunClock.is_dark) is given — the plan's own night once the
    race is dated —, else between 21:00 and 06:00 on the race's clock
    (``clock``, plan_clock.PlanClock; the start's offset without one). Time is
    linear inside a section; the curve is walked boundary by boundary (every
    5 min in the dark mode) so multi-night races come out right."""
    clock = clock or PlanClock(start_offset_s)
    curve = _km_elapsed_curve(sections, clock, use_target)
    if len(curve) < 2:
        return []
    end_t = curve[-1][1]
    if dark is not None:
        return _dark_bands(curve, dark)

    def is_night(t: float) -> bool:
        h = (clock.clock_s(t) % 86400) / 3600
        return h >= NIGHT_START_H or h < NIGHT_END_H

    bands = []
    t = curve[0][1]
    open_km = _km_at(curve, t) if is_night(t) else None
    # walk to each 21:00 / 06:00 boundary of the race's clock up to the finish
    boundaries = []
    for day in range(clock.clock_s(t) // 86400, clock.clock_s(end_t) // 86400 + 2):
        boundaries += [clock.elapsed_s(day * 86400 + NIGHT_END_H * 3600), clock.elapsed_s(day * 86400 + NIGHT_START_H * 3600)]
    for b in sorted(boundaries):
        if b <= t or b >= end_t:
            continue
        if is_night(b) and open_km is None:
            open_km = _km_at(curve, b)
        elif not is_night(b) and open_km is not None:
            bands.append({"start_km": round(open_km, 2), "end_km": round(_km_at(curve, b), 2)})
            open_km = None
    if open_km is not None:
        bands.append({"start_km": round(open_km, 2), "end_km": round(curve[-1][0], 2)})
    return bands


def build_plan_data(
    sections: list[dict],
    start_offset_s: int,
    use_target: bool,
    total_km: float,
    blocks: list[dict] | None = None,
    scenarios: dict | None = None,
    autonomy: list[dict] | None = None,
    dark=None,
    clock: PlanClock | None = None,
) -> dict:
    clock = clock or PlanClock(start_offset_s)
    points = []
    for s in sections:
        plan = use_target and s.get("adjusted_clock_time_s") is not None
        clock_val = s.get("adjusted_clock_time_s") if plan else s.get("clock_time_s")
        cum = s.get("adjusted_cumulative_time_s") if (use_target and s.get("adjusted_cumulative_time_s") is not None) else s.get("cumulative_time_s")
        elapsed = clock.elapsed_of(s, plan)  # seconds after the start, stops included
        stops = (float(elapsed) - float(cum)) if (elapsed is not None and cum is not None) else 0.0
        points.append({
            "km": float(s["end_km"]), "start_km": float(s["start_km"]), "name": s["end_name"],
            "cp_index": s.get("end_checkpoint_index"), "clock_s": int(clock_val) if clock_val is not None else None,
            "elapsed_s": int(elapsed) if elapsed is not None else None,
            "cum_s": float(cum) if cum is not None else None, "stops_s": round(stops),
            "stop_s": int(s.get("stop_s") or 0),  # the stop planned at this point (the point dialog shows it)
            "kind": s.get("kind") or "none", "cutoff_clock": s.get("cutoff_clock"),
            "cutoff_margin_s": s.get("cutoff_margin_s"), "temp": s.get("temperature_c"), "code": s.get("weather_code"), "wind": s.get("wind_kmh"),
            "drop_bag": bool(s.get("drop_bag")), "crew": bool(s.get("crew")),
            # pinned passage time (the point dialog reads these)
            "pinned": bool(s.get("pinned")), "pin_clamped": bool(s.get("pin_clamped")),
            "pin_target_s": s.get("pin_target_s"), "pin_clock_s": s.get("pin_clock_s"), "auto_clock_s": s.get("auto_clock_s"),
        })
    return {
        "start_offset_s": int(start_offset_s), "total_km": float(total_km), "use_target": bool(use_target),
        "points": points,
        # the hero shows where the pinned plan finishes when no objective is set
        "pins": sum(1 for p in points if p["pinned"]),
        "plan_total_s": points[-1]["elapsed_s"] if points and points[-1]["elapsed_s"] is not None else None,
        # the daylight-saving switches in the plan (a day past the finish for a time typed beyond it): the pin
        # sheet reads a typed clock with them, as the server reads a passage typed on race day
        "clock_switches": clock.switches(int((points[-1]["elapsed_s"] if points else 0) or 0) + 86400),
        "night": night_bands_km(sections, start_offset_s, use_target, dark, clock=clock),
        "blocks": [{"start_km": b["start_km"], "end_km": b["end_km"], "cls": b["cls"], "hr_cap": b.get("hr_cap"), "vam": b.get("vam_m_per_h")} for b in (blocks or [])],
        "scenarios": ({"fast_pct": scenarios["fast_pct"], "safe_pct": scenarios["safe_pct"],
                       "switch_cp_index": scenarios["switch"]["cp_index"] if scenarios.get("switch") else None} if scenarios else None),
        # long stretches without a full aid station: drawn as brackets under the profile
        "autonomy": [{"start_km": l["from_km"], "end_km": l["to_km"], "km": l["km"], "time_s": l["time_s"],
                      "from_name": l["from_name"], "to_name": l["to_name"]} for l in (autonomy or []) if l.get("alert")],
    }
