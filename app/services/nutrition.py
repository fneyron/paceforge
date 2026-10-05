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
# athlete can still edit them in « Tes produits »). Keys are stable slugs;
# "short" is what a chip, a bag and the shopping list show.
PRODUCT_CATALOG: list[dict] = [
    {"key": "maurten-gel-100", "name": "Maurten Gel 100", "short": "Maurten 100", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "kcal": 100, "caffeine_mg": None, "volume_ml": None},
    {"key": "maurten-gel-100-caf", "name": "Maurten Gel 100 CAF 100", "short": "Maurten 100 CAF", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "kcal": 100, "caffeine_mg": 100, "volume_ml": None},
    {"key": "maurten-gel-160", "name": "Maurten Gel 160", "short": "Maurten 160", "kind": "gel", "carbs_g": 40, "sodium_mg": 30, "kcal": 160, "caffeine_mg": None, "volume_ml": None},
    {"key": "pf-30-gel", "name": "Precision Fuel PF 30 Gel", "short": "PF 30", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "kcal": 120, "caffeine_mg": None, "volume_ml": None},
    {"key": "pf-30-caf", "name": "Precision Fuel PF 30 Caffeine Gel", "short": "PF 30 Caféine", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "kcal": 120, "caffeine_mg": 100, "volume_ml": None},
    {"key": "pf-90-gel", "name": "Precision Fuel PF 90 Gel", "short": "PF 90", "kind": "gel", "carbs_g": 90, "sodium_mg": 0, "kcal": 360, "caffeine_mg": None, "volume_ml": None},
    {"key": "ph-1500", "name": "Precision Hydration PH 1500 (pastille, 500 ml)", "short": "PH 1500", "kind": "salt", "carbs_g": 0, "sodium_mg": 750, "kcal": None, "caffeine_mg": None, "volume_ml": None},
    {"key": "ph-1000", "name": "Precision Hydration PH 1000 (pastille, 500 ml)", "short": "PH 1000", "kind": "salt", "carbs_g": 0, "sodium_mg": 500, "kcal": None, "caffeine_mg": None, "volume_ml": None},
    {"key": "baouw-gel", "name": "Baouw Gel", "short": "Baouw", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "kcal": 120, "caffeine_mg": None, "volume_ml": None},
    {"key": "gu-gel", "name": "GU Energy Gel", "short": "GU", "kind": "gel", "carbs_g": 22, "sodium_mg": 60, "kcal": 100, "caffeine_mg": None, "volume_ml": None},
    {"key": "neversecond-c30", "name": "Neversecond C30 Gel", "short": "Neversecond C30", "kind": "gel", "carbs_g": 30, "sodium_mg": 200, "kcal": 120, "caffeine_mg": None, "volume_ml": None},
    {"key": "tailwind", "name": "Tailwind Endurance Fuel (1 dose)", "short": "Tailwind", "kind": "drink", "carbs_g": 25, "sodium_mg": 303, "kcal": 100, "caffeine_mg": None, "volume_ml": 500},
    {"key": "saltstick", "name": "SaltStick Caps", "short": "SaltStick", "kind": "salt", "carbs_g": 0, "sodium_mg": 215, "kcal": None, "caffeine_mg": None, "volume_ml": None},
]
CATALOG_BY_KEY = {c["key"]: c for c in PRODUCT_CATALOG}
CATALOG_BY_NAME = {c["name"].lower(): c for c in PRODUCT_CATALOG}

# Generic quick picks: virtual products (never pantry rows), always available
# next to the pantry. -1/-2/-3 keep their historical values so old plans that
# point at them keep their numbers.
GENERIC_PRODUCTS: list[dict] = [
    {"id": -1, "name": "Gel", "kind": "gel", "carbs_g": 25, "sodium_mg": 0, "kcal": 100, "caffeine_mg": None, "volume_ml": None,
     "label": "Gel", "one": "gel", "many": "gels"},
    {"id": -2, "name": "Boisson glucidique", "kind": "drink", "carbs_g": 22, "sodium_mg": 300, "kcal": 90, "caffeine_mg": None, "volume_ml": 500,
     "label": "Boisson", "one": "dose de boisson", "many": "doses de boisson"},
    {"id": -3, "name": "Pastille de sel", "kind": "salt", "carbs_g": 0, "sodium_mg": 300, "kcal": None, "caffeine_mg": None, "volume_ml": None,
     "label": "Sel", "one": "pastille de sel", "many": "pastilles de sel"},
    {"id": -4, "name": "Gel caféiné", "kind": "gel", "carbs_g": 25, "sodium_mg": 0, "kcal": 100, "caffeine_mg": 50, "volume_ml": None,
     "label": "Gel caféiné", "one": "gel caféiné", "many": "gels caféinés"},
    {"id": -5, "name": "Barre", "kind": "bar", "carbs_g": 30, "sodium_mg": 100, "kcal": 150, "caffeine_mg": None, "volume_ml": None,
     "label": "Barre", "one": "barre", "many": "barres"},
]
GENERIC_BY_ID = {p["id"]: p for p in GENERIC_PRODUCTS}
GENERIC_ORDER = [-1, -4, -2, -5, -3]  # how the unpicked quick picks line up
LEVEL_CARBS = {"fragile": 60, "normal": 75, "solide": 90}
CARBS_LEVEL = {v: k for k, v in LEVEL_CARBS.items()}
SWEAT_FACTOR = {"peu": 0.8, "normal": 1.0, "beaucoup": 1.25}
CARRY_CHOICES = [1000, 1500, 2000]


def with_generics(pantry_by_id: dict) -> dict:
    """Generic quick picks + the pantry: the products a plan can point at."""
    return {**{p["id"]: dict(p) for p in GENERIC_PRODUCTS}, **pantry_by_id}


def short_label(p: dict) -> str:
    """Chip / bag label: the quick pick's word, the catalogue's short name, else the name."""
    if p.get("label"):
        return p["label"]
    name = (p.get("name") or "").strip()
    c = CATALOG_BY_NAME.get(name.lower())
    return c["short"] if c else (name or "?")


def role(p: dict) -> str:
    """What a product stands in for: caf, salt, drink, bar or gel."""
    if (p.get("caffeine_mg") or 0) > 0:
        return "caf"
    if not (p.get("carbs_g") or 0) and (p.get("sodium_mg") or 0) > 0:
        return "salt"
    if p.get("kind") == "drink":
        return "drink"
    if p.get("kind") in ("bar", "solid"):
        return "bar"
    return "gel"


def _fr(x: float) -> str:
    """12.5 → '12,5', 3.0 → '3'."""
    return (f"{x:.2f}".rstrip("0").rstrip(".") if not float(x).is_integer() else str(int(x))).replace(".", ",")


def unit_label(n: float, p: dict) -> str:
    """'1 gel', '3 gels', '2 pastilles de sel', '4 × PF 90'."""
    n = int(round(n))
    if p.get("one"):
        return f"{n} {p['one'] if n == 1 else p['many']}"
    return f"{n} × {short_label(p)}"


def rule_noun(p: dict) -> str:
    """The noun of a rule line: '1 gel', '1 PF 90'."""
    return f"1 {p['one']}" if p.get("one") else f"1 {short_label(p)}"


def _hm_words(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    if h and m:
        return f"{h}h{m:02d}"
    return f"{h} h" if h else f"{m} min"


def interval_words(rate: float) -> str:
    """Units per hour → words: 0.5 'toutes les 2 h', 1 'par heure', 3 'toutes les 20 min'."""
    rate = float(rate or 0)
    if rate <= 0:
        return ""
    if rate > 4:
        return f"{_fr(rate)} par heure"
    if rate == 1:
        return "par heure"
    if rate > 1:
        return f"toutes les {int(5 * math.floor(60 / rate / 5 + 0.5))} min"
    return "toutes les " + _hm_words(10 * math.floor(60 / rate / 10 + 0.5))


def liters(ml: float, step: int = 50) -> str:
    """650 → '0,65', 1500 → '1,5' (rounded to `step` ml)."""
    return _fr(round(float(ml or 0) / step) * step / 1000)


def ceil_half_l(ml: float) -> float:
    """Water to carry, rounded up to 0,5 L (in litres)."""
    return math.ceil(float(ml or 0) / 500 - 1e-9) * 0.5


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
    # "by_unit" (the new plan): a caffeinated product is ONLY taken at the
    # caffeine times, never per hour, even with no plain carb next to it.
    caffeine_on = caffeine_on and (has_plain_carb or bool((caffeine or {}).get("by_unit")))
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
            "volume_per_unit": float(p.get("volume_ml") or 0) if p.get("kind") == "drink" else 0.0,
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
                leg_units.append({"name": ln["name"], "kind": ln["kind"], "is_water": ln["is_water"], "units": u, "product_id": ln["product_id"]})
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
                "carry_ml": round(carried),  # fluid drunk since the last refill
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
    # Water to drink on a leg = the fluid target minus what the drink doses
    # already bring (a 500 ml flask of drink IS fluid).
    for leg in schedule:
        drink_ml = sum(u["units"] * lines[li]["volume_per_unit"] for li, u in enumerate(leg["units"]))
        leg["water_ml"] = max(0, round(leg["fluid_ml"] - drink_ml))

    # Where does each unit travel? From the start bag until the first drop bag /
    # crew point, then from that bag until the next one. What you carry at
    # once is the max over carry segments — that's the "sac" size.
    packing = []
    if schedule:
        resupply = {round(float(r.get("km") or 0), 1): r.get("name", "") for r in (resupply_points or []) if r.get("km")}

        def _group(at: str, km: float) -> dict:
            return {"at": at, "km": km, "until": None, "until_km": None, "legs": 0, "fluid_ml": 0, "units": {}, "carbs_g": 0, "time_s": 0}
        group = _group("Départ", 0.0)
        for leg in schedule:
            group["legs"] += 1
            group["fluid_ml"] += leg["fluid_ml"]
            group["carbs_g"] += leg["carbs_real_g"]
            group["time_s"] += leg["leg_time_s"]
            for u in leg["units"]:
                if u["units"] and not u["is_water"]:
                    key = u.get("product_id", u["name"])
                    ent = group["units"].setdefault(key, {"name": u["name"], "units": 0, "product_id": u.get("product_id")})
                    ent["units"] += u["units"]
            km = round(float(leg["km"] or 0), 1)
            if km in resupply:
                group["until"], group["until_km"] = resupply[km], km
                packing.append(group)
                group = _group(resupply[km], km)
        group["until"] = "Arrivée"
        group["until_km"] = schedule[-1]["km"]
        if group["legs"]:
            packing.append(group)
        for g in packing:
            g["units"] = list(g["units"].values())
            g["is_start"] = g["at"] == "Départ"


    # Hydration: between two refill points you carry what you drink. With a
    # capacity (flasks) a stretch that needs more is flagged; without one
    # (« auto ») the plan says how much to take from each refill point.
    hydration = None
    if fluid_per_h > 0 and schedule:
        refills = {round(float(k), 1) for k in (refill_kms or set())}
        cap = float(flask_capacity_ml) if flask_capacity_ml else None
        segs = []
        seg_from, seg_from_km = "Départ", 0.0
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
                    "from_km": seg_from_km,
                    "to_km": float(leg["km"] or 0),
                    "time_s": int(seg_time),
                    "need_ml": round(seg_fluid),
                    "capacity_ml": round(cap) if cap else None,
                    "ok": (seg_fluid <= cap + 1) if cap else True,
                    "shortfall_ml": max(0, round(seg_fluid - cap)) if cap else 0,
                })
                seg_from, seg_from_km = leg["to_name"], float(leg["km"] or 0)
                seg_time = 0.0
                seg_fluid = 0.0
        not_ok = [s for s in segs if not s["ok"]]
        worst = max(segs, key=lambda s: s["need_ml"], default=None)
        # Suggested carry = cover the longest single inter-refill segment.
        suggested = int(math.ceil((worst["need_ml"] if worst else (cap or 0)) / 100.0) * 100)
        hydration = {
            "capacity_ml": round(cap) if cap else None,
            "segments": segs,
            "feasible": all(s["ok"] for s in segs) if cap else None,
            "max_shortfall_ml": max((s["shortfall_ml"] for s in segs), default=0),
            "has_refills": bool(refills),
            "dry_count": len(not_ok),
            "segment_count": len(segs),
            "worst_segment": {"from_name": worst["from_name"], "to_name": worst["to_name"],
                              "need_ml": worst["need_ml"]} if worst else None,
            "suggested_capacity_ml": suggested,
        }
        # each carry segment (bag): its longest stretch without water
        for g in packing:
            lo, hi = float(g["km"] or 0), float(g["until_km"] or 0)
            inside = [s for s in segs if lo - 0.05 <= s["from_km"] < hi - 0.05]
            top = max(inside, key=lambda s: s["need_ml"], default=None)
            g["need_ml_max"] = top["need_ml"] if top else 0
            g["dry_stretch"] = ({"from": top["from_name"], "to": top["to_name"], "time_s": top["time_s"], "need_ml": top["need_ml"]}
                                if top else None)
            g["is_longest"] = bool(top is not None and top is worst and len(segs) > 1)
            g["short"] = bool(top is not None and not top["ok"])

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


def _legacy_auto_rates(target_carbs_per_h: float, target_sodium_per_h: float, products: list[dict]) -> dict[int, float]:
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


# ── The « Ravitaillement » plan: a few taps, the rest computed ──────────────
#
# The athlete picks products (chips), a stomach level and, rarely, a sweat
# level / the water he carries / a quantity by hand. Everything else (rates,
# caffeine times, bags, shopping list) is computed here, one way, for the
# Ravitaillement card, the passage rows, the print band and the watch export.

def _fill_carbs(target: float, prods: list[dict]) -> dict[int, float]:
    """Every product at least ½ per hour, then halves where they bring the
    total closest to the target (small products fine-tune)."""
    cr = {p["id"]: 0.5 for p in prods}

    def total() -> float:
        return sum(cr[p["id"]] * float(p.get("carbs_g") or 0) for p in prods)
    for _ in range(60):
        gap = target - total()
        best = min(prods, key=lambda p: abs(gap - 0.5 * float(p.get("carbs_g") or 0)))
        if abs(gap - 0.5 * float(best.get("carbs_g") or 0)) >= abs(gap):
            break
        cr[best["id"]] += 0.5
    return cr


def _fill_salt(target_sodium: float, covered: float, salts: list[dict]) -> dict[int, float]:
    """Salt covers whatever sodium the rest leaves: the half-step closest in RATIO."""
    out: dict[int, float] = {}
    target_sodium = float(target_sodium or 0)
    remaining = max(0.0, target_sodium - covered)
    for p in salts:
        if remaining <= 0:
            out[p["id"]] = 0.0
            continue
        exact = remaining / float(p["sodium_mg"]) / len(salts)
        lo, hi = math.floor(exact * 2) / 2, math.ceil(exact * 2) / 2

        def miss(r: float, p=p) -> float:
            got = covered + r * float(p["sodium_mg"]) * len(salts)
            return abs(math.log(max(got, 1.0) / max(target_sodium, 1.0)))
        out[p["id"]] = min((c for c in (lo, hi) if c > 0), key=miss, default=hi)
    return out


def auto_rates(
    target_carbs_per_h: float, target_sodium_per_h: float, products: list[dict],
    fluid_ml_per_h: float | None = None, manual: dict | None = None,
) -> dict[int, float]:
    """Units/h per product so the SELECTED products meet the targets together.

    Called with the three historical arguments it keeps its old behaviour. With
    a fluid target and/or a manual map (the Ravitaillement plan):
      1. manual products keep their rate; their carbs and sodium come off the targets;
      2. caffeinated products get no hourly rate (the caffeine plan places them,
         1.0 is only a "picked" marker that compute_plan zeroes);
      3. drinks: as many doses as the fluid target allows, never above it;
      4. bars: ½ per hour each next to a gel or a drink, else they fill like gels;
      5. gels fill the remaining carbs (each at least ½ per hour);
      6. salt fills the remaining sodium.
    """
    if fluid_ml_per_h is None and manual is None:
        return _legacy_auto_rates(target_carbs_per_h, target_sodium_per_h, products)
    manual = {int(k): float(v) for k, v in (manual or {}).items()}
    rates: dict[int, float] = {}
    got_carbs = got_sodium = got_fluid = 0.0
    auto = []
    for p in products:
        if p["id"] in manual:
            r = max(0.0, manual[p["id"]])
            rates[p["id"]] = r
            if role(p) != "caf":
                got_carbs += r * float(p.get("carbs_g") or 0)
                got_sodium += r * float(p.get("sodium_mg") or 0)
                if p.get("kind") == "drink":
                    got_fluid += r * float(p.get("volume_ml") or 500)
        else:
            auto.append(p)

    def of(r: str) -> list[dict]:
        return [p for p in auto if role(p) == r]
    for p in of("caf"):
        rates[p["id"]] = 1.0
    drinks = of("drink")
    fluid_left = max(0.0, float(fluid_ml_per_h or 0) - got_fluid)
    for p in drinks:
        vol = float(p.get("volume_ml") or 500)
        r = max(0.5, math.floor(fluid_left / vol / len(drinks) * 2 + 1e-9) / 2)
        rates[p["id"]] = r
        got_carbs += r * float(p.get("carbs_g") or 0)
        got_sodium += r * float(p.get("sodium_mg") or 0)
    bars, gels = of("bar"), of("gel")
    if bars and any(role(p) in ("gel", "drink") for p in products):
        for p in bars:
            rates[p["id"]] = 0.5
            got_carbs += 0.5 * float(p.get("carbs_g") or 0)
            got_sodium += 0.5 * float(p.get("sodium_mg") or 0)
        fill = gels
    else:
        fill = gels + bars
    if fill:
        for pid, r in _fill_carbs(float(target_carbs_per_h or 0) - got_carbs, fill).items():
            rates[pid] = r
            got_sodium += r * float(next(p for p in fill if p["id"] == pid).get("sodium_mg") or 0)
    rates.update(_fill_salt(target_sodium_per_h, got_sodium, of("salt")))
    return rates


def caffeine_cap_mg(weight_kg: float | None) -> int:
    return int(round(min(CAFFEINE_MAX_MG, CAFFEINE_MAX_MG_PER_KG * weight_kg) if weight_kg else CAFFEINE_MAX_MG))


def auto_caffeine(duration_s: float, unit_mg: float | None, weight_kg: float | None = None) -> dict:
    """One caffeinated unit per dose from 3 h, spaced so the doses the cap
    allows cover the race (100 mg gels on 19 h: 3 h, 8 h, 13 h, 18 h), double
    at dawn with small units. The spacing is floored to the half hour so the
    last allowed dose still lands before the finish."""
    unit = float(unit_mg or 0) or 50.0
    n = int(caffeine_cap_mg(weight_kg) // unit)
    hours = max(0.0, float(duration_s or 0) / 3600.0)
    every = max(2.5, math.floor((hours - 3.5) / max(1, n - 1) * 2) / 2)
    return {"enabled": True, "from_h": 3.0, "every_h": every, "dose_mg": unit, "boost_dawn": True, "by_unit": True}


def _carry(v) -> int | None:
    try:
        ml = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return ml if ml > 0 else None


def _legacy_targets_into(st: dict, nj: dict) -> None:
    """Old typed targets → a stomach level (60/75/90) or « ton réglage »."""
    t = nj.get("targets") or {}
    c = t.get("carbs_g_per_h")
    try:
        c = round(float(c)) if c not in (None, "") else None
    except (TypeError, ValueError):
        c = None
    if c and c > 0:
        if c in CARBS_LEVEL:
            st["level"] = CARBS_LEVEL[c]
        else:
            st["custom"]["carbs_g_per_h"] = c
    for k in ("fluid_ml_per_h", "sodium_mg_per_h"):
        try:
            if t.get(k) not in (None, ""):
                st["custom"][k] = round(float(t[k]))
        except (TypeError, ValueError):
            pass


def _empty_state(refills=None) -> dict:
    return {"picks": [], "manual": {}, "level": None, "sweat": None, "carry_ml": None, "custom": {},
            "refills": list(refills or []), "legacy": False, "explicit": False}


def upgrade_legacy(nj: dict) -> dict:
    """A plan saved before the Ravitaillement view (items with typed rates),
    read in memory: every item stays as typed (all manual), its targets and
    caffeine settings become « ton réglage ». compute_plan then receives the
    same arguments as before, so the numbers do not move."""
    nj = nj or {}
    st = _empty_state(nj.get("refills"))
    st["legacy"] = True
    for it in nj.get("items") or []:
        try:
            pid = int(it.get("product_id"))
        except (TypeError, ValueError):
            continue
        if pid not in st["picks"]:
            st["picks"].append(pid)
            st["manual"][pid] = float(it.get("per_hour") or 0)
    _legacy_targets_into(st, nj)
    if nj.get("caffeine") is not None:
        st["custom"]["caffeine"] = dict(nj["caffeine"])
    st["carry_ml"] = _carry(nj.get("flask_capacity_ml")) or 1000
    return st


def read_state(nj: dict | None) -> dict:
    """The athlete's choices stored on a route, whatever the shape it was saved in."""
    nj = nj or {}
    if nj.get("v") == 2:
        st = _empty_state(nj.get("refills"))
        for x in nj.get("picks") or []:
            try:
                pid = int(x)
            except (TypeError, ValueError):
                continue
            if pid not in st["picks"]:
                st["picks"].append(pid)
        for k, v in (nj.get("manual") or {}).items():
            try:
                st["manual"][int(k)] = max(0.0, float(v))
            except (TypeError, ValueError):
                continue
        st["custom"] = {k: v for k, v in (nj.get("custom") or {}).items() if v is not None}
        st["level"] = nj.get("level") if nj.get("level") in LEVEL_CARBS else None
        st["sweat"] = nj.get("sweat") if nj.get("sweat") in SWEAT_FACTOR else None
        st["carry_ml"] = _carry(nj.get("carry_ml"))
        # the athlete unticked everything: an empty plan, not the default again
        st["explicit"] = isinstance(nj.get("picks"), list)
        return st
    if nj.get("items"):
        return upgrade_legacy(nj)
    st = _empty_state(nj.get("refills"))  # no plan yet: the products will come from the default
    _legacy_targets_into(st, nj)
    st["carry_ml"] = _carry(nj.get("flask_capacity_ml"))
    return st


def resolve_inputs(
    nutrition_json: dict | None, pantry_by_id: dict, duration_s: float, mean_temp: float | None,
    weight_kg: float | None, default_from: dict | None = None,
) -> dict:
    """Everything compute_plan needs, from what is stored on the route.

    No plan yet → a virtual default (never saved by a read): the products of
    the athlete's newest other race that has some (``default_from`` =
    {"name", "nutrition_json"}), else Gel + Sel (+ Gel caféiné from 8 h).
    """
    products = with_generics(pantry_by_id or {})
    st = read_state(nutrition_json)
    picks = [p for p in st["picks"] if p in products]
    level, sweat, carry = st["level"], st["sweat"], st["carry_ml"]
    custom = dict(st["custom"])
    is_virtual = not picks and not (st.get("explicit") and not st["picks"])
    legacy = st["legacy"] and not is_virtual
    source = None
    if is_virtual:
        src = read_state(default_from.get("nutrition_json")) if default_from else None
        src_picks = [p for p in (src or {}).get("picks", []) if p in products]
        if src_picks:
            picks, source = src_picks, default_from.get("name")
            # the route's own settings (old typed targets) win over the copied ones
            if level is None and "carbs_g_per_h" not in custom:
                level = src["level"]
            if sweat is None and "fluid_ml_per_h" not in custom and "sodium_mg_per_h" not in custom:
                sweat = src["sweat"]
            if carry is None:
                carry = src["carry_ml"]
        else:
            picks = [-1, -3] + ([-4] if (duration_s or 0) >= 8 * 3600 else [])
    manual = {} if is_virtual else {pid: r for pid, r in st["manual"].items() if pid in picks}

    base = default_targets((duration_s or 0) / 3600.0, mean_temp)
    fac = SWEAT_FACTOR[sweat or "normal"]
    carbs = custom.get("carbs_g_per_h") or (LEVEL_CARBS[level] if level else (base["carbs_g_per_h"] if legacy else LEVEL_CARBS["normal"]))
    fluid = custom["fluid_ml_per_h"] if "fluid_ml_per_h" in custom else int(round(base["fluid_ml_per_h"] * fac / 10.0) * 10)
    sodium = custom["sodium_mg_per_h"] if "sodium_mg_per_h" in custom else int(round(base["sodium_mg_per_h"] * fac / 10.0) * 10)
    targets = {"carbs_g_per_h": carbs, "fluid_ml_per_h": fluid, "sodium_mg_per_h": sodium}
    sel = [products[p] for p in picks]
    caf = [p for p in sel if role(p) == "caf"]
    if legacy:  # exactly what the plan said before (see upgrade_legacy)
        items = [{"product_id": pid, "per_hour": manual.get(pid, 0.0)} for pid in picks]
        caffeine = {**CAFFEINE_DEFAULTS, **(custom.get("caffeine") or {})}
    else:
        rates = auto_rates(carbs, sodium, sel, fluid_ml_per_h=fluid, manual=manual)
        items = [{"product_id": pid, "per_hour": round(rates[pid], 2)} for pid in picks if rates.get(pid, 0) > 0]
        if custom.get("caffeine"):
            caffeine = {**CAFFEINE_DEFAULTS, **custom["caffeine"], "enabled": bool(caf), "by_unit": True}
        elif caf:
            caffeine = auto_caffeine(duration_s, caf[0].get("caffeine_mg"), weight_kg)
        else:
            caffeine = {**CAFFEINE_DEFAULTS, "enabled": False}
    sweat_eff = None if ("fluid_ml_per_h" in custom or "sodium_mg_per_h" in custom) else (sweat or "normal")
    level_eff = None if "carbs_g_per_h" in custom else CARBS_LEVEL.get(carbs)
    return {
        "targets": targets, "items": items, "products_by_id": products, "flask_capacity_ml": carry,
        "caffeine": caffeine, "picks": picks, "manual": manual, "level": level_eff, "sweat": sweat_eff,
        "carry_ml": carry, "custom": custom, "source": source, "is_virtual": is_virtual, "legacy": legacy,
        "refills": st["refills"], "rates": {it["product_id"]: float(it["per_hour"]) for it in items},
        "has_caf": bool(caf), "weight_kg": weight_kg, "cap_mg": caffeine_cap_mg(weight_kg),
        "hand_set": bool(manual) or any(k in custom for k in ("carbs_g_per_h", "fluid_ml_per_h", "sodium_mg_per_h", "caffeine")),
        # what a write starts from (the resolved state, the copied default included)
        "state": {"picks": list(picks), "manual": dict(manual), "level": level, "sweat": sweat, "carry_ml": carry,
                  "custom": dict(custom), "refills": list(st["refills"])},
    }


def pick_into(picks: list[int], pid: int, products_by_id: dict) -> list[int]:
    """Add a product to the picks; a real product takes the place of the picked
    quick pick of the same role (PF 90 replaces « Gel »)."""
    if pid in picks or pid not in products_by_id:
        return list(picks)
    out, pos = list(picks), len(picks)
    if pid > 0:
        r = role(products_by_id[pid])
        same = [i for i, x in enumerate(out) if x < 0 and x in products_by_id and role(products_by_id[x]) == r]
        if same:
            pos = same[0]
            out = [x for i, x in enumerate(out) if i not in same]
    out.insert(min(pos, len(out)), pid)
    return out


def apply_op(state: dict, op: str, value=None, products_by_id: dict | None = None, rates: dict | None = None) -> dict:
    """One tap → the next stored state (see the write rules of the plan)."""
    st = {**state, "picks": list(state.get("picks") or []), "manual": dict(state.get("manual") or {}),
          "custom": dict(state.get("custom") or {}), "refills": list(state.get("refills") or [])}
    products_by_id = products_by_id or {}
    if op == "level" and value in LEVEL_CARBS:
        st["level"] = value
        st["custom"].pop("carbs_g_per_h", None)
        st["manual"] = {}
    elif op == "toggle":
        pid = int(value)
        if pid in st["picks"]:
            st["picks"] = [x for x in st["picks"] if x != pid]
        else:
            st["picks"] = pick_into(st["picks"], pid, products_by_id)
        st["manual"] = {}
    elif op == "pick":  # add, never remove (catalogue, « un produit à toi »)
        pid = int(value)
        if pid not in st["picks"]:
            st["picks"] = pick_into(st["picks"], pid, products_by_id)
            st["manual"] = {}
    elif op == "unpick":
        pid = int(value)
        st["picks"] = [x for x in st["picks"] if x != pid]
        st["manual"].pop(pid, None)
    elif op == "sweat" and value in SWEAT_FACTOR:
        st["sweat"] = value
        st["custom"].pop("fluid_ml_per_h", None)
        st["custom"].pop("sodium_mg_per_h", None)
    elif op == "carry":
        st["carry_ml"] = None if value in (None, "", "auto") else _carry(value)
    elif op == "step":
        pid, step = value
        cur = st["manual"].get(pid, (rates or {}).get(pid, 0.5))
        st["manual"][pid] = max(0.5, round((float(cur) + float(step)) * 2) / 2)
    elif op == "reset":
        st["manual"] = {}
        st["custom"] = {}
    return st


def state_json(st: dict) -> dict:
    """The stored v2 shape (the echo keys are added by with_echo)."""
    custom = {k: v for k, v in (st.get("custom") or {}).items() if v is not None}
    nj = {"v": 2, "picks": list(st.get("picks") or []), "manual": {str(k): v for k, v in (st.get("manual") or {}).items()},
          "carry_ml": st.get("carry_ml"), "custom": custom, "refills": list(st.get("refills") or [])}
    if st.get("level") in LEVEL_CARBS and "carbs_g_per_h" not in custom:
        nj["level"] = st["level"]
    if st.get("sweat") in SWEAT_FACTOR and "fluid_ml_per_h" not in custom and "sodium_mg_per_h" not in custom:
        nj["sweat"] = st["sweat"]
    return nj


def with_echo(nj: dict, resolved: dict) -> dict:
    """Old readers (and a rollback) read targets / items / flask / caffeine: write them, never read them."""
    return {**nj, "targets": resolved["targets"], "items": resolved["items"],
            "flask_capacity_ml": resolved["carry_ml"] or 1000, "caffeine": resolved["caffeine"]}


def main_product_id(plan: dict) -> int | None:
    """The product a spare is packed of: the non-caffeinated, non-salt one
    that brings the most carbs per hour (tie → the first picked)."""
    best, best_v = None, 0.0
    for ln in plan.get("lines") or []:
        if ln["caffeine_per_unit"] > 0 or ln["carbs_per_unit"] <= 0 or ln["is_water"] or ln.get("by_caffeine"):
            continue
        v = ln["per_hour"] * ln["carbs_per_unit"]
        if v > best_v + 1e-9:
            best, best_v = ln["product_id"], v
    return best


def _product_of(pid, name: str, products_by_id: dict) -> dict:
    return products_by_id.get(pid) or {"name": name}


def leg_labels(leg: dict, products_by_id: dict) -> list[str]:
    """'6 gels', '1 gel caféiné'… for one leg of the schedule (no spare)."""
    return [unit_label(u["units"], _product_of(u.get("product_id"), u["name"], products_by_id))
            for u in leg["units"] if u["units"] and not u["is_water"]]


def shopping_list(plan: dict, main_pid: int | None, products_by_id: dict) -> list[dict]:
    """What to buy = the sum of the bags, each bag holding one spare of the
    main carb product. Sets plan["bags"] (the packing groups with their spare
    and their labels); the per-leg schedule is left untouched."""
    bags = []
    lines_by_pid = {ln["product_id"]: ln for ln in plan.get("lines") or []}
    order = {pid: i for i, pid in enumerate(lines_by_pid)}
    for g in plan.get("packing") or []:
        # in the order of the picks, whatever leg a product first shows up on
        units = sorted((dict(u) for u in g["units"]), key=lambda u: order.get(u.get("product_id"), len(order)))
        if main_pid is not None and main_pid in lines_by_pid:
            ent = next((u for u in units if u.get("product_id") == main_pid), None)
            if ent is None:
                ent = {"name": lines_by_pid[main_pid]["name"], "units": 0, "product_id": main_pid}
                units.append(ent)
            ent["units"] += 1
            ent["spare"] = 1
        bags.append({**g, "units": units, "spare": 1 if main_pid is not None else 0,
                     "labels": [unit_label(u["units"], _product_of(u.get("product_id"), u["name"], products_by_id)) for u in units if u["units"]]})
    plan["bags"] = bags
    totals: dict = {}
    for g in bags:
        for u in g["units"]:
            t = totals.setdefault(u.get("product_id"), {"n": 0, "spares": 0, "name": u["name"]})
            t["n"] += u["units"]
            t["spares"] += u.get("spare", 0)
    out = []
    for pid in [ln["product_id"] for ln in plan.get("lines") or [] if ln["product_id"] in totals]:
        t = totals[pid]
        if t["n"] <= 0:
            continue
        p = _product_of(pid, t["name"], products_by_id)
        noun = (p["one"] if t["n"] == 1 else p["many"]) if p.get("one") else f"× {short_label(p)}"
        note = ""
        if t["spares"]:
            note = f"dont {t['spares']} de secours"
        elif (p.get("caffeine_mg") or 0) > 0:
            note = f"{int(round(t['n'] * float(p['caffeine_mg'])))} mg de caféine en tout"
        elif pid == -2:
            note = "1 dose = 1 flasque de 500 ml"
        out.append({"product_id": pid, "n": t["n"], "spares": t["spares"], "noun": noun, "label": f"{t['n']} {noun}", "note": note})
    return out


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " et " + parts[-1]


def _clock(s: float) -> str:
    s = int(s) % 86400
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"


def caffeine_words(cfp: dict | None, noun: str) -> str | None:
    """'1 gel caféiné' + its clock times, in one rule line (HTML)."""
    from markupsafe import escape

    if not cfp or not cfp.get("doses"):
        return None
    doses = cfp["doses"]
    if len(doses) <= 6:
        parts = [_clock(d["clock_s"]) + (f" (×{d['units']})" if (d.get("units") or 1) > 1 else "") for d in doses]
        return f"<b>{escape(noun)}</b> à {_join(parts)}"
    every = _hm_words(float((cfp.get("settings") or {}).get("every_h") or 2.5) * 60)
    dawn2 = any((d.get("units") or 1) > 1 for d in doses)
    return (f"<b>{escape(noun)}</b> toutes les {every}, de {_clock(doses[0]['clock_s'])} à {_clock(doses[-1]['clock_s'])}"
            + (", 2 à l'aube" if dawn2 else ""))


def rule_lines(plan: dict, picks: list[int], products_by_id: dict, has_refills: bool) -> list[dict]:
    """« Ta règle pour toute la course »: one line per product in whole units
    and plain intervals, then the water line. [{"icon", "html"}]."""
    from markupsafe import escape

    by_pid = {ln["product_id"]: ln for ln in plan.get("lines") or []}
    entries = []
    for pid in picks:
        ln, p = by_pid.get(pid), products_by_id.get(pid)
        if not ln or not p or ln["is_water"]:
            continue
        if ln.get("by_caffeine"):
            entries.append(("caf", pid))
        elif ln["per_hour"] > 0:
            entries.append(("rate", pid))
    groups: dict = {}
    for kind, pid in entries:
        if kind == "rate" and role(products_by_id[pid]) in ("gel", "bar"):
            groups.setdefault(by_pid[pid]["per_hour"], []).append(pid)
    merged = {r: pids for r, pids in groups.items() if len(pids) >= 2}
    out, done = [], set()
    for kind, pid in entries:
        if pid in done:
            continue
        p, ln = products_by_id[pid], by_pid[pid]
        done.add(pid)
        if kind == "caf":
            html = caffeine_words(plan.get("caffeine"), rule_noun(p))
            if html:
                out.append({"icon": "bolt", "html": html})
            continue
        rate, r = ln["per_hour"], role(p)
        if r in ("gel", "bar") and pid in merged.get(rate, []):
            pids = merged[rate]
            done.update(pids)
            noun = "1 gel" if any(role(products_by_id[x]) == "gel" for x in pids) else "1 barre"
            names = _join([str(escape(short_label(products_by_id[x]))) for x in pids])
            out.append({"icon": "gel", "html": f"<b>{noun}</b> {interval_words(rate * len(pids))}, en alternant {names}"})
            continue
        if rate > 4:
            many = p.get("many") or f"× {short_label(p)}"
            html = f"<b>{_fr(rate)} {escape(many)}</b> par heure"
        else:
            html = f"<b>{escape(rule_noun(p))}</b> {interval_words(rate)}"
        if r == "drink":
            html += f" (une flasque de {int(p.get('volume_ml') or 500)} ml)"
        out.append({"icon": {"drink": "drink", "salt": "salt", "bar": "bar"}.get(r, "gel"), "html": html})
    cfp = plan.get("caffeine")
    if cfp and cfp.get("doses") and not any(ln.get("by_caffeine") for ln in plan.get("lines") or []):
        html = caffeine_words(cfp, f"{cfp['doses'][0]['mg']} mg de caféine")  # an old plan: caffeine without a product
        if html:
            out.append({"icon": "bolt", "html": html})
    fluid = float((plan.get("targets") or {}).get("fluid_ml_per_h") or 0)
    if fluid > 0:
        drink = any(role(products_by_id.get(ln["product_id"]) or {}) == "drink" and ln["per_hour"] > 0 for ln in plan.get("lines") or [])
        out.append({"icon": "drop", "html": f"<b>Bois {liters(fluid)} L</b> par heure"
                    + (", remplis à chaque point d'eau" if has_refills else "") + (" (ta boisson comprise)" if drink else "")})
    return out


def plan_real_rates(plan: dict) -> tuple[float, float]:
    """Real carbs and sodium per hour over the race, from the whole units."""
    legs = plan.get("schedule") or []
    secs = sum(lg["leg_time_s"] for lg in legs)
    if not secs:
        ph = plan.get("per_hour") or {}
        return float(ph.get("carbs_g") or 0), float(ph.get("sodium_mg") or 0)
    carbs = sum(lg["carbs_real_g"] for lg in legs) / (secs / 3600.0)
    sodium = sum(lg["sodium_real_per_h"] * lg["leg_time_s"] for lg in legs) / secs
    return carbs, sodium


def rule_warnings(plan: dict, picks: list[int], products_by_id: dict, has_refills: bool) -> list[dict]:
    """Only what the runner can act on, with the fix in the same line."""
    t = plan.get("targets") or {}
    carbs, sodium = plan_real_rates(plan)
    tc, ts = float(t.get("carbs_g_per_h") or 0), float(t.get("sodium_mg_per_h") or 0)
    sel = [products_by_id[p] for p in picks if p in products_by_id]
    out = []
    if tc > 0 and carbs < 0.85 * tc:
        out.append({"tone": "warn", "text": "Pas assez de glucides avec ça : ajoute un gel ou une boisson."})
    elif tc > 0 and carbs > 1.2 * tc:
        out.append({"tone": "warn", "text": "Un peu trop pour ton estomac : retire un produit."})
    if ts > 0 and sodium < 0.5 * ts:
        out.append({"tone": "muted", "text": "Pas de sel dans tes produits : ajoute « Sel »."})
    if any(role(p) == "caf" for p in sel) and not any(role(p) in ("gel", "drink", "bar") and (p.get("carbs_g") or 0) > 0 for p in sel):
        out.append({"tone": "warn", "text": "Ajoute un gel sans caféine : la caféine ne se prend pas toutes les heures."})
    if not has_refills:
        out.append({"tone": "muted", "text": "Indique les points d'eau dans le plan (ouvre un point › Type de poste) pour savoir combien d'eau porter."})
    return out


def copy_text(course: str, shop: list[dict], bags: list[dict], bag_titles: list[str]) -> str:
    """The shopping list as plain text, for « Copier la liste »."""
    lines = [f"{course} : à acheter"]
    lines += [it["label"] + (f" ({it['note']})" if it["note"] else "") for it in shop]
    lines.append("")
    lines += [f"{title} : " + (", ".join(g.get("labels") or []) or "rien") for g, title in zip(bags, bag_titles, strict=False)]
    return "\n".join(lines)
