"""The lockbox: score a RELEASE once on the events first collected after 2026-10-05.

The six test events of fit_model.py are not pristine any more (several of the
app's constants were chosen looking at them, see fit_model.py SPLIT). The
events not in lockbox_baseline.json are kept out of every fit and every
selection (the finish-time benchmark too); this scores the app as it ships on
them, once per version of the model code (race_simulator.py +
race_calibration.py): the shape (the primary metric, as fit_model.py) and the
level (level_benchmark.lockbox_level: finish MAE %, coverage of Optimiste -
Sécurité), and appends the result to lockbox_ledger.json. A second run on the same code is refused (``--force``
runs it and records that it was forced): a lockbox scored at every attempt is
a test set like the others.

Run (records built by fit_data.py with the release's code):
  .venv/bin/python scripts/race_data/lockbox.py --data FITDATA.pkl [--livetrail LT.pkl] [--root data/races] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import pickle
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dataset  # noqa: E402
import fit_model as fm  # noqa: E402
import level_benchmark  # noqa: E402

LEDGER = HERE / "lockbox_ledger.json"


def model_sha() -> str:
    h = hashlib.sha256()
    for rel in ("app/services/race_simulator.py", "app/services/race_calibration.py"):
        h.update((HERE.parents[1] / rel).read_bytes())
    return h.hexdigest()


def score(races) -> dict | None:
    if not races:
        return None
    prob = fm.Problem(races, night="sun")
    if not prob.pairs or not prob.has_obs.any():
        return None
    pred = prob.predict(None, "current")
    return {"races": len(races), "events": sorted({r["event"] for r in races}),
            "runners": int(prob.nrun[prob.has_obs].sum()),
            "primary": round(prob.rel_score(pred), 3), "mean_abs_gap_min": round(prob.score(pred), 2),
            "by_band": {k: round(v[1], 3) for k, v in prob.rel_by_band(pred).items()}}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True)
    ap.add_argument("--livetrail")
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--root", default=str(dataset.ROOT), help="the race corpus (the level is read on it)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    ledger_path = pathlib.Path(a.ledger)
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {"entries": []}
    sha = model_sha()
    if any(e["model_sha256"] == sha for e in ledger["entries"]) and not a.force:
        raise SystemExit("this model code was already scored on the lockbox: once per release (--force to override, recorded)")
    with open(a.data, "rb") as f:
        utmb = fm.lockbox(pickle.load(f))
    lt = []
    if a.livetrail:
        with open(a.livetrail, "rb") as f:
            lt = fm.lockbox(pickle.load(f))
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model_sha256": sha, "forced": bool(a.force),
             "utmb": score(utmb), "livetrail": score(lt), "level": level_benchmark.lockbox_level(pathlib.Path(a.root))}
    ledger["entries"].append(entry)
    ledger_path.write_text(json.dumps(ledger, indent=1) + "\n")
    print(json.dumps(entry, indent=1))


if __name__ == "__main__":
    main()
