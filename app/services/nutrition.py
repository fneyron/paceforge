"""Race nutrition: the evidence, the product catalogue, the caffeine cap.

The athlete fills « Tes produits » once (NutritionProduct, the same for every
race) and says, per race, when he eats each one; app.services.nutrition_plan
counts the rest. This module holds what that plan is built FROM and checked
AGAINST: the carbohydrate guidelines, the catalogue of common products, the
caffeine cap.

Nothing here calls an LLM: the numbers are reproducible and explainable.
"""

# Recommended intake guidelines (endurance, trained gut): the triathlon plan
# and « Plan type » take their carbs per hour from the race's duration.
_CARBS_SHORT = 60.0   # g/h, efforts < 3h
_CARBS_MID = 75.0     # g/h, 3–6h
_CARBS_LONG = 80.0    # g/h, > 6h
_FLUID_BASE = 500.0   # ml/h at mild temperature
_SODIUM_BASE = 400.0  # mg/h at mild temperature

# The zone a race plan is read against (Jeukendrup 2014): below 60 g/h a long
# effort runs short of carbs; above 90 g/h only a gut trained for it copes.
CARBS_ZONE_G_H = (60, 90)

# The carbs per hour of the level picker of older plans (fragile / normal /
# solide), kept to read those plans.
LEVEL_CARBS = {"fragile": 60, "normal": 75, "solide": 90}


def default_targets(duration_h: float, mean_temp_c: float | None) -> dict:
    """Hourly targets from duration and temperature (triathlon plan)."""
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
        fluid += min(400.0, over * 25.0)
        sodium += min(500.0, over * 35.0)
    return {
        "carbs_g_per_h": round(carbs),
        "fluid_ml_per_h": int(round(fluid / 10.0) * 10),
        "sodium_mg_per_h": int(round(sodium / 10.0) * 10),
    }


# One-click catalogue of common race products (per unit, label values; the
# athlete can still edit them in « Tes produits »). Keys are stable slugs;
# "short" is what a plan line and a ravito row show. ``servings`` = the prises
# in one unit (PF 90: a resealable 90 g pouch taken in 3 goes of 30 g).
PRODUCT_CATALOG: list[dict] = [
    {"key": "maurten-gel-100", "name": "Maurten Gel 100", "short": "Maurten 100", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "kcal": 100, "caffeine_mg": None, "volume_ml": None},
    {"key": "maurten-gel-100-caf", "name": "Maurten Gel 100 CAF 100", "short": "Maurten 100 CAF", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "kcal": 100, "caffeine_mg": 100, "volume_ml": None},
    {"key": "maurten-gel-160", "name": "Maurten Gel 160", "short": "Maurten 160", "kind": "gel", "carbs_g": 40, "sodium_mg": 30, "kcal": 160, "caffeine_mg": None, "volume_ml": None},
    {"key": "pf-30-gel", "name": "Precision Fuel PF 30 Gel", "short": "PF 30", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "kcal": 120, "caffeine_mg": None, "volume_ml": None},
    {"key": "pf-30-caf", "name": "Precision Fuel PF 30 Caffeine Gel", "short": "PF 30 Caféine", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "kcal": 120, "caffeine_mg": 100, "volume_ml": None},
    {"key": "pf-90-gel", "name": "Precision Fuel PF 90 Gel", "short": "PF 90", "kind": "gel", "carbs_g": 90, "sodium_mg": 0, "kcal": 360, "caffeine_mg": None, "volume_ml": None, "servings": 3},
    {"key": "ph-1500", "name": "Precision Hydration PH 1500 (pastille, 500 ml)", "short": "PH 1500", "kind": "salt", "carbs_g": 0, "sodium_mg": 750, "kcal": None, "caffeine_mg": None, "volume_ml": 500},
    {"key": "ph-1000", "name": "Precision Hydration PH 1000 (pastille, 500 ml)", "short": "PH 1000", "kind": "salt", "carbs_g": 0, "sodium_mg": 500, "kcal": None, "caffeine_mg": None, "volume_ml": 500},
    {"key": "baouw-gel", "name": "Baouw Gel", "short": "Baouw", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "kcal": 120, "caffeine_mg": None, "volume_ml": None},
    {"key": "gu-gel", "name": "GU Energy Gel", "short": "GU", "kind": "gel", "carbs_g": 22, "sodium_mg": 60, "kcal": 100, "caffeine_mg": None, "volume_ml": None},
    {"key": "neversecond-c30", "name": "Neversecond C30 Gel", "short": "Neversecond C30", "kind": "gel", "carbs_g": 30, "sodium_mg": 200, "kcal": 120, "caffeine_mg": None, "volume_ml": None},
    {"key": "tailwind", "name": "Tailwind Endurance Fuel (1 dose)", "short": "Tailwind", "kind": "drink", "carbs_g": 25, "sodium_mg": 303, "kcal": 100, "caffeine_mg": None, "volume_ml": 500},
    {"key": "saltstick", "name": "SaltStick Caps", "short": "SaltStick", "kind": "salt", "carbs_g": 0, "sodium_mg": 215, "kcal": None, "caffeine_mg": None, "volume_ml": None},
]
CATALOG_BY_KEY = {c["key"]: c for c in PRODUCT_CATALOG}
CATALOG_BY_NAME = {c["name"].lower(): c for c in PRODUCT_CATALOG}

# Generic products: virtual (never pantry rows). « Plan type » takes the
# generic gel when « Tes produits » holds no gel; older plans may point at the
# others. The ids keep their historical values.
GENERIC_PRODUCTS: list[dict] = [
    {"id": -1, "name": "Gel", "kind": "gel", "carbs_g": 25, "sodium_mg": 0, "kcal": 100, "caffeine_mg": None, "volume_ml": None,
     "label": "Gel", "one": "gel", "many": "gels"},
    {"id": -2, "name": "Boisson glucidique", "kind": "drink", "carbs_g": 22, "sodium_mg": 300, "kcal": 90, "caffeine_mg": None, "volume_ml": 500,
     "label": "Boisson", "one": "dose de boisson", "many": "doses de boisson"},
    {"id": -3, "name": "Pastille de sel", "kind": "salt", "carbs_g": 0, "sodium_mg": 300, "kcal": None, "caffeine_mg": None, "volume_ml": 500,
     "label": "Sel", "one": "pastille de sel", "many": "pastilles de sel"},
    {"id": -4, "name": "Gel caféiné", "kind": "gel", "carbs_g": 25, "sodium_mg": 0, "kcal": 100, "caffeine_mg": 50, "volume_ml": None,
     "label": "Gel caféiné", "one": "gel caféiné", "many": "gels caféinés"},
    {"id": -5, "name": "Barre", "kind": "bar", "carbs_g": 30, "sodium_mg": 100, "kcal": 150, "caffeine_mg": None, "volume_ml": None,
     "label": "Barre", "one": "barre", "many": "barres"},
]
GENERIC_BY_ID = {p["id"]: p for p in GENERIC_PRODUCTS}
GENERIC_GEL = -1


def with_generics(pantry_by_id: dict) -> dict:
    """The generic products + the pantry: what a plan line can point at."""
    return {**{p["id"]: dict(p) for p in GENERIC_PRODUCTS}, **pantry_by_id}


def short_label(p: dict) -> str:
    """A plan line's / a ravito row's label: the generic's word, the catalogue's short name, else the name."""
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


def servings_of(p: dict) -> int:
    """Prises in one unit. A pantry row always carries its own value (the
    athlete may set a PF 90 to 1 prise): it is trusted. Only a product without
    one (a catalogue entry, an old dict) takes the catalogue's, and only when
    its carbs are those of a whole pouch (a PF 90 typed per prise, 30 g, is 1)."""
    raw = p.get("servings")
    if raw not in (None, ""):
        try:
            return max(1, min(int(raw), 12))
        except (TypeError, ValueError, OverflowError):
            return 1
    c = CATALOG_BY_NAME.get((p.get("name") or "").strip().lower())
    if not c or int(c.get("servings") or 1) <= 1:
        return 1
    try:
        carbs = float(p["carbs_g"]) if p.get("carbs_g") not in (None, "") else None
    except (TypeError, ValueError):
        carbs = None
    if carbs is not None and carbs < float(c["carbs_g"]) * 2 / 3:
        return 1
    return int(c["servings"])


def per_prise(p: dict, key: str) -> float:
    """A label value (carbs_g, sodium_mg, caffeine_mg) for ONE prise."""
    try:
        v = float(p.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0
    return v / servings_of(p) if v > 0 else 0.0


def _fr(x: float) -> str:
    """12.5 → '12,5', 3.0 → '3'."""
    return (f"{x:.2f}".rstrip("0").rstrip(".") if not float(x).is_integer() else str(int(x))).replace(".", ",")


# ── Caffeine ────────────────────────────────────────────────────────────────
#
# A HARD cap of min(400 mg, 6 mg/kg) per rolling 24 h (EFSA; Guest 2021): a
# 27 h race keeps a dose for its second night. The plan says where it stands.

CAFFEINE_MAX_MG = 400
CAFFEINE_MAX_MG_PER_KG = 6.0
CAFFEINE_WINDOW_S = 24 * 3600


def caffeine_cap_mg(weight_kg: float | None) -> int:
    """The most caffeine in any 24 h: min(400 mg, 6 mg/kg)."""
    return int(round(min(CAFFEINE_MAX_MG, CAFFEINE_MAX_MG_PER_KG * weight_kg) if weight_kg else CAFFEINE_MAX_MG))


def rolling_max_mg(events: list[tuple[float, float]], window_s: float = CAFFEINE_WINDOW_S) -> int:
    """The most caffeine taken inside any window (seconds, mg)."""
    ev = sorted((float(t), float(mg)) for t, mg in events if mg)
    best, lo, acc = 0.0, 0, 0.0
    for t, mg in ev:
        acc += mg
        while ev[lo][0] <= t - window_s:
            acc -= ev[lo][1]
            lo += 1
        best = max(best, acc)
    return int(round(best))
