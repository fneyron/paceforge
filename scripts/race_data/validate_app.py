"""Score the APP itself before and after a refit is written into race_simulator.py.

fit_model.py scores candidates on its fast path; once the adopted constants
are in the app, this re-runs the app's own course code (fit_data.py writes
base_cur and the app's fatigue / night constants into each record) and
compares, on the SAME held-out test events and finishers:

  before   fit_data.py output built with the app before the change
  after    fit_data.py output built with the app after the change

Run (records built with each version of the app):
  .venv/bin/python scripts/race_data/validate_app.py --before B.pkl --after A.pkl \\
      [--lt-before LB.pkl --lt-after LA.pkl] [--out summary.json] [--append fit_results.txt]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fit_model as fm  # noqa: E402

CUR = {"current": (None, "current")}


def score(races):
    prob = fm.Problem(races)
    pred = prob.predict(None, "current")
    return prob, pred, prob.score(pred)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--lt-before")
    ap.add_argument("--lt-after")
    ap.add_argument("--out", help="JSON summary")
    ap.add_argument("--append", help="append the report to this file (fit_results.txt)")
    a = ap.parse_args()
    lines = []

    def say(s=""):
        print(s, flush=True)
        lines.append(s)

    before, after = pickle.load(open(a.before, "rb")), pickle.load(open(a.after, "rb"))
    assert [r["id"] for r in before] == [r["id"] for r in after], "the two runs must hold the same races"
    say("APP CHECK: the app's own path (gradient every 25 m, surface, fatigue, night) before / after the change")
    say(f"  before constants {before[0].get('app')}")
    say(f"  after  constants {after[0].get('app')}")
    out = {}
    for nm, idx in (("train", 0), ("test", 1)):
        sb, sa = fm.split(before)[idx], fm.split(after)[idx]
        pb, predb, gb = score(sb)
        pa, preda, ga = score(sa)
        say(f"  {nm}: {len(sb)} races, mean |gap| {gb:.2f} -> {ga:.2f} min")
        out[nm] = {"races": len(sb), "before": round(gb, 2), "after": round(ga, 2)}
        if nm == "test":
            rb = fm.per_race(pb, {"current": predb})
            ra = fm.per_race(pa, {"current": preda})
            improved = sum(ra[k][1]["current"] < rb[k][1]["current"] for k in rb)
            say(f"  test races improved: {improved}/{len(rb)}")
            for k in sorted(rb):
                say(f"    {k:40s}{rb[k][0]:5d}{rb[k][1]['current']:8.1f}{ra[k][1]['current']:8.1f}")
            out["test"]["races_improved"] = improved
            out["test"]["held_out_events"] = len({r["event"] for r in sb})
            out["test"]["runners"] = int(pb.nrun[pb.has_obs].sum())
    for rid in ("utmb/transjeju_2025/100m", fm.OWNER_RACE):
        gb = fm.guard(next(r for r in before if r["id"] == rid), CUR)
        ga = fm.guard(next(r for r in after if r["id"] == rid), CUR)
        if gb and ga:
            say(f"  guard {rid} 16-21 h ({gb['runners']} runners): {gb['mean_abs_gap_current']} -> {ga['mean_abs_gap_current']}")
            out[f"guard {rid}"] = {"runners": gb["runners"], "before": gb["mean_abs_gap_current"], "after": ga["mean_abs_gap_current"]}
    ob = fm.owner_runner(next(r for r in before if r["id"] == fm.OWNER_RACE), CUR)
    oa = fm.owner_runner(next(r for r in after if r["id"] == fm.OWNER_RACE), CUR)
    if ob and oa:
        say(f"  {fm.OWNER_RACE} bib {fm.OWNER_BIB} (official {ob['actual_s']} s), plan shape at his own finish: "
            f"{ob['mean_abs_gap_current']} -> {oa['mean_abs_gap_current']} min")
        out["owner"] = {"actual_s": ob["actual_s"], "moving_s": ob["moving_s"], "before": ob["mean_abs_gap_current"], "after": oa["mean_abs_gap_current"],
                        "checkpoints": [{"cp": cb["cp"], "km": cb["km"], "actual_moving_s": cb["actual_moving_s"], "before_s": cb["current_s"], "after_s": ca["current_s"]}
                                        for cb, ca in zip(ob["checkpoints"], oa["checkpoints"])]}
    tb = [r for r in before if r["id"] == fm.OWNER_RACE]
    ta = [r for r in after if r["id"] == fm.OWNER_RACE]
    gb, ga = score(tb)[2], score(ta)[2]
    say(f"  {fm.OWNER_RACE} all finishers by level group: {gb:.1f} -> {ga:.1f}")
    out["transjeju_2026_all"] = {"before": round(gb, 1), "after": round(ga, 1)}
    if a.lt_before and a.lt_after:
        lb, la = pickle.load(open(a.lt_before, "rb")), pickle.load(open(a.lt_after, "rb"))
        pb, predb, gb = score(lb)
        pa, preda, ga = score(la)
        rb = fm.per_race(pb, {"current": predb})
        ra = fm.per_race(pa, {"current": preda})
        improved = sum(ra[k][1]["current"] < rb[k][1]["current"] for k in rb)
        say(f"  LiveTrail ({len(lb)} courses, no GPS track, night not scored): {gb:.2f} -> {ga:.2f} min, courses improved {improved}/{len(rb)}")
        out["livetrail"] = {"courses": len(lb), "before": round(gb, 2), "after": round(ga, 2), "courses_improved": improved, "courses_scored": len(rb)}
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)
    if a.append:
        with open(a.append, "a") as f:
            f.write("\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
