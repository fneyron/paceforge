"""Count what the committed race dataset holds once filtered to solo running races.

A race counts when its archive loads with 10+ finishers, its checkpoints have
distances, and it is a solo running race: not a bike / triathlon / relay /
team / walk / stage format by name (dataset.is_running), and a median finisher
effort speed a running field can hold (dataset.plausible_running: bikes and
broken timings out). Prints a JSON summary (the "dataset" block of
app/data/model_stats.json) and, with --list, every race left out and why.

    .venv/bin/python scripts/race_data/dataset_stats.py [--list]
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dataset  # noqa: E402


def classify(path: pathlib.Path, source: str):
    raw = json.loads(gzip.decompress(path.read_bytes()))
    s = raw["summary"]
    if not dataset.is_running(s.get("name"), path.name, path.parent.name):
        return None, "not a solo running race (name)"
    race = dataset.load_utmb(path) if source == "utmb" else dataset.load_livetrail(path)
    if not race or len(race["runners"]) < 10:
        return None, "fewer than 10 finishers"
    if not race["cps"][-1]["km"]:
        return None, "no checkpoint distances"
    dplus = s.get("elevation_gain") or s.get("elevationGain") or race["cps"][-1].get("dplus")
    if not dataset.plausible_running(race, dplus):
        return None, "median effort speed of a bike / broken timing"
    return race, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    out = {"races": 0, "events": set(), "editions": set(), "finisher_results": 0, "checkpoint_passages": 0,
           "sources": {}, "stored": {}, "left_out": collections.Counter(), "with_gps_track": 0, "by_source": {}}
    for src in ("utmb", "livetrail"):
        paths = sorted((dataset.ROOT / src).glob("*/*.json.gz"))
        out["stored"][src] = len(paths)
        n = 0
        bs = out["by_source"][src] = {"finisher_results": 0, "checkpoint_passages": 0, "with_gps_track": 0}
        for path in paths:
            race, why = classify(path, src)
            if race is None:
                out["left_out"][f"{src}: {why}"] += 1
                if a.list:
                    print(f"left out {src}/{path.parent.name}/{path.name}: {why}", file=sys.stderr)
                continue
            n += 1
            out["events"].add(path.parent.name.rsplit("_", 1)[0])
            out["editions"].add(f"{src}/{path.parent.name}")
            fr = len(race["runners"])
            cp = sum(1 for r in race["runners"] for t in r["arr"][1:] if t is not None)
            out["finisher_results"] += fr
            out["checkpoint_passages"] += cp
            out["with_gps_track"] += bool(race.get("has_track"))
            bs["finisher_results"] += fr
            bs["checkpoint_passages"] += cp
            bs["with_gps_track"] += bool(race.get("has_track"))
        out["sources"][src] = n
        out["races"] += n
    res = {"races": out["races"], "events": len(out["events"]), "editions": len(out["editions"]),
           "finisher_results": out["finisher_results"], "checkpoint_passages": out["checkpoint_passages"],
           "races_with_gps_track": out["with_gps_track"],
           "sources": {"utmb_live": out["sources"]["utmb"], "livetrail": out["sources"]["livetrail"]},
           "by_source": {"utmb_live": out["by_source"]["utmb"], "livetrail": out["by_source"]["livetrail"]},
           "stored": {"utmb_live": out["stored"]["utmb"], "livetrail": out["stored"]["livetrail"]},
           "left_out": dict(sorted(out["left_out"].items()))}
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
