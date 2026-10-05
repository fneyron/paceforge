"""Precompute, once per race, everything the gradient/fatigue fit needs.

For every UTMB Live running race of 40-180 km with a GPS track
(data/races/utmb/*/*.json.gz; bike / relay / team formats out, see
dataset.is_running) this runs the app's own course code once
(gpx.build_course_profile + race_simulator's _fine_terrain) and stores, per
1 km segment:

  * base_prod   the LEGACY terrain factor (the app before the first fit:
                calibrate.LEGACY_GRADIENT_FACTORS, read every 25 m over 100 m,
                x surface, x altitude);
  * base_cur    the CURRENT app's terrain factor (race_simulator's
                _DEFAULT_GRADIENT_FACTORS, same reading), i.e. exactly what
                the app multiplies by flat pace, fatigue and night today;
  * fat_prod    the legacy fatigue factor (progress, cumulative D+);
  * hist        a histogram of the 25 m sample points over (integer gradient
                in [-45, 50] %, LiveTrail terrain class), built with the SAME
                resampling / 100 m window / rounding as _fine_terrain, so any
                candidate gradient curve x terrain multipliers can be scored
                with one matrix product (no app call per candidate);
  * alt         the altitude factor, dist, start/end km, cumulative D+.

and per race the checkpoints (km on the course ruler), local start hour and the
finishers' moving times at each checkpoint (stops removed where timed,
calibrate.moving). Output: a pickle (default in the scratch fit dir).

--livetrail builds the same records for the LiveTrail archives (running
courses of 40-180 km, no GPS track): the profile is rebuilt from each
section's D+ / D- (two slopes per section, terrain class unknown), so they
only serve as an extra validation set (fatigue by hours, level of the plan by
section), never for the fit or the model selection.

Run:  .venv/bin/python scripts/race_data/fit_data.py [--out PATH] [--livetrail]
numpy is needed by fit_model.py only; this script is pure python + app code.
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import os
import pathlib
import pickle
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

import app.services.race_simulator as rs  # noqa: E402
import calibrate as cal  # noqa: E402
import dataset  # noqa: E402
from app.schemas.simulator import AthleteGradientProfile, GpxPoint  # noqa: E402
from app.services.gpx import build_course_profile  # noqa: E402

DEFAULT_OUT = os.environ.get(
    "PACEFORGE_FIT_DIR", "/tmp/claude-0/-home-user-paceforge/cb4ce7f8-b738-551f-a7c5-c130c0de27e8/scratchpad/fit"
) + "/fitdata.pkl"
G_MIN, G_MAX = -45, 50  # integer gradient bins (clamped)
CLASSES = ["R", "F", "T", "T1", "T2", "T3", "S", "U"]
CIDX = {c: i for i, c in enumerate(CLASSES)}
MIN_KM, MAX_KM = 40.0, 180.0


def _hist(course, track):
    """(n_seg x n_grade x n_class) counts of 25 m samples, as _fine_terrain samples them
    (``track`` None: no terrain classes, every sample is U)."""
    coords = course.route_coords or []
    if len(coords) < 50:
        return None
    xs, es = [], []
    j, x, end = 0, float(coords[0][2]), float(coords[-1][2])
    while x <= end:
        while j < len(coords) - 2 and coords[j + 1][2] < x:
            j += 1
        p, n = coords[j], coords[j + 1]
        span = n[2] - p[2]
        t = (x - p[2]) / span if span > 0 else 0.0
        xs.append(x)
        es.append(p[3] + t * (n[3] - p[3]))
        x += rs.FINE_STEP_KM
    tkm = [q[0] / 1000 for q in track] if track else []
    half = max(1, round(rs.FINE_WINDOW_KM / rs.FINE_STEP_KM / 2))
    ng = G_MAX - G_MIN + 1
    segs = course.segments
    H = [[[0] * len(CLASSES) for _ in range(ng)] for _ in segs]
    si = 0
    for i in range(len(xs)):
        a, b = max(0, i - half), min(len(xs) - 1, i + half)
        dm = (xs[b] - xs[a]) * 1000
        g = (es[b] - es[a]) / dm * 100 if dm > 0 else 0.0
        gi = max(G_MIN, min(G_MAX, round(g))) - G_MIN
        if track:
            k = bisect.bisect_right(tkm, xs[i]) - 1
            k = max(0, min(k, len(track) - 1))
            if k + 1 < len(track) and abs(tkm[k + 1] - xs[i]) < abs(tkm[k] - xs[i]):
                k += 1
            c = CIDX.get(track[k][4], CIDX["U"])
        else:
            c = CIDX["U"]
        while si < len(segs) - 1 and xs[i] >= segs[si].end_km:
            si += 1
        if xs[i] >= segs[si].start_km and xs[i] < segs[si].end_km:
            H[si][gi][c] += 1
    for s, seg in zip(H, segs):  # a segment with no sample: its mean gradient, unknown terrain
        if not any(any(r) for r in s):
            s[max(G_MIN, min(G_MAX, round(seg.avg_gradient_pct))) - G_MIN][CIDX["U"]] = 1
    return H


# the app's generic curve as it stands (what "current" scores)
CURRENT_PROFILE = AthleteGradientProfile(flat_pace_s_per_km=360, gradient_factors=dict(rs._DEFAULT_GRADIENT_FACTORS),
                                         data_points=0, sport_types_used=[])


def build(path: pathlib.Path, source: str = "utmb"):
    raw = json.loads(gzip.decompress(path.read_bytes()))
    name = raw["summary"].get("name")
    if not dataset.is_running(name, path.name, path.parent.name):
        return None, "not a solo running race"
    race = dataset.load_utmb(path) if source == "utmb" else dataset.load_livetrail(path)
    if not race or len(race["runners"]) < 10:
        return None, "no runners"
    if source == "utmb" and not race["has_track"]:
        return None, "no track"
    km = race["cps"][-1].get("km_official", race["cps"][-1]["km"])
    if not (MIN_KM <= km <= MAX_KM):
        return None, f"{km:.0f} km out of range"
    dplus = raw["summary"].get("elevationGain") or raw["summary"].get("elevation_gain") or race["cps"][-1].get("dplus")
    if not dataset.plausible_running(race, dplus):
        return None, "median effort speed of a bike / broken timing"
    track = raw.get("track") if source == "utmb" else None
    pts = [GpxPoint(**p) for p in race["points"]]
    course = build_course_profile(pts, name=race["id"])
    course.surface_km = race["surface"]
    fine = rs._fine_terrain(course, cal.PROFILE)
    fine_cur = rs._fine_terrain(course, CURRENT_PROFILE)
    if fine is None or fine_cur is None:
        return None, "no fine terrain"
    segs = course.segments
    total = course.total_distance_km
    scale = total / race["cps"][-1]["km"] if race["cps"][-1]["km"] else 1.0
    alt = [rs._altitude_factor((s.min_elevation + s.max_elevation) / 2) for s in segs]
    gain, fat = 0.0, []
    for s in segs:
        gain += s.elevation_gain
        fat.append(cal.legacy_fatigue(s.end_km / total, total, gain))
    runners = []
    for r in race["runners"]:
        mv = cal.moving(r)
        if mv[-1] and mv[-1] > 0 and all(a is None or a >= 0 for a in mv):
            runners.append({"bib": r["bib"], "mv": mv, "arr": r["arr"], "time_s": r["time_s"]})
    prep = {"total": total, "cp_km": [c["km"] * scale for c in race["cps"]], "dist": [s.distance_m / 1000 for s in segs],
            "end": [s.end_km for s in segs], "start": [s.start_km for s in segs], "gain": [s.elevation_gain for s in segs],
            "base": [t * a for t, a in zip(fine, alt)], "base_cur": [t * a for t, a in zip(fine_cur, alt)]}
    out = {
        "id": race["id"], "source": source, "event": path.parent.name.rsplit("_", 1)[0], "year": path.parent.name.rsplit("_", 1)[1],
        "name": race["name"], "km": km, "dplus": dplus,
        "start": race["start"], "start_hour": cal.start_hour(race), "has_clock": bool(race["start"]), "cps": race["cps"], "prep": prep,
        "fat_prod": fat, "alt": alt, "hist": _hist(course, track), "runners": runners,
        # the app's fatigue / night when this record was built (base_cur's companions)
        "app": {"fatigue_c": rs.FATIGUE_C, "fatigue_q": rs.FATIGUE_Q, "fatigue_fresh": rs.FATIGUE_FRESH, "night": rs.NIGHT_FACTOR},
    }
    return out, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--livetrail", action="store_true", help="LiveTrail archives (validation only) instead of UTMB Live")
    a = ap.parse_args()
    source = "livetrail" if a.livetrail else "utmb"
    out = a.out or (DEFAULT_OUT.replace("fitdata.pkl", "livetrail.pkl") if a.livetrail else DEFAULT_OUT)
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    res, t0 = [], time.time()
    for path in sorted((dataset.ROOT / source).glob("*/*.json.gz")):
        try:
            r, why = build(path, source)
        except Exception as e:  # noqa: BLE001 — one bad archive must not stop the run
            r, why = None, f"error {e}"
        print(f"{path.parent.name}/{path.name}: {why}" + (f" ({len(r['runners'])} runners)" if r else ""), flush=True)
        if r:
            res.append(r)
    pickle.dump(res, open(out, "wb"))
    print(f"{len(res)} races -> {out} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
