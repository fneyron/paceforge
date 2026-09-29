"""Read the race dataset (data/races) into one shape for model calibration.

Each race: checkpoints (km, altitude, cumulative D+ when known), a course
profile the simulator can run (the UTMB GPS track with LiveTrail terrain, or
for LiveTrail archives a profile rebuilt from each section's D+ and D-), and
the finishers' arrival (and, where timed, departure) seconds at each
checkpoint.
"""
from __future__ import annotations

import gzip
import json
import pathlib
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2] / "data" / "races"
ROAD, TRACK = {"R"}, {"F"}


def _iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _profile_from_track(track):
    """GpxPoint-like dicts from the compact track, and surface shares per km."""
    pts = [{"lat": p[2], "lon": p[3], "elevation": p[1], "distance_from_start": p[0]} for p in track]
    n = int(track[-1][0] / 1000) + 1
    cnt = [[0, 0, 0] for _ in range(n)]
    for p in track:
        cls = 0 if p[4] in ROAD else 1 if p[4] in TRACK else 2
        cnt[int(p[0] / 1000)][cls] += 1
    surface = [[c / sum(x) for c in x] if sum(x) else [0.0, 0.0, 1.0] for x in cnt]
    return pts, surface


def _profile_from_checkpoints(cps):
    """Two-slope profile per section honouring its D+ and D- (no GPS track)."""
    pts, dist = [], 0.0
    lat, lon = cps[0].get("lat") or 45.0, cps[0].get("lon") or 6.0
    alt = cps[0]["alt"] or 0.0
    pts.append({"lat": lat, "lon": lon, "elevation": alt, "distance_from_start": 0.0})
    for a, b in zip(cps, cps[1:]):
        d = (b["km"] - a["km"]) * 1000
        if d <= 0:
            continue
        gain = max(0.0, (b.get("dplus") or 0) - (a.get("dplus") or 0)) if b.get("dplus") is not None else max(0.0, (b["alt"] or 0) - (a["alt"] or 0))
        loss = max(0.0, gain - ((b["alt"] or 0) - (a["alt"] or 0)))
        up = d * gain / (gain + loss) if gain + loss > 0 else d / 2
        for frac_d, target in ((up, alt + gain), (d - up, alt + gain - loss)):
            steps = max(1, int(frac_d / 50))
            start_alt = alt
            for s in range(1, steps + 1):
                dist += frac_d / steps
                alt = start_alt + (target - start_alt) * s / steps
                pts.append({"lat": lat, "lon": lon, "elevation": alt, "distance_from_start": dist})
            alt = target
    return pts, None


def load_utmb(path: pathlib.Path) -> dict | None:
    d = json.loads(gzip.decompress(path.read_bytes()))
    points = [p for p in d["points"] if p.get("distance") is not None]
    if len(points) < 3 or not d["runners"]:
        return None
    total_official = points[-1]["distance"] / 1000
    track = d.get("track")
    if track and len(track) > 50:
        pts, surface = _profile_from_track(track)
        scale = (track[-1][0] / 1000) / total_official if total_official else 1.0
    else:
        cps0 = [{"km": p["distance"] / 1000, "alt": p["altitude"], "dplus": p.get("gainElevation"), "lat": p.get("latitude"), "lon": p.get("longitude")} for p in points]
        pts, surface = _profile_from_checkpoints(cps0)
        scale = 1.0
    cps = [{"pid": p["pointId"], "name": p.get("shortName") or p.get("name"), "km": p["distance"] / 1000 * scale, "km_official": p["distance"] / 1000,
            "alt": p.get("altitude"), "dplus": p.get("gainElevation")} for p in points]
    start = _iso((d.get("info") or {}).get("startDate"))
    runners = []
    for bib, r in d["runners"].items():
        by = {x[0]: x for x in r["passings"]}
        t0 = _iso((by.get(points[0]["pointId"]) or [None, None, None])[2]) or start
        if not t0:
            continue
        arr, dep = [], []
        for c in cps:
            x = by.get(c["pid"])
            a = _iso(x[1] or x[2]) if x else None
            o = _iso(x[2]) if x and x[1] and x[2] else None
            arr.append((a - t0).total_seconds() if a else None)
            dep.append((o - t0).total_seconds() if o else None)
        if arr[-1] and arr[-1] > 0:
            runners.append({"bib": bib, "sex": r.get("sex"), "index": r.get("index"), "time_s": arr[-1], "arr": arr, "dep": dep})
    tz = ((d.get("info") or {}).get("eventTimezone") or {}).get("eventTimezone")
    local = start
    if start and tz:
        try:
            from zoneinfo import ZoneInfo

            local = start.astimezone(ZoneInfo(tz))
        except Exception:
            local = start
    return {"id": f"utmb/{path.parent.name}/{path.stem.replace('.json', '')}", "source": "utmb", "name": d["summary"].get("name"),
            "start": local.isoformat() if local else None, "cps": cps, "points": pts, "surface": surface,
            "has_track": bool(track), "runners": runners}


def load_livetrail(path: pathlib.Path) -> dict | None:
    d = json.loads(gzip.decompress(path.read_bytes()))
    cps = [{"pid": p["id"], "name": p["name"], "km": p["km"], "alt": p["alt"], "dplus": p["dplus"], "lat": p["lat"], "lon": p["lon"]}
           for p in d["points"] if p.get("km") is not None and p.get("alt") is not None]
    if len(cps) < 3 or not d["runners"]:
        return None
    pts, surface = _profile_from_checkpoints(cps)
    runners = []
    for bib, r in d["runners"].items():
        by = {x[0]: x for x in r["passings"]}
        arr = [(by.get(c["pid"]) or [None, None, None])[1] for c in cps]
        dep = [(by.get(c["pid"]) or [None, None, None])[2] for c in cps]
        dep = [o if (o is not None and a is not None and o > a) else None for a, o in zip(arr, dep)]
        if arr[-1]:
            runners.append({"bib": bib, "sex": r.get("sex"), "time_s": arr[-1], "arr": arr, "dep": dep})
    return {"id": f"livetrail/{path.parent.name}/{path.stem.replace('.json', '')}", "source": "livetrail", "name": d["summary"].get("name"),
            "start": None, "cps": cps, "points": pts, "surface": surface, "has_track": False, "runners": runners}


def races(sources=("utmb", "livetrail")):
    for src in sources:
        for path in sorted((ROOT / src).glob("*/*.json.gz")):
            try:
                r = load_utmb(path) if src == "utmb" else load_livetrail(path)
            except Exception as e:  # a malformed archive must not stop the calibration
                print("skip", path, e)
                continue
            if r and len(r["runners"]) >= 10:
                yield r
