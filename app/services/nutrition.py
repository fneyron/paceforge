"""Deterministic race-nutrition planning.

The athlete defines an intake *rate* per product (units per hour); this module
turns that into totals, per-hour intake of carbs/fluid/sodium, a comparison
against recommended targets, and a schedule mapped to the race checkpoints.

Nothing here calls an LLM — the numbers are reproducible and explainable.
"""

# Recommended intake guidelines (endurance, trained gut). These are defaults the
# athlete can override; they are not medical advice.
_CARBS_SHORT = 60.0   # g/h, efforts < 3h
_CARBS_MID = 75.0     # g/h, 3–6h
_CARBS_LONG = 80.0    # g/h, > 6h
_FLUID_BASE = 500.0   # ml/h at mild temperature
_SODIUM_BASE = 400.0  # mg/h at mild temperature


def default_targets(duration_h: float, mean_temp_c: float | None) -> dict:
    """Recommended hourly targets from race duration and mean temperature.

    Fluid and sodium scale up with heat above ~15 °C (more sweat, more loss).
    """
    if duration_h < 3:
        carbs = _CARBS_SHORT
    elif duration_h < 6:
        carbs = _CARBS_MID
    else:
        carbs = _CARBS_LONG

    fluid = _FLUID_BASE
    sodium = _SODIUM_BASE
    if mean_temp_c is not None and mean_temp_c > 15:
        over = mean_temp_c - 15
        fluid += min(400.0, over * 25.0)    # +25 ml/h per °C, capped +400
        sodium += min(500.0, over * 35.0)   # +35 mg/h per °C, capped +500

    return {
        "carbs_g_per_h": round(carbs),
        "fluid_ml_per_h": round(fluid),
        "sodium_mg_per_h": round(sodium),
    }


def _status(provided: float, target: float) -> str:
    """under / ok / over relative to a target (±15% band = ok)."""
    if target <= 0:
        return "ok"
    ratio = provided / target
    if ratio < 0.85:
        return "under"
    if ratio > 1.2:
        return "over"
    return "ok"


def compute_plan(
    duration_s: float,
    targets: dict,
    items: list[dict],
    products_by_id: dict[int, dict],
    sections: list[dict] | None = None,
) -> dict:
    """Build the nutrition plan.

    Args:
        duration_s: race duration used for totals (target or predicted).
        targets: {carbs_g_per_h, fluid_ml_per_h, sodium_mg_per_h}.
        items: [{"product_id": int, "per_hour": float}].
        products_by_id: {id: {name, kind, carbs_g, sodium_mg, volume_ml, ...}}.
        sections: optional passage sections (with cumulative_time_s, end_name)
            to build a checkpoint-mapped schedule.
    """
    hours = max(duration_s / 3600.0, 0.0)

    per_h = {"carbs_g": 0.0, "fluid_ml": 0.0, "sodium_mg": 0.0, "caffeine_mg": 0.0, "kcal": 0.0}
    lines = []
    for it in items:
        pid = it.get("product_id")
        rate = float(it.get("per_hour") or 0)
        p = products_by_id.get(pid)
        if not p or rate <= 0:
            continue
        carbs = (p.get("carbs_g") or 0) * rate
        sodium = (p.get("sodium_mg") or 0) * rate
        fluid = (p.get("volume_ml") or 0) * rate
        caff = (p.get("caffeine_mg") or 0) * rate
        kcal = (p.get("kcal") or 0) * rate
        per_h["carbs_g"] += carbs
        per_h["sodium_mg"] += sodium
        per_h["fluid_ml"] += fluid
        per_h["caffeine_mg"] += caff
        per_h["kcal"] += kcal
        lines.append({
            "product_id": pid,
            "name": p.get("name", "?"),
            "kind": p.get("kind", "gel"),
            "per_hour": rate,
            "total_units": rate * hours,
            "carbs_g_per_h": round(carbs),
            "fluid_ml_per_h": round(fluid),
        })

    totals = {
        "carbs_g": round(per_h["carbs_g"] * hours),
        "fluid_ml": round(per_h["fluid_ml"] * hours),
        "sodium_mg": round(per_h["sodium_mg"] * hours),
        "caffeine_mg": round(per_h["caffeine_mg"] * hours),
        "kcal": round(per_h["kcal"] * hours),
    }

    coverage = {
        "carbs": {
            "provided_per_h": round(per_h["carbs_g"]),
            "target_per_h": targets.get("carbs_g_per_h", 0),
            "status": _status(per_h["carbs_g"], targets.get("carbs_g_per_h", 0)),
        },
        "fluid": {
            "provided_per_h": round(per_h["fluid_ml"]),
            "target_per_h": targets.get("fluid_ml_per_h", 0),
            "status": _status(per_h["fluid_ml"], targets.get("fluid_ml_per_h", 0)),
        },
        "sodium": {
            "provided_per_h": round(per_h["sodium_mg"]),
            "target_per_h": targets.get("sodium_mg_per_h", 0),
            "status": _status(per_h["sodium_mg"], targets.get("sodium_mg_per_h", 0)),
        },
    }

    # Checkpoint-mapped cumulative schedule: by the time you reach each point,
    # roughly this much should already be consumed (rate × elapsed hours).
    schedule = []
    if sections:
        for s in sections:
            cum_s = s.get("cumulative_time_s") or 0
            cum_h = cum_s / 3600.0
            schedule.append({
                "name": s.get("end_name", ""),
                "km": s.get("end_km"),
                "cumulative_time_s": cum_s,
                "carbs_g": round(per_h["carbs_g"] * cum_h),
                "fluid_ml": round(per_h["fluid_ml"] * cum_h),
                "sodium_mg": round(per_h["sodium_mg"] * cum_h),
                "units": [
                    {"name": ln["name"], "kind": ln["kind"], "units": round(ln["per_hour"] * cum_h, 1)}
                    for ln in lines
                ],
            })

    return {
        "duration_s": int(duration_s),
        "hours": round(hours, 2),
        "targets": targets,
        "per_hour": {k: round(v) for k, v in per_h.items()},
        "totals": totals,
        "coverage": coverage,
        "lines": lines,
        "schedule": schedule,
    }
