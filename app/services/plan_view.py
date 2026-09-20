"""Data the plan page draws on the elevation profile: where the night falls
along the course, the pacing terrain bands, checkpoint clocks and cutoffs.
Computed once per plan refresh, sent as JSON next to the table."""

from __future__ import annotations

NIGHT_START_H = 21.0
NIGHT_END_H = 6.0


def _km_clock_curve(sections: list[dict], start_offset_s: int, use_target: bool) -> list[tuple[float, float]]:
    pts = [(0.0, float(start_offset_s))]
    for s in sections:
        clock = s.get("adjusted_clock_time_s") if (use_target and s.get("adjusted_clock_time_s") is not None) else s.get("clock_time_s")
        if clock is None:
            continue
        pts.append((float(s["end_km"]), float(clock)))
    return pts


def night_bands_km(sections: list[dict], start_offset_s: int, use_target: bool) -> list[dict]:
    """[{start_km, end_km}] where the athlete is on the course between 21:00
    and 06:00. Clock is linear inside a section; the curve is walked hour by
    hour so multi-night races come out right."""
    curve = _km_clock_curve(sections, start_offset_s, use_target)
    if len(curve) < 2:
        return []
    end_clock = curve[-1][1]

    def km_at(clock: float) -> float:
        for (k0, c0), (k1, c1) in zip(curve, curve[1:], strict=False):
            if c1 >= clock:
                span = c1 - c0
                return k0 + (clock - c0) / span * (k1 - k0) if span > 0 else k1
        return curve[-1][0]

    def is_night(clock: float) -> bool:
        h = (clock % 86400) / 3600
        return h >= NIGHT_START_H or h < NIGHT_END_H

    bands = []
    t = curve[0][1]
    open_km = km_at(t) if is_night(t) else None
    # walk to each 21:00 / 06:00 boundary up to the finish
    day0 = int(t // 86400)
    boundaries = []
    for day in range(day0, int(end_clock // 86400) + 2):
        boundaries += [day * 86400 + NIGHT_END_H * 3600, day * 86400 + NIGHT_START_H * 3600]
    for b in sorted(boundaries):
        if b <= t or b >= end_clock:
            continue
        if is_night(b) and open_km is None:
            open_km = km_at(b)
        elif not is_night(b) and open_km is not None:
            bands.append({"start_km": round(open_km, 2), "end_km": round(km_at(b), 2)})
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
) -> dict:
    points = []
    cum_stops = 0.0
    for s in sections:
        clock = s.get("adjusted_clock_time_s") if (use_target and s.get("adjusted_clock_time_s") is not None) else s.get("clock_time_s")
        cum = s.get("adjusted_cumulative_time_s") if (use_target and s.get("adjusted_cumulative_time_s") is not None) else s.get("cumulative_time_s")
        stops = (float(clock) - start_offset_s - float(cum)) if (clock is not None and cum is not None) else 0.0
        points.append({
            "km": float(s["end_km"]), "start_km": float(s["start_km"]), "name": s["end_name"],
            "cp_index": s.get("end_checkpoint_index"), "clock_s": int(clock) if clock is not None else None,
            "cum_s": float(cum) if cum is not None else None, "stops_s": round(stops),
            "kind": s.get("kind") or "none", "cutoff_clock": s.get("cutoff_clock"),
            "cutoff_margin_s": s.get("cutoff_margin_s"), "temp": s.get("temperature_c"), "code": s.get("weather_code"),
            "drop_bag": bool(s.get("drop_bag")), "crew": bool(s.get("crew")),
        })
    return {
        "start_offset_s": int(start_offset_s), "total_km": float(total_km), "use_target": bool(use_target),
        "points": points,
        "night": night_bands_km(sections, start_offset_s, use_target),
        "blocks": [{"start_km": b["start_km"], "end_km": b["end_km"], "cls": b["cls"], "hr_cap": b.get("hr_cap"), "vam": b.get("vam_m_per_h")} for b in (blocks or [])],
        "scenarios": ({"fast_pct": scenarios["fast_pct"], "safe_pct": scenarios["safe_pct"],
                       "switch_cp_index": scenarios["switch"]["cp_index"] if scenarios.get("switch") else None} if scenarios else None),
        # long stretches without a full aid station: drawn as brackets under the profile
        "autonomy": [{"start_km": l["from_km"], "end_km": l["to_km"], "km": l["km"], "time_s": l["time_s"],
                      "from_name": l["from_name"], "to_name": l["to_name"]} for l in (autonomy or []) if l.get("alert")],
    }
