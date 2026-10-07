"""The stretch plan: « Tu prépares par tronçon, tu manges au bip ».

One row per stretch between two food ravitos (start, full / base vie, crew or
drop bag: the boundaries of checkpoints.autonomy_legs), timed on the section
CLOCKS arrival to arrival, so the stops count and Σ d_s = race time. Each row
is one sachet of WHOLE units (or whole prises of a multi-serve product, PF 90
= 3 prises of 30 g). The target is a cumulative line, rate × elapsed time;
each automatic row takes the whole servings that bring the running total
closest to it and the rounding remainder passes to the next row (|B| ≤ g/2).
A row the athlete set by hand stays as set: the following automatic rows
catch up, each by at most +17 % / −12 % of its need (PLAN_BAND), so a hand
edit never makes another row too much for the stomach.

Products change by phase (« À partir d'ici : autre produit »), one watch beep
fits every phase (the band-constrained common interval I*), the bags are the
sums of their rows plus a reserve in minutes, the shopping list the sum of
the bags. Pure functions: no database, no request.
"""

from __future__ import annotations

import math

from app.services import nutrition as N
from app.services.checkpoints import is_resupply

PLAN_BAND = (0.88, 1.17)   # what one beep must keep every phase inside (× the target)
CATCH_UP = (0.17, 0.12)    # the most a row takes on for a balance: above, below (from PLAN_BAND)
BEEPS = (15, 20, 25, 30, 35, 40, 45)  # watch nutrition alerts take 5–60 min
BLOCK_H = {"trail": 3.0, "bike": 2.0}  # no food ravito yet: stretches of about this long
NIGHT = (21.0, 6.0)
HEAT_C = 25.0
ALTITUDE_M = 2500
CATS = 5  # --pf-cat-1..5


def _round(x: float) -> int:
    """Half up (Python's round() is banker's rounding)."""
    return int(math.floor(x + 0.5))


def arrival_clock(s: dict, start_offset_s: int) -> float:
    v = s.get("adjusted_clock_time_s")
    if v is None:
        v = s.get("clock_time_s")
    if v is None:
        v = start_offset_s + float(moving_cum(s))
    return float(v)


def moving_time(s: dict) -> float:
    if s.get("adjusted_clock_time_s") is not None and s.get("adjusted_time_s") is not None:
        return float(s["adjusted_time_s"])
    return float(s.get("predicted_time_s") or 0)


def moving_cum(s: dict) -> float:
    v = s.get("adjusted_cumulative_time_s")
    if v is None:
        v = s.get("cumulative_time_s")
    return float(v or 0)


def _night(clock_s: float) -> bool:
    h = (clock_s % 86400) / 3600.0
    return h >= NIGHT[0] or h < NIGHT[1]


def fluid_rate(temp_c: float | None, sweat_factor: float = 1.0, custom: float | None = None) -> float:
    """Water to CARRY per hour (drink to thirst): 500 ml at ≤ 15 °C, +25 ml per °C
    above, at most 900, × the sweat self-rating."""
    if custom:
        return float(custom)
    base = 500.0 + 25.0 * max(0.0, float(temp_c) - 15.0) if temp_c is not None else 500.0
    return min(900.0, base) * float(sweat_factor or 1.0)


# ── stretches ───────────────────────────────────────────────────────────────

def _new_stretch(name: str, km: float, clock: float, cp: dict | None) -> dict:
    crew, drop = bool(cp and cp.get("crew")), bool(cp and cp.get("drop_bag"))
    return {
        "from_name": name, "from_km": round(float(km), 1), "key": round(float(km), 1), "start_clock_s": clock,
        "moving_s": 0.0, "temp_w": 0.0, "temp_t": 0.0, "max_elev": None, "section_idx": [],
        "bag": cp is None or crew or drop, "bag_kind": "start" if cp is None else ("drop" if drop else ("crew" if crew else None)),
        "aid_food": bool(cp and ((cp.get("kind") in ("full", "base")) or crew)), "is_block": False,
    }


def food_stretches(sections: list[dict], checkpoints: list[dict], start_offset_s: int, sport: str = "trail") -> list[dict]:
    """The stretches between food ravitos, timed arrival to arrival on the
    section clocks (target plan when there is one, the prediction otherwise).
    No food ravito at all: blocks of about 3 h (2 h on a bike)."""
    if not sections:
        return []
    out: list[dict] = []
    cur = _new_stretch("Départ", 0.0, float(start_offset_s), None)
    for i, s in enumerate(sections):
        mt = moving_time(s)
        cur["moving_s"] += mt
        if s.get("temperature_c") is not None:
            cur["temp_w"] += float(s["temperature_c"]) * mt
            cur["temp_t"] += mt
        if s.get("end_elevation") is not None:
            cur["max_elev"] = max(cur["max_elev"] or -1e9, float(s["end_elevation"]))
        cur["section_idx"].append(i)
        idx = s.get("end_checkpoint_index")
        cp = checkpoints[idx] if idx is not None and 0 <= idx < len(checkpoints) else None
        last = i == len(sections) - 1
        if last or (cp and is_resupply(cp)):
            end = arrival_clock(s, start_offset_s)
            cur.update({"to_name": "Arrivée" if last else s.get("end_name", ""), "to_km": round(float(s.get("end_km") or 0), 1),
                        "end_clock_s": end, "end_cp_index": None if last else idx})
            out.append(cur)
            if not last:
                cur = _new_stretch(s.get("end_name", ""), float(s.get("end_km") or 0), end, cp)
                cur["start_cp_index"] = idx
    if len(out) == 1:
        out = _blocks(out[0], sections, start_offset_s, BLOCK_H.get(sport, 3.0))
    for k, st in enumerate(out):
        st["i"] = k
        st["d_s"] = max(0.0, st["end_clock_s"] - st["start_clock_s"])
        st["temp_c"] = round(st["temp_w"] / st["temp_t"], 1) if st["temp_t"] else None
        st["night"] = _night(st["start_clock_s"])
        st.setdefault("start_cp_index", None)
    return out


def _blocks(whole: dict, sections: list[dict], start_offset_s: int, block_h: float) -> list[dict]:
    """No food ravito: the race in blocks of about ``block_h`` (whole units and
    the balance still hold; the rows say they are blocks, not ravitos)."""
    total = whole["end_clock_s"] - whole["start_clock_s"]
    n = max(1, _round(total / 3600.0 / block_h))
    if n == 1:
        whole["is_block"] = False
        return [whole]
    pts = [(float(start_offset_s), 0.0)]  # (clock, km): the stops are flat steps
    prev_end, prev_stop = float(start_offset_s), 0.0
    for s in sections:
        start = prev_end + prev_stop
        pts.append((start, float(s.get("start_km") or 0)))
        end = arrival_clock(s, start_offset_s)
        pts.append((end, float(s.get("end_km") or 0)))
        prev_end, prev_stop = end, float(s.get("stop_s") or 0)

    def km_at(t: float) -> float:
        for (t0, k0), (t1, k1) in zip(pts, pts[1:], strict=False):
            if t <= t1:
                return k0 + (k1 - k0) * ((t - t0) / (t1 - t0) if t1 > t0 else 1.0)
        return pts[-1][1]
    out = []
    for b in range(n):
        a, z = whole["start_clock_s"] + total * b / n, whole["start_clock_s"] + total * (b + 1) / n
        st = {**whole, "from_km": round(km_at(a), 1), "key": round(km_at(a), 1), "start_clock_s": a, "end_clock_s": z,
              "to_km": round(km_at(z), 1), "moving_s": whole["moving_s"] / n, "is_block": True,
              "from_name": N._clock(a), "to_name": "Arrivée" if b == n - 1 else N._clock(z),
              "bag": b == 0, "bag_kind": "start" if b == 0 else None, "aid_food": False, "section_idx": list(whole["section_idx"])}
        out.append(st)
    keys = set()
    for st in out:  # keys stay unique (a long stop can hold two block starts)
        while st["key"] in keys:
            st["key"] = round(st["key"] + 0.1, 1)
        keys.add(st["key"])
    return out


def _snapper(keys: list[float]):
    def snap(km: float | None) -> float | None:
        if km is None or not keys:
            return None
        best = min(keys, key=lambda k: abs(k - km))
        return best if abs(best - km) <= 0.5 + 1e-9 else None
    return snap


# ── water ───────────────────────────────────────────────────────────────────

def water_segments(sections: list[dict], stretches: list[dict], refill_kms: set, fluid_fn) -> list[dict]:
    """Water to carry between two refill points (water points and ravitos):
    Σ rate(section temperature) × moving time."""
    bounds = {round(float(k), 1) for k in (refill_kms or set())} | {st["to_km"] for st in stretches}
    segs, need, time_s = [], 0.0, 0.0
    frm, frm_km = "Départ", 0.0
    for i, s in enumerate(sections):
        mt = moving_time(s)
        need += fluid_fn(s.get("temperature_c")) * mt / 3600.0
        time_s += mt
        km = round(float(s.get("end_km") or 0), 1)
        if km in bounds or i == len(sections) - 1:
            to = "Arrivée" if i == len(sections) - 1 else s.get("end_name", "")
            segs.append({"from_name": frm, "from_km": frm_km, "to_name": to, "to_km": km, "need_ml": round(need), "time_s": int(time_s)})
            frm, frm_km, need, time_s = to, km, 0.0, 0.0
    return segs


# ── the plan ────────────────────────────────────────────────────────────────

def _serving_g(p: dict) -> float:
    return float(p.get("carbs_g") or 0) / N.servings_of(p)


def _snap5(minutes: float) -> int:
    return int(min(max(5 * _round(minutes / 5.0), BEEPS[0]), BEEPS[-1]))


def pill_label(n: int, p: dict) -> str:
    """'4 Maurten 160', '10 prises PF 90', '3 gels'."""
    if N.servings_of(p) > 1:
        return f"{n} prise{'s' if n > 1 else ''} {N.short_label(p)}"
    return N.unit_label(n, p)


def build_plan(stretches: list[dict], inp: dict, *, sections: list[dict], start_offset_s: int,
               refill_kms: set | None = None, sport: str = "trail") -> dict:
    """Rows, phases, beep, bags, list. ``inp`` = nutrition.resolve_inputs()."""
    products: dict = inp["products_by_id"]
    picks: list[int] = inp["picks"]
    race_s = sum(st["d_s"] for st in stretches)
    rate = N.race_rate(inp["level_carbs"], race_s)
    keys = [st["key"] for st in stretches]
    snap = _snapper(keys)
    sweat = inp.get("sweat_factor", 1.0)
    custom_fluid = (inp.get("custom") or {}).get("fluid_ml_per_h")

    mean_temp = inp.get("mean_temp")

    def fluid_fn(t):  # a section without a forecast takes the race's
        return fluid_rate(t if t is not None else mean_temp, sweat, custom_fluid)

    # colours: the fuel products of the list, in its order
    fuel_order = [p for p in picks if N.is_fuel(products[p])]
    cat = {p: (i % CATS) + 1 for i, p in enumerate(fuel_order)}

    # ── phases, rows, ravito food on the stretch keys (km keys drift when a point moves)
    phases: list[dict] = []
    for ph in inp["phases"]:
        k = snap(ph["km"]) if ph["km"] > 0 else (keys[0] if keys else 0.0)
        if k is None:
            k = next((x for x in keys if x >= ph["km"]), None)
        if k is None:
            continue
        mix = {p: s for p, s in ph["mix"].items() if p in products}
        if phases and abs(phases[-1]["km"] - k) < 0.05:
            phases[-1]["mix"] = mix
        else:
            phases.append({"km": k, "mix": mix})
    if not phases:
        phases = [{"km": keys[0] if keys else 0.0, "mix": {}}]
    phases[0]["km"] = keys[0] if keys else 0.0
    rows, dropped = {}, 0
    for k, v in inp["rows"].items():
        sk = snap(k)
        if sk is None:
            dropped += 1
        else:
            rows[sk] = v
    aid = {}
    for k, v in inp["aid"].items():
        sk = snap(k)
        if sk is not None:
            aid[sk] = v

    def phase_index(st: dict) -> int:
        j = 0
        for i, ph in enumerate(phases):
            if ph["km"] <= st["key"] + 1e-6:
                j = i
        return j

    # ── caffeine: doses on moving time, placed on the stretches by their CLOCK
    caf_pid = inp.get("caf_pid")
    caf = products.get(caf_pid) if caf_pid is not None else None
    cola = []
    for st in stretches:
        for a, n in (aid.get(st["key"]) or {}).items():
            mg = float(N.AID_BY_ID[a].get("caffeine_mg") or 0) * n
            if mg:
                cola.append((st["start_clock_s"], mg))
    doses_by_k: dict[int, list] = {}
    caffeine = None
    if caf and sections:
        legs = [{"cum_s": moving_cum(s), "clock_s": arrival_clock(s, start_offset_s), "to_name": s.get("end_name", "")} for s in sections]
        caffeine = N.caffeine_schedule(legs[-1]["cum_s"], legs, inp["caffeine"], start_offset_s, inp.get("weight_kg"),
                                       N.short_label(caf), unit_mg=caf.get("caffeine_mg"), events=cola)
        for d in (caffeine or {}).get("doses") or []:
            k = next((st["i"] for st in stretches if d["clock_s"] < st["end_clock_s"]), len(stretches) - 1)
            doses_by_k.setdefault(k, []).append(d)

    segs = water_segments(sections, stretches, refill_kms or set(), fluid_fn) if sections else []
    cap = inp.get("carry_ml")
    salts = [p for p in picks if N.role(products[p]) == "salt"]

    # ── the rows
    P = T = 0.0
    used: dict = {}            # servings handed out so far, per product (round robin)
    due: dict = {}             # drinks, bars, salt: owed so far (rate × time), whole units handed out below
    given: dict = {}
    open_prises: dict = {}     # multi-serve: prises left in the open pouch
    out = []
    for st in stretches:
        k, key = st["i"], st["key"]
        need = rate * st["d_s"] / 3600.0
        pi = phase_index(st)
        mix = phases[pi]["mix"]
        mix_p = [products[p] for p in mix]
        gels = [p for p in mix_p if N.role(p) == "gel"]
        drinks = [p for p in mix_p if N.role(p) == "drink"]
        bars = [p for p in mix_p if N.role(p) == "bar"]
        base_bars = bars if (gels or drinks) else []
        fill = gels if (gels or drinks) else bars
        g_max = max((_serving_g(p) for p in fill), default=25.0) or 25.0
        moving_h = st["moving_s"] / 3600.0
        fluid_ml = fluid_fn(st.get("temp_c")) * moving_h
        fluid_h = fluid_ml / moving_h if moving_h > 0 else 0.0
        for d in drinks:
            vol = float(d.get("volume_ml") or 500)
            r = max(0.5, math.floor(fluid_h / vol / len(drinks) * 2 + 1e-9) / 2)
            due[d["id"]] = due.get(d["id"], 0.0) + r * moving_h
        for b in base_bars:
            due[b["id"]] = due.get(b["id"], 0.0) + 0.5 * moving_h
        frozen = key in rows
        counts: dict = {}
        aid_here = dict(aid.get(key) or {}) if st.get("aid_food") else {}
        if frozen:
            counts = {p: int(n) for p, n in rows[key].items() if p in products}
        else:
            for d in doses_by_k.get(k, []):
                counts[caf_pid] = counts.get(caf_pid, 0) + int(d.get("units") or 1)
            for p in drinks + base_bars:
                n = max(0, _round(due[p["id"]]) - given.get(p["id"], 0))
                if n:
                    counts[p["id"]] = n
            fixed = sum(n * _serving_g(products[p]) for p, n in counts.items())
            fixed += sum(n * float(N.AID_BY_ID[a]["carbs_g"]) for a, n in aid_here.items())
            bal = P - T
            hi, lo = max(g_max / 2, CATCH_UP[0] * need), max(g_max / 2, CATCH_UP[1] * need)
            gap = need - min(max(bal, -hi), lo) - fixed
            order = {p["id"]: i for i, p in enumerate(fill)}
            while fill:
                cands = sorted(fill, key=lambda p: (used.get(p["id"], 0) / max(mix.get(p["id"], 1.0), 1e-6), order[p["id"]]))
                pick = next((p for p in cands if abs(gap - _serving_g(p)) < abs(gap) - 1e-9), None)
                if pick is None:
                    break
                counts[pick["id"]] = counts.get(pick["id"], 0) + 1
                used[pick["id"]] = used.get(pick["id"], 0) + 1
                gap -= _serving_g(pick)
        # salt: one tablet per flask of water drunk on the stretch (not the drink's)
        drink_ml = sum(n * float(products[p].get("volume_ml") or 500) for p, n in counts.items() if N.role(products[p]) == "drink")
        water_ml = max(0.0, fluid_ml - drink_ml)
        for s_ in salts:
            vol = float(products[s_].get("volume_ml") or 500)
            due[s_] = due.get(s_, 0.0) + water_ml / vol / len(salts)
            if not frozen:
                n = max(0, _round(due[s_]) - given.get(s_, 0))
                if n:
                    counts[s_] = n
        for p, n in counts.items():
            given[p] = given.get(p, 0) + n
            if frozen and p in mix:
                used[p] = used.get(p, 0) + n

        items, carbs, sodium, caf_mg, beep_n, beep_g = [], 0.0, 0.0, 0.0, 0, 0.0
        order_ids = {p: i for i, p in enumerate(picks)}
        for p in sorted(counts, key=lambda x: order_ids.get(x, 999)):
            n, prod = counts[p], products[p]
            u = N.servings_of(prod)
            o_in = open_prises.get(p, 0)
            if u > 1:
                pouches = math.ceil(max(0, n - o_in) / u)
                open_prises[p] = o_in + pouches * u - n
                packed = pouches
            else:
                packed = n
            r = N.role(prod)
            items.append({"pid": p, "n": n, "packed": packed, "servings": u, "open_in": o_in if u > 1 else 0,
                          "open_out": open_prises.get(p, 0) if u > 1 else 0, "role": r, "cat": cat.get(p, 0),
                          "name": N.short_label(prod), "title": prod.get("name") or "", "label": pill_label(n, prod), "at_aid": False})
            carbs += n * _serving_g(prod)
            sodium += n * float(prod.get("sodium_mg") or 0) / u
            caf_mg += n * float(prod.get("caffeine_mg") or 0) / u
            if r in ("gel", "bar", "caf") and not (r == "bar" and p in {b["id"] for b in base_bars}):
                beep_n += n
                beep_g += n * _serving_g(prod)
        for a, n in aid_here.items():
            f = N.AID_BY_ID[a]
            items.append({"pid": a, "n": n, "packed": 0, "servings": 1, "open_in": 0, "open_out": 0, "role": "aid", "cat": 0,
                          "name": f["label"], "title": f["name"], "label": f"≈ {f['label']} {n}" if n > 1 else f"≈ {f['label']}", "at_aid": True})
            carbs += n * float(f["carbs_g"])
            sodium += n * float(f.get("sodium_mg") or 0)
            caf_mg += n * float(f.get("caffeine_mg") or 0)
        P += carbs
        T += need
        bal = P - T
        d_h = st["d_s"] / 3600.0
        band = max(g_max, 0.15 * need)
        status = "ok" if abs(bal) <= band + 1e-6 else ("ahead" if bal > 0 else "behind")
        stomach = d_h >= 1 and carbs > 1.2 * need + g_max / 2
        # water: what to leave with (the first refill-to-refill segment), and any segment the flasks cannot hold
        mine = [s_ for s_ in segs if st["from_km"] - 0.05 <= s_["from_km"] < st["to_km"] - 0.05]
        first = mine[0] if mine else None
        worst = max(mine, key=lambda s_: s_["need_ml"], default=None)
        water_ml_out = int(N.ceil_half_l(first["need_ml"]) * 1000) if first else 0
        over = bool(cap and worst and worst["need_ml"] > cap + 1)
        if st["is_block"] and not refill_kms:  # no water point known: no litres to promise
            water_ml_out, over, first, worst = 0, False, None, None
        main = max((it for it in items if it["role"] in ("gel", "drink", "bar")), key=lambda it: it["n"] * _serving_g(products[it["pid"]]), default=None)
        out.append({
            **{x: st[x] for x in ("i", "key", "from_name", "to_name", "from_km", "to_km", "start_clock_s", "end_clock_s", "d_s",
                                   "moving_s", "max_elev", "night", "bag", "bag_kind", "aid_food", "is_block", "start_cp_index")},
            "temp_c": st.get("temp_c") if st.get("temp_c") is not None else mean_temp,
            "clock": N._clock(st["start_clock_s"]), "phase": pi, "phase_start": pi > 0 and abs(phases[pi]["km"] - key) < 0.05,
            "items": items, "labels": [it["label"] for it in items if it["n"] and not it["at_aid"]],
            "carbs_g": round(carbs), "need_g": round(need), "g_h": _round(carbs / d_h) if d_h > 0 else 0,
            "sodium_h": int(100 * _round(sodium / d_h / 100)) if d_h > 0 else 0, "caffeine_mg": _round(caf_mg),
            "balance_g": _round(bal), "band_g": round(band), "status": status, "stomach": stomach, "frozen": frozen,
            "g_max": g_max, "beep_n": beep_n, "beep_g": beep_g, "water_ml": water_ml_out, "water_over": over,
            "water_worst": worst if over else None, "water_first": first, "fluid_ml": round(fluid_ml),
            "main_cat": main["cat"] if main else 0,
        })

    beep = _beep(out, phases, rate)
    for r in out:
        r["interval_min"] = _snap5(r["d_s"] / 60.0 / r["beep_n"]) if r["beep_n"] else None
        ph_iv = beep["per_phase"].get(r["phase"]) if beep else None
        r["beep_note"] = ph_iv if (beep and r["phase_start"] and ph_iv and ph_iv != beep["prev_phase_iv"].get(r["phase"])) else None
        r["own_interval"] = r["interval_min"] if (beep and r["interval_min"] and r["interval_min"] != (ph_iv or beep["interval"])) else None

    # ── phases as the header names them
    ph_out = []
    for i, ph in enumerate(phases):
        ids = [p for p in picks if p in ph["mix"]] + [p for p in ph["mix"] if p not in picks]
        start = next((r["i"] for r in out if r["phase"] == i), None)
        prev = set(phases[i - 1]["mix"]) if i else set()
        new = [p for p in ids if p not in prev] or ids
        ph_out.append({"i": i, "key": ph["km"], "start_idx": start, "pids": ids, "names": [N.short_label(products[p]) for p in ids],
                       "cat": cat.get(ids[0], 0) if ids else 0, "from_name": out[start]["from_name"] if start is not None else "",
                       "new_names": [N.short_label(products[p]) for p in new], "end_key": phases[i + 1]["km"] if i + 1 < len(phases) else None})
    switch_notes = {}
    for ph in ph_out[1:]:
        if ph["start_idx"] is not None and ph["new_names"]:
            switch_notes[out[ph["start_idx"]]["from_km"]] = "Dès ici : " + " · ".join(ph["new_names"])

    bags = _bags(out, products, rate, inp, picks, race_s, sport)
    shop = _shop(bags, products, picks, inp.get("have") or {})
    events = []
    for r in out:
        for it in r["items"]:
            if it["role"] == "caf" and not it["at_aid"] and not r["frozen"]:
                continue
            mg = float((products.get(it["pid"]) or N.AID_BY_ID.get(it["pid"]) or {}).get("caffeine_mg") or 0) * it["n"]
            if mg:
                events.append((r["start_clock_s"] + r["d_s"] / 2 if not it["at_aid"] else r["start_clock_s"], mg))
    for k, ds in doses_by_k.items():
        if not out[k]["frozen"]:
            events += [(d["clock_s"], d["mg"]) for d in ds]
    caf_24 = N.rolling_max_mg(events)
    water_notes = {}
    food_kms = {r["from_km"] for r in out}
    for s_ in segs:
        if s_["from_km"] <= 0 or s_["from_km"] in food_kms:
            continue  # a ravito's water is in its row
        if cap and s_["need_ml"] > cap + 1:
            water_notes[s_["from_km"]] = (f"Remplis tout ici : il faut {N._fr(N.ceil_half_l(s_['need_ml']))} L jusqu'à {s_['to_name']}, tu portes {N.liters(cap)} L.", True)
        elif not cap and s_["need_ml"] >= 1000:
            water_notes[s_["from_km"]] = (f"Remplis {N._fr(N.ceil_half_l(s_['need_ml']))} L ici : pas d'eau avant {s_['to_name']}.", False)
    return {
        "stretches": out, "phases": ph_out, "beep": beep, "bags": bags, "shop": shop, "caffeine": caffeine,
        "caffeine_24h_mg": caf_24, "caffeine_over": caf_24 > inp.get("cap_mg", N.CAFFEINE_MAX_MG) + 1,
        "rate_g_h": rate, "race_s": race_s, "segments": segs, "water_notes": water_notes, "switch_notes": switch_notes,
        "dropped_rows": dropped, "fluid_ml_per_h": fluid_fn(None),
        "state_snapped": {"phases": [{"km": ph["km"], "mix": dict(ph["mix"])} for ph in phases], "rows": rows, "aid": aid},
    }


def _beep(rows: list[dict], phases: list[dict], rate: float) -> dict | None:
    """One watch interval for the whole race: the I in 15…45 min that keeps
    every phase inside PLAN_BAND (one beep = one gel or one prise), closest to
    the target over the race, ties to the longer interval. When no common I
    exists, each phase gets its own and the switch row says it."""
    if rate <= 0:
        return None
    per = {}
    for r in rows:
        a = per.setdefault(r["phase"], {"n": 0, "g": 0.0, "fixed_g": 0.0, "h": 0.0})
        a["n"] += r["beep_n"]
        a["g"] += r["beep_g"]
        a["h"] += r["d_s"] / 3600.0
        # drinks and a bar now and then come on top of the beep; ravito food is eaten there
        aid_g = sum(it["n"] * float(N.AID_BY_ID[it["pid"]]["carbs_g"]) for it in r["items"] if it["at_aid"])
        a["fixed_g"] += max(0.0, r["carbs_g"] - r["beep_g"] - aid_g)
    per = {p: a for p, a in per.items() if a["n"] > 0 and a["h"] > 0}
    if not per:
        return None

    def r_of(a: dict, iv: int) -> float:
        return a["g"] / a["n"] * 60.0 / iv + max(0.0, a["fixed_g"]) / a["h"]

    def score(iv: int, items) -> tuple[bool, float]:
        ok = all(PLAN_BAND[0] - 1e-9 <= r_of(a, iv) / rate <= PLAN_BAND[1] + 1e-9 for _, a in items)
        err = sum(a["h"] * abs(r_of(a, iv) / rate - 1) for _, a in items)
        return ok, err
    items = list(per.items())
    common = [(score(iv, items)[1], -iv, iv) for iv in BEEPS if score(iv, items)[0]]
    per_phase = {}
    for p, a in items:
        per_phase[p] = min(BEEPS, key=lambda iv: (abs(r_of(a, iv) / rate - 1), -iv))
    if common:
        iv = min(common)[2]
        return {"interval": iv, "common": True, "per_phase": {p: iv for p in per_phase}, "prev_phase_iv": {p: iv for p in per_phase}}
    first = min(per_phase)
    prev, last = {}, per_phase[first]
    for p in sorted(per_phase):
        prev[p] = last
        last = per_phase[p]
    return {"interval": per_phase[first], "common": False, "per_phase": per_phase, "prev_phase_iv": prev}


def _bags(rows: list[dict], products: dict, rate: float, inp: dict, picks: list[int], race_s: float, sport: str) -> list[dict]:
    """A bag = its stretches until the next load point (start, drop bag, crew),
    plus a reserve of its main product (minutes of the target, in whole units).
    An open pouch carried over is in the pocket: the ledger already left it out."""
    reserve_min = inp.get("reserve_set")
    if reserve_min is None:
        reserve_min = 60 if race_s >= 6 * 3600 else 30
    starts = [r["i"] for r in rows if r["bag"] or r["i"] == 0] if sport != "bike" else [0]
    out = []
    order = {p: i for i, p in enumerate(picks)}
    for j, s in enumerate(starts):
        end = starts[j + 1] if j + 1 < len(starts) else len(rows)
        units: dict = {}
        for r in rows[s:end]:
            for it in r["items"]:
                if not it["at_aid"] and it["packed"]:
                    units[it["pid"]] = units.get(it["pid"], 0) + it["packed"]
        main = max((p for p in units if N.role(products[p]) in ("gel", "drink", "bar")),
                   key=lambda p: (units[p] * float(products[p].get("carbs_g") or 0), -order.get(p, 999)), default=None)
        reserve = 0
        if main is not None and reserve_min and rate > 0:
            reserve = max(1, math.ceil(rate * reserve_min / 60.0 / max(float(products[main].get("carbs_g") or 1), 1.0) - 1e-9))
        first = rows[s]
        kind = first.get("bag_kind") or "start"
        title = "Sac au départ" if s == 0 else f"{'Assistance' if kind == 'crew' else 'Drop bag'} · {first['from_name']}"
        pids = sorted(units, key=lambda p: order.get(p, 999))
        labels = [N.unit_label(units[p] + (reserve if p == main else 0), products[p]) for p in pids]
        out.append({"key": first["key"], "title": title, "stretch_idx": list(range(s, end)), "is_start": s == 0, "kind": kind,
                    "units": {p: units[p] for p in pids}, "main": main, "reserve": reserve, "reserve_min": reserve_min,
                    "pills": [{"pid": p, "n": units[p] + (reserve if p == main else 0), "cat": 0, "label": lb} for p, lb in zip(pids, labels, strict=True)],
                    "labels": labels})
    return out


def _shop(bags: list[dict], products: dict, picks: list[int], have: dict) -> list[dict]:
    """What to buy = every bag and its reserve, less what he has."""
    need: dict = {}
    for g in bags:
        for p, n in g["units"].items():
            need[p] = need.get(p, 0) + n
        if g["main"] is not None and g["reserve"]:
            need[g["main"]] = need.get(g["main"], 0) + g["reserve"]
    order = {p: i for i, p in enumerate(picks)}
    pids = sorted(set(need) | {p for p, n in have.items() if n and p in products}, key=lambda p: order.get(p, 999))
    out = []
    for p in pids:
        n, h = need.get(p, 0), int(have.get(p, 0))
        prod = products[p]
        out.append({"pid": p, "need": n, "have": h, "to_buy": max(0, n - h), "left": max(0, h - n),
                    "label": N.short_label(prod) if not prod.get("one") else (prod["one"] if max(0, n - h) == 1 else prod["many"]),
                    "name": N.short_label(prod), "title": prod.get("name") or "", "role": N.role(prod),
                    "reserve": sum(g["reserve"] for g in bags if g["main"] == p)})
    return out
