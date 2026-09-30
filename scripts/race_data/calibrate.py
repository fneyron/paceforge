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

# "production" in fit_results.txt is the app BEFORE it adopted the fit (hours
# fatigue, fitted curve, night 1.0325): frozen here so the scripts keep
# reproducing that baseline once the app has moved on.
LEGACY_GRADIENT_FACTORS = {
    -20: 1.10, -15: 0.92, -10: 0.82, -8: 0.80, -6: 0.80, -5: 0.82, -4: 0.85, -3: 0.88, -2: 0.92, -1: 0.96,
    0: 1.0, 1: 1.05, 2: 1.10, 3: 1.16, 4: 1.23, 5: 1.31, 6: 1.40, 7: 1.50, 8: 1.62, 10: 1.85, 12: 2.10,
    15: 2.45, 20: 3.10, 25: 3.90, 30: 4.70,
}
LEGACY_FATIGUE_SCALE = 1.3
LEGACY_FATIGUE_TILT = 0.22


def legacy_fatigue(progress, total_km, gain, tilt=None):
    """The app's fatigue before the fit: progress² scaled by the distance, tilt, glycogen, D+."""
    tilt = LEGACY_FATIGUE_TILT if tilt is None else tilt
    if total_km < 20:
        return 1.0
    base = 1.0 + 0.12 * LEGACY_FATIGUE_SCALE * (total_km / 42) * progress ** 2 + tilt * (progress - 0.45)
    glycogen = min(35 / total_km, 0.7)
    if progress > glycogen:
        base += 0.04 * LEGACY_FATIGUE_SCALE * ((progress - glycogen) / (1 - glycogen)) ** 1.5
    if gain > 0:
        base += gain / 8000 * 0.02
    return max(0.85, min(base, 1.55))


def legacy_night(cum_s, start_hour=6.0):
    """The app's night penalty before the fit: 8 % at night, 3 % at dusk / dawn."""
    h = (start_hour + cum_s / 3600) % 24
    if h >= 21 or h < 6:
        return 1.08
    if 20 <= h < 21 or 6 <= h < 7:
        return 1.03
    return 1.0


night_penalty = legacy_night  # what simulate() applies (fit_fatigue_duration swaps it)
PROFILE = AthleteGradientProfile(flat_pace_s_per_km=360, gradient_factors=LEGACY_GRADIENT_FACTORS, data_points=0, sport_types_used=[])
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
        f = b * fatigue(e / total, total, gain, cum / 3600) * night_penalty(cum, start_hour)
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
    return h + m / 60  # local (event timezone) when the archive gives one, else UTC


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
    return legacy_fatigue(progress, total_km, gain, tilt)
