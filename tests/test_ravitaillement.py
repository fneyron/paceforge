"""« Ravitaillement »: Nutrition + Sacs in one view, Pilotage folded into the
passage rows. A complete plan with zero input, a few taps to make it yours,
old plans unchanged, the same numbers on every surface."""

import json
import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.nutrition import NutritionProduct
from app.models.route import Route
from app.models.user import User
from app.services import nutrition as N
from app.services.pacing_guide import build_pacing_guide, leg_instructions, resolve_hr_caps
from tests.test_race_plan_services import CPS, _course, _sections


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


async def _route(client: AsyncClient, target_s: int = 5 * 3600, cps: list[dict] = CPS, name: str = "Jeju test") -> int:
    r = await client.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(cps),
        "name": name, "target_time_s": target_s, "race_date": "2099-10-02",
        "start_hour": 21, "start_minute": 0, "sport_type": "trail", "stop_minutes": 3,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _nj(db: AsyncSession, route_id: int):
    route = (await db.execute(select(Route).where(Route.id == route_id))).scalar_one()
    await db.refresh(route)
    return route.nutrition_json


def _pressed(html: str, title: str) -> bool:
    return f'aria-pressed="true" title="{title}"' in html


# ── the legacy plan (the seeded Transjeju shape) keeps its numbers ──────────

LEGACY_PRODUCTS = {
    11: {"id": 11, "name": "Precision Fuel PF 90 Gel", "kind": "gel", "carbs_g": 90, "sodium_mg": 0, "kcal": 360, "caffeine_mg": None, "volume_ml": None},
    12: {"id": 12, "name": "Baouw Gel", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "kcal": 120, "caffeine_mg": None, "volume_ml": None},
    13: {"id": 13, "name": "Maurten Gel 160", "kind": "gel", "carbs_g": 40, "sodium_mg": 30, "kcal": 160, "caffeine_mg": None, "volume_ml": None},
    14: {"id": 14, "name": "Maurten Gel 100 CAF 100", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "kcal": 100, "caffeine_mg": 100, "volume_ml": None},
    15: {"id": 15, "name": "Precision Hydration PH 1500 (pastille, 500 ml)", "kind": "salt", "carbs_g": 0, "sodium_mg": 750, "kcal": None, "caffeine_mg": None, "volume_ml": None},
}
LEGACY_PLAN = {
    "targets": {"carbs_g_per_h": 75, "fluid_ml_per_h": 650, "sodium_mg_per_h": 575},
    "items": [{"product_id": 11, "per_hour": 0.5}, {"product_id": 12, "per_hour": 0.5}, {"product_id": 13, "per_hour": 0.5},
              {"product_id": 14, "per_hour": 1.0}, {"product_id": 15, "per_hour": 1.0}],
    "flask_capacity_ml": 1000, "refills": [],
    "caffeine": {"enabled": True, "from_h": 3.0, "every_h": 2.5, "dose_mg": 100, "boost_dawn": True},
}


def _numbers(plan: dict) -> dict:
    return {
        "legs": [[(u["name"], u["units"]) for u in lg["units"]] + [lg["carbs_real_g"], lg["fluid_ml"], lg["dry"]] for lg in plan["schedule"]],
        "caffeine": [(d["elapsed_s"], d["mg"], d.get("units")) for d in (plan["caffeine"] or {}).get("doses", [])],
        "packing": [(g["at"], g["until"], sorted((u["name"], u["units"]) for u in g["units"])) for g in plan["packing"]],
        "lines": [(ln["name"], ln["per_hour"], ln["total_units"]) for ln in plan["lines"]],
        "targets": plan["targets"], "feasible": (plan["hydration"] or {}).get("feasible"),
    }


def test_legacy_plan_numbers_are_unchanged():
    _, secs = _sections(target=10 * 3600, start_hour=21)
    resupply = [{"km": 10.0, "name": "Col"}]
    duration = 10 * 3600
    # before: what the previous Nutrition view handed compute_plan
    before = N.compute_plan(
        duration, {**N.default_targets(duration / 3600, None), **LEGACY_PLAN["targets"]}, LEGACY_PLAN["items"], LEGACY_PRODUCTS, secs,
        flask_capacity_ml=1000, refill_kms={6.0, 10.0, 20.0}, resupply_points=resupply,
        caffeine={**N.CAFFEINE_DEFAULTS, **LEGACY_PLAN["caffeine"]}, start_offset_s=21 * 3600, weight_kg=68,
    )
    # after: through the one resolver
    inp = N.resolve_inputs(LEGACY_PLAN, LEGACY_PRODUCTS, duration, None, 68)
    assert inp["legacy"] and not inp["is_virtual"] and inp["level"] == "normal" and inp["sweat"] is None and inp["carry_ml"] == 1000
    after = N.compute_plan(
        duration, inp["targets"], inp["items"], inp["products_by_id"], secs, flask_capacity_ml=inp["flask_capacity_ml"],
        refill_kms={6.0, 10.0, 20.0}, resupply_points=resupply, caffeine=inp["caffeine"], start_offset_s=21 * 3600, weight_kg=68,
    )
    assert _numbers(before) == _numbers(after)
    assert before["caffeine"]["doses"]  # the caffeine plan is part of what is compared


@pytest.mark.asyncio
async def test_seeded_transjeju_plan_renders_as_set(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    rid = await _route(as_user, target_s=10 * 3600)
    ids = {}
    for p in LEGACY_PRODUCTS.values():
        prod = NutritionProduct(user_id=test_user.id, **{k: v for k, v in p.items() if k != "id"})
        db_session.add(prod)
        await db_session.flush()
        ids[p["id"]] = prod.id
    route = await db_session.get(Route, rid)
    route.nutrition_json = {**LEGACY_PLAN, "items": [{"product_id": ids[it["product_id"]], "per_hour": it["per_hour"]} for it in LEGACY_PLAN["items"]]}
    await db_session.flush()
    t = (await as_user.get(f"/partials/simulator/nutrition/{rid}")).text
    for p in LEGACY_PRODUCTS.values():
        assert _pressed(t, p["name"]), p["name"]
    assert '"v":"normal"}\' aria-pressed="true"' in t  # 75 g/h = Normal
    assert "transpiration : ton réglage · 1 L portés · quantités à la main" in t
    assert "Ton réglage : 100 mg toutes les 2h30 dès 3 h." in t
    assert "en alternant PF 90, Baouw et Maurten 160" in t  # three gels at ½ an hour each = 1 every 40 min
    assert "réglé à la main" in t
    assert await _nj(db_session, rid) == route.nutrition_json  # reading never writes


# ── zero input: a complete plan ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_race_without_a_plan_shows_a_complete_default_and_the_read_writes_nothing(as_user: AsyncClient, db_session: AsyncSession):
    short = await _route(as_user, target_s=5 * 3600)
    t = (await as_user.get(f"/partials/simulator/nutrition/{short}")).text
    assert _pressed(t, "Gel") and _pressed(t, "Pastille de sel") and not _pressed(t, "Gel caféiné")
    assert "<b>1 gel</b> toutes les 20 min" in t and "<b>1 pastille de sel</b> toutes les 40 min" in t
    assert "Bois 0,5 L</b> par heure, remplis à chaque point d'eau" in t
    assert "Sac au départ" in t and "Drop bag · Col" in t and "dont 2 de secours" in t
    assert "pf-rv-warn is-warn" not in t  # nothing to fix
    assert "<table" not in t and 'type="number"' not in t.split('id="rv-brands"')[0]  # taps, not typing
    assert await _nj(db_session, short) is None
    # the passage rows carry the same plan
    r = await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(CPS), "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0, "route_id": short, "stop_minutes": 3,
    })
    assert re.search(r"Sur ce tronçon : \d+ gels?", r.text)
    # from 8 h, a caffeinated gel joins the default
    long_ = await _route(as_user, target_s=9 * 3600, name="Long")
    t = (await as_user.get(f"/partials/simulator/nutrition/{long_}")).text
    assert _pressed(t, "Gel caféiné") and re.search(r"<b>1 gel caféiné</b> à \d\d:\d\d", t)


@pytest.mark.asyncio
async def test_a_new_race_starts_from_the_newest_plan_of_another_race(as_user: AsyncClient, db_session: AsyncSession):
    first = await _route(as_user, name="CCC 2025")
    r = await as_user.post(f"/partials/simulator/nutrition/{first}/catalog/pf-90-gel")
    assert r.status_code == 200
    r = await as_user.post(f"/partials/simulator/nutrition/{first}/plan", data={"op": "level", "v": "solide"})
    second = await _route(as_user, name="UTMB")
    t = (await as_user.get(f"/partials/simulator/nutrition/{second}")).text
    assert _pressed(t, "Precision Fuel PF 90 Gel") and "Repris de « CCC 2025 »" in t
    assert '"v":"solide"}\' aria-pressed="true"' in t
    assert await _nj(db_session, second) is None


def test_generic_products_survive_a_non_empty_pantry():
    pantry = {7: {"id": 7, "name": "Mon gel", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "caffeine_mg": None, "volume_ml": None}}
    inp = N.resolve_inputs({"targets": {"carbs_g_per_h": 75}, "items": [{"product_id": -1, "per_hour": 3}, {"product_id": -3, "per_hour": 1}]}, pantry, 5 * 3600, None, None)
    assert inp["items"] == [{"product_id": -1, "per_hour": 3.0}, {"product_id": -3, "per_hour": 1.0}]
    assert -1 in inp["products_by_id"] and 7 in inp["products_by_id"]


# ── the taps ────────────────────────────────────────────────────────────────

def test_level_tap_clears_manual_step_makes_it_manual_reset_clears_everything():
    products = N.with_generics({})
    st = {"picks": [-1, -3], "manual": {}, "level": None, "sweat": None, "carry_ml": None, "custom": {"carbs_g_per_h": 80, "fluid_ml_per_h": 700}, "refills": []}
    st = N.apply_op(st, "step", (-1, 0.5), products, {-1: 3.0})
    assert st["manual"] == {-1: 3.5}
    st = N.apply_op(st, "step", (-3, -0.5), products, {-3: 0.5})
    assert st["manual"][-3] == 0.5  # never below ½
    lv = N.apply_op(st, "level", "fragile", products)
    assert lv["manual"] == {} and lv["level"] == "fragile" and "carbs_g_per_h" not in lv["custom"] and lv["custom"]["fluid_ml_per_h"] == 700
    rs = N.apply_op(st, "reset", None, products)
    assert rs["manual"] == {} and rs["custom"] == {} and rs["picks"] == [-1, -3]


@pytest.mark.asyncio
async def test_the_taps_write_a_v2_plan_and_change_rows_bags_and_shopping(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user, target_s=6 * 3600)
    t0 = (await as_user.get(f"/partials/simulator/nutrition/{rid}")).text
    r = await as_user.post(f"/partials/simulator/nutrition/{rid}/plan", data={"op": "level", "v": "solide"})
    t1 = r.text
    nj = await _nj(db_session, rid)
    assert nj["v"] == 2 and nj["level"] == "solide" and nj["picks"] == [-1, -3] and nj["items"] and nj["targets"]["carbs_g_per_h"] == 90
    assert "<b>1 gel</b> toutes les 20 min" in t0 and "<b>1 gel</b> toutes les 15 min" in t1  # 75 g/h = 3 gels an hour, 90 g/h = 3,5
    shop0 = re.search(r"<li><b>(\d+)</b><span>gels</span>", t0).group(1)
    shop1 = re.search(r"<li><b>(\d+)</b><span>gels</span>", t1).group(1)
    assert int(shop1) > int(shop0)
    # a step makes it « à la main », a level tap brings it back to auto
    r = await as_user.post(f"/partials/simulator/nutrition/{rid}/plan", data={"op": "step", "pid": "-1", "step": "-0.5", "open": "adjust"})
    assert "quantités à la main" in r.text and 'id="rv-adjust" class="pf-rv-disc pf-rv-adjust" open' in r.text
    assert (await _nj(db_session, rid))["manual"] == {"-1": 3.0}
    r = await as_user.post(f"/partials/simulator/nutrition/{rid}/plan", data={"op": "level", "v": "normal"})
    assert "quantités auto" in r.text and (await _nj(db_session, rid))["manual"] == {}
    # sweat and water carried
    r = await as_user.post(f"/partials/simulator/nutrition/{rid}/plan", data={"op": "sweat", "v": "beaucoup", "open": "adjust"})
    assert "beaucoup de transpiration" in r.text and "Bois 0,6 L" in r.text  # 500 × 1,25
    r = await as_user.post(f"/partials/simulator/nutrition/{rid}/plan", data={"op": "carry", "v": "1500"})
    assert (await _nj(db_session, rid))["carry_ml"] == 1500 and "1,5 L portés" in r.text
    # drop bags are checkpoint flags: the plan's autosave sends them, bags and rows follow
    cps = [dict(c) for c in CPS]
    cps[2]["drop_bag"] = True  # Village
    r = await as_user.post("/api/simulator/routes", data={"route_id": rid, "checkpoints_json": json.dumps(cps), "name": "Jeju test", "target_time_s": 6 * 3600, "start_hour": 21, "start_minute": 0})
    t = (await as_user.get(f"/partials/simulator/nutrition/{rid}")).text
    assert "Drop bag · Col" in t and "Drop bag · Village" in t and 'aria-pressed="true" data-ci="2"' in t
    r = await as_user.post("/partials/simulator/passage-times", data={"checkpoints_json": json.dumps(cps), "target_time_s": 6 * 3600, "start_hour": 21, "start_minute": 0, "route_id": rid})
    assert r.text.count("Drop bag ici") == 2


@pytest.mark.asyncio
async def test_catalogue_pick_replaces_the_generic_of_the_same_role(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user)
    r = await as_user.post(f"/partials/simulator/nutrition/{rid}/catalog/pf-90-gel")
    t = r.text
    assert _pressed(t, "Precision Fuel PF 90 Gel") and 'title="Gel"' not in t  # « Gel » is replaced, not offered
    assert "<b>1 PF 90</b>" in t and 'open' not in t.split('id="rv-brands"')[1][:40]  # the panel closes
    nj = await _nj(db_session, rid)
    assert -1 not in nj["picks"] and -3 in nj["picks"]
    # toggling it off brings the quick pick back
    pid = nj["picks"][0]
    r = await as_user.post(f"/partials/simulator/nutrition/{rid}/plan", data={"op": "toggle", "v": str(pid)})
    assert 'aria-pressed="false" title="Gel"' in r.text
    assert (await as_user.post("/api/nutrition/products", data={"name": "x"})).status_code in (404, 405)


# ── the engine rules ────────────────────────────────────────────────────────

def test_a_drink_never_exceeds_the_fluid_target_and_water_excludes_it():
    drink = N.GENERIC_BY_ID[-2]
    for fluid in (500, 650, 900):
        r = N.auto_rates(75, 400, [drink], fluid_ml_per_h=fluid, manual={})[-2]
        assert r * 500 <= fluid and r >= 0.5
    _, secs = _sections(target=5 * 3600, start_hour=21)
    inp = N.resolve_inputs({"v": 2, "picks": [-2]}, {}, 5 * 3600, None, None)
    assert inp["items"] == [{"product_id": -2, "per_hour": 1.0}]
    plan = N.compute_plan(5 * 3600, inp["targets"], inp["items"], inp["products_by_id"], secs, refill_kms={6.0, 10.0, 20.0}, caffeine=inp["caffeine"])
    for lg in plan["schedule"]:
        assert lg["water_ml"] == max(0, lg["fluid_ml"] - lg["units"][0]["units"] * 500)
    warn = N.rule_warnings(plan, inp["picks"], inp["products_by_id"], True)
    assert any("Pas assez de glucides" in w["text"] for w in warn)


def test_a_caffeinated_gel_is_only_taken_at_caffeine_times_and_covers_the_race():
    _, secs = _sections(target=19 * 3600, start_hour=21)
    caf = {"id": 9, "name": "Maurten Gel 100 CAF 100", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "caffeine_mg": 100, "volume_ml": None}
    inp = N.resolve_inputs({"v": 2, "picks": [9]}, {9: caf}, 19 * 3600, None, 70)
    plan = N.compute_plan(19 * 3600, inp["targets"], inp["items"], inp["products_by_id"], secs, caffeine=inp["caffeine"], start_offset_s=21 * 3600, weight_kg=70)
    line = plan["lines"][0]
    assert line["by_caffeine"] and line["per_hour"] == 0  # never « per hour »
    cf = plan["caffeine"]
    assert cf["total_mg"] <= cf["max_mg"] and len(cf["doses"]) == 4
    assert [round(d["elapsed_s"] / 3600) for d in cf["doses"]] == [3, 8, 13, 18]
    assert any("sans caféine" in w["text"] for w in N.rule_warnings(plan, [9], inp["products_by_id"], True))


def test_spares_live_in_bags_and_shopping_only():
    _, secs = _sections(target=8 * 3600, start_hour=21)
    inp = N.resolve_inputs(None, {}, 8 * 3600, None, None)
    plan = N.compute_plan(8 * 3600, inp["targets"], inp["items"], inp["products_by_id"], secs, refill_kms={6.0, 10.0, 20.0},
                          resupply_points=[{"km": 10.0, "name": "Col"}], caffeine=inp["caffeine"], start_offset_s=21 * 3600)
    legs_gels = sum(u["units"] for lg in plan["schedule"] for u in lg["units"] if u["product_id"] == -1)
    main = N.main_product_id(plan)
    assert main == -1
    shop = N.shopping_list(plan, main, inp["products_by_id"])
    bags_gels = sum(u["units"] for g in plan["bags"] for u in g["units"] if u["product_id"] == -1)
    assert bags_gels == legs_gels + len(plan["bags"]) == legs_gels + 2
    assert sum(u["units"] for lg in plan["schedule"] for u in lg["units"] if u["product_id"] == -1) == legs_gels  # legs untouched
    for it in shop:
        assert it["n"] == sum(u["units"] for g in plan["bags"] for u in g["units"] if u["product_id"] == it["product_id"])
    gel = next(it for it in shop if it["product_id"] == -1)
    assert gel["spares"] == 2 and gel["note"] == "dont 2 de secours"


def test_unit_labels_and_intervals_in_words():
    assert N.unit_label(1, N.GENERIC_BY_ID[-1]) == "1 gel"
    assert N.unit_label(3, N.GENERIC_BY_ID[-1]) == "3 gels"
    assert N.unit_label(2, N.GENERIC_BY_ID[-3]) == "2 pastilles de sel"
    assert N.unit_label(4, {"name": "Precision Fuel PF 90 Gel"}) == "4 × PF 90"
    assert N.unit_label(1, N.GENERIC_BY_ID[-4]) == "1 gel caféiné" and N.unit_label(5, N.GENERIC_BY_ID[-4]) == "5 gels caféinés"
    words = {0.5: "toutes les 2 h", 1: "par heure", 1.5: "toutes les 40 min", 2: "toutes les 30 min", 2.5: "toutes les 25 min",
             3: "toutes les 20 min", 4: "toutes les 15 min", 0.4: "toutes les 2h30", 4.5: "4,5 par heure"}
    for rate, text in words.items():
        assert N.interval_words(rate) == text, rate


# ── Pilotage in the passage rows ────────────────────────────────────────────

def test_the_heart_rate_ceiling_is_per_leg_and_the_watch_shows_the_same():
    course = _course()
    cps = [{"name": f"P{k}", "distance_km": float(k), "elevation": 0, "kind": "water", "crew": False, "drop_bag": False, "cutoff_clock": None}
           for k in range(2, 30, 2)]
    from app.services.race_simulator import compute_passage_times

    secs = compute_passage_times(course, cps, 5 * 3600, 1.0, 21, 0, None)
    guide = build_pacing_guide(course, 5 * 3600, hr_cap_climb=140, hr_cap_flat=136, hr_release_descent=130, plan_sections=secs)
    legs = leg_instructions(guide, secs)
    same = [(a, b) for a, b in zip(legs, legs[1:], strict=False) if a["cls"] == b["cls"]]
    assert same and all(b["hr_cap"] < a["hr_cap"] for a, b in same)
    for lg in legs:
        assert f"FC{lg['hr_cap']}" in lg["code"]


def test_effort_and_steep_lines():
    from app.services.pacing_guide import effort_sentence, steep_on_leg, steep_sentence

    assert effort_sentence("flat", 129) == "<b>Cardio sous 129</b> · roulant : cours régulier, mange et bois."
    assert effort_sentence("descent", 111) == "<b>Cardio vers 111</b> · descente : relâché, sans freiner. Mange avant, en haut."
    assert effort_sentence("climb", 124, 18) == "<b>Cardio sous 124</b> · montée : marche dès que ça dépasse 18 %, cours le reste."
    assert effort_sentence("stairs", None) == "Très raide : marche, mains sur les cuisses."
    alerts = [{"start_km": 92.0, "end_km": 96.0, "max_grade": 21}, {"start_km": 98.0, "end_km": 101.0, "max_grade": 24}]
    on_leg = steep_on_leg(alerts, 90.0, 100.0)
    assert on_leg[-1]["end_km"] == 100.0  # clipped to the leg
    assert steep_sentence(on_leg) == "Raide km 92 → 96 et km 98 → 100, jusqu'à 24 % : marche, mains sur les cuisses."
    assert steep_sentence(on_leg[1:]) == "Raide km 98 → 100, jusqu'à 24 % : marche, mains sur les cuisses."
    assert steep_on_leg(alerts, 60.0, 80.0) == [] and steep_sentence([]) is None


def test_resolve_hr_caps_derives_and_honours_old_values():
    assert resolve_hr_caps({}, 180) == {"climb": 135, "flat": 131, "descent": 125, "walk_grade": 18.0, "source": "activités"}
    assert resolve_hr_caps({"hr_cap_climb": 150}, 180)["flat"] == 146 and resolve_hr_caps({"hr_cap_climb": "150"}, None)["descent"] == 140
    old = resolve_hr_caps({"hr_cap_climb": 140, "hr_cap_flat": 136, "hr_release_descent": 128, "walk_grade": 20}, 185)
    assert old == {"climb": 140, "flat": 136, "descent": 128, "walk_grade": 20.0, "source": "toi"}
    assert resolve_hr_caps({}, None)["climb"] is None


@pytest.mark.asyncio
async def test_rows_and_watch_codes_carry_the_same_ceiling(as_user: AsyncClient):
    rid = await _route(as_user)
    r = await as_user.post(f"/api/simulator/routes/{rid}/params", data={"hr_cap_climb": 150})
    assert r.status_code == 204
    t = (await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(CPS), "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0, "route_id": rid, "stop_minutes": 3,
    })).text
    row_caps = [int(x) for x in re.findall(r"FC ≤ (\d+)", t)]
    csv = (await as_user.get(f"/api/simulator/routes/{rid}/pace-export?format=csv")).text
    codes = [int(x) for x in re.findall(r"FC(\d+)", csv)]
    assert row_caps and row_caps == codes[:len(row_caps)]
    assert row_caps[0] <= 150 and len(set(row_caps)) > 1  # falls along the race, not one repeated number


# ── water ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_water_auto_has_no_red_and_a_short_capacity_is_named(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    rid = await _route(as_user, target_s=10 * 3600)
    t = (await as_user.get(f"/partials/simulator/nutrition/{rid}")).text
    assert "is-alert" not in t and "Eau : prévois" in t
    r = await as_user.post(f"/partials/simulator/nutrition/{rid}/plan", data={"op": "carry", "v": "1000"})
    t = r.text
    assert "pf-rv-water is-alert" in t and "Prends une flasque de plus." in t
    stretch = re.search(r"il te faudrait [\d,]+ L entre (\w[\w ]*?) et (\w[\w ]*?) \(", t)
    assert stretch
    # the row of the point where that stretch starts says it, in red
    from app.routers.simulator import _passage_table_context
    from app.schemas.simulator import CourseProfile

    route = await db_session.get(Route, rid)
    await db_session.refresh(route)
    ctx = await _passage_table_context(db_session, test_user.id, CourseProfile(**route.course_json), CPS, 10 * 3600, 1.0, 21, 0, None,
                                       route, 3, None, None, route_id=rid)
    names = [s["end_name"] for s in ctx["sections"]]
    row = ctx["legs"][names.index(stretch.group(1))]
    assert row["water_alert"] and row["water_note"].startswith("Remplis tout ici") and stretch.group(2) in row["water_note"]
    assert all(lg["effort"] for lg in ctx["legs"]) and all(lg["hr_cap"] is None or lg["hr_cap"] > 0 for lg in ctx["legs"])
