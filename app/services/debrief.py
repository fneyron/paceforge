"""Post-race debrief: the real activity laid over the plan, leg by leg.

For every leg between two checkpoints: planned time vs real moving time, the
stops (elapsed − moving), the mean heart rate, and ONE probable reason for the
gap — stops, pace above/below plan, heart rate over the ceiling, or a low heart
rate on a slow leg (legs or fuel). Plus the race-level pattern: where the time
went, and whether the athlete faded (fast early, slow late).
"""

from __future__ import annotations

STOP_SHARE = 0.40        # stops explain the gap when they are ≥ 40 % of it
STOP_MIN_S = 120         # …and at least 2 minutes
HR_OVER_MARGIN = 3       # bpm over the ceiling to call it "over"
HR_LOW_MARGIN = 12       # bpm under the ceiling, on a slow leg → legs/fuel
SIGNIFICANT_S = 90       # gaps under this are "on plan"


def _cumulative(splits: list) -> list[tuple[float, float, float, float | None]]:
    """(cum_dist_m, cum_moving_s, cum_elapsed_s, hr) per split end."""
    pts = [(0.0, 0.0, 0.0, None)]
    d = mt = et = 0.0
    for s in splits or []:
        dist = s.get("distance", 0) or 0
        m = s.get("moving_time", 0) or 0
        e = s.get("elapsed_time", 0) or m
        if dist <= 0 or m <= 0:
            continue
        d += dist
        mt += m
        et += max(e, m)
        pts.append((d, mt, et, s.get("average_heartrate")))
    return pts


def _interp(pts, dist_m: float, idx: int) -> float:
    if dist_m <= 0:
        return 0.0
    if dist_m >= pts[-1][0]:
        return pts[-1][idx]
    for i in range(1, len(pts)):
        if pts[i][0] >= dist_m:
            d0, d1 = pts[i - 1][0], pts[i][0]
            span = d1 - d0
            frac = (dist_m - d0) / span if span > 0 else 0
            return pts[i - 1][idx] + frac * (pts[i][idx] - pts[i - 1][idx])
    return pts[-1][idx]


def _mean_hr(pts, d0: float, d1: float) -> float | None:
    vals, ws = [], []
    for i in range(1, len(pts)):
        a, b = pts[i - 1][0], pts[i][0]
        hr = pts[i][3]
        if hr is None or b <= d0 or a >= d1:
            continue
        w = min(b, d1) - max(a, d0)
        if w > 0:
            vals.append(hr * w)
            ws.append(w)
    return round(sum(vals) / sum(ws)) if ws else None


def leg_debrief(
    splits_metric: list,
    sections: list[dict],
    route_total_km: float,
    use_target: bool,
    stop_s_per_aid: int = 0,
    hr_cap: int | None = None,
) -> dict:
    """Return {"legs": [...], "summary": {...}}.

    ``sections`` are the plan's passage sections (start_km/end_km/end_name,
    adjusted_time_s or predicted_time_s). The activity is mapped onto the
    route by proportion of total distance (GPS totals differ a little).
    """
    pts = _cumulative(splits_metric)
    total_d = pts[-1][0]
    if total_d <= 0 or not sections:
        return {"legs": [], "summary": None}
    scale = total_d / (route_total_km * 1000) if route_total_km else 1.0
    n = len(sections)
    legs = []
    cum_plan = cum_real = 0.0
    for i, s in enumerate(sections):
        d0 = float(s["start_km"]) * 1000 * scale
        d1 = float(s["end_km"]) * 1000 * scale
        plan = float(s["adjusted_time_s"] if (use_target and s.get("adjusted_time_s") is not None) else s["predicted_time_s"])
        moving = _interp(pts, d1, 1) - _interp(pts, d0, 1)
        elapsed = _interp(pts, d1, 2) - _interp(pts, d0, 2)
        stops = max(0.0, elapsed - moving)
        planned_stop = stop_s_per_aid if (i < n - 1 and s.get("end_checkpoint_index") is not None and s.get("kind") in ("water", "full", "base")) else 0
        delta_moving = moving - plan
        delta_total = elapsed - (plan + planned_stop)
        hr = _mean_hr(pts, d0, d1)
        dist_km = float(s.get("distance_km") or (s["end_km"] - s["start_km"]) or 0)
        real_pace = moving / dist_km if dist_km > 0 else 0
        plan_pace = plan / dist_km if dist_km > 0 else 0
        progress = float(s["end_km"]) / route_total_km if route_total_km else 0

        # one probable reason
        if abs(delta_total) < SIGNIFICANT_S:
            reason, tag = "dans le plan", "ok"
        elif delta_total > 0 and stops - planned_stop >= max(STOP_MIN_S, STOP_SHARE * delta_total):
            reason, tag = f"arrêts : {int(round((stops - planned_stop) / 60))} min de plus que prévu", "stops"
        elif delta_moving > 0 and hr_cap and hr is not None and hr <= hr_cap - HR_LOW_MARGIN:
            reason, tag = "allure : FC basse pour l'effort — jambes ou carburant", "fuel"
        elif delta_moving > 0:
            reason, tag = "allure plus lente que le plan", "slow"
        elif delta_moving < 0 and hr_cap and hr is not None and hr >= hr_cap + HR_OVER_MARGIN:
            reason, tag = f"plus vite que le plan, FC au-dessus du plafond ({hr} > {hr_cap})", "over"
        elif delta_moving < 0:
            reason, tag = ("parti plus vite que le plan" if progress < 0.35 else "plus vite que le plan"), "fast"
        else:
            reason, tag = "arrêts plus courts que prévu", "ok"

        cum_plan += plan + planned_stop
        cum_real += elapsed
        legs.append({
            "from_name": s["start_name"], "to_name": s["end_name"], "start_km": s["start_km"], "end_km": s["end_km"],
            "plan_s": int(round(plan)), "planned_stop_s": int(planned_stop),
            "moving_s": int(round(moving)), "elapsed_s": int(round(elapsed)), "stops_s": int(round(stops)),
            "delta_s": int(round(delta_total)), "delta_moving_s": int(round(delta_moving)),
            "hr": hr, "hr_over": bool(hr_cap and hr is not None and hr >= hr_cap + HR_OVER_MARGIN),
            "real_pace_s": int(round(real_pace)), "plan_pace_s": int(round(plan_pace)),
            "cum_plan_s": int(round(cum_plan)), "cum_real_s": int(round(cum_real)),
            "reason": reason, "tag": tag,
        })

    # race-level pattern
    total_delta = legs[-1]["cum_real_s"] - legs[-1]["cum_plan_s"] if legs else 0
    total_stops = sum(l["stops_s"] for l in legs)
    planned_stops = sum(l["planned_stop_s"] for l in legs)
    third = max(1, len(legs) // 3)
    early = legs[:third]
    late = legs[-third:]
    early_ratio = sum(l["moving_s"] for l in early) / max(1, sum(l["plan_s"] for l in early))
    late_ratio = sum(l["moving_s"] for l in late) / max(1, sum(l["plan_s"] for l in late))
    fade = late_ratio - early_ratio  # > 0 = slower late than early, relative to plan
    lost = sorted([l for l in legs if l["delta_s"] > SIGNIFICANT_S], key=lambda l: -l["delta_s"])[:3]
    gained = sorted([l for l in legs if l["delta_s"] < -SIGNIFICANT_S], key=lambda l: l["delta_s"])[:3]
    hr_early = [l["hr"] for l in early if l["hr"] is not None]
    hr_late = [l["hr"] for l in late if l["hr"] is not None]
    verdict = []
    if fade > 0.08:
        verdict.append("Le schéma classique : plus vite que le plan au début, plus lent à la fin"
                       + (f" (FC {int(sum(hr_early)/len(hr_early))} → {int(sum(hr_late)/len(hr_late))} bpm)" if hr_early and hr_late else "")
                       + ". Le début a coûté la fin.")
    elif fade < -0.05:
        verdict.append("Fin de course plus forte que le début : le pacing a tenu, tu as remonté.")
    if total_stops - planned_stops > max(600, 0.3 * abs(total_delta)) and total_delta > 0:
        verdict.append(f"Les arrêts pèsent {int(round((total_stops - planned_stops) / 60))} min de plus que prévu.")
    if hr_cap and any(l["hr_over"] for l in early):
        verdict.append("FC au-dessus du plafond sur les premiers tronçons.")
    if not verdict:
        verdict.append("Course proche du plan.")
    return {
        "legs": legs,
        "summary": {
            "total_delta_s": int(total_delta), "total_stops_s": int(total_stops), "planned_stops_s": int(planned_stops),
            "fade": round(fade, 3), "early_ratio": round(early_ratio, 3), "late_ratio": round(late_ratio, 3),
            "lost": lost, "gained": gained, "verdict": " ".join(verdict),
        },
    }
