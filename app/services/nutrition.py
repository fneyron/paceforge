"""Deterministic race-nutrition planning.

The athlete defines an intake *rate* per product (units per hour); this module
turns that into totals, per-hour intake of carbs/fluid/sodium, a comparison
against recommended targets, and a schedule mapped to the race checkpoints.

Nothing here calls an LLM — the numbers are reproducible and explainable.
"""

import math

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
        "fluid_ml_per_h": int(round(fluid / 10.0) * 10),
        "sodium_mg_per_h": int(round(sodium / 10.0) * 10),
    }


def suggest_rates(target_carbs_per_h: float, products: list[dict]) -> dict[int, float]:
    """Suggest an intake rate (units/h) to roughly meet the carb target.

    Strategy: lean on the highest-carb product as the main fuel and set its
    rate so it alone covers the carb target. Simple and transparent; the
    athlete fine-tunes from there (e.g. swapping some gels for drink/solids).
    """
    carb_products = [p for p in products if (p.get("carbs_g") or 0) > 0]
    if not carb_products or target_carbs_per_h <= 0:
        return {}
    main = max(carb_products, key=lambda p: p.get("carbs_g") or 0)
    rate = target_carbs_per_h / (main["carbs_g"])
    return {main["id"]: round(rate * 2) / 2}  # nearest 0.5 unit/h


def rate_for_target(target_carbs_per_h: float, carbs_per_unit: float) -> float:
    """Units/h of one product needed to hit the carb target alone."""
    if carbs_per_unit and carbs_per_unit > 0 and target_carbs_per_h > 0:
        return round((target_carbs_per_h / carbs_per_unit) * 2) / 2
    return 0.0


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


def _clock_hour(clock_s: float) -> float:
    return (clock_s % 86400) / 3600.0


# One-click catalogue of common race products (per unit, label values; the
# athlete can still edit them in the pantry). Keys are stable slugs.
PRODUCT_CATALOG: list[dict] = [
    {"key": "maurten-gel-100", "name": "Maurten Gel 100", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "kcal": 100, "caffeine_mg": None, "volume_ml": None},
    {"key": "maurten-gel-100-caf", "name": "Maurten Gel 100 CAF 100", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "kcal": 100, "caffeine_mg": 100, "volume_ml": None},
    {"key": "maurten-gel-160", "name": "Maurten Gel 160", "kind": "gel", "carbs_g": 40, "sodium_mg": 30, "kcal": 160, "caffeine_mg": None, "volume_ml": None},
    {"key": "pf-30-gel", "name": "Precision Fuel PF 30 Gel", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "kcal": 120, "caffeine_mg": None, "volume_ml": None},
    {"key": "pf-30-caf", "name": "Precision Fuel PF 30 Caffeine Gel", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "kcal": 120, "caffeine_mg": 100, "volume_ml": None},
    {"key": "pf-90-gel", "name": "Precision Fuel PF 90 Gel", "kind": "gel", "carbs_g": 90, "sodium_mg": 0, "kcal": 360, "caffeine_mg": None, "volume_ml": None},
    {"key": "ph-1500", "name": "Precision Hydration PH 1500 (pastille, 500 ml)", "kind": "salt", "carbs_g": 0, "sodium_mg": 750, "kcal": None, "caffeine_mg": None, "volume_ml": None},
    {"key": "ph-1000", "name": "Precision Hydration PH 1000 (pastille, 500 ml)", "kind": "salt", "carbs_g": 0, "sodium_mg": 500, "kcal": None, "caffeine_mg": None, "volume_ml": None},
    {"key": "baouw-gel", "name": "Baouw Gel", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "kcal": 120, "caffeine_mg": None, "volume_ml": None},
    {"key": "gu-gel", "name": "GU Energy Gel", "kind": "gel", "carbs_g": 22, "sodium_mg": 60, "kcal": 100, "caffeine_mg": None, "volume_ml": None},
    {"key": "neversecond-c30", "name": "Neversecond C30 Gel", "kind": "gel", "carbs_g": 30, "sodium_mg": 200, "kcal": 120, "caffeine_mg": None, "volume_ml": None},
    {"key": "tailwind", "name": "Tailwind Endurance Fuel (1 dose)", "kind": "drink", "carbs_g": 25, "sodium_mg": 303, "kcal": 100, "caffeine_mg": None, "volume_ml": 500},
    {"key": "saltstick", "name": "SaltStick Caps", "kind": "salt", "carbs_g": 0, "sodium_mg": 215, "kcal": None, "caffeine_mg": None, "volume_ml": None},
]
CATALOG_BY_KEY = {c["key"]: c for c in PRODUCT_CATALOG}


# Caffeine defaults (per dose, timing). Guidance for a night start: small doses
# every 2–3 h once the night sets in, a full dose at dawn. Totals are capped
# (≈ 6 mg/kg, never > 400 mg) — the athlete sees the total, not just the plan.
CAFFEINE_DEFAULTS = {"enabled": False, "from_h": 3.0, "every_h": 2.5, "dose_mg": 50, "boost_dawn": True}
CAFFEINE_MAX_MG = 400
CAFFEINE_MAX_MG_PER_KG = 6.0


def caffeine_schedule(
    duration_s: float,
    legs: list[dict],
    settings: dict | None,
    start_offset_s: int = 0,
    weight_kg: float | None = None,
    product_name: str | None = None,
    unit_mg: float | None = None,
) -> dict | None:
    """Timed caffeine doses mapped onto the legs. Returns {doses, total_mg,
    max_mg, over} or None when disabled."""
    cfg = {**CAFFEINE_DEFAULTS, **(settings or {})}
    if not cfg.get("enabled") or duration_s <= 0:
        return None
    from_s = max(0.0, float(cfg.get("from_h") or 0)) * 3600
    every_s = max(0.5, float(cfg.get("every_h") or 2.5)) * 3600
    dose = max(0.0, float(cfg.get("dose_mg") or 0))
    units_per_dose = 1
    if unit_mg:  # caffeinated gels: whole gels per dose, you can't take half
        units_per_dose = max(1, int(round(dose / float(unit_mg)))) if dose > 0 else 1
        dose = float(unit_mg) * units_per_dose
    if dose <= 0:
        return None
    max_mg = min(CAFFEINE_MAX_MG, CAFFEINE_MAX_MG_PER_KG * weight_kg) if weight_kg else CAFFEINE_MAX_MG

    def clock_at(elapsed: float) -> float:
        """Elapsed moving time → clock, walking the legs (stops shift the clock)."""
        prev_cum, prev_clock = 0.0, float(start_offset_s)
        for leg in legs:
            cum, clk = float(leg["cum_s"]), float(leg["clock_s"])
            if elapsed <= cum:
                span = cum - prev_cum
                return prev_clock + (elapsed - prev_cum) / span * (clk - prev_clock) if span > 0 else clk
            prev_cum, prev_clock = cum, clk
        return prev_clock + (elapsed - prev_cum)

    def leg_at(elapsed: float) -> dict | None:
        for leg in legs:
            if elapsed <= float(leg["cum_s"]):
                return leg
        return legs[-1] if legs else None

    doses, t, total = [], from_s, 0.0
    dawn_done = False
    while t < duration_s - 20 * 60:  # no point dosing in the last 20 min
        clk = clock_at(t)
        mg = dose
        label = "petite dose"
        if cfg.get("boost_dawn") and not dawn_done and 5.0 <= _clock_hour(clk) < 7.5:
            # dawn: a full dose — with 100 mg gels one gel already is one
            mg, label, dawn_done = (dose if (unit_mg and unit_mg >= 100) else dose * 2), "dose pleine (aube)", True
        if unit_mg and total + mg > max_mg + 1:
            break  # never plan past the safe total with whole gels
        leg = leg_at(t)
        doses.append({
            "elapsed_s": int(t), "clock_s": int(clk), "mg": int(round(mg)), "label": label,
            "leg_to": leg["to_name"] if leg else "", "product": product_name,
            "units": int(round(mg / unit_mg)) if unit_mg else None,
        })
        total += mg
        t += every_s
    return {"doses": doses, "total_mg": int(round(total)), "max_mg": int(round(max_mg)), "over": total > max_mg, "settings": cfg}


def compute_plan(
    duration_s: float,
    targets: dict,
    items: list[dict],
    products_by_id: dict[int, dict],
    sections: list[dict] | None = None,
    flask_capacity_ml: float = 0,
    refill_kms: set | None = None,
    resupply_points: list[dict] | None = None,
    caffeine: dict | None = None,
    start_offset_s: int = 0,
    weight_kg: float | None = None,
) -> dict:
    """Build the nutrition plan.

    Args:
        duration_s: race duration used for totals (target or predicted).
        targets: {carbs_g_per_h, fluid_ml_per_h, sodium_mg_per_h}.
        items: [{"product_id": int, "per_hour": float}].
        products_by_id: {id: {name, kind, carbs_g, sodium_mg, volume_ml, ...}}.
        sections: optional passage sections (with cumulative_time_s, end_name)
            to build a checkpoint-mapped schedule. The PLAN times are used when
            a target exists (adjusted_*), the prediction otherwise.
        resupply_points: [{"km", "name"}] where a drop bag / crew lets you
            restock → the pack list is split per carry segment.
        caffeine: {enabled, from_h, every_h, dose_mg, boost_dawn}.
    """
    hours = max(duration_s / 3600.0, 0.0)
    # Hydration is driven by the fluid target, not by a "water product": water
    # is what you drink/refill (flasks + aid stations), not a fuel you dose.
    fluid_per_h = float(targets.get("fluid_ml_per_h", 0) or 0)

    per_h = {"carbs_g": 0.0, "fluid_ml": 0.0, "sodium_mg": 0.0, "caffeine_mg": 0.0, "kcal": 0.0}
    caffeine_on = bool((caffeine or {}).get("enabled"))
    def _p(it): return products_by_id.get(it.get("product_id")) or {}
    has_plain_carb = any(float(it.get("per_hour") or 0) > 0 and (_p(it).get("carbs_g") or 0) > 0 and not (_p(it).get("caffeine_mg") or 0) for it in items)
    caffeine_on = caffeine_on and has_plain_carb
    lines = []
    for it in items:
        pid = it.get("product_id")
        rate = float(it.get("per_hour") or 0)
        p = products_by_id.get(pid)
        if not p or rate <= 0:
            continue
        # A caffeinated gel is not taken « n per hour »: it is the caffeine
        # plan's dose. Its units come from the schedule below.
        by_caffeine = caffeine_on and (p.get("caffeine_mg") or 0) > 0
        if by_caffeine:
            rate = 0.0
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
        is_water = (
            (p.get("carbs_g") or 0) == 0
            and (p.get("sodium_mg") or 0) == 0
            and p.get("kind") == "drink"
        )
        lines.append({
            "product_id": pid,
            "name": p.get("name", "?"),
            "kind": p.get("kind", "gel"),
            "per_hour": rate,
            "total_units": rate * hours,
            "carbs_g_per_h": round(carbs),
            "fluid_ml_per_h": round(fluid),
            "carbs_per_unit": float(p.get("carbs_g") or 0),
            "sodium_per_unit": float(p.get("sodium_mg") or 0),
            "caffeine_per_unit": float(p.get("caffeine_mg") or 0),
            "is_water": is_water,
            "by_caffeine": by_caffeine,
        })

    totals = {
        "carbs_g": round(per_h["carbs_g"] * hours),
        "fluid_ml": round(fluid_per_h * hours),
        "sodium_mg": round(per_h["sodium_mg"] * hours),
        "caffeine_mg": round(per_h["caffeine_mg"] * hours),
        "kcal": round(per_h["kcal"] * hours),
    }

    # Coverage is product-driven (what your gels/drinks/salt provide). Water is
    # not here — it's the fluid target + the flask/refill model below.
    coverage = {
        "carbs": {
            "provided_per_h": round(per_h["carbs_g"]),
            "target_per_h": targets.get("carbs_g_per_h", 0),
            "status": _status(per_h["carbs_g"], targets.get("carbs_g_per_h", 0)),
        },
        "sodium": {
            "provided_per_h": round(per_h["sodium_mg"]),
            "target_per_h": targets.get("sodium_mg_per_h", 0),
            "status": _status(per_h["sodium_mg"], targets.get("sodium_mg_per_h", 0)),
        },
    }

    # Per-leg schedule: WHOLE units to take between two checkpoints (you never
    # take half a gel/sachet). The fraction left over on one leg carries to the
    # next (cumulative rounding), so a 50-minute leg does not get a full hour's
    # worth of every product and the race total stays within half a unit of
    # rate × duration. Also flags legs where you'd run dry before the next
    # refill (carried > capacity), and the REAL g/h and sodium/h the whole
    # units give on that leg.
    refills = {round(float(k), 1) for k in (refill_kms or set())}
    cap = float(flask_capacity_ml or 0)
    schedule = []
    line_totals = [0] * len(lines)
    line_due = [0.0] * len(lines)  # units owed so far = rate × elapsed hours
    prev_cum_s = 0.0
    prev_clock_s = float(start_offset_s)
    prev_name = "Départ"
    carried = 0.0  # fluid consumed since the last refill
    target_carbs = float(targets.get("carbs_g_per_h", 0) or 0)
    target_sodium = float(targets.get("sodium_mg_per_h", 0) or 0)
    if sections:
        for s in sections:
            cum_s = s.get("adjusted_cumulative_time_s")
            if cum_s is None:
                cum_s = s.get("cumulative_time_s") or 0
            clock_s = s.get("adjusted_clock_time_s")
            if clock_s is None:
                clock_s = s.get("clock_time_s")
            if clock_s is None:
                clock_s = start_offset_s + cum_s
            leg_h = max((cum_s - prev_cum_s) / 3600.0, 0.0)
            leg_fluid = round(fluid_per_h * leg_h)
            carried += leg_fluid
            dry = bool(cap and carried > cap + 1)
            leg_units = []
            real_carbs = real_sodium = real_caff = 0.0
            for li, ln in enumerate(lines):
                if ln["per_hour"] > 0:
                    line_due[li] += ln["per_hour"] * leg_h
                    u = max(0, int(math.floor(line_due[li] + 0.5)) - line_totals[li])
                else:
                    u = 0
                line_totals[li] += u
                real_carbs += u * ln["carbs_per_unit"]
                real_sodium += u * ln["sodium_per_unit"]
                real_caff += u * ln["caffeine_per_unit"]
                leg_units.append({"name": ln["name"], "kind": ln["kind"], "is_water": ln["is_water"], "units": u})
            schedule.append({
                "from_name": prev_name,
                "to_name": s.get("end_name", ""),
                "km": s.get("end_km"),
                "leg_time_s": int(cum_s - prev_cum_s),
                "cum_s": int(cum_s),
                "clock_s": int(clock_s),
                "start_clock_s": int(prev_clock_s),
                "night": _clock_hour(prev_clock_s) >= 21 or _clock_hour(prev_clock_s) < 6,
                "carbs_g": round(per_h["carbs_g"] * leg_h),
                "carbs_real_g": round(real_carbs),
                "carbs_real_per_h": round(real_carbs / leg_h) if leg_h > 0 else 0,
                "carbs_status": _status(real_carbs / leg_h, target_carbs) if leg_h > 0 else "ok",
                "sodium_real_per_h": round(real_sodium / leg_h) if leg_h > 0 else 0,
                "sodium_status": _status(real_sodium / leg_h, target_sodium) if leg_h > 0 else "ok",
                "caffeine_mg": round(real_caff),
                "fluid_ml": leg_fluid,
                "dry": dry,
                "over_ml": max(0, round(carried - cap)) if dry else 0,
                "units": leg_units,
            })
            if round(float(s.get("end_km") or 0), 1) in refills:
                carried = 0.0  # topped up at this aid station
            prev_cum_s = cum_s
            prev_clock_s = clock_s
            prev_name = s.get("end_name", "")
    # Caffeine: timed doses. With a caffeinated gel ticked, each dose IS one of
    # those gels: it lands in the leg where the dose falls (carbs included).
    caff_line = next((ln for ln in lines if ln.get("by_caffeine")), None) or next((ln for ln in lines if ln["caffeine_per_unit"] > 0), None)
    caffeine_plan = caffeine_schedule(
        duration_s, schedule, caffeine, start_offset_s, weight_kg,
        caff_line["name"] if caff_line else None,
        unit_mg=caff_line["caffeine_per_unit"] if caff_line and caff_line.get("by_caffeine") else None,
    )
    if caffeine_plan and caff_line and caff_line.get("by_caffeine") and schedule:
        li = lines.index(caff_line)
        for d in caffeine_plan["doses"]:
            leg = next((lg for lg in schedule if d["elapsed_s"] <= lg["cum_s"]), schedule[-1])
            n = d.get("units") or 1
            leg["units"][li]["units"] += n
            line_totals[li] += n
            leg["carbs_real_g"] += round(caff_line["carbs_per_unit"] * n)
            leg["caffeine_mg"] += round(caff_line["caffeine_per_unit"] * n)
            lh = leg["leg_time_s"] / 3600.0
            if lh > 0:
                leg["carbs_real_per_h"] = round(leg["carbs_real_g"] / lh)
                leg["sodium_real_per_h"] = round(leg["sodium_real_per_h"] + caff_line["sodium_per_unit"] * n / lh)
                leg["carbs_status"] = _status(leg["carbs_real_g"] / lh, target_carbs)
    # Pack list = sum of the whole per-leg units (consistent with the schedule).
    for li, ln in enumerate(lines):
        ln["total_units"] = line_totals[li]

    # Where does each unit travel? From the start bag until the first drop bag /
    # crew point, then from that bag until the next one. What you carry at
    # once is the max over carry segments — that's the "sac" size.
    packing = []
    if schedule:
        resupply = {round(float(r.get("km") or 0), 1): r.get("name", "") for r in (resupply_points or []) if r.get("km")}
        group = {"at": "Départ", "km": 0.0, "until": None, "until_km": None, "legs": 0, "fluid_ml": 0, "units": {}, "carbs_g": 0}
        for leg in schedule:
            group["legs"] += 1
            group["fluid_ml"] += leg["fluid_ml"]
            group["carbs_g"] += leg["carbs_real_g"]
            for u in leg["units"]:
                if u["units"] and not u["is_water"]:
                    group["units"][u["name"]] = group["units"].get(u["name"], 0) + u["units"]
            km = round(float(leg["km"] or 0), 1)
            if km in resupply:
                group["until"], group["until_km"] = resupply[km], km
                packing.append(group)
                group = {"at": resupply[km], "km": km, "until": None, "until_km": None, "legs": 0, "fluid_ml": 0, "units": {}, "carbs_g": 0}
        group["until"] = "Arrivée"
        group["until_km"] = schedule[-1]["km"]
        if group["legs"]:
            packing.append(group)
        for g in packing:
            g["units"] = [{"name": n, "units": u} for n, u in g["units"].items()]
            g["is_start"] = g["at"] == "Départ"


    # Hydration feasibility: between two refill points you only carry
    # `flask_capacity_ml`. If a segment needs more fluid than you can carry,
    # flag the shortfall — you'd run dry before the next aid station.
    hydration = None
    if flask_capacity_ml and fluid_per_h > 0 and schedule:
        refills = {round(float(k), 1) for k in (refill_kms or set())}
        cap = float(flask_capacity_ml)
        segs = []
        seg_from = "Départ"
        seg_time = 0.0
        seg_fluid = 0.0
        for i, leg in enumerate(schedule):
            seg_time += leg["leg_time_s"]
            seg_fluid += leg["fluid_ml"]
            is_refill = round(float(leg["km"]), 1) in refills if leg["km"] is not None else False
            is_last = i == len(schedule) - 1
            if is_refill or is_last:
                segs.append({
                    "from_name": seg_from,
                    "to_name": leg["to_name"],
                    "time_s": int(seg_time),
                    "need_ml": round(seg_fluid),
                    "capacity_ml": round(cap),
                    "ok": seg_fluid <= cap + 1,
                    "shortfall_ml": max(0, round(seg_fluid - cap)),
                })
                seg_from = leg["to_name"]
                seg_time = 0.0
                seg_fluid = 0.0
        not_ok = [s for s in segs if not s["ok"]]
        worst = max(segs, key=lambda s: s["need_ml"], default=None)
        # Suggested carry = cover the longest single inter-refill segment.
        suggested = int(math.ceil((worst["need_ml"] if worst else cap) / 100.0) * 100)
        hydration = {
            "capacity_ml": round(cap),
            "segments": segs,
            "feasible": all(s["ok"] for s in segs),
            "max_shortfall_ml": max((s["shortfall_ml"] for s in segs), default=0),
            "has_refills": bool(refills),
            "dry_count": len(not_ok),
            "segment_count": len(segs),
            "worst_segment": {"from_name": worst["from_name"], "to_name": worst["to_name"],
                              "need_ml": worst["need_ml"]} if worst else None,
            "suggested_capacity_ml": suggested,
        }

    return {
        "duration_s": int(duration_s),
        "hydration": hydration,
        "hours": round(hours, 2),
        "targets": targets,
        "per_hour": {k: round(v) for k, v in per_h.items()},
        "totals": totals,
        "coverage": coverage,
        "lines": lines,
        "schedule": schedule,
        "packing": packing,
        "caffeine": caffeine_plan,
    }


def _round_half(x: float) -> float:
    return round(x * 2) / 2


def auto_rates(target_carbs_per_h: float, target_sodium_per_h: float, products: list[dict]) -> dict[int, float]:
    """Units/h per product so the SELECTED products meet the targets together.

    Carb products share the carb target equally (each brings target/n g/h);
    salt products cover whatever sodium the carb products leave uncovered.
    Rates are in halves (2.5 gels/h), never below 0.5 for a carb product the
    athlete chose to use — you don't pack a product to not take it.
    """
    caff = [p for p in products if (p.get("caffeine_mg") or 0) > 0]
    if not any((p.get("carbs_g") or 0) > 0 for p in products if p not in caff):
        caff = []  # only caffeinated products: they carry the carbs, per hour like any gel
    carb = [p for p in products if (p.get("carbs_g") or 0) > 0 and p not in caff]
    salt = [p for p in products if (p.get("carbs_g") or 0) <= 0 and (p.get("sodium_mg") or 0) > 0 and p not in caff]
    rates: dict[int, float] = {p["id"]: 1.0 for p in caff}  # kept « used »; the caffeine plan sets the count
    sodium_covered = 0.0
    if carb and target_carbs_per_h > 0:
        # every chosen product at least ½ per hour, then add halves where they
        # bring the total closest to the target (small products fine-tune)
        cr = {p["id"]: 0.5 for p in carb}
        def total() -> float:
            return sum(cr[p["id"]] * float(p["carbs_g"]) for p in carb)
        for _ in range(60):
            gap = target_carbs_per_h - total()
            best = min(carb, key=lambda p: abs(gap - 0.5 * float(p["carbs_g"])))
            if abs(gap - 0.5 * float(best["carbs_g"])) >= abs(gap):
                break
            cr[best["id"]] += 0.5
        for p in carb:
            rates[p["id"]] = cr[p["id"]]
            sodium_covered += cr[p["id"]] * float(p.get("sodium_mg") or 0)
    remaining = max(0.0, float(target_sodium_per_h or 0) - sodium_covered)
    for p in salt:
        if remaining <= 0:
            rates[p["id"]] = 0.0
            continue
        exact = remaining / float(p["sodium_mg"]) / len(salt)
        lo, hi = math.floor(exact * 2) / 2, math.ceil(exact * 2) / 2
        # the half-step whose sodium is closest to the target in RATIO (under-dosing is not « closer » by default)
        def miss(r: float) -> float:
            got = sodium_covered + r * float(p["sodium_mg"]) * len(salt)
            return abs(math.log(max(got, 1.0) / max(float(target_sodium_per_h), 1.0)))
        rates[p["id"]] = min((c for c in (lo, hi) if c > 0), key=miss, default=hi)
    return rates
