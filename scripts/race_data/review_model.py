"""Fail-closed quality checks for a candidate. Passing never edits production."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def review(params: dict) -> dict:
    validation = params.get("validation") or {}
    selected = (params.get("selected") or {}).get("model", "fit")
    checks = []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

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
    scores = validation.get("mean_abs_gap") or {}
    current, candidate = scores.get("current"), scores.get("fit")
    valid = number(current) and number(candidate) and current > 0
    check(
        "overall_improvement",
        valid and current - candidate >= max(0.25, current * 0.02),
        "Mean checkpoint error must improve by at least 2% and 0.25 minutes",
    )
    for key in ("by_group", "by_band"):
        rows = validation.get(key) or {}
        check(
            key + "_coverage",
            len(rows) >= 4,
            "All four distance bands / at least four finish-time groups",
        )
        for label, row in rows.items():
            before, after = row.get("current"), row.get("fit")
            check(
                f"{key}:{label}",
                number(before) and number(after) and after <= before + max(0.25, before * 0.02),
                "No material regression in this group",
            )
    per_race = validation.get("per_race") or {}
    paired = [v for v in per_race.values() if number(v.get("current")) and number(v.get("fit"))]
    check(
        "race_coverage",
        len(paired) >= 20 and len(paired) == validation.get("held_out_races"),
        "Every held-out race must have a comparison",
    )
    check(
        "races_improved",
        bool(paired) and sum(v["fit"] < v["current"] for v in paired) / len(paired) >= 0.6,
        "At least 60% of held-out races improve",
    )
    for race, row in per_race.items():
        before, after = row.get("current"), row.get("fit")
        check(
            "race:" + race,
            number(before) and number(after) and after <= before + max(1, before * 0.05),
            "No race may regress by more than 1 minute or 5%",
        )
    guards = validation.get("guards") or {}
    for race in ("utmb/transjeju_2025/100m", "utmb/transjeju_2026/100m"):
        row = guards.get(race) or {}
        before, after = row.get("current"), row.get(selected)
        check(
            "guard:" + race,
            number(before) and number(after) and after <= before + 0.1,
            "Transjeju guard must be present and not regress (0.1 minute rounding tolerance)",
        )
    live = validation.get("livetrail") or {}
    ls = live.get("mean_abs_gap") or {}
    before, after = ls.get("current"), ls.get(selected)
    check(
        "livetrail",
        live.get("courses_scored", 0) >= 20
        and number(before)
        and number(after)
        and after <= before + max(0.2, before * 0.01),
        "At least 20 LiveTrail courses, no material regression",
    )
    failed = [row["name"] for row in checks if not row["passed"]]
    return {
        "status": "rejected" if failed else "eligible_for_review",
        "published": False,
        "production_path_verified": False,
        "failed_checks": failed,
        "checks": checks,
        "next_step": (
            "An eligible candidate still requires validate_app.py on the production "
            "implementation and a reviewed deployment."
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
