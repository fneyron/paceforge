"""Collect LiveTrail archive data (livetrail.net/histo) for the calibration dataset.

For each archived edition and each course of 40 km or more: the checkpoints
(km, cumulative D+, altitude, position, cutoff) and the passing times of the
finishers — arrival and, where timed, departure (stops at aid stations). All
finishers, or an even sample by rank above --max-runners. No names are kept:
bib, sex, category, rank and times only. Stored compressed under
data/races/livetrail/<edition>/<course>.json.gz; resumable.

    python scripts/race_data/collect_livetrail.py --events events.json --years 2022 2023 2024 2025 [--meta-only]
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import re
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2] / "data" / "races" / "livetrail"
MIN_KM = 40.0


def fetch(url: str, delay: float) -> str | None:
    for attempt in range(3):
        r = subprocess.run(["curl", "-s", "-m", "60", "-A", "PaceForge research (paceforge.fr)", "-w", "\n%{http_code}", url], capture_output=True)
        time.sleep(delay)
        body, _, code = r.stdout.decode("utf-8", "replace").rpartition("\n")
        if code == "200" and body.lstrip().startswith("<?xml"):
            return body
        if code == "404":
            return None
        time.sleep(2 * (attempt + 1))
    return None


def xml(text: str) -> ET.Element | None:
    text = re.sub(r"<\?xml-stylesheet[^>]*\?>", "", text)
    try:
        return ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return None


def hms(s: str | None) -> int | None:
    if not s:
        return None
    try:
        parts = [int(x) for x in s.split(":")]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def num(x: str | None) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def edition_courses(base: str, delay: float) -> dict:
    """{course id: {"name", "points": [...]}} from parcours.php."""
    txt = fetch(f"{base}/parcours.php", delay)
    root = xml(txt) if txt else None
    if root is None:
        return {}
    names = {c.get("id"): c.get("n") for c in root.iter("c") if c.get("id")}
    out = {}
    for pts in root.iter("points"):
        cid = pts.get("course")
        points = [{
            "id": p.get("idpt"), "name": p.get("n"), "km": num(p.get("km")), "dplus": num(p.get("d")), "alt": num(p.get("a")),
            "lat": num(p.get("lat")), "lon": num(p.get("lon")), "cutoff": p.get("b") or None, "type": p.get("t"),
        } for p in pts.iter("pt")]
        if cid and points:
            out[cid] = {"name": names.get(cid), "points": points}
    return out


def finishers(base: str, cid: str, delay: float) -> list[dict]:
    txt = fetch(f"{base}/classement.php?course={cid}&cat=scratch", delay)
    root = xml(txt) if txt else None
    if root is None:
        return []
    cl = root.find(".//classement")
    rows = []
    for c in (cl if cl is not None else []):
        if c.tag != "c" or not c.get("doss"):
            continue
        t = hms(c.get("tps"))
        if t:
            rows.append({"bib": c.get("doss"), "rank": int(c.get("class") or 0) or None, "sex": c.get("sx"), "cat": c.get("cat"), "time_s": t})
    return rows


def passings(base: str, bib: str, delay: float) -> list | None:
    txt = fetch(f"{base}/coureur.php?rech={bib}", delay)
    root = xml(txt) if txt else None
    if root is None:
        return None
    ps = root.find(".//pass")
    if ps is None:
        return None
    out = []
    for e in ps.iter("e"):
        tn, tnd = num(e.get("tn")), num(e.get("tnd"))
        out.append([e.get("idpt"), round(tn * 3600) if tn is not None else None, round(tnd * 3600) if tnd is not None else None])
    return out


def collect_course(ed: str, base: str, cid: str, course: dict, max_runners: int, delay: float, meta_only: bool) -> dict | None:
    path = ROOT / ed / f"{re.sub(r'[^A-Za-z0-9_-]', '_', cid)}.json.gz"
    partial = path.with_suffix("").with_suffix(".partial.json")
    if path.exists():
        return json.loads(gzip.decompress(path.read_bytes()))["summary"]
    kms = [p["km"] for p in course["points"] if p["km"] is not None]
    if not kms or max(kms) < MIN_KM:
        return None
    fins = finishers(base, cid, delay)
    summary = {"edition": ed, "course": cid, "name": course["name"], "distance_km": max(kms),
               "elevation_gain": max((p["dplus"] or 0) for p in course["points"]), "checkpoints": len(course["points"]), "finishers": len(fins)}
    if meta_only or len(fins) < 10:
        return summary
    sample = fins
    if len(fins) > max_runners:
        step = len(fins) / max_runners
        sample = [fins[int(i * step)] for i in range(max_runners)]
    runners = json.loads(partial.read_text()) if partial.exists() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(sample):
        if f["bib"] in runners:
            continue
        p = passings(base, f["bib"], delay)
        if p is None:
            continue
        runners[f["bib"]] = f | {"passings": p}
        if i % 50 == 0:
            partial.write_text(json.dumps(runners))
    record = {"summary": summary | {"runners_sampled": len(runners)}, "base": base, "points": course["points"], "runners": runners}
    path.write_bytes(gzip.compress(json.dumps(record, separators=(",", ":")).encode()))
    partial.unlink(missing_ok=True)
    return record["summary"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--years", nargs="*", type=int, default=[2022, 2023, 2024, 2025])
    ap.add_argument("--skip-events", nargs="*", default=[], help="event ids already collected elsewhere (UTMB Live)")
    ap.add_argument("--max-runners", type=int, default=200)
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--meta-only", action="store_true")
    ap.add_argument("--only", nargs="*", help="edition/course ids (default: all)")
    ap.add_argument("--shard", default="0/1", help="k/n: take every n-th edition from k")
    args = ap.parse_args()
    events = json.loads(pathlib.Path(args.events).read_text())
    editions = sorted({(e["id"], h["year"], h["url"].rstrip("/")) for e in events.values() for h in e.get("history") or []
                       if h.get("year") in args.years and h.get("url") and e["id"] not in args.skip_events})
    k, n = (int(x) for x in args.shard.split("/"))
    editions = editions[k::n]
    only = set(args.only or [])
    index = []
    for ev, year, base in editions:
        ed = base.rsplit("/", 1)[-1]
        if only and not any(o.startswith(ed + "/") for o in only):
            continue
        courses = edition_courses(base, args.delay)
        for cid, course in courses.items():
            if only and f"{ed}/{cid}" not in only:
                continue
            s = collect_course(ed, base, cid, course, args.max_runners, args.delay, args.meta_only)
            if s:
                index.append(s)
                print(json.dumps(s, ensure_ascii=False), flush=True)
    out = ROOT / (f"index_meta_{k}.json" if args.meta_only else f"index_{k}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
