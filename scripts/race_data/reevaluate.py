"""Monthly refit of the current hours-based model, with fixed regularisation.

Model family and penalties are frozen from the approved fit, never selected
on the held-out test events. More expensive family selection remains a
separate research operation (fit_model.py). Reports do not alter app constants.

Scored as review_model.py decides: the primary metric (mean |ln(predicted /
actual)| at the checkpoints) with its event-bootstrap 95 % CI and per band;
minutes, per-race results, the Transjeju guards, LiveTrail and the finish-time
benchmark (level_benchmark.py) are diagnostics. The lockbox (events first
collected after 2026-10-05) is never read here: lockbox.py, once per release.
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
        extra = [r for r in pickle.load(stream) if not fm.in_lockbox(r)]  # the lockbox is lockbox.py's alone
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

    from scripts.race_data import level_benchmark

    validation = {
        "held_out_races": len(per),
        "held_out_runners": int(pte.nrun[pte.has_obs].sum()),
        "train_races": len(train),
        "test_events": test_events,
        "primary": fm.primary_summary(pte, predictions["fit"], predictions["current"]),
        "level": level_benchmark.benchmark(corpus),
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
    pr = validation["primary"]
    lv = validation.get("level") or {}
    text = (
        f"# Réévaluation du modèle\n\nStatut : **{result['status']}**. "
        "Paramètres publiés : **non**.\n\n"
        f"Erreur relative moyenne aux points de passage (|ln(prévu / réel)|) : {pr['current']:.2f} → "
        f"{pr['fit']:.2f} %, écart {pr['diff']:+.3f} [{pr['ci95'][0]:+.3f} ; {pr['ci95'][1]:+.3f}] "
        f"(intervalle à 95 %, rééchantillonnage des {pr['events']} événements de validation). "
        "Adoption seulement si la borne haute est sous 0 et qu'aucune bande de distance ne se dégrade de plus de 2 %.\n\n"
        f"En minutes (indicatif) : {current:.2f} → {candidate:.2f} min.\n\n"
        f"Validation : {len(per)} courses, {validation['held_out_runners']} arrivants, "
        "événements séparés de l'entraînement.\n\n"
        "Les temps sont comparés à durée finale connue, arrêts retirés lorsque disponibles : "
        "cette mesure valide la répartition des temps sur le parcours.\n\n"
        + (f"Temps final (indice UTMB, un événement laissé de côté à la fois, {lv.get('finishers')} arrivants) : "
           f"erreur moyenne {lv.get('mae_pct')} %, {lv.get('coverage_pct')} % entre Optimiste et Sécurité (cible 60 %).\n\n"
           if lv else "")
        + "Le candidat doit encore être vérifié avec le code de production "
        "(validate_app.py), puis sur les événements mis de côté (lockbox.py, une fois par version) avant adoption.\n\n"
        + "\n".join(
            f"- {'OK' if row['passed'] else 'ÉCHEC'} — {row['name']}: {row['detail']}"
            for row in result["checks"]
        )
        + "\n\nIndicateurs (sans veto) :\n\n"
        + "\n".join(f"- {row['name']}: {row['detail']}" for row in result.get("diagnostics", []))
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
