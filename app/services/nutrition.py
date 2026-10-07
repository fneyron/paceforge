"""Race nutrition: products, the athlete's choices for a race, caffeine.

« Tu prépares par tronçon, tu manges au bip » : the athlete packs one sachet
per stretch between two food ravitos and eats on one watch beep. This module
holds what the plan is built FROM (the catalogue, the quick picks, the stored
choices of a race, the caffeine doses); app.services.nutrition_plan turns that
into the stretch table, the bags and the shopping list.

Nothing here calls an LLM: the numbers are reproducible and explainable.
"""

import math

# Recommended intake guidelines (endurance, trained gut), used by the triathlon
# plan. The trail plan takes its carbs from the level the athlete has practised.
_CARBS_SHORT = 60.0   # g/h, efforts < 3h
_CARBS_MID = 75.0     # g/h, 3–6h
_CARBS_LONG = 80.0    # g/h, > 6h
_FLUID_BASE = 500.0   # ml/h at mild temperature
_SODIUM_BASE = 400.0  # mg/h at mild temperature


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


def _clock_hour(clock_s: float) -> float:
    return (clock_s % 86400) / 3600.0


# One-click catalogue of common race products (per unit, label values; the
# athlete can still edit them in « Tes produits »). Keys are stable slugs;
# "short" is what a chip, a bag and the shopping list show. ``servings`` = the
# prises in one unit (PF 90: a resealable 90 g pouch taken in 3 goes of 30 g);
# a salt tablet's ``volume_ml`` is the water it dissolves in.
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

# Generic quick picks: virtual products (never pantry rows), always available
# next to the pantry. -1/-2/-3 keep their historical values so old plans that
# point at them keep their numbers.
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
GENERIC_ORDER = [-1, -4, -2, -5, -3]  # how the unpicked quick picks line up

# What a ravito offers, eaten there (never packed, never bought): ≈ values,
# nothing assumed until the athlete taps one. Coca counts ≈ 10 mg of caffeine
# per 100 ml.
AID_FOODS: list[dict] = [
    {"id": -10, "name": "Coca (25 cl)", "label": "Coca", "kind": "aid", "carbs_g": 26, "sodium_mg": 10, "caffeine_mg": 25, "volume_ml": None},
    {"id": -11, "name": "Banane", "label": "Banane", "kind": "aid", "carbs_g": 25, "sodium_mg": 0, "caffeine_mg": None, "volume_ml": None},
    {"id": -12, "name": "Soupe", "label": "Soupe", "kind": "aid", "carbs_g": 5, "sodium_mg": 700, "caffeine_mg": None, "volume_ml": None},
    {"id": -13, "name": "Pâtes ou riz", "label": "Pâtes/riz", "kind": "aid", "carbs_g": 40, "sodium_mg": 300, "caffeine_mg": None, "volume_ml": None},
    {"id": -14, "name": "Compote", "label": "Compote", "kind": "aid", "carbs_g": 20, "sodium_mg": 0, "caffeine_mg": None, "volume_ml": None},
]
AID_BY_ID = {p["id"]: p for p in AID_FOODS}

LEVEL_CARBS = {"fragile": 60, "normal": 75, "solide": 90}
CARBS_LEVEL = {v: k for k, v in LEVEL_CARBS.items()}
SWEAT_FACTOR = {"peu": 0.8, "normal": 1.0, "beaucoup": 1.25}
CARRY_CHOICES = [1000, 1500, 2000]
RESERVE_CHOICES = [0, 30, 60]  # minutes of the bag's main product


def with_generics(pantry_by_id: dict) -> dict:
    """Quick picks + ravito food + the pantry: the products a plan can point at."""
    return {**{p["id"]: dict(p) for p in GENERIC_PRODUCTS}, **{p["id"]: dict(p) for p in AID_FOODS}, **pantry_by_id}


def short_label(p: dict) -> str:
    """Chip / bag label: the quick pick's word, the catalogue's short name, else the name."""
    if p.get("label"):
        return p["label"]
    name = (p.get("name") or "").strip()
    c = CATALOG_BY_NAME.get(name.lower())
    return c["short"] if c else (name or "?")


def role(p: dict) -> str:
    """What a product stands in for: aid, caf, salt, drink, bar or gel."""
    if p.get("kind") == "aid":
        return "aid"
    if (p.get("caffeine_mg") or 0) > 0:
        return "caf"
    if not (p.get("carbs_g") or 0) and (p.get("sodium_mg") or 0) > 0:
        return "salt"
    if p.get("kind") == "drink":
        return "drink"
    if p.get("kind") in ("bar", "solid"):
        return "bar"
    return "gel"


def is_fuel(p: dict) -> bool:
    """A product the stretches fill with (gels, drinks, bars)."""
    return role(p) in ("gel", "drink", "bar")


def servings_of(p: dict) -> int:
    """Prises in one unit: the product's own value, else the catalogue's (a PF 90
    the athlete typed himself), else 1."""
    try:
        own = int(p.get("servings") or 0)
    except (TypeError, ValueError):
        own = 0
    if own > 1:
        return min(own, 12)
    c = CATALOG_BY_NAME.get((p.get("name") or "").strip().lower())
    return int(c.get("servings") or 1) if c else 1


def _fr(x: float) -> str:
    """12.5 → '12,5', 3.0 → '3'."""
    return (f"{x:.2f}".rstrip("0").rstrip(".") if not float(x).is_integer() else str(int(x))).replace(".", ",")


def unit_label(n: float, p: dict) -> str:
    """'1 gel', '3 gels', '2 pastilles de sel', '4 PF 90'."""
    n = int(round(n))
    if p.get("one"):
        return f"{n} {p['one'] if n == 1 else p['many']}"
    return f"{n} {short_label(p)}"


def _hm_words(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    if h and m:
        return f"{h}h{m:02d}"
    return f"{h} h" if h else f"{m} min"


def liters(ml: float, step: int = 50) -> str:
    """650 → '0,65', 1500 → '1,5' (rounded to `step` ml)."""
    return _fr(round(float(ml or 0) / step) * step / 1000)


def ceil_half_l(ml: float) -> float:
    """Water to carry, rounded up to 0,5 L (in litres)."""
    return math.ceil(float(ml or 0) / 500 - 1e-9) * 0.5


def _clock(s: float) -> str:
    s = int(s) % 86400
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"


# ── Caffeine ────────────────────────────────────────────────────────────────
#
# Small doses (1–3 mg/kg) from 3 h, spaced, a full dose at dawn. A HARD cap of
# min(400 mg, 6 mg/kg) per rolling 24 h (EFSA; Guest 2021): a 27 h race keeps
# a dose for its second night. None in the last 30 min. Cola at a ravito
# counts (≈ 10 mg/100 ml).

CAFFEINE_DEFAULTS = {"enabled": False, "from_h": 3.0, "every_h": 2.5, "dose_mg": 50, "boost_dawn": True}
CAFFEINE_MAX_MG = 400
CAFFEINE_MAX_MG_PER_KG = 6.0
CAFFEINE_WINDOW_S = 24 * 3600
CAFFEINE_LAST_S = 30 * 60  # no dose in the last 30 min


def caffeine_cap_mg(weight_kg: float | None) -> int:
    """The most caffeine in any 24 h: min(400 mg, 6 mg/kg)."""
    return int(round(min(CAFFEINE_MAX_MG, CAFFEINE_MAX_MG_PER_KG * weight_kg) if weight_kg else CAFFEINE_MAX_MG))


def rolling_max_mg(events: list[tuple[float, float]], window_s: float = CAFFEINE_WINDOW_S) -> int:
    """The most caffeine taken inside any window (clock seconds, mg)."""
    ev = sorted((float(t), float(mg)) for t, mg in events if mg)
    best, lo, acc = 0.0, 0, 0.0
    for t, mg in ev:
        acc += mg
        while ev[lo][0] <= t - window_s:
            acc -= ev[lo][1]
            lo += 1
        best = max(best, acc)
    return int(round(best))


def caffeine_schedule(
    duration_s: float,
    legs: list[dict],
    settings: dict | None,
    start_offset_s: int = 0,
    weight_kg: float | None = None,
    product_name: str | None = None,
    unit_mg: float | None = None,
    events: list[tuple[float, float]] | None = None,
) -> dict | None:
    """Timed caffeine doses on MOVING time (the legs' cum_s), each with the clock
    it falls on (stops included: ``clock_s``). A dose that would put any 24 h
    above the cap is skipped, the next one is tried (``events`` = caffeine
    already planned elsewhere, e.g. cola at a ravito: (clock_s, mg)).
    Returns {doses, total_mg, max_mg, over} or None when disabled."""
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
    max_mg = caffeine_cap_mg(weight_kg)

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

    taken = [(float(t), float(mg)) for t, mg in (events or []) if mg]
    doses, t, total = [], from_s, 0.0
    dawn_done = False
    end_s = min(float(duration_s), float(legs[-1]["cum_s"])) if legs else float(duration_s)
    while t < end_s - CAFFEINE_LAST_S:
        clk = clock_at(t)
        mg = dose
        label = "petite dose"
        dawn = cfg.get("boost_dawn") and not dawn_done and 5.0 <= _clock_hour(clk) < 7.5
        if dawn:
            # dawn: a full dose — with 100 mg gels one gel already is one
            mg, label = (dose if (unit_mg and unit_mg >= 100) else dose * 2), "dose pleine (aube)"
        def fits(m: float) -> bool:  # every 24 h holding this dose stays under the cap
            return rolling_max_mg(taken + [(clk, m)]) <= max_mg + 1
        if dawn and mg > dose and not fits(mg) and fits(dose):
            mg, label = dose, "petite dose"  # no room for the double: a single one
        if fits(mg):
            if dawn:
                dawn_done = True
            leg = leg_at(t)
            doses.append({
                "elapsed_s": int(t), "clock_s": int(clk), "mg": int(round(mg)), "label": label,
                "leg_to": leg["to_name"] if leg else "", "product": product_name,
                "units": int(round(mg / unit_mg)) if unit_mg else None,
            })
            taken.append((clk, mg))
            total += mg
        t += every_s
    return {"doses": doses, "total_mg": int(round(total)), "max_mg": int(round(max_mg)),
            "over": rolling_max_mg(taken) > max_mg + 1, "settings": cfg}


def auto_caffeine(duration_s: float, unit_mg: float | None, weight_kg: float | None = None) -> dict:
    """One caffeinated unit per dose from 3 h. Up to a day long, the doses the
    24 h cap allows are spread over the race (100 mg gels on 19 h: 3 h, 8 h,
    13 h, 18 h; on 27 h: 3 h, 10h30, 18 h, 25h30, the second night). Longer,
    they are spaced so no 24 h holds more than the cap. ``duration_s`` is the
    MOVING time: the doses are placed on it, the stops come on top."""
    unit = float(unit_mg or 0) or 50.0
    n = max(1, int(caffeine_cap_mg(weight_kg) // unit))
    hours = max(0.0, float(duration_s or 0) / 3600.0)
    if hours - 3.5 <= 24 or n <= 1:
        every = max(2.5, math.floor((hours - 3.5) / max(1, n - 1) * 2) / 2)
    else:
        every = max(2.5, math.ceil(24.0 / n * 2) / 2)
    return {"enabled": True, "from_h": 3.0, "every_h": every, "dose_mg": unit, "boost_dawn": True, "by_unit": True}


# ── The athlete's choices for a race (Route.nutrition_json) ────────────────
#
# Stored shape v3 (written by state_json, the echo keys by with_echo):
#   {"v": 3, "picks": [pid], "phases": [{"km", "mix": {pid: share}}],
#    "rows": {"58.1": {pid: n}}, "aid": {"58.1": {aid_id: n}}, "have": {pid: n},
#    "reserve_min": 0|30|60|null, "level", "sweat", "carry_ml", "custom", "undo"}
# Keys are the stretch's start km rounded to 0.1 (checkpoints have no stable
# id). Older shapes (v2, the legacy items) are read in memory, never rewritten
# by a read.

def _carry(v) -> int | None:
    try:
        ml = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return ml if ml > 0 else None


def _km(v) -> float | None:
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _legacy_targets_into(st: dict, nj: dict) -> None:
    """Old typed targets → a level (60/75/90) or « ton réglage »."""
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
    try:
        if t.get("fluid_ml_per_h") not in (None, ""):
            st["custom"]["fluid_ml_per_h"] = round(float(t["fluid_ml_per_h"]))
    except (TypeError, ValueError):
        pass


def _empty_state(refills=None) -> dict:
    return {"picks": [], "shares": {}, "phases": [], "rows": {}, "aid": {}, "have": {}, "reserve_min": None,
            "level": None, "sweat": None, "carry_ml": None, "custom": {}, "refills": list(refills or []),
            "legacy": False, "explicit": False, "version": 0, "undo": None}


def upgrade_legacy(nj: dict) -> dict:
    """A plan saved before the Ravitaillement view (items with typed rates), read
    in memory: its products are the list, their rates the shares of one phase,
    its targets and caffeine settings « ton réglage »."""
    nj = nj or {}
    st = _empty_state(nj.get("refills"))
    st["legacy"] = True
    st["version"] = 1
    for it in nj.get("items") or []:
        pid = _int(it.get("product_id"))
        if pid is None:
            continue
        if pid not in st["picks"]:
            st["picks"].append(pid)
            st["shares"][pid] = float(it.get("per_hour") or 0)
    _legacy_targets_into(st, nj)
    # the old form saved its caffeine block on every save, switched off by
    # default: only a caffeine plan the athlete turned on is « his setting »
    if isinstance(nj.get("caffeine"), dict) and nj["caffeine"].get("enabled"):
        st["custom"]["caffeine"] = dict(nj["caffeine"])
    st["carry_ml"] = _carry(nj.get("flask_capacity_ml")) or 1000
    return st


def _read_mix(mix) -> dict | None:
    if mix is None:
        return None
    out: dict = {}
    if isinstance(mix, list):
        for x in mix:
            pid = _int(x)
            if pid is not None:
                out[pid] = 1.0
        return out
    if isinstance(mix, dict):
        for k, v in mix.items():
            pid = _int(k)
            try:
                share = float(v)
            except (TypeError, ValueError):
                share = 1.0
            if pid is not None and share > 0:
                out[pid] = share
        return out
    return None


def _read_counts(d) -> dict:
    out: dict = {}
    for k, v in (d or {}).items() if isinstance(d, dict) else []:
        km = _km(k)
        if km is None or not isinstance(v, dict):
            continue
        row = {}
        for pk, n in v.items():
            pid, n = _int(pk), _int(n)
            if pid is not None and n is not None and n >= 0:
                row[pid] = n
        out[km] = row
    return out


def read_state(nj: dict | None) -> dict:
    """The athlete's choices stored on a route, whatever the shape it was saved in."""
    nj = nj or {}
    if nj.get("v") in (2, 3):
        st = _empty_state(nj.get("refills"))
        st["version"] = int(nj["v"])
        for x in nj.get("picks") or []:
            pid = _int(x)
            if pid is not None and pid not in st["picks"]:
                st["picks"].append(pid)
        st["custom"] = {k: v for k, v in (nj.get("custom") or {}).items() if v is not None and k != "sodium_mg_per_h"}
        st["level"] = nj.get("level") if nj.get("level") in LEVEL_CARBS else None
        st["sweat"] = nj.get("sweat") if nj.get("sweat") in SWEAT_FACTOR else None
        st["carry_ml"] = _carry(nj.get("carry_ml"))
        # the athlete unticked everything: an empty plan, not the default again
        st["explicit"] = isinstance(nj.get("picks"), list)
        if nj["v"] == 2:
            # per-hour rates by hand → the shares of one phase (no per-stretch meaning left)
            for k, v in (nj.get("manual") or {}).items():
                pid = _int(k)
                try:
                    if pid is not None and float(v) > 0:
                        st["shares"][pid] = float(v)
                except (TypeError, ValueError):
                    continue
            return st
        for ph in nj.get("phases") or []:
            if not isinstance(ph, dict):
                continue
            km = _km(ph.get("km"))
            if km is None:
                continue
            st["phases"].append({"km": km, "mix": _read_mix(ph.get("mix"))})
        st["phases"].sort(key=lambda p: p["km"])
        st["rows"] = _read_counts(nj.get("rows"))
        st["aid"] = {k: {a: n for a, n in v.items() if a in AID_BY_ID and n > 0} for k, v in _read_counts(nj.get("aid")).items()}
        st["aid"] = {k: v for k, v in st["aid"].items() if v}
        for k, v in (nj.get("have") or {}).items() if isinstance(nj.get("have"), dict) else []:
            pid, n = _int(k), _int(v)
            if pid is not None and n and n > 0:
                st["have"][pid] = n
        rm = _int(nj.get("reserve_min"))
        st["reserve_min"] = rm if rm in RESERVE_CHOICES else None
        st["undo"] = nj.get("undo") if isinstance(nj.get("undo"), dict) else None
        return st
    if nj.get("items"):
        return upgrade_legacy(nj)
    st = _empty_state(nj.get("refills"))  # no plan yet: the products will come from the default
    _legacy_targets_into(st, nj)
    st["carry_ml"] = _carry(nj.get("flask_capacity_ml"))
    return st


def race_rate(level_or_custom: float, race_s: float) -> float:
    """The practised level, capped by the duration ladder (Jeukendrup 2014):
    under 1 h nothing, 1–2 h up to 30 g/h, 2–3 h up to 60."""
    h = float(race_s or 0) / 3600.0
    r = float(level_or_custom or 0)
    if h < 1:
        return 0.0
    if h < 2:
        return min(r, 30.0)
    if h < 3:
        return min(r, 60.0)
    return r


def resolve_inputs(
    nutrition_json: dict | None, pantry_by_id: dict, duration_s: float, mean_temp: float | None,
    weight_kg: float | None, default_from: dict | None = None, moving_s: float | None = None,
    start_offset_s: float | None = None,
) -> dict:
    """Everything the stretch plan needs, from what is stored on the route.

    No plan yet → a virtual default (never saved by a read): the list and the
    first mix of the athlete's newest other race that has some
    (``default_from`` = {"name", "nutrition_json"}), else Gel + Sel (+ Gel
    caféiné from 8 h). ``moving_s`` (the plan without its stops) places the
    caffeine doses; the duration is used when it is not known.
    """
    products = with_generics(pantry_by_id or {})
    st = read_state(nutrition_json)
    picks = [p for p in st["picks"] if p in products and p not in AID_BY_ID]
    level, sweat, carry = st["level"], st["sweat"], st["carry_ml"]
    custom = dict(st["custom"])
    shares = dict(st["shares"])
    phases = [dict(p) for p in st["phases"]]
    is_virtual = not picks and not (st.get("explicit") and not st["picks"])
    legacy = st["legacy"] and not is_virtual
    source = None
    if is_virtual:
        src = read_state(default_from.get("nutrition_json")) if default_from else None
        src_picks = [p for p in (src or {}).get("picks", []) if p in products and p not in AID_BY_ID]
        if src_picks:
            picks, source = src_picks, default_from.get("name")
            shares = dict(src["shares"])
            # the first mix travels; the kms of another race do not
            first = (src["phases"] or [{}])[0].get("mix") if src["phases"] else None
            phases = [{"km": 0.0, "mix": dict(first)}] if first else []
            if level is None and "carbs_g_per_h" not in custom:
                level = src["level"]
                if level is None and src["custom"].get("carbs_g_per_h"):
                    custom["carbs_g_per_h"] = src["custom"]["carbs_g_per_h"]
            if sweat is None and "fluid_ml_per_h" not in custom:
                sweat = src["sweat"]
            # an old plan's flask (1 L: the old form's default) is not a choice
            # to spread to a new race: only a carry picked in the new view is
            if carry is None and not src["legacy"]:
                carry = src["carry_ml"]
        else:
            picks = [-1, -3] + ([-4] if (duration_s or 0) >= 8 * 3600 else [])
    level_carbs = custom.get("carbs_g_per_h") or (LEVEL_CARBS[level] if level else LEVEL_CARBS["normal"])
    sel = [products[p] for p in picks]
    caf = [p for p in sel if role(p) == "caf"]
    moving = float(moving_s or duration_s or 0)
    if custom.get("caffeine"):
        caffeine = {**CAFFEINE_DEFAULTS, **custom["caffeine"], "enabled": bool(caf), "by_unit": True}
    elif caf:
        caffeine = auto_caffeine(moving, caf[0].get("caffeine_mg"), weight_kg)
    else:
        caffeine = {**CAFFEINE_DEFAULTS, "enabled": False}
    fuel = [p["id"] for p in sel if is_fuel(p)]
    if not phases:
        phases = [{"km": 0.0, "mix": None}]
    if phases[0]["km"] > 0:
        phases.insert(0, {"km": 0.0, "mix": None})
    for ph in phases:
        if ph["mix"] is None:  # the default: every fuel product of the list (an old plan's rates as shares)
            ph["mix"] = {pid: (shares.get(pid) or 1.0) for pid in fuel}
        ph["mix"] = {pid: s for pid, s in ph["mix"].items() if pid in products and is_fuel(products[pid]) and pid in picks}
    rows = {} if is_virtual else {k: {p: n for p, n in v.items() if p in products and p not in AID_BY_ID} for k, v in st["rows"].items()}
    aid = {} if is_virtual else {k: dict(v) for k, v in st["aid"].items()}
    have = {p: n for p, n in st["have"].items() if p in products}
    reserve_set = st["reserve_min"]
    sweat_eff = None if "fluid_ml_per_h" in custom else (sweat or "normal")
    level_eff = None if "carbs_g_per_h" in custom else CARBS_LEVEL.get(level_carbs)
    return {
        "products_by_id": products, "picks": picks, "phases": phases, "rows": rows, "aid": aid, "have": have,
        "reserve_set": reserve_set, "level": level_eff, "level_carbs": float(level_carbs), "sweat": sweat_eff,
        "sweat_factor": SWEAT_FACTOR[sweat or "normal"], "carry_ml": carry, "flask_capacity_ml": carry,
        "custom": custom, "caffeine": caffeine, "caf_pid": caf[0]["id"] if caf else None,
        "source": source, "is_virtual": is_virtual, "legacy": legacy, "upgraded": (not is_virtual) and st["version"] < 3,
        "refills": st["refills"], "has_caf": bool(caf), "weight_kg": weight_kg, "cap_mg": caffeine_cap_mg(weight_kg),
        "hand_set": bool(rows) or any(k in custom for k in ("carbs_g_per_h", "fluid_ml_per_h", "caffeine")),
        "undo": None if is_virtual else st["undo"],
        # what a write starts from (the resolved state, the copied default included)
        "state": {"picks": list(picks), "phases": [{"km": p["km"], "mix": dict(p["mix"])} for p in phases],
                  "rows": {k: dict(v) for k, v in rows.items()}, "aid": {k: dict(v) for k, v in aid.items()},
                  "have": dict(have), "reserve_min": reserve_set, "level": level, "sweat": sweat, "carry_ml": carry,
                  "custom": dict(custom), "refills": list(st["refills"]), "undo": None},
    }


def pick_into(picks: list[int], pid: int, products_by_id: dict) -> list[int]:
    """Add a product to the list; a real product takes the place of the picked
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


def _replaced(before: list[int], after: list[int]) -> list[int]:
    return [x for x in before if x not in after]


def _phase_at(phases: list[dict], km: float) -> int | None:
    for i, ph in enumerate(phases):
        if abs(ph["km"] - km) < 0.05:
            return i
    return None


def _mix_in_force(phases: list[dict], km: float) -> dict:
    mix: dict = {}
    for ph in phases:
        if ph["km"] <= km + 1e-6:
            mix = dict(ph["mix"])
    return mix


def _phase_end(phases: list[dict], km: float) -> float:
    later = [p["km"] for p in phases if p["km"] > km + 0.05]
    return min(later) if later else math.inf


def _purge(st: dict, pid: int) -> None:
    st["picks"] = [x for x in st["picks"] if x != pid]
    for ph in st["phases"]:
        ph["mix"].pop(pid, None)
    for k in list(st["rows"]):
        st["rows"][k].pop(pid, None)
        if not any(st["rows"][k].values()):
            st["rows"].pop(k)
    st["have"].pop(pid, None)


def _seed(plan: dict | None, key: float) -> dict:
    """A row's current automatic counts (what a first tap freezes)."""
    for s in (plan or {}).get("stretches") or []:
        if abs(s["key"] - key) < 0.05:
            return {it["pid"]: it["n"] for it in s["items"] if not it.get("at_aid")}
    return {}


def apply_op(state: dict, op: str, value=None, products_by_id: dict | None = None, plan: dict | None = None) -> dict:
    """One tap → the next stored state.

    ``value`` per op: level/sweat → the key; carry/reserve → the number;
    toggle/pick/unpick/have → pid (have: (pid, d)); n → (km, pid, d) one row,
    frozen whole on its first tap; add → (km, pid) one more on that row only;
    auto → km; aid → (km, aid_id, d); mix → (km, pid) « à partir d'ici »;
    unswitch → km; pick with (pid, km) → into the phase at km.
    """
    products_by_id = products_by_id or {}
    st = {
        **state,
        "picks": list(state.get("picks") or []),
        "phases": [{"km": p["km"], "mix": dict(p.get("mix") or {})} for p in state.get("phases") or []] or [{"km": 0.0, "mix": {}}],
        "rows": {k: dict(v) for k, v in (state.get("rows") or {}).items()},
        "aid": {k: dict(v) for k, v in (state.get("aid") or {}).items()},
        "have": dict(state.get("have") or {}),
        "custom": dict(state.get("custom") or {}),
        "refills": list(state.get("refills") or []),
    }
    prev_undo = state.get("undo")
    st["undo"] = None
    stretch_keys = [s["key"] for s in (plan or {}).get("stretches") or []]

    def snap(km) -> float | None:
        km = _km(km)
        if km is None:
            return None
        if not stretch_keys:
            return km
        best = min(stretch_keys, key=lambda k: abs(k - km))
        return best if abs(best - km) <= 0.5 else None

    def add_to_mix(mix: dict, pid: int, before: list[int]) -> None:
        for x in _replaced(before, st["picks"]):
            mix.pop(x, None)
        mix.setdefault(pid, 1.0)

    if op == "level" and value in LEVEL_CARBS:
        st["level"] = value
        st["custom"].pop("carbs_g_per_h", None)
    elif op in ("toggle", "pick", "unpick"):
        pid, km = (value if isinstance(value, tuple) else (value, None))
        pid = int(pid)
        if op == "unpick" or (op == "toggle" and pid in st["picks"]):
            _purge(st, pid)
        elif pid in products_by_id and pid not in AID_BY_ID:
            before = list(st["picks"])
            phase_only = km is not None and (snap(km) or 0) > 0
            # on the whole race a real product takes the quick pick's place; from a
            # ravito on it only joins (the earlier phases keep their « Gel »)
            st["picks"] = (st["picks"] + ([pid] if pid not in st["picks"] else [])) if phase_only else pick_into(st["picks"], pid, products_by_id)
            gone = _replaced(before, st["picks"])
            for x in gone:  # the quick pick it replaces leaves every phase and row
                for ph in st["phases"]:
                    ph["mix"].pop(x, None)
                for k in st["rows"]:
                    st["rows"][k].pop(x, None)
            if is_fuel(products_by_id[pid]):
                k = snap(km) if km is not None else None
                if k is None or k <= 0:
                    targets = st["phases"] if km is None else [st["phases"][0]]
                    for ph in targets:
                        ph["mix"].setdefault(pid, 1.0)
                else:
                    i = _phase_at(st["phases"], k)
                    if i is None:
                        st["phases"].append({"km": k, "mix": _mix_in_force(st["phases"], k)})
                        st["phases"].sort(key=lambda p: p["km"])
                        i = _phase_at(st["phases"], k)
                    add_to_mix(st["phases"][i]["mix"], pid, before)
    elif op == "sweat" and value in SWEAT_FACTOR:
        st["sweat"] = value
        st["custom"].pop("fluid_ml_per_h", None)
    elif op == "carry":
        st["carry_ml"] = None if value in (None, "", "auto") else _carry(value)
    elif op == "reserve":
        v = _int(value)
        if v in RESERVE_CHOICES:
            st["reserve_min"] = v
    elif op in ("n", "add"):
        km, pid, d = (value + (1,))[:3] if op == "add" else value
        k, pid = snap(km), int(pid)
        if k is not None and pid in products_by_id and pid not in AID_BY_ID:
            row = st["rows"].get(k)
            if row is None:
                row = _seed(plan, k)
            if op == "add":
                row[pid] = max(0, row.get(pid, 0)) + 1
            else:
                row[pid] = max(0, row.get(pid, 0) + int(d))
            st["rows"][k] = row
            if pid not in st["picks"]:
                st["picks"].append(pid)
    elif op == "auto":
        k = snap(value)
        if k is not None:
            st["rows"].pop(k, None)
    elif op == "aid":
        km, aid_id, d = value
        k, aid_id = snap(km), int(aid_id)
        if k is not None and aid_id in AID_BY_ID:
            row = st["aid"].get(k, {})
            n = max(0, row.get(aid_id, 0) + int(d))
            if n:
                row[aid_id] = n
            else:
                row.pop(aid_id, None)
            if row:
                st["aid"][k] = row
            else:
                st["aid"].pop(k, None)
    elif op == "have":
        pid, d = value
        pid = int(pid)
        n = max(0, st["have"].get(pid, 0) + int(d))
        if n:
            st["have"][pid] = n
        else:
            st["have"].pop(pid, None)
    elif op == "mix":
        km, pid = value
        k, pid = snap(km), int(pid)
        if k is not None and pid in products_by_id and is_fuel(products_by_id[pid]):
            snapshot = {"phases": [{"km": p["km"], "mix": {str(x): s for x, s in p["mix"].items()}} for p in st["phases"]],
                        "rows": {str(r): {str(x): n for x, n in v.items()} for r, v in st["rows"].items()}}
            before = list(st["picks"])
            if pid not in st["picks"]:
                st["picks"].append(pid)  # joins the list; the other phases keep what they have
            i = _phase_at(st["phases"], k)
            if i is None:
                st["phases"].append({"km": k, "mix": _mix_in_force(st["phases"], k)})
                st["phases"].sort(key=lambda p: p["km"])
                i = _phase_at(st["phases"], k)
            mix = st["phases"][i]["mix"]
            cleared = 0
            if pid in mix:
                mix.pop(pid)
                # « à partir d'ici » sans ce produit : the hand-set rows of this
                # phase that hold it go back to auto (an « Annuler » undoes it)
                end = _phase_end(st["phases"], k)
                for r in [r for r in st["rows"] if k - 0.05 <= r < end - 0.05 and st["rows"][r].get(pid)]:
                    st["rows"].pop(r)
                    cleared += 1
            else:
                add_to_mix(mix, pid, before)
            # a change back to what was already in force is no change
            if i > 0 and set(st["phases"][i]["mix"]) == set(st["phases"][i - 1]["mix"]):
                st["phases"].pop(i)
            if cleared:
                st["undo"] = {**snapshot, "cleared": cleared}
    elif op == "unswitch":
        k = snap(value)
        i = _phase_at(st["phases"], k) if k is not None else None
        if i:
            st["undo"] = {"phases": [{"km": p["km"], "mix": {str(x): s for x, s in p["mix"].items()}} for p in st["phases"]],
                          "rows": {str(r): {str(x): n for x, n in v.items()} for r, v in st["rows"].items()}, "cleared": 0}
            st["phases"].pop(i)
    elif op == "undo" and prev_undo:
        st["phases"] = [{"km": _km(p["km"]), "mix": _read_mix(p.get("mix")) or {}} for p in prev_undo.get("phases") or []] or st["phases"]
        st["rows"] = _read_counts(prev_undo.get("rows"))
    elif op == "reset":
        st["rows"], st["aid"] = {}, {}
        st["phases"] = st["phases"][:1]
        st["custom"] = {}
        st["reserve_min"] = None
    return st


def state_json(st: dict) -> dict:
    """The stored v3 shape (the echo keys are added by with_echo)."""
    custom = {k: v for k, v in (st.get("custom") or {}).items() if v is not None}
    nj = {
        "v": 3, "picks": list(st.get("picks") or []),
        "phases": [{"km": p["km"], "mix": {str(k): v for k, v in (p.get("mix") or {}).items()}} for p in st.get("phases") or []],
        "rows": {str(k): {str(p): int(n) for p, n in v.items()} for k, v in (st.get("rows") or {}).items()},
        "aid": {str(k): {str(p): int(n) for p, n in v.items()} for k, v in (st.get("aid") or {}).items() if v},
        "have": {str(k): int(v) for k, v in (st.get("have") or {}).items() if v},
        "reserve_min": st.get("reserve_min"),
        "carry_ml": st.get("carry_ml"), "custom": custom, "refills": list(st.get("refills") or []),
    }
    if st.get("undo"):
        nj["undo"] = st["undo"]
    if st.get("level") in LEVEL_CARBS and "carbs_g_per_h" not in custom:
        nj["level"] = st["level"]
    if st.get("sweat") in SWEAT_FACTOR and "fluid_ml_per_h" not in custom:
        nj["sweat"] = st["sweat"]
    return nj


def with_echo(nj: dict, plan: dict | None, resolved: dict) -> dict:
    """Old readers (and a rollback) read targets / items / flask / caffeine:
    write them, never read them. ``items`` = each product's race average
    (total units / hours)."""
    hours = max(float((plan or {}).get("race_s") or 0) / 3600.0, 1e-9)
    totals: dict = {}
    for s in (plan or {}).get("stretches") or []:
        for it in s["items"]:
            if not it.get("at_aid"):
                totals[it["pid"]] = totals.get(it["pid"], 0) + it["packed"]
    items = [{"product_id": pid, "per_hour": round(n / hours, 2)} for pid, n in totals.items() if n > 0] if plan else []
    rate = (plan or {}).get("rate_g_h") or resolved.get("level_carbs") or 75
    return {**nj, "manual": {}, "targets": {"carbs_g_per_h": round(rate), "fluid_ml_per_h": round((plan or {}).get("fluid_ml_per_h") or 500)},
            "items": items, "flask_capacity_ml": resolved.get("carry_ml") or 1000, "caffeine": resolved.get("caffeine")}


def copy_text(course: str, shop: list[dict], bags: list[dict], stretches: list[dict]) -> str:
    """The list, then each bag and its stretches, as plain text for « Copier »."""
    lines = [f"{course} : à acheter"]
    lines += [f"{it['to_buy']} {it['label']}" for it in shop if it["to_buy"] > 0] or ["rien, tu as tout"]
    for g in bags:
        lines.append("")
        lines.append(f"{g['title']} : " + (", ".join(g.get("labels") or []) or "rien"))
        for i in g["stretch_idx"]:
            s = stretches[i]
            lines.append(f"  {s['clock']} → {s['to_name']} : " + (", ".join(s.get("labels") or []) or "rien"))
    return "\n".join(lines)
