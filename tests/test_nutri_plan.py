"""The stretch plan engine: « tu prépares par tronçon, tu manges au bip ».

Invariants: the stretches add up to the race time, whole units only, the
automatic rows stay within half a serving of the target line, a hand edit is
caught up without overloading a row, products switch by phase, multi-serve
products count in prises, caffeine never passes the 24 h cap, bags add up to
their stretches and the list to the bags."""

import math

from app.services import nutrition as N
from app.services import nutrition_plan as NP
from tests.nutri_course import LONG_CPS, PANTRY, long_sections

START = 21 * 3600
REFILLS = {cp["distance_km"] for cp in LONG_CPS}
OWNER = {"v": 3, "picks": [1, 2, 3, 4, 5], "level": "normal",
         "phases": [{"km": 0, "mix": {"1": 1, "2": 1}}, {"km": 64.0, "mix": {"3": 1}}]}


def _plan(nj=OWNER, target_s=27 * 3600, weight=68, cps=None, temps=None, sport="trail"):
    _, secs = long_sections(target_s, cps=cps, temps=temps)
    cps = LONG_CPS if cps is None else cps
    inp = N.resolve_inputs(nj, PANTRY, target_s or 0, None, weight, moving_s=NP.moving_cum(secs[-1]), start_offset_s=START)
    st = NP.food_stretches(secs, cps, START, sport)
    return inp, NP.build_plan(st, inp, sections=secs, start_offset_s=START, refill_kms={c["distance_km"] for c in cps}, sport=sport), secs


def test_stretches_run_between_food_ravitos_arrival_to_arrival():
    _, secs = long_sections()
    st = NP.food_stretches(secs, LONG_CPS, START)
    assert [s["to_name"] for s in st] == ["Yeongsil", "Eorimok", "Gwanumsa", "Seongpanak", "Gasiri", "Meochewat", "Camping", "Arrivée"]
    # Σ d_s = race time: the stops at the ravitos are inside the stretches (the 27 h target holds 4 water + 7 food stops)
    assert sum(s["d_s"] for s in st) == 27 * 3600
    assert sum(s["moving_s"] for s in st) < 27 * 3600 - 7 * 300
    for a, b in zip(st, st[1:], strict=False):
        assert a["end_clock_s"] == b["start_clock_s"]
    assert [s["bag"] for s in st] == [True, False, False, False, False, True, False, True]
    # without a target: the prediction's clocks, still arrival to arrival
    _, secs = long_sections(None)
    st = NP.food_stretches(secs, LONG_CPS, START)
    assert sum(s["d_s"] for s in st) == secs[-1]["clock_time_s"] - START


def test_whole_units_and_the_automatic_balance_stays_within_half_a_serving():
    for level in ("fragile", "normal", "solide"):
        for nj in ({**OWNER, "level": level}, {"v": 3, "picks": [-1, -3], "level": level}, {"v": 3, "picks": [3], "level": level}):
            inp, plan, _ = _plan(nj)
            target = 0.0
            for r in plan["stretches"]:
                for it in r["items"]:
                    assert isinstance(it["n"], int) and it["n"] >= 0 and isinstance(it["packed"], int)
                target += r["need_g"]
                # |B_k| ≤ g/2 (g = the largest serving of the phase), never « 0,5 gel »
                assert abs(r["balance_g"]) <= r["g_max"] / 2 + 1, (level, nj["picks"], r["to_name"], r["balance_g"])
                assert r["status"] == "ok" and not r["stomach"]


def test_a_hand_set_row_is_caught_up_without_overloading_the_next_rows():
    inp, plan, secs = _plan()
    row = plan["stretches"][1]
    # he takes nothing on Yeongsil → Eorimok: the row is frozen whole, the deficit spreads
    st = N.apply_op(inp["state"], "n", (row["key"], 1, -99), inp["products_by_id"], plan)
    st = N.apply_op(st, "n", (row["key"], 2, -99), inp["products_by_id"], plan)
    assert st["rows"][row["key"]][1] == 0 and st["rows"][row["key"]][2] == 0 and 5 in st["rows"][row["key"]]
    inp2, plan2, _ = _plan(N.state_json(st))
    after = plan2["stretches"]
    assert after[1]["frozen"] and after[1]["carbs_g"] < 30
    for r in after[2:]:
        assert not r["stomach"], r  # each catch-up capped at +17 %
        assert r["carbs_g"] <= 1.17 * r["need_g"] + r["g_max"] / 2 + 1
    behind = [r["balance_g"] for r in after[1:]]
    assert behind[0] < -60 and all(b2 >= b1 - 1 for b1, b2 in zip(behind, behind[1:], strict=False) if b1 < -r["g_max"])
    assert after[2]["carbs_g"] > plan["stretches"][2]["carbs_g"]  # the next row takes some of it
    # « Remettre en auto »
    st = N.apply_op(st, "auto", row["key"], inp["products_by_id"], plan2)
    assert row["key"] not in st["rows"]


def test_the_product_switches_from_its_ravito_on():
    _, plan, _ = _plan()
    for r in plan["stretches"]:
        pids = {it["pid"] for it in r["items"] if it["role"] in ("gel", "drink", "bar")}
        assert pids <= ({1, 2} if r["from_km"] < 64 else {3}), (r["to_name"], pids)
    assert plan["stretches"][4]["phase_start"] and plan["switch_notes"] == {64.0: "Dès ici : PF 90"}
    assert [p["names"] for p in plan["phases"]] == [["Maurten 160", "Baouw"], ["PF 90"]]


def test_multi_serve_products_count_in_prises_and_the_open_pouch_rolls_on():
    _, plan, _ = _plan()
    open_ = 0
    total_prises = total_pouches = 0
    for r in plan["stretches"]:
        pf = next((it for it in r["items"] if it["pid"] == 3), None)
        if not pf:
            continue
        assert pf["servings"] == 3 and pf["label"].endswith("prises PF 90")
        assert pf["open_in"] == open_
        assert pf["packed"] == math.ceil(max(0, pf["n"] - open_) / 3)
        open_ = pf["open_out"]
        assert open_ == pf["open_in"] + 3 * pf["packed"] - pf["n"] and 0 <= open_ < 3
        total_prises += pf["n"]
        total_pouches += pf["packed"]
    assert total_pouches == math.ceil(total_prises / 3) or total_pouches * 3 - total_prises < 3


def test_bags_add_up_to_their_stretches_and_the_list_to_the_bags():
    _, plan, _ = _plan()
    rows = plan["stretches"]
    assert [b["title"] for b in plan["bags"]] == ["Sac au départ", "Drop bag · Gasiri", "Drop bag · Camping"]
    covered = []
    for b in plan["bags"]:
        covered += b["stretch_idx"]
        for pid, n in b["units"].items():
            assert n == sum(it["packed"] for i in b["stretch_idx"] for it in rows[i]["items"] if it["pid"] == pid and not it["at_aid"])
        assert b["reserve"] >= 1 and b["main"] in b["units"]
    assert covered == list(range(len(rows)))
    # Σ sachets + reserves = what to buy
    for it in plan["shop"]:
        sachets = sum(x["packed"] for r in rows for x in r["items"] if x["pid"] == it["pid"] and not x["at_aid"])
        reserves = sum(b["reserve"] for b in plan["bags"] if b["main"] == it["pid"])
        assert it["need"] == sachets + reserves and it["to_buy"] == it["need"]
    # « J'en ai » lowers what to buy, never below 0, and says what is left
    have = {"v": 3, **{k: v for k, v in OWNER.items() if k != "v"}, "have": {"1": 100}}
    _, plan2, _ = _plan(have)
    m = next(it for it in plan2["shop"] if it["pid"] == 1)
    assert m["to_buy"] == 0 and m["left"] == 100 - m["need"]
    # reserve in minutes: none, 30 min, 1 h
    for minutes in (0, 30, 60):
        _, p3, _ = _plan({**OWNER, "reserve_min": minutes})
        assert all(b["reserve"] == (0 if minutes == 0 else max(1, math.ceil(75 * minutes / 60 / {1: 40, 3: 90}[b["main"]]))) for b in p3["bags"])


def test_ravito_food_counts_in_its_stretch_but_is_never_packed_or_bought():
    inp, plan, _ = _plan()
    gasiri = plan["stretches"][5]
    assert gasiri["aid_food"]
    st = N.apply_op(inp["state"], "aid", (gasiri["key"], -10, 2), inp["products_by_id"], plan)
    st = N.apply_op(st, "aid", (gasiri["key"], -12, 1), inp["products_by_id"], plan)
    _, plan2, _ = _plan(N.state_json(st))
    row = plan2["stretches"][5]
    aid = [it for it in row["items"] if it["at_aid"]]
    assert {(it["pid"], it["n"]) for it in aid} == {(-10, 2), (-12, 1)} and all(it["packed"] == 0 for it in aid)
    assert sum(it["n"] for it in row["items"] if it["pid"] == 3) < sum(it["n"] for it in gasiri["items"] if it["pid"] == 3)
    assert all(it["pid"] > 0 or it["pid"] in (-1, -3) for it in plan2["shop"])
    assert row["caffeine_mg"] >= 50  # 2 × 25 cl of cola ≈ 50 mg
    # nothing at the start (no ravito there) and nothing assumed by default
    assert not plan["stretches"][0]["aid_food"] and not any(it["at_aid"] for r in plan["stretches"] for it in r["items"])


def test_caffeine_keeps_a_second_night_dose_and_never_passes_the_rolling_cap():
    for weight in (50, 68, 90):
        _, plan, _ = _plan(weight=weight)
        cf = plan["caffeine"]
        cap = N.caffeine_cap_mg(weight)
        assert cf and cf["max_mg"] == cap and plan["caffeine_24h_mg"] <= cap
        doses = [(d["clock_s"], d["mg"]) for d in cf["doses"]]
        assert N.rolling_max_mg(doses) <= cap
        end = plan["stretches"][-1]["end_clock_s"]
        assert all(c < end - 30 * 60 for c, _ in doses)
    # 27 h from 21:00: the second night (after 21:00 on day 2) still gets one
    _, plan, _ = _plan(weight=68)
    assert any(d["clock_s"] >= START + 24 * 3600 for d in plan["caffeine"]["doses"])
    # longer than a day: the cap is per 24 h, not for the race
    _, plan, _ = _plan(target_s=40 * 3600, weight=68)
    doses = [(d["clock_s"], d["mg"]) for d in plan["caffeine"]["doses"]]
    assert sum(mg for _, mg in doses) > 400 and N.rolling_max_mg(doses) <= 400
    # cola at a ravito counts against the cap
    inp, plan, _ = _plan(weight=50)
    st = inp["state"]
    for r in plan["stretches"]:
        if r["aid_food"]:
            st = N.apply_op(st, "aid", (r["key"], -10, 4), inp["products_by_id"], plan)
    _, plan2, _ = _plan(N.state_json(st), weight=50)
    assert len(plan2["caffeine"]["doses"]) < len(plan["caffeine"]["doses"])
    # a caffeinated gel added by hand past the cap: said, not hidden
    inp, plan, _ = _plan(weight=50)
    k = plan["stretches"][0]["key"]
    st = N.apply_op(inp["state"], "n", (k, 4, 3), inp["products_by_id"], plan)
    _, plan3, _ = _plan(N.state_json(st), weight=50)
    assert plan3["caffeine_over"] and plan3["caffeine_24h_mg"] > 300


def test_one_beep_fits_every_phase_or_the_switch_row_says_so():
    _, plan, _ = _plan()
    b = plan["beep"]
    assert b["common"] and b["interval"] == 25  # Maurten 160 + Baouw and PF 90 prises both on 25 min
    # Maurten 160 alone at 30 min (80 g/h) cannot share a beep with 30 g prises at Fragile: one per phase
    nj = {"v": 3, "picks": [1, 3], "level": "fragile", "phases": [{"km": 0, "mix": {"1": 1}}, {"km": 64.0, "mix": {"3": 1}}]}
    _, plan, _ = _plan(nj)
    b = plan["beep"]
    assert not b["common"] and b["interval"] == 40 and b["per_phase"] == {0: 40, 1: 30}
    switch = next(r for r in plan["stretches"] if r["phase_start"])
    assert switch["beep_note"] == 30 and not any(r["beep_note"] for r in plan["stretches"] if r is not switch)
    for iv in b["per_phase"].values():
        assert iv in NP.BEEPS


def test_no_food_ravito_gives_blocks_and_no_checkpoint_at_all_still_works():
    water_only = [{**cp, "kind": "water", "drop_bag": False} for cp in LONG_CPS]
    _, plan, _ = _plan(cps=water_only)
    rows = plan["stretches"]
    assert len(rows) == 9 and all(r["is_block"] for r in rows)  # 27 h in blocks of 3 h
    assert sum(r["d_s"] for r in rows) == 27 * 3600 and len(plan["bags"]) == 1
    _, plan, _ = _plan(cps=[])
    assert len(plan["stretches"]) == 9 and plan["stretches"][-1]["to_name"] == "Arrivée"


def test_heat_brings_more_water_to_carry_not_more_carbs():
    _, cool, _ = _plan()
    _, hot, _ = _plan(temps=28.0)
    assert [r["carbs_g"] for r in cool["stretches"]] == [r["carbs_g"] for r in hot["stretches"]]
    assert sum(r["fluid_ml"] for r in hot["stretches"]) > sum(r["fluid_ml"] for r in cool["stretches"])
    assert NP.fluid_rate(15) == 500 and NP.fluid_rate(28) == 825 and NP.fluid_rate(40) == 900 and NP.fluid_rate(20, 1.25) == 781.25


def test_short_races_follow_the_duration_ladder():
    assert N.race_rate(90, 50 * 60) == 0 and N.race_rate(90, 1.5 * 3600) == 30 and N.race_rate(90, 2.5 * 3600) == 60
    assert N.race_rate(90, 5 * 3600) == 90


def test_older_plans_read_in_memory_as_one_phase():
    legacy = {"targets": {"carbs_g_per_h": 75, "fluid_ml_per_h": 650, "sodium_mg_per_h": 575},
              "items": [{"product_id": 3, "per_hour": 0.5}, {"product_id": 2, "per_hour": 0.5}, {"product_id": 1, "per_hour": 0.5},
                        {"product_id": 4, "per_hour": 1.0}, {"product_id": 5, "per_hour": 1.0}],
              "flask_capacity_ml": 1000, "caffeine": {"enabled": True, "from_h": 3.0, "every_h": 2.5, "dose_mg": 100, "boost_dawn": True}}
    inp = N.resolve_inputs(legacy, PANTRY, 27 * 3600, None, 68)
    assert inp["legacy"] and inp["upgraded"] and inp["level"] == "normal" and inp["carry_ml"] == 1000
    assert inp["phases"] == [{"km": 0.0, "mix": {3: 0.5, 2: 0.5, 1: 0.5}}] and inp["caf_pid"] == 4
    v2 = {"v": 2, "picks": [1, 2, 4, 5], "manual": {"1": 1.0}, "level": "solide", "carry_ml": 1500}
    inp = N.resolve_inputs(v2, PANTRY, 27 * 3600, None, 68)
    assert inp["phases"][0]["mix"] == {1: 1.0, 2: 1.0} and inp["level"] == "solide" and inp["upgraded"]
    # the echo keys keep a rollback readable: race averages per product
    _, plan, _ = _plan()
    echo = N.with_echo(N.state_json(inp["state"]), plan, inp)
    assert echo["v"] == 3 and echo["items"] and all(it["per_hour"] > 0 for it in echo["items"]) and echo["manual"] == {}


def test_phase_ops_clear_hand_rows_only_when_a_product_leaves_and_can_be_undone():
    inp, plan, _ = _plan()
    rows = plan["stretches"]
    k5, k6 = rows[5]["key"], rows[6]["key"]
    st = N.apply_op(inp["state"], "n", (k6, 3, 1), inp["products_by_id"], plan)
    st = N.apply_op(st, "mix", (k5, 1), inp["products_by_id"], plan)  # Maurten 160 joins from Gasiri: nothing cleared
    assert k6 in st["rows"] and not st["undo"] and any(abs(p["km"] - k5) < 0.05 for p in st["phases"])
    st = N.apply_op(st, "mix", (k5, 3), inp["products_by_id"], plan)  # PF 90 leaves from Gasiri: the hand row with PF 90 goes back to auto
    assert k6 not in st["rows"] and st["undo"]["cleared"] == 1
    back = N.apply_op(st, "undo", None, inp["products_by_id"], plan)
    assert k6 in back["rows"] and back["rows"][k6][3] >= 1
    # a change back to the mix already in force is no change; a switch can be removed
    st = N.apply_op(inp["state"], "mix", (k5, 1), inp["products_by_id"], plan)
    st = N.apply_op(st, "mix", (k5, 1), inp["products_by_id"], plan)
    assert [p["km"] for p in st["phases"]] == [0.0, 64.0]
    st = N.apply_op(st, "unswitch", 64.0, inp["products_by_id"], plan)
    assert [p["km"] for p in st["phases"]] == [0.0] and st["undo"]


def test_a_catalogue_product_replaces_the_quick_pick_of_its_role_everywhere():
    products = N.with_generics(PANTRY)
    st = {"picks": [-1, -3], "phases": [{"km": 0.0, "mix": {-1: 1.0}}, {"km": 20.0, "mix": {-1: 1.0}}], "rows": {33.0: {-1: 3}}, "aid": {}, "have": {}, "custom": {}}
    st = N.apply_op(st, "pick", 1, products)
    assert st["picks"] == [1, -3] and all(p["mix"] == {1: 1.0} for p in st["phases"]) and st["rows"][33.0] == {}
    st = N.apply_op(st, "pick", (3, 20.0), products)  # « + Marque » in a phase: that phase only
    assert st["phases"][0]["mix"] == {1: 1.0} and st["phases"][1]["mix"] == {1: 1.0, 3: 1.0}
    st = N.apply_op(st, "toggle", 1, products)  # off the list: out of every phase and row
    assert 1 not in st["picks"] and all(1 not in p["mix"] for p in st["phases"])


def test_servings_fall_back_on_the_catalogue():
    assert N.servings_of({"name": "Precision Fuel PF 90 Gel"}) == 3
    assert N.servings_of({"name": "Precision Fuel PF 90 Gel", "servings": 1}) == 3
    assert N.servings_of({"name": "Mon gel", "servings": 2}) == 2 and N.servings_of({"name": "Mon gel"}) == 1
    assert N.CATALOG_BY_KEY["pf-90-gel"]["servings"] == 3
