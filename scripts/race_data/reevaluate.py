"""Monthly refit of the current hours-based model, with fixed regularisation.

Model family and penalties are frozen from the approved fit, never selected
on the held-out test events. More expensive family selection remains a
separate research operation (fit_model.py). Reports do not alter app constants.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
from pathlib import Path

from scripts.race_data import maintenance as m
from scripts.race_data.review_model import review


def evaluate(data: Path, livetrail: Path, out: Path, corpus: Path):
    from scripts.race_data import fit_model as fm

    with data.open("rb") as stream:
        races = pickle.load(stream)
    with livetrail.open("rb") as stream:
        extra = pickle.load(stream)
    train, test, test_events = fm.split(races)
    if (
        len({r["event"] for r in train}) < 8
        or len({r["event"] for r in test}) < 4
        or len(extra) < 20
    ):
        raise ValueError("Insufficient training or validation coverage")
    ptr, pte = fm.Problem(train), fm.Problem(test)
    print(f"Fitting on {len(train)} races; validating on {len(test)} separate races", flush=True)
    fitted = fm.fit(
        ptr, fm.x0_from_production(), form="hours", lam_c=0.0, lam_t=3.0, label="monthly"
    )
    models = {"current": (None, "current"), "fit_hours": (fitted, "fit")}
    predictions = {"current": pte.predict(None, "current"), "fit": pte.predict(fitted, "fit")}
    per = fm.per_race(pte, predictions)
    guards = {}
    for rid in ("utmb/transjeju_2025/100m", "utmb/transjeju_2026/100m"):
        race = next((r for r in races if r["id"] == rid), None)
        guard = fm.guard(race, models) if race else None
        if guard:
            guards[rid] = {name: guard["mean_abs_gap_" + name] for name in models}
            guards[rid]["runners"] = guard["runners"]

    def groups(method):
        tables = {k: getattr(pte, method)(v) for k, v in predictions.items()}
        return {
            label: {"runners": pair[0], "current": pair[1], "fit": tables["fit"][label][1]}
            for label, pair in tables["current"].items()
        }

    validation = {
        "held_out_races": len(per),
        "held_out_runners": int(pte.nrun[pte.has_obs].sum()),
        "train_races": len(train),
        "test_events": test_events,
        "mean_abs_gap": {k: float(pte.score(v)) for k, v in predictions.items()},
        "by_group": groups("by_group"),
        "by_band": groups("by_band"),
        "per_race": {rid: {"runners": n, **scores} for rid, (n, scores) in per.items()},
        "guards": guards,
        "livetrail": fm.livetrail_eval(extra, models, lambda s: print(s, flush=True)),
    }
    params = {
        "created_at": m.utc_now(),
        "corpus_sha256": m.digest_corpus(corpus),
        "simulator_sha256": hashlib.sha256(Path(fm.rs.__file__).read_bytes()).hexdigest(),
        "train_events": sorted({r["event"] for r in train}),
        "test_events": test_events,
        "selected": {"model": "fit_hours", "form": "hours", "lam_c": 0.0, "lam_t": 3.0},
        "fit": fm.describe(fitted),
        "raw_x": fitted.tolist(),
        "validation": validation,
    }
    # Scientific summaries include numpy scalar counts. Persist plain JSON numbers.
    params = json.loads(json.dumps(params, default=lambda value: value.item(), allow_nan=False))
    out.mkdir(parents=True, exist_ok=True)
    m.save_json(out / "candidate.json", params)
    result = review(params)
    m.save_json(out / "validation.json", result)
    current, candidate = (validation["mean_abs_gap"][k] for k in ("current", "fit"))
    text = (
        f"# Réévaluation du modèle\n\nStatut : **{result['status']}**. "
        "Paramètres publiés : **non**.\n\n"
        f"Erreur moyenne aux points de passage : {current:.2f} → {candidate:.2f} min.\n\n"
        f"Validation : {len(per)} courses, {validation['held_out_runners']} arrivants, "
        "événements séparés de l'entraînement.\n\n"
        "Les temps sont comparés à durée finale connue, arrêts retirés lorsque disponibles : "
        "cette mesure valide la répartition des temps sur le parcours.\n\n"
        "Le candidat doit encore être vérifié avec le code de production "
        "(validate_app.py) avant adoption.\n\n"
        + "\n".join(
            f"- {'OK' if row['passed'] else 'ÉCHEC'} — {row['name']}: {row['detail']}"
            for row in result["checks"]
        )
        + "\n"
    )
    m.atomic_write(out / "summary.md", text.encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--livetrail", required=True, type=Path)
    parser.add_argument("--out", default="reports/race-data/model", type=Path)
    parser.add_argument("--corpus", default="data/races", type=Path)
    args = parser.parse_args()
    result = evaluate(args.data, args.livetrail, args.out, args.corpus)
    print(
        json.dumps({"status": result["status"], "failed_checks": result["failed_checks"]}),
        flush=True,
    )


if __name__ == "__main__":
    main()
