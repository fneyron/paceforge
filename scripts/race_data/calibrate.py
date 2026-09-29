"""Score and fit the plan's fatigue shape on the race dataset.

The terrain (gradient every 25 m, surface) is computed once per race with
the app's own code; fatigue, night and level are then replayed quickly for
each candidate setting. A runner's predicted checkpoint times are levelled
on his own finish time: what is scored is the SHAPE of the plan, the part
the race calibration does not set. Stops are removed wherever departures
were timed, so the shape compares running with running.
"""
from __future__ import annotations

import math
import statistics
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import app.services.race_simulator as rs  # noqa: E402
from app.schemas.simulator import AthleteGradientProfile, CourseProfile, GpxPoint  # noqa: E402
from app.services.gpx import build_course_profile  # noqa: E402

PROFILE = AthleteGradientProfile(flat_pace_s_per_km=360, gradient_factors=rs._DEFAULT_GRADIENT_FACTORS, data_points=0, sport_types_used=[])
MAX_KM = 180.0  # beyond: multi-day, sleep-dominated (PTL, Tor…)


def prepare(race: dict) -> dict | None:
    pts = [GpxPoint(**p) for p in race["points"]]
    if len(pts) < 20:
        return None
    course = build_course_profile(pts, name=race["id"])
    if race.get("surface"):
        course.surface_km = race["surface"]
    fine = rs._fine_terrain(course, PROFILE)
    segs = course.segments
    terr = fine if fine is not None else [rs._get_factor(PROFILE, s.avg_gradient_pct) for s in segs]
    total = course.total_distance_km
    scale = total / race["cps"][-1]["km"] if race["cps"][-1]["km"] else 1.0
    return {
        "total": total, "cp_km": [c["km"] * scale for c in race["cps"]],
        "dist": [s.distance_m / 1000 for s in segs], "end": [s.end_km for s in segs], "start": [s.start_km for s in segs],
        "gain": [s.elevation_gain for s in segs],
        "base": [t * rs._altitude_factor((s.min_elevation + s.max_elevation) / 2) for t, s in zip(terr, segs)],
    }


def simulate(prep: dict, flat: float, fatigue, start_hour: float = 6.0) -> list[float]:
    """Moving time (s) at each checkpoint km."""
    total, cum, gain, t_at = prep["total"], 0.0, 0.0, []
    ends = []
    for d, e, g, b in zip(prep["dist"], prep["end"], prep["gain"], prep["base"]):
        gain += g
        f = b * fatigue(e / total, total, gain, cum / 3600) * rs._night_penalty(cum, start_hour)
        cum += flat * f * d
        ends.append((e, cum))
    out, j, prev_e, prev_t = [], 0, 0.0, 0.0
    for km in prep["cp_km"]:
        while j < len(ends) and ends[j][0] < km:
            prev_e, prev_t = ends[j]
            j += 1
        if j < len(ends):
            e, t = ends[j]
            out.append(prev_t + (t - prev_t) * ((km - prev_e) / (e - prev_e) if e > prev_e else 1))
        else:
            out.append(cum)
    return out


def moving(r: dict) -> list[float | None]:
    """Arrival times minus the stops already made (where departures are timed)."""
    out, stops = [], 0.0
    for a, o in zip(r["arr"], r["dep"]):
        out.append(a - stops if a is not None else None)
        if a is not None and o is not None and 0 < o - a < 6 * 3600:
            stops += o - a
    return out


def start_hour(race: dict) -> float:
    s = race.get("start")
    if not s:
        return 6.0
    h, m = int(s[11:13]), int(s[14:16])
    return h + m / 60  # UTC here; local offset unknown → night is approximate


GROUPS = [(0, 12), (12, 18), (18, 24), (24, 30), (30, 99)]


def score_race(race: dict, prep: dict, fatigue, groups=GROUPS) -> dict:
    """{group: (n, mean |gap| min, median signed gap per checkpoint)} for one race."""
    sh = start_hour(race)
    res = {}
    runs = [(r, moving(r)) for r in race["runners"]]
    for lo, hi in groups:
        grp = [(r, mv) for r, mv in runs if mv[-1] and lo * 3600 <= mv[-1] < hi * 3600]
        if len(grp) < 3:
            continue
        med = statistics.median(mv[-1] for _, mv in grp)
        flat = 360.0
        for _ in range(3):
            pred = simulate(prep, flat, fatigue, sh)
            if pred[-1] <= 0:
                break
            flat *= med / pred[-1]
        gaps, signed = [], [[] for _ in pred]
        for r, mv in grp:
            k = mv[-1] / pred[-1]
            for i, (p, a) in enumerate(zip(pred[:-1], mv[:-1])):
                if a is None or i == 0:
                    continue
                g = (p * k - a) / 60
                gaps.append(abs(g))
                signed[i].append(g)
        if gaps:
            res[f"{lo}-{hi}h"] = (len(grp), statistics.mean(gaps), [round(statistics.median(s)) if s else None for s in signed], med / 3600)
    return res


def current_fatigue(progress, total_km, gain, hours, tilt=None):
    return rs._fatigue_factor(progress, total_km, gain, tilt)
