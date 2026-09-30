"""Collect UTMB Live race data for the pacing-model calibration dataset.

For every event (tenant = "<event>_<year>") and every race of 40 km or more:
checkpoints, GPS track with LiveTrail's terrain classes, and the passing
times of the finishers (all of them, or an even sample by rank when there are
more than --max-runners). Public data from utmblive-api.utmb.world, fetched
slowly (--delay between requests) and stored once, compressed, under
data/races/utmb/<tenant>/<race>.json.gz. Resumable: finished races are
skipped, partial runner downloads continue where they stopped.

    python scripts/race_data/collect_utmb.py --tenants tenants.json [--meta-only]
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import subprocess
import time

API = "https://utmblive-api.utmb.world"
ROOT = pathlib.Path(__file__).resolve().parents[2] / "data" / "races" / "utmb"
MIN_KM = 40.0


def get(url: str, tenant: str | None = None, delay: float = 0.5) -> dict | None:
    cmd = ["curl", "-s", "-m", "60", "-A", "PaceForge research (paceforge.fr)"]
    if tenant:
        cmd += ["-H", f"x-tenant: {tenant}"]
    for attempt in range(3):
        out = subprocess.run(cmd + [url], capture_output=True).stdout
        time.sleep(delay)
        try:
            return json.loads(out)
        except ValueError:
            time.sleep(2 * (attempt + 1))
    return None


def secs(hms: str | None) -> int | None:
    if not hms:
        return None
    try:
        h, m, s = (int(x) for x in hms.split(":"))
        return h * 3600 + m * 60 + s
    except ValueError:
        return None


def compact_track(tr: dict) -> list:
    """[distance m, altitude m, lat, lon, terrain] every point LiveTrail keeps (~20 m)."""
    out = []
    for seg in tr.get("segments") or []:
        for p in seg.get("segment") or []:
            if p[0] is None or p[1] is None or not p[2] or p[2][0] is None:
                continue  # a point without altitude or position: dropped
            out.append([int(p[0]), round(float(p[1]), 1), round(p[2][1], 5), round(p[2][0], 5), p[5] if len(p) > 5 else None])
    return out


def pick(finishers: list, max_runners: int, fast_share: float) -> list:
    """Every runner of the fastest ``fast_share`` of the field (the levels the
    fits lack: a 300 sample holds a handful of them), then an even sample by
    rank of the rest, up to ``max_runners`` in all."""
    if len(finishers) <= max_runners:
        return finishers
    n_fast = min(max_runners, int(round(len(finishers) * fast_share)))
    fast, rest = finishers[:n_fast], finishers[n_fast:]
    room = max_runners - n_fast
    if room <= 0 or not rest:
        return fast
    step = len(rest) / room
    return fast + [rest[int(i * step)] for i in range(min(room, len(rest)))]


def fetch_runner(tenant: str, bib: str, delay: float) -> dict | None:
    d = get(f"{API}/runners/{bib}?locale=en", tenant, delay)
    if not d or "detail" not in d:
        return None
    res = d.get("resume") or {}
    return {
        "sex": (res.get("info") or {}).get("sex"), "index": (res.get("info") or {}).get("index"),
        "age": (res.get("info") or {}).get("age"), "rank": (res.get("ranking") or {}).get("scratch"),
        "time_s": secs(res.get("raceTime")),
        "passings": [[x.get("pointId"), x.get("datetimeIn"), x.get("datetimeOut"), x.get("restTimeSeconds")] for x in d["detail"].get("passings") or []],
    }


def topup_race(path: pathlib.Path, tenant: str, race_id: str, max_runners: int, fast_share: float, delay: float) -> dict:
    """Add runners to a race already stored (the fast third first), keeping those there."""
    record = json.loads(gzip.decompress(path.read_bytes()))
    rank = get(f"{API}/races/{race_id}/progressive?type=FINAL_RANKING&limit=10000", tenant, delay) or {}
    finishers = [r for r in rank.get("runners") or [] if r.get("isFinisher")]
    runners = record["runners"]
    added = 0
    for f in pick(finishers, max_runners, fast_share):
        bib = str(f["bib"])
        if bib in runners:
            continue
        r = fetch_runner(tenant, bib, delay)
        if r:
            runners[bib] = r
            added += 1
            if added % 50 == 0:  # a restart keeps what was fetched
                record["summary"]["runners_sampled"] = len(runners)
                path.write_bytes(gzip.compress(json.dumps(record, separators=(",", ":")).encode()))
    record["summary"]["runners_sampled"] = len(runners)
    if added:
        path.write_bytes(gzip.compress(json.dumps(record, separators=(",", ":")).encode()))
    return record["summary"] | {"added": added}


def collect_race(tenant: str, race_id: str, max_runners: int, delay: float, meta_only: bool,
                 fast_share: float = 0.0, topup: bool = False) -> dict | None:
    path = ROOT / tenant / f"{race_id}.json.gz"
    partial = ROOT / tenant / f"{race_id}.partial.json"
    if path.exists():
        if topup:
            return topup_race(path, tenant, race_id, max_runners, fast_share, delay)
        return json.loads(gzip.decompress(path.read_bytes()))["summary"]
    st = get(f"{API}/races/{race_id}/static", tenant, delay)
    if not st or not st.get("info"):
        return None
    info = st["info"]
    if (info.get("distance") or 0) < MIN_KM:
        return None
    rank = get(f"{API}/races/{race_id}/progressive?type=FINAL_RANKING&limit=10000", tenant, delay) or {}
    finishers = [r for r in rank.get("runners") or [] if r.get("isFinisher")]
    summary = {
        "tenant": tenant, "race": race_id, "name": info.get("name"), "date": (info.get("startDate") or "")[:10],
        "distance_km": info.get("distance"), "elevation_gain": info.get("elevationGain"),
        "finishers": len(finishers), "checkpoints": len(st.get("points") or []), "track": bool(info.get("track")),
    }
    if meta_only:
        return summary
    track = get(info["track"], None, delay) if info.get("track") else None
    # every level of runner: the fast share whole, an even sample by rank of the rest
    finishers = pick(finishers, max_runners, fast_share)
    runners = json.loads(partial.read_text()) if partial.exists() else {}
    partial.parent.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(finishers):
        bib = str(f["bib"])
        if bib in runners:
            continue
        r = fetch_runner(tenant, bib, delay)
        if not r:
            continue
        runners[bib] = r
        if i % 50 == 0:
            partial.write_text(json.dumps(runners))
    record = {
        "summary": summary | {"runners_sampled": len(runners)},
        "info": {k: info.get(k) for k in ("name", "distance", "elevationGain", "startDate", "eventTimezone", "category")},
        "points": [{k: p.get(k) for k in ("pointId", "name", "shortName", "distance", "altitude", "gainElevation", "latitude", "longitude", "cutoff", "isPublic")}
                   | {"services": [s.get("name") if isinstance(s, dict) else s for s in p.get("services") or []]} for p in st.get("points") or []],
        "profile": (st.get("profile") or {}).get("profile"),
        "track": compact_track(track) if track else None,
        "track_stats": (track or {}).get("stats"),
        "runners": runners,
    }
    path.write_bytes(gzip.compress(json.dumps(record, separators=(",", ":")).encode()))
    partial.unlink(missing_ok=True)
    return record["summary"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenants", required=True, help="JSON {tenant: {races: [[raceId, name, status], ...]}}")
    ap.add_argument("--max-runners", type=int, default=400)
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--meta-only", action="store_true")
    ap.add_argument("--only", nargs="*", help="tenant/race ids to collect (default: all)")
    ap.add_argument("--fast-share", type=float, default=0.0, help="take every runner of this fastest share of the field")
    ap.add_argument("--topup", action="store_true", help="add runners to races already stored")
    ap.add_argument("--index-name", help="file name for the --meta-only index (default index_meta.json)")
    args = ap.parse_args()
    tenants = json.loads(pathlib.Path(args.tenants).read_text())
    index = []
    todo = [(tenant, race_id) for tenant, t in sorted(tenants.items()) for race_id, _n, status in t["races"] if status in (None, "FINISHED")]
    if args.only:  # in the order given
        todo = [tuple(x.split("/", 1)) for x in args.only]
    for tenant, race_id in todo:
        if True:
            s = collect_race(tenant, race_id, args.max_runners, args.delay, args.meta_only, args.fast_share, args.topup)
            if s:
                index.append(s)
                print(json.dumps(s, ensure_ascii=False), flush=True)
    out = ROOT / ((args.index_name or "index_meta.json") if args.meta_only else "index.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    if not args.meta_only and out.exists():
        old = {(x["tenant"], x["race"]): x for x in json.loads(out.read_text())}
        old.update({(x["tenant"], x["race"]): x for x in index})
        index = list(old.values())
    out.write_text(json.dumps(sorted(index, key=lambda x: (x["tenant"], x["race"])), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
