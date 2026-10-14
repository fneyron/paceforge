"""The LEVEL (finish time) the app predicts, scored on real finishers.

fit_model.py scores the SHAPE (each runner planned at his own finish): it never
sees the finish time itself. Here every UTMB Live finisher with a UTMB index
(a level read on his past races) is predicted from his index and the course's
effort-km only (E = km + D+/100), leave-one-EVENT-out:

    ln T = c + k · ln E + β · ln(index / 600)

with the app's exponent k (race_calibration.K_PRIOR) and c, β fitted on the
other events; the Optimiste / Sécurité band is the app's
(race_calibration.scenario_pcts, the index read as 3 past races). Reported:
finish MAE %, median actual / predicted, coverage of the band (target 60 %),
interval score (Gneiting & Raftery 2007, central 60 %, ln units × 100), by
distance band and by level (UTMB index band: does the law fit strong runners as
well as the others?); the exponent fitted freely, leave-one-event-out, and per
index band with its 95 % CI (leave-one-event-out jackknife): k drifting away
from the app's, overall or for one level, is a reason to look again.
Diagnostics only: nothing here changes the app. The lockbox's events (first
collected after 2026-10-05, lockbox_baseline.json) are never read here:
lockbox.py scores them once per release (lockbox_level).

Run: .venv/bin/python scripts/race_data/level_benchmark.py [--root data/races] [--out level.json]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

import dataset  # noqa: E402

BANDS = ((15, 60), (60, 100), (100, 140), (140, 200))
LEVELS = ((0, 550), (550, 650), (650, 750), (750, None))  # UTMB index bands
ALPHA = 0.4  # central 60 % interval: P20-P80
BASELINE = HERE / "lockbox_baseline.json"
INDEX_RACES = 3  # the UTMB index averages several results: read as 3 past races


def finishers(root: pathlib.Path, lockbox: bool = False, baseline: pathlib.Path = BASELINE) -> list[tuple]:
    """(event, ln E, ln(index / 600), ln T hours, km, index) of every indexed finisher of a solo running race
    of 15 km or more with 25 indexed finishers or more: of the development events, or (``lockbox``) of the
    lockbox's only."""
    known = dataset.known_events(baseline)
    out = []
    for path in sorted((root / "utmb").glob("*/*.json.gz")):
        d = json.loads(gzip.decompress(path.read_bytes()))
        s = d["summary"]
        if not dataset.is_running(s.get("name"), path.name, path.parent.name):
            continue
        pts = [p for p in d["points"] if p.get("distance") is not None]
        if len(pts) < 2:
            continue
        km = pts[-1]["distance"] / 1000
        dp = (d.get("info") or {}).get("elevationGain") or s.get("elevation_gain") or pts[-1].get("gainElevation")
        run = [(r["index"], r["time_s"]) for r in d["runners"].values() if r.get("index") and r["index"] > 0 and r.get("time_s")]
        if km < 15 or not dp or len(run) < 25:
            continue
        ev = path.parent.name.rsplit("_", 1)[0]
        if dataset.in_lockbox({"event": ev, "source": "utmb"}, known) != lockbox:
            continue
        for i, t in run:
            out.append((ev, math.log(km + dp / 100), math.log(i / 600), math.log(t / 3600), km, i))
    return out


def k_band(design, y, ev, sel) -> dict:
    """The exponent fitted freely on one level band (all events), its 95 % CI by a leave-one-event-out
    jackknife."""
    import numpy as np

    def fit(s):
        c, *_ = np.linalg.lstsq(design[s], y[s], rcond=None)
        return float(c[1])

    k = fit(sel)
    ks = np.array([fit(sel & (ev != e)) for e in sorted(set(ev[sel]))])
    g = len(ks)
    se = float(np.sqrt((g - 1) / g * np.sum((ks - ks.mean()) ** 2))) if g > 1 else 0.0
    return {"finishers": int(sel.sum()), "k": round(k, 3), "ci95": [round(k - 1.96 * se, 3), round(k + 1.96 * se, 3)]}


def _stats(y, pred, sel) -> dict:
    """Finish MAE %, median actual / predicted, inside the app's band (the index read as INDEX_RACES races)."""
    import numpy as np

    from app.services.race_calibration import scenario_pcts

    fast, safe = scenario_pcts({"n": INDEX_RACES, "n_eff": INDEX_RACES}, None)
    lo, hi = pred[sel] + math.log(1 - fast / 100), pred[sel] + math.log(1 + safe / 100)
    yy = y[sel]
    err = yy - pred[sel]  # ln(actual / predicted)
    isc = (hi - lo) + (2 / ALPHA) * np.maximum(0, lo - yy) + (2 / ALPHA) * np.maximum(0, yy - hi)
    return {"finishers": int(sel.sum()),
            "mae_pct": round(float(np.mean(np.abs(np.exp(-err) - 1)) * 100), 1),
            "median_actual_vs_pred_pct": round(float((np.exp(np.median(err)) - 1) * 100), 1),
            "coverage_pct": round(float(((yy >= lo) & (yy <= hi)).mean() * 100), 1),
            "interval_score": round(float(isc.mean() * 100), 1)}


def lockbox_level(root: pathlib.Path, baseline: pathlib.Path = BASELINE) -> dict | None:
    """The finish time of the lockbox's indexed finishers, from the law (the app's k; c and beta fitted on every
    development event): finish MAE %, median actual / predicted, coverage of the band. None without one."""
    import numpy as np

    from app.services.race_calibration import K_PRIOR

    dev, box = finishers(root, baseline=baseline), finishers(root, lockbox=True, baseline=baseline)
    if not dev or not box:
        return None
    d = np.array([[1.0, r[1], r[2], r[3]] for r in dev])
    c, *_ = np.linalg.lstsq(d[:, [0, 2]], d[:, 3] - K_PRIOR * d[:, 1], rcond=None)
    b = np.array([[r[1], r[2], r[3]] for r in box])
    pred = c[0] + K_PRIOR * b[:, 0] + c[1] * b[:, 1]
    return {"events": sorted({r[0] for r in box}), **_stats(b[:, 2], pred, np.ones(len(b), dtype=bool))}


def benchmark(root: pathlib.Path, baseline: pathlib.Path = BASELINE) -> dict | None:
    import numpy as np

    from app.services.race_calibration import K_PRIOR, scenario_pcts

    rows = finishers(root, baseline=baseline)
    if len({r[0] for r in rows}) < 5:
        return None
    ev = np.array([r[0] for r in rows])
    design = np.array([[1.0, r[1], r[2]] for r in rows])
    y = np.array([r[3] for r in rows])
    km = np.array([r[4] for r in rows])
    events = sorted(set(ev))
    pred, ks = np.zeros(len(y)), []
    for e in events:
        tr, te = ev != e, ev == e
        c, *_ = np.linalg.lstsq(design[tr][:, [0, 2]], y[tr] - K_PRIOR * design[tr][:, 1], rcond=None)
        pred[te] = c[0] + K_PRIOR * design[te][:, 1] + c[1] * design[te][:, 2]
        free, *_ = np.linalg.lstsq(design[tr], y[tr], rcond=None)
        ks.append(float(free[1]))
    fast, safe = scenario_pcts({"n": INDEX_RACES, "n_eff": INDEX_RACES}, None)

    def stats(sel):
        return _stats(y, pred, sel)

    out = {"k_app": K_PRIOR, "k_free_loeo": [round(min(ks), 3), round(max(ks), 3)], "events": len(events),
           "band_pct": [-fast, safe], **stats(np.ones(len(y), dtype=bool))}
    out["by_band"] = {f"{a}-{b} km": stats((km >= a) & (km < b)) for a, b in BANDS if ((km >= a) & (km < b)).any()}
    ix = np.array([r[5] for r in rows])
    levels = {(f"index {a}-{b}" if b else f"index {a}+"): (ix >= a) & (ix < (b or 10 ** 6)) for a, b in LEVELS}
    out["by_level"] = {lab: stats(sel) for lab, sel in levels.items() if sel.any()}
    out["k_by_level"] = {lab: k_band(design, y, ev, sel) for lab, sel in levels.items() if sel.sum() >= 50}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(dataset.ROOT))
    ap.add_argument("--out")
    a = ap.parse_args()
    res = benchmark(pathlib.Path(a.root))
    print(json.dumps(res, indent=1, ensure_ascii=False))
    if a.out and res:
        pathlib.Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
