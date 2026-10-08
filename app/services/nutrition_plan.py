"""Nutrition v2: « tes produits une fois, un rythme par produit, le reste calculé ».

You eat on a clock; you refill at aid stations. The athlete says only WHEN he
eats each product: a rhythm, « 1 toutes les 60 min, de 0 h à la fin » (two
lines switch product mid-race: Maurten until 8 h, Precision 90 from 8 h). The
aid stations are only where the bag is refilled: each refill point gets the
intakes that fall before the next one, in whole units. A ravito at 30 min
simply gets what the next stretch needs (0 or 1 gel), never half a gel. A
product taken in prises (a PF 90 pouch, 3 × 30 g) is taken in whole pouches,
and the prises left in an opened pouch count toward the next stretch.

Each refill point also gets the water to carry to the next one, from the
stretch's predicted time (water_ml); a hot race (is_hot, from the temperatures
the simulator read in the race's saved forecast) is counted at a higher rate
and has its sodium per hour said against the hot-weather range.

Times are seconds after the start, on the plan's arrival clocks (stops
included). Pure functions: no database, no request.

Route.nutrition_json, v2:
    {"v": 2, "rhythms": [{"product_id", "every_min", "from_min", "to_min" | null}], "spare": false}
Older shapes are read by read_plan, never rewritten by a read.
"""

from __future__ import annotations

import math

from app.services import nutrition as N
from app.services.checkpoints import is_resupply

INTERVALS = (15, 20, 30, 40, 45, 60, 90, 120)  # minutes between two intakes of one line
MAX_LINES = 8
MAX_MIN = 7 * 24 * 60  # a « de » or « à » past a week is a typo
ROW_CHARS = 36  # characters a ravito row holds on one line at 358 px, its name and what to take (the clock aside)


def _round(x: float) -> int:
    """Half up (Python's round() is banker's rounding)."""
    return int(math.floor(x + 0.5))


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):  # « abc », « nan », « inf »
        return None


# ── the clocks (the simulator's sections) ───────────────────────────────────

def moving_cum(s: dict) -> float:
    v = s.get("adjusted_cumulative_time_s")
    if v is None:
        v = s.get("cumulative_time_s")
    return float(v or 0)


def arrival_clock(s: dict, start_offset_s: int) -> float:
    """A section's arrival clock: the plan's when there is one, else the prediction's."""
    v = s.get("adjusted_clock_time_s")
    if v is None:
        v = s.get("clock_time_s")
    if v is None:
        v = start_offset_s + moving_cum(s)
    return float(v)


def clock_hm(clock_s: float) -> str:
    s = int(clock_s) % 86400
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"


def race_end_s(sections: list[dict], start_offset_s: int) -> int | None:
    """Seconds from the start to the finish, or None without a simulation (no
    sections, or no running time in them: the stops alone are no race)."""
    if not sections or moving_cum(sections[-1]) <= 0:
        return None
    end = _round(arrival_clock(sections[-1], start_offset_s) - start_offset_s)
    return end if end > 0 else None


def refill_points(sections: list[dict], checkpoints: list[dict], start_offset_s: int) -> list[dict]:
    """Départ, then every point where the bag is refilled (a food ravito, a base
    vie, a crew or drop-bag point: checkpoints.is_resupply), in race order, with
    its arrival time. Two points at the same km are one stop."""
    pts = [{"key": 0.0, "name": "Départ", "km": 0.0, "t_s": 0, "clock_s": float(start_offset_s),
            "crew": False, "base": False, "drop": False, "start": True}]
    for s in sections[:-1]:  # the last section ends at the finish
        idx = s.get("end_checkpoint_index")
        cp = checkpoints[idx] if isinstance(idx, int) and 0 <= idx < len(checkpoints) else None
        if not cp or not is_resupply(cp):
            continue
        km = round(float(s.get("end_km") or 0), 1)
        flags = {"crew": bool(cp.get("crew")), "base": cp.get("kind") == "base", "drop": bool(cp.get("drop_bag"))}
        last = pts[-1]
        if not last["start"] and abs(km - last["km"]) < 0.05:
            for k, v in flags.items():
                last[k] = last[k] or v
            continue
        clock = arrival_clock(s, start_offset_s)
        pts.append({"key": km, "name": s.get("end_name") or cp.get("name") or "", "km": km,
                    "t_s": max(last["t_s"], _round(clock - start_offset_s)), "clock_s": clock, **flags, "start": False})
    return pts


# ── the plan ────────────────────────────────────────────────────────────────

def intakes(rhythms: list[dict], end_s: int) -> list[tuple[int, int, int]]:
    """[(t_s, product_id, prises)], in time order. A line's first intake is
    its start plus one interval; « à » is the last moment it may fall on, the
    finish is not (nobody eats on the line)."""
    out = []
    for order, r in enumerate(rhythms):
        every = int(r["every_min"]) * 60
        if every <= 0:
            continue
        to = r.get("to_min")
        limit, inclusive = (to * 60, True) if to is not None and to * 60 < end_s else (end_s, False)
        t = int(r.get("from_min") or 0) * 60 + every
        while t < limit or (inclusive and t == limit):
            out.append((t, order, int(r["product_id"])))
            t += every
    out.sort()
    return [(t, pid, 1) for t, _order, pid in out]


def _units(n: int, per: int, have: int) -> tuple[int, int]:
    """(whole units to take, prises left in the opened one) for ``n`` prises
    with ``have`` prises already in an opened unit."""
    if per <= 1:
        return n, 0
    units = math.ceil(max(0, n - have) / per)
    return units, have + units * per - n


def ravito_rows(points: list[dict], end_s: int, intake_list: list[tuple[int, int, int]], products: dict,
                order: list[int] | None = None, spare_pid: int | None = None) -> list[dict]:
    """One row per refill point: what to take when leaving it, the intakes of
    [this point, the next one) in whole units. ``spare_pid``: one more unit of
    it on every row (« +1 de secours »)."""
    rank = {p: i for i, p in enumerate(order or [])}
    bounds = [p["t_s"] for p in points[1:]] + [end_s]
    open_left: dict[int, int] = {}
    rows, j = [], 0
    for i, p in enumerate(points):
        counts: dict[int, int] = {}
        while j < len(intake_list) and intake_list[j][0] < bounds[i]:
            t, pid, n = intake_list[j]
            if t >= p["t_s"]:
                counts[pid] = counts.get(pid, 0) + n
            j += 1
        items = []
        for pid in sorted(counts, key=lambda x: rank.get(x, 999)):
            have = open_left.get(pid, 0)
            units, left = _units(counts[pid], N.servings_of(products[pid]), have)
            if N.servings_of(products[pid]) > 1:
                open_left[pid] = left
            items.append({"pid": pid, "units": units, "prises": counts[pid], "left": left, "spare": 0})
        if spare_pid is not None and spare_pid in products:
            it = next((x for x in items if x["pid"] == spare_pid), None)
            if it is None:
                it = {"pid": spare_pid, "units": 0, "prises": 0, "left": 0, "spare": 0}
                items.append(it)
            it["units"] += 1
            it["spare"] = 1
        rows.append({**p, "end_s": bounds[i], "items": items})
    return rows


def shopping(rows: list[dict]) -> dict[int, int]:
    """Units per product for the race: the sum of the rows (spares included)."""
    out: dict[int, int] = {}
    for r in rows:
        for it in r["items"]:
            if it["units"]:
                out[it["pid"]] = out.get(it["pid"], 0) + it["units"]
    return out


def label_per_hour(intake_list: list[tuple[int, int, int]], products: dict, end_s: int, key: str) -> float:
    """Σ of a label value (carbs_g, sodium_mg) taken, per prise, / race hours."""
    if not end_s or end_s <= 0:
        return 0.0
    return sum(n * N.per_prise(products[pid], key) for _t, pid, n in intake_list) / (end_s / 3600.0)


def carbs_per_hour(intake_list: list[tuple[int, int, int]], products: dict, end_s: int) -> float:
    """Σ carbs eaten / race hours."""
    return label_per_hour(intake_list, products, end_s, "carbs_g")


def sodium_per_hour(intake_list: list[tuple[int, int, int]], products: dict, end_s: int) -> float:
    """Σ sodium taken / race hours (mg/h), counted like the carbs: the plan's products' label values, per prise."""
    return label_per_hour(intake_list, products, end_s, "sodium_mg")


def caffeine_24h(intake_list: list[tuple[int, int, int]], products: dict) -> int:
    """The most caffeine in any 24 h of the plan (mg)."""
    return N.rolling_max_mg([(t, n * N.per_prise(products[pid], "caffeine_mg")) for t, pid, n in intake_list])


def holds_caffeine(rhythms: list[dict], products: dict) -> bool:
    return any(N.per_prise(products[r["product_id"]], "caffeine_mg") > 0 for r in rhythms if r["product_id"] in products)


def main_product(rhythms: list[dict], intake_list: list[tuple[int, int, int]], products: dict) -> int | None:
    """The plan's main product: the most carbs over the race, then the most
    prises, then the first line."""
    order = [r["product_id"] for r in rhythms if r["product_id"] in products]
    if not order:
        return None
    g: dict[int, float] = {}
    n: dict[int, int] = {}
    for _t, pid, k in intake_list:
        g[pid] = g.get(pid, 0.0) + k * N.per_prise(products[pid], "carbs_g")
        n[pid] = n.get(pid, 0) + k
    return max(dict.fromkeys(order), key=lambda p: (round(g.get(p, 0.0), 6), n.get(p, 0), -order.index(p)))


def carbs_note(g_h: int, duration_h: float) -> tuple[str, str]:
    """(the sentence, its tone: ok, warn, plain) for a plan's carbs per hour, against the range of the race's
    duration (nutrition.carbs_zone): « Dans le repère pour un ultra : 30 à 50 g par heure. »; above it, train
    the gut (ISSN 2019), never a warning; below it, the warning colour."""
    zone = N.carbs_zone(duration_h)
    if zone is None:
        return "Sur moins d'une heure, manger n'est pas nécessaire.", "plain"
    lo, hi = zone
    which, span = ("pour un ultra" if duration_h > N.ULTRA_H else "pour cette durée"), f"{lo} à {hi} g par heure"
    if g_h < lo:
        return f"Moins que le repère {which} : {span}.", "warn"
    if g_h > hi:
        return f"Plus que le repère {which} ({span}) : habitue ton ventre à cette dose à l'entraînement.", "plain"
    return f"Dans le repère {which} : {span}.", "ok"


def snap_interval(minutes: float | None) -> int | None:
    """The nearest allowed interval (ties: the longer one)."""
    if minutes is None or minutes <= 0:
        return None
    return min(INTERVALS, key=lambda i: (abs(i - minutes), -i))


def starter_interval(product: dict, target_g_h: float) -> int:
    """« Plan type »: one prise every N min, N so that the carbs per hour come
    closest to the target (ties: the longer interval, easier on the stomach)."""
    g = N.per_prise(product, "carbs_g")
    if g <= 0 or target_g_h <= 0:
        return 60
    return min(INTERVALS, key=lambda i: (abs(g * 60.0 / i - target_g_h), -i))


# ── water and sodium (ISSN 2019; nutrition.WATER_*, SODIUM_HOT_MG_H) ──────────

def mean_temp_c(sections: list[dict], start_offset_s: int) -> float | None:
    """The race's mean temperature over its predicted hours (°C): each section's
    temperature (the one the simulator read in the race's saved forecast for
    its arrival point and hour, as the passage table shows it) weighted by the
    section's time on the plan's clocks. None when a section has none (no
    forecast saved): no guess."""
    prev = float(start_offset_s)
    acc = span = 0.0
    for s in sections:
        t = s.get("temperature_c")
        if t is None:
            return None
        clock = max(prev, arrival_clock(s, start_offset_s))
        acc += float(t) * (clock - prev)
        span += clock - prev
        prev = clock
    return acc / span if span > 0 else None


def is_hot(sections: list[dict], start_offset_s: int) -> bool:
    """A hot race (H): its mean temperature over its predicted hours is 25 °C or
    more. Without temperatures the race is not hot."""
    t = mean_temp_c(sections, start_offset_s)
    return t is not None and t >= N.HOT_RACE_C


def water_ml(t_s: int, end_s: int, hot: bool = False) -> int:
    """The water to carry from a refill point to the next one (ml), over the
    stretch its row's food is for (its arrival to the next one's): the
    stretch's time × 0,5 L/h, 0,75 L/h when the race is hot, rounded up to half
    a litre, at least half a litre (H)."""
    rate = N.WATER_HOT_ML_H if hot else N.WATER_ML_H
    steps = -(-max(0, int(end_s) - int(t_s)) * rate // (3600 * N.WATER_STEP_ML))  # rounded up, in whole numbers
    return max(N.WATER_MIN_ML, steps * N.WATER_STEP_ML)


def litres(ml: float) -> str:
    """500 → '0,5 L', 1000 → '1 L', 1500 → '1,5 L', 750 → '0,75 L'."""
    return f"{N._fr(ml / 1000)} L"


def water_note(hot: bool) -> str:
    """The card's water line: drink to thirst (ISSN 2019), then the rate its rows are counted at."""
    if hot:
        return f"Eau : bois à ta soif. Il fera chaud : je compte {litres(N.WATER_HOT_ML_H)} par heure jusqu'au ravito suivant."
    return f"Eau : bois à ta soif. Je compte {litres(N.WATER_ML_H)} par heure jusqu'au ravito suivant."


def sodium_note(mg_h: int) -> tuple[str, str]:
    """(the sentence, its tone: ok, warn, plain) for a hot race's sodium per
    hour, against ISSN 2019's 300 to 600 mg/h in the heat: under it, the warning
    colour and what to add; in it, ok; over it, plain."""
    lo, hi = N.SODIUM_HOT_MG_H
    if mg_h < lo:
        return (f"Il fera chaud : vise {lo} à {hi} mg de sodium par heure. "
                "Ajoute du sel à ton plan (pastilles ou boisson salée).", "warn")
    if mg_h > hi:
        return f"Plus que le repère par temps chaud ({lo} à {hi} mg par heure).", "plain"
    return f"Dans le repère par temps chaud : {lo} à {hi} mg de sodium par heure.", "ok"


# ── words ───────────────────────────────────────────────────────────────────

def unit_words(n: int, p: dict) -> str:
    """'2 Maurten 100', '1 gel', '3 gels' (a generic product says its own words)."""
    if p.get("one"):
        return f"{n} {p['one'] if n == 1 else p['many']}"
    return f"{n} {N.short_label(p)}"


def hours_words(minutes: int) -> str:
    """480 → '8 h', 527 → '8 h 47', 0 → '0 h'."""
    h, m = divmod(int(minutes), 60)
    return f"{h} h {m:02d}" if m else f"{h} h"


def line_words(r: dict, products: dict) -> str:
    """A plan line in words: « PF 90 · 1 prise toutes les 20 min dès 8 h »."""
    p = products[r["product_id"]]
    one = "1 prise" if N.servings_of(p) > 1 else "1"
    when = ""
    if r["from_min"] and r.get("to_min") is not None:
        when = f" de {hours_words(r['from_min'])} à {hours_words(r['to_min'])}"
    elif r["from_min"]:
        when = f" dès {hours_words(r['from_min'])}"
    elif r.get("to_min") is not None:
        when = f" jusqu'à {hours_words(r['to_min'])}"
    return f"{N.short_label(p)} · {one} toutes les {r['every_min']} min{when}"


def take_parts(items: list[dict], products: dict) -> list[dict]:
    """A row's products to take: [{"words": "1 PF 90", "note": "(il t'en reste 1 prise)" | None}]."""
    return [{"words": unit_words(it["units"], products[it["pid"]]),
             "note": f"(il t'en reste {it['left']} prise{'s' if it['left'] > 1 else ''})" if it["left"] else None}
            for it in items if it["units"] > 0]


def take_text(items: list[dict], products: dict, room: int | None = None) -> str:
    """What a row says: « 2 Maurten 100 · 1 PF 90 », « rien à prendre » when
    there is nothing to take. The prises left in an opened pouch, « 1 PF 90
    (il t'en reste 1 prise) », only when that stays within ``room`` characters
    (one short line; None: always)."""
    parts = take_parts(items, products)
    if not parts:
        return "rien à prendre"
    text = " · ".join(p["words"] + (f" {p['note']}" if p["note"] else "") for p in parts)
    return text if room is None or len(text) <= room else " · ".join(p["words"] for p in parts)


# ── the stored plan ─────────────────────────────────────────────────────────

def _clean_line(r, products: dict) -> dict | None:
    if not isinstance(r, dict):
        return None
    pid = _int(r.get("product_id"))
    every = snap_interval(_int(r.get("every_min")))
    if pid is None or pid not in products or every is None:
        return None
    frm = min(max(_int(r.get("from_min")) or 0, 0), MAX_MIN)
    to = _int(r.get("to_min")) if r.get("to_min") is not None else None
    if to is not None and not (frm < to <= MAX_MIN):
        to = None
    return {"product_id": pid, "every_min": every, "from_min": frm, "to_min": to}


def clean_lines(raw, products: dict) -> list[dict]:
    """The lines of a stored plan, each one checked: a product he still has,
    an allowed interval, « de » before « à » (else « à la fin »)."""
    out = []
    for r in raw if isinstance(raw, list) else []:
        line = _clean_line(r, products)
        if line:
            out.append(line)
        if len(out) >= MAX_LINES:
            break
    return out


def plan_json(rhythms: list[dict], spare: bool, refills: list | None = None) -> dict:
    """The stored v2 shape. ``refills`` (an older plan's extra stops, read by
    the passage table) are carried as they were."""
    nj = {"v": 2, "rhythms": [{"product_id": int(r["product_id"]), "every_min": int(r["every_min"]),
                               "from_min": int(r["from_min"]), "to_min": None if r.get("to_min") is None else int(r["to_min"])}
                              for r in rhythms],
          "spare": bool(spare)}
    if refills:
        nj["refills"] = list(refills)
    return nj


def read_plan(nj, products: dict, *, time_at_km=None, race_s: float | None = None) -> dict:
    """{"rhythms", "spare", "old"} from whatever is stored, never failing.

    v2 is read as stored (each line checked). An older plan is mapped to lines
    when it maps cleanly: one product per phase, at the interval its beep had,
    each phase from the time its ravito is reached (``time_at_km``: km →
    seconds after the start). Otherwise the plan starts empty and ``old`` says
    so once (« Ton ancien plan était trop compliqué… »)."""
    empty = {"rhythms": [], "spare": False, "old": False}
    if not isinstance(nj, dict):
        return empty
    if nj.get("v") == 2 and isinstance(nj.get("rhythms"), list):
        return {"rhythms": clean_lines(nj["rhythms"], products), "spare": nj.get("spare") is True, "old": False}
    try:
        lines = _map_old(nj, products, time_at_km, race_s)
    except Exception:  # malformed beyond what the reader expects: start over, say so
        lines = None
    if lines is None:
        return {**empty, "old": True}
    return {**empty, "rhythms": lines[:MAX_LINES]}


# older plans ────────────────────────────────────────────────────────────────

def _ids(raw) -> list[int]:
    out = []
    for x in raw if isinstance(raw, list) else []:
        v = _int(x)
        if v is not None and v not in out:
            out.append(v)
    return out


def _mix_ids(mix) -> list[int]:
    if isinstance(mix, list):
        return _ids(mix)
    if isinstance(mix, dict):
        out = []
        for k, v in mix.items():
            pid, share = _int(k), _num(v)
            if pid is not None and (share is None or share > 0) and pid not in out:
                out.append(pid)
        return out
    return []


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _old_rate(nj: dict, race_s: float | None) -> float:
    """The carbs per hour an older plan was built on, with its duration ladder
    (under 1 h nothing, 1–2 h up to 30 g/h, 2–3 h up to 60)."""
    custom = nj.get("custom") if isinstance(nj.get("custom"), dict) else {}
    targets = nj.get("targets") if isinstance(nj.get("targets"), dict) else {}
    rate = _num(custom.get("carbs_g_per_h")) or N.LEVEL_CARBS.get(nj.get("level")) or _num(targets.get("carbs_g_per_h")) or 75.0
    h = float(race_s or 0) / 3600.0
    if race_s and h < 1:
        return 0.0
    if race_s and h < 2:
        return min(rate, 30.0)
    if race_s and h < 3:
        return min(rate, 60.0)
    return float(rate)


def _every(p: dict, rate: float, per_hour: float | None = None) -> int | None:
    """The interval of one prise: from an older plan's units per hour when it
    had them, else from its carbs per hour."""
    if per_hour and per_hour > 0:
        return snap_interval(60.0 / (per_hour * N.servings_of(p)))
    g = N.per_prise(p, "carbs_g")
    return snap_interval(60.0 * g / rate) if g > 0 and rate > 0 else None


def _fuel(pid: int, products: dict) -> bool:
    return pid in products and N.role(products[pid]) in ("gel", "drink", "bar")


def _map_old(nj: dict, products: dict, time_at_km, race_s: float | None) -> list[dict] | None:
    """[] when there is nothing to map, None when it does not map cleanly."""
    rate = _old_rate(nj, race_s)
    if nj.get("v") == 3 or isinstance(nj.get("phases"), list):
        picks = [p for p in _ids(nj.get("picks")) if p in products]
        rows = nj.get("rows") if isinstance(nj.get("rows"), dict) else {}
        if any(isinstance(v, dict) and any((_int(n) or 0) > 0 for n in v.values()) for v in rows.values()):
            return None  # stretches set by hand
        phases = []
        for ph in nj.get("phases") or []:
            km = _num(ph.get("km")) if isinstance(ph, dict) else None
            if km is not None:
                phases.append((km, ph.get("mix")))
        phases.sort(key=lambda x: x[0])
        if not phases or phases[0][0] > 0:
            phases.insert(0, (0.0, None))
        default = [p for p in picks if _fuel(p, products)]
        seq = []
        for km, mix in phases:
            pids = [p for p in (default if mix is None else _mix_ids(mix)) if p in products]
            seq.append((km, pids))
        used = {p for _km, pids in seq for p in pids}
        if not used and not picks:
            return []
        if any(len(pids) != 1 or not _fuel(pids[0], products) for _km, pids in seq) or any(p not in used for p in picks):
            return None
        return _phase_lines([(km, pids[0], None) for km, pids in seq], products, rate, time_at_km)
    if nj.get("v") == 2:  # before the stretches: a list and its rates per hour
        picks = [p for p in _ids(nj.get("picks")) if p in products]
        if not picks:
            return []
        manual = nj.get("manual") if isinstance(nj.get("manual"), dict) else {}
        if len(picks) != 1 or not _fuel(picks[0], products):
            return None
        return _phase_lines([(0.0, picks[0], _num(manual.get(str(picks[0]))))], products, rate, time_at_km)
    items = [it for it in (nj.get("items") or []) if isinstance(it, dict)] if isinstance(nj.get("items"), list) else []
    kept = [(_int(it.get("product_id")), _num(it.get("per_hour"))) for it in items]
    kept = [(pid, ph) for pid, ph in kept if pid is not None and pid in products]
    if not kept:
        return []
    if len(kept) != 1 or not _fuel(kept[0][0], products):
        return None
    return _phase_lines([(0.0, kept[0][0], kept[0][1])], products, rate, time_at_km)


def _phase_lines(seq: list[tuple[float, int, float | None]], products: dict, rate: float, time_at_km) -> list[dict] | None:
    lines: list[dict] = []
    for km, pid, per_hour in seq:
        every = _every(products[pid], rate, per_hour)
        if every is None:
            return None
        if km <= 0:
            start = 0
        else:
            t = time_at_km(km) if time_at_km else None
            if t is None:
                return None
            start = int(t // 60)  # the ravito's minute, as its row shows it
        if lines:
            if start <= lines[-1]["from_min"]:
                return None
            if lines[-1]["product_id"] == pid and lines[-1]["every_min"] == every:
                continue  # the same product at the same interval: one line
            lines[-1]["to_min"] = start
        lines.append({"product_id": pid, "every_min": every, "from_min": start, "to_min": None})
    return lines
