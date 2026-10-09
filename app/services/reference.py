"""Reference finisher: another runner's splits aligned on your route.

Paste a Strava activity URL (fetched through the API when the activity is
public) or the passage table copied from a live-tracking page (UTMB Live,
LiveTrail…). The points are aligned on the route by distance and compared
with your plan at every checkpoint, then the three places where the reference
gained or lost the most against your plan are called out.
"""

from __future__ import annotations

import re

_URL_RE = re.compile(r"strava\.com/activities/(\d+)")
_TIME_RE = re.compile(r"(?<![\d:])(\d{1,3}):(\d{2})(?::(\d{2}))?(?![\d:])")
_KM_RE = re.compile(r"(?i)(?:km\s*)?(\d{1,3}(?:[.,]\d)?)\s*km|\bkm\s*(\d{1,3}(?:[.,]\d)?)")


def parse_strava_activity_id(text: str) -> int | None:
    m = _URL_RE.search(text or "")
    return int(m.group(1)) if m else None


def _time_to_s(h: str, m: str, s: str | None) -> int:
    return int(h) * 3600 + int(m) * 60 + (int(s) if s else 0)


def parse_pasted_splits(text: str) -> list[dict]:
    """Lines like ``Ravito X  km 32  4:12:05``, ``32.4 km 4:12``, ``Col Y 04:12:05``.

    Returns [{name, km|None, time_s}] in file order. The time is the RACE time
    (elapsed since the start) — live-tracking pages show it next to the clock
    time; the time written with seconds (HH:MM:SS) wins, else the first one.
    """
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        times = _TIME_RE.findall(line)
        if not times:
            continue
        # Live-tracking pages print the race time with seconds (HH:MM:SS) next
        # to the clock time without (HH:MM): prefer the one with seconds, else
        # the first time on the line.
        with_secs = [t for t in times if t[2]]
        chosen = with_secs[0] if with_secs else times[0]
        time_s = _time_to_s(*chosen)
        km = None
        km_m = _KM_RE.search(line)
        if km_m:
            km = float((km_m.group(1) or km_m.group(2)).replace(",", "."))
        name = _TIME_RE.sub("", line)
        name = _KM_RE.sub("", name)
        name = re.sub(r"[\t|;]+", " ", name)
        name = re.sub(r"\s{2,}", " ", name).strip(" -–·:")
        out.append({"name": name[:60], "km": km, "time_s": int(time_s)})
    return out


def points_from_splits_metric(splits: list, total_km: float | None = None) -> list[dict]:
    """Strava per-km splits → cumulative (km, elapsed_s) points."""
    d = t = 0.0
    pts = []
    for s in splits or []:
        dist = s.get("distance", 0) or 0
        e = s.get("elapsed_time") or s.get("moving_time") or 0
        if dist <= 0 or e <= 0:
            continue
        d += dist / 1000
        t += e
        pts.append({"name": "", "km": round(d, 2), "time_s": int(t)})
    return pts


def align_reference(points: list[dict], checkpoints: list[dict], route_total_km: float, ref_total_km: float | None = None) -> list[dict]:
    """Time of the reference at each of MY checkpoints.

    - points with a km: interpolated on distance (scaled if the reference
      recorded a different total distance);
    - points without a km: matched to a checkpoint by name (case-insensitive
      containment), else by order when counts match.
    Returns [{name, km, time_s|None, matched_by}] in checkpoint order.
    """
    cps = sorted(checkpoints, key=lambda c: float(c["distance_km"]))
    with_km = sorted([p for p in points if p.get("km") is not None], key=lambda p: p["km"])
    if ref_total_km and route_total_km and with_km:
        scale = float(route_total_km) / float(ref_total_km)
    else:
        scale = 1.0
    named = [p for p in points if p.get("km") is None]

    def interp(km: float) -> int | None:
        if not with_km:
            return None
        pts = [(0.0, 0)] + [(p["km"] * scale, p["time_s"]) for p in with_km]
        if km <= 0:
            return 0
        if km > pts[-1][0] + 0.5:
            return None
        for (k0, t0), (k1, t1) in zip(pts, pts[1:], strict=False):
            if k1 >= km:
                span = k1 - k0
                return int(round(t0 + (km - k0) / span * (t1 - t0))) if span > 0 else int(t1)
        return int(pts[-1][1])

    out = []
    for i, cp in enumerate(cps):
        km = float(cp["distance_km"])
        t = interp(km) if with_km else None
        by = "km" if t is not None else None
        if t is None and named:
            key = (cp.get("name") or "").strip().lower()
            hit = next((p for p in named if key and (key in p["name"].lower() or p["name"].lower() in key) and len(p["name"]) > 2), None)
            if hit is None and len(named) == len(cps):
                hit = named[i]
                by = "ordre"
            elif hit is not None:
                by = "nom"
            if hit is not None:
                t = int(hit["time_s"])
        out.append({"name": cp.get("name", ""), "km": km, "time_s": t, "matched_by": by})
    return out


def _to_min(s: float) -> int:
    """Seconds → whole minutes in seconds, rounded as the times are shown (to the nearest minute, as the plan)."""
    return int((float(s) + 30) // 60) * 60


def compare_to_plan(aligned: list[dict], plan_sections: list[dict], use_target: bool, ref_total_s: int | None, plan_total_s: int | None) -> dict:
    """Per-leg gaps (reference − my plan) and the 3 biggest gains/losses.

    The gaps are taken between times rounded to the minute, as they are shown: on every row the
    reference minus the plan is the gap, and a leg's gap is the change of the gap since the
    previous point both sides have a time at (a point without a reference time is skipped)."""
    plan_at = {}
    for s in plan_sections:
        cum = s["adjusted_cumulative_time_s"] if (use_target and s.get("adjusted_cumulative_time_s") is not None) else s["cumulative_time_s"]
        if s.get("end_checkpoint_index") is not None:
            plan_at[round(float(s["end_km"]), 1)] = float(cum)
    rows, prev_ref, prev_plan, prev_name = [], 0, 0, "Départ"
    for a in aligned:
        p = plan_at.get(round(a["km"], 1))
        if a["time_s"] is None or p is None:
            rows.append({**a, "plan_s": int(p) if p is not None else None, "delta_s": None, "leg_delta_s": None, "from_name": prev_name})
            continue
        ref_m, plan_m = _to_min(a["time_s"]), _to_min(p)
        leg_ref, leg_plan = ref_m - prev_ref, plan_m - prev_plan
        rows.append({**a, "plan_s": int(p), "delta_s": ref_m - plan_m, "leg_ref_s": leg_ref, "leg_plan_s": leg_plan,
                     "leg_delta_s": leg_ref - leg_plan, "from_name": prev_name})
        prev_ref, prev_plan, prev_name = ref_m, plan_m, a["name"]
    if ref_total_s and plan_total_s:
        ref_m, plan_m = _to_min(ref_total_s), _to_min(plan_total_s)
        rows.append({"name": "Arrivée", "km": None, "time_s": int(ref_total_s), "plan_s": int(plan_total_s),
                     "delta_s": ref_m - plan_m, "leg_ref_s": ref_m - prev_ref,
                     "leg_plan_s": plan_m - prev_plan, "leg_delta_s": (ref_m - prev_ref) - (plan_m - prev_plan),
                     "from_name": prev_name, "matched_by": "total"})
    legs = [r for r in rows if r.get("leg_delta_s") is not None]
    faster = sorted([r for r in legs if r["leg_delta_s"] < 0], key=lambda r: r["leg_delta_s"])[:3]
    slower = sorted([r for r in legs if r["leg_delta_s"] > 0], key=lambda r: -r["leg_delta_s"])[:3]
    return {"rows": rows, "faster": faster, "slower": slower}
