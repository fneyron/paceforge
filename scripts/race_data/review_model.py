"""Fail-closed quality checks for a candidate. Passing never edits production.

Adoption rule (fit_model.py, WHAT IS SCORED / ADOPTION):

- the evidence is there: separate train / validation events, enough held-out
  races and finishers, every held-out race compared, all four distance bands;
- PRIMARY: mean |ln(predicted / actual)| at the checkpoints; the event-bootstrap
  95 % CI of candidate − current must lie below 0 (upper bound < 0): an
  average gain inside the noise of 6 events is not a gain;
- no distance band gets worse by more than 2 % (relative) on the primary metric.

Per-race results, the Transjeju guards, the mean |gap| in minutes, the share of
races improved and LiveTrail are reported as diagnostics: they inform the
reviewer, they do not veto (one race or one runner is not a validation set).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

BAND_TOLERANCE = 0.02


def number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def review(params: dict) -> dict:
    validation = params.get("validation") or {}
    selected = (params.get("selected") or {}).get("model", "fit")
    checks, diagnostics = [], []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    def note(name, ok, detail):
        diagnostics.append({"name": name, "ok": bool(ok), "detail": detail})

    train, test = set(params.get("train_events", [])), set(params.get("test_events", []))
    check(
        "separate_events",
        len(train) >= 8 and len(test) >= 4 and not train.intersection(test),
        "At least 8 training events and 4 separate validation events",
    )
    check(
        "validation_size",
        validation.get("held_out_races", 0) >= 20 and validation.get("held_out_runners", 0) >= 1000,
        "At least 20 held-out races and 1,000 finishers",
    )
    primary = validation.get("primary") or {}
    ci = primary.get("ci95") or [None, None]
    valid = (number(primary.get("current")) and number(primary.get("fit")) and finite(primary.get("diff"))
             and len(ci) == 2 and all(finite(v) for v in ci) and ci[0] <= primary.get("diff") <= ci[1])
    check(
        "primary_improvement",
        valid and ci[1] < 0,
        "Mean |ln(predicted / actual)| at the checkpoints: the event-bootstrap 95 % CI of candidate - current "
        "lies below 0",
    )
    bands = primary.get("by_band") or {}
    check("by_band_coverage", len(bands) >= 4, "All four distance bands")
    for label, row in bands.items():
        before, after = row.get("current"), row.get("fit")
        check(
            f"by_band:{label}",
            number(before) and number(after) and after <= before * (1 + BAND_TOLERANCE),
            "No distance band worse by more than 2 % (relative) on the primary metric",
        )
    per_race = validation.get("per_race") or {}
    paired = [v for v in per_race.values() if number(v.get("current")) and number(v.get("fit"))]
    check(
        "race_coverage",
        len(paired) >= 20 and len(paired) == validation.get("held_out_races"),
        "Every held-out race must have a comparison",
    )

    # ── diagnostics: reported, never a veto ──
    def fmt(v):
        return f"{v:.2f}" if number(v) else str(v)

    scores = validation.get("mean_abs_gap") or {}
    cur, cand = scores.get("current"), scores.get("fit")
    note("minutes", number(cur) and number(cand) and cand <= cur,
         f"Mean |gap| at the checkpoints: {fmt(cur)} -> {fmt(cand)} min")
    if paired:
        share = sum(v["fit"] < v["current"] for v in paired) / len(paired)
        note("races_improved", share >= 0.5, f"{share:.0%} of the held-out races improve")
    worse = sorted(race for race, row in per_race.items()
                   if number(row.get("current")) and number(row.get("fit"))
                   and row["fit"] > row["current"] + max(1, row["current"] * 0.05))
    note("races_worse", not worse, "Races worse by more than 1 minute and 5 %: " + (", ".join(worse) or "none"))
    guards = validation.get("guards") or {}
    for race in ("utmb/transjeju_2025/100m", "utmb/transjeju_2026/100m"):
        row = guards.get(race) or {}
        before, after = row.get("current"), row.get(selected)
        note("guard:" + race, number(before) and number(after) and after <= before + 0.1,
             f"Finishers 16-21 h: {fmt(before)} -> {fmt(after)} min")
    live = validation.get("livetrail") or {}
    ls = live.get("mean_abs_gap") or {}
    note("livetrail", number(ls.get("current")) and number(ls.get(selected)) and ls[selected] <= ls["current"],
         f"LiveTrail ({live.get('courses_scored', 0)} courses): {fmt(ls.get('current'))} -> {fmt(ls.get(selected))} min")
    level = validation.get("level") or {}
    if level:
        note("level", True, f"Finish time (UTMB index, leave-one-event-out): MAE {level.get('mae_pct')} %, "
                            f"inside Optimiste-Sécurité {level.get('coverage_pct')} % (target 60 %)")

    failed = [row["name"] for row in checks if not row["passed"]]
    return {
        "status": "rejected" if failed else "eligible_for_review",
        "published": False,
        "production_path_verified": False,
        "failed_checks": failed,
        "checks": checks,
        "diagnostics": diagnostics,
        "next_step": (
            "An eligible candidate still requires validate_app.py on the production "
            "implementation, the lockbox (lockbox.py, once per release) and a reviewed deployment."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = review(json.loads(Path(args.params).read_text()))
    Path(args.out).write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps({"status": result["status"], "failed_checks": result["failed_checks"]}))


if __name__ == "__main__":
    main()
