"""Nutrition v2 : « tes produits une fois, un rythme par produit, le reste calculé ».

The athlete says when he eats each product (a rhythm: 1 every N min, from h to
h); the aid stations are only where the bag is refilled, and each refill point
gets the intakes until the next one in whole units, and the water to carry to
it (more when the race is hot, its sodium said then). Older plans are read in
memory (mapped when they map cleanly, else started over with one line), reading
never writes, every form works without JS (303 back to the race page, the card
open there)."""

import json
import math
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
from app.services import nutrition_plan as NP
from app.services.race_simulator import compute_passage_times
from tests.nutri_course import LONG_CPS, long_course, long_sections
from tests.test_race_plan_services import CPS, _course

P = "/partials/simulator/nutrition"
HX = {"HX-Request": "true"}

MAURTEN = {"id": 1, "name": "Maurten Gel 100", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "caffeine_mg": None, "servings": 1}
PF90 = {"id": 2, "name": "Precision Fuel PF 90 Gel", "kind": "gel", "carbs_g": 90, "sodium_mg": 0, "caffeine_mg": None, "servings": 3}
CAF = {"id": 3, "name": "Maurten Gel 100 CAF 100", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "caffeine_mg": 100, "servings": 1}
PRODUCTS = N.with_generics({1: MAURTEN, 2: PF90, 3: CAF})


def _line(pid: int, every: int, frm: int = 0, to: int | None = None) -> dict:
    return {"product_id": pid, "every_min": every, "from_min": frm, "to_min": to}


def _toy(points: list[tuple], finish_min: int, start_h: int = 6) -> tuple[list[dict], list[dict]]:
    """A race's sections and checkpoints: [(name, km, arrival minute, kind)], the finish at ``finish_min``."""
    cps, secs = [], []
    for i, (name, km, t, kind) in enumerate(points):
        cps.append({"name": name, "distance_km": km, "kind": kind, "crew": False, "drop_bag": False})
        secs.append({"end_km": km, "end_name": name, "end_checkpoint_index": i, "clock_time_s": start_h * 3600 + t * 60, "cumulative_time_s": t * 60})
    secs.append({"end_km": 50.0, "end_name": "Arrivée", "end_checkpoint_index": None, "clock_time_s": start_h * 3600 + finish_min * 60,
                 "cumulative_time_s": finish_min * 60})
    return secs, cps


def _count(lines: list[dict], secs: list[dict], cps: list[dict], spare: bool = False, start_h: int = 6):
    end = NP.race_end_s(secs, start_h * 3600)
    il = NP.intakes(lines, end)
    order = list(dict.fromkeys(r["product_id"] for r in lines))
    sp = NP.main_product(lines, il, PRODUCTS) if spare else None
    return NP.ravito_rows(NP.refill_points(secs, cps, start_h * 3600), end, il, PRODUCTS, order, sp), il, end


def _texts(rows: list[dict], room: int | None = None) -> list[str]:
    return [NP.take_text(r["items"], PRODUCTS, room) for r in rows]


# ── the counting ────────────────────────────────────────────────────────────

def test_the_owners_case_a_ravito_at_30_min_takes_what_the_next_stretch_needs():
    """Maurten every 60 min, a ravito at 0:30: nothing to take at the start, the
    0:30 ravito takes the intakes until the next ravito, never half a gel."""
    secs, cps = _toy([("Bambi", 4.0, 30, "full"), ("Col", 25.0, 190, "full")], 300)
    rows, il, end = _count([_line(1, 60)], secs, cps)
    assert end == 5 * 3600 and [t for t, _pid, _n in il] == [3600, 7200, 10800, 14400]  # none on the finish line
    assert [r["name"] for r in rows] == ["Départ", "Bambi", "Col"]
    assert _texts(rows) == ["rien à prendre", "3 Maurten 100", "1 Maurten 100"]  # Bambi: 1 h, 2 h, 3 h (Col at 3:10)
    assert NP.shopping(rows) == {1: 4}
    # « +1 de secours à chaque ravito »: one more of the main product on every row, in the list too
    rows, _, _ = _count([_line(1, 60)], secs, cps, spare=True)
    assert _texts(rows) == ["1 Maurten 100", "4 Maurten 100", "2 Maurten 100"]
    assert NP.shopping(rows) == {1: 7}


def test_a_product_switch_at_8_h_on_a_16_h_race_with_ten_ravitos():
    """Maurten until 8 h, Precision 90 from 8 h: whole units on every row, each
    product only where its line runs, the list the sum of the rows."""
    _, secs = long_sections()  # 145 km, 06:00, 16 h 30
    pts = NP.refill_points(secs, LONG_CPS, 6 * 3600)
    assert len(pts) == 11 and "Jeongseok" not in [p["name"] for p in pts]  # Départ + 10 refill points; the water point is not one
    assert [p["name"] for p in pts if p["base"]] == ["Gasiri", "Camping"] and [p["name"] for p in pts if p["crew"]] == ["Meochewat"]
    end = NP.race_end_s(secs, 6 * 3600)
    assert end == int(16.5 * 3600)
    lines = [_line(1, 60, 0, 480), _line(2, 20, 480)]
    il = NP.intakes(lines, end)
    assert [t for t, p, _ in il if p == 1] == [h * 3600 for h in range(1, 9)]  # 1 h … 8 h: « à 8 h » is the last one
    pf = [t for t, p, _ in il if p == 2]
    assert pf[0] == 8 * 3600 + 20 * 60 and pf[-1] == 16 * 3600 + 20 * 60 and len(pf) == 25
    rows = NP.ravito_rows(pts, end, il, PRODUCTS, [1, 2])
    for r in rows:
        units = {it["pid"]: it["units"] for it in r["items"]}
        assert all(isinstance(u, int) and u >= 0 for u in units.values())
        if r["t_s"] > 8 * 3600:
            assert not units.get(1), r["name"]  # after 8 h no Maurten
        if r["end_s"] <= 8 * 3600 + 20 * 60:
            assert not units.get(2), r["name"]  # before 8:20 no PF 90
    shop = NP.shopping(rows)
    assert shop == {1: 8, 2: math.ceil(25 / 3)}  # the opened pouch carries on: 25 prises, 9 pouches
    assert sum(it["prises"] for r in rows for it in r["items"]) == 8 + 25
    g = NP._round(NP.carbs_per_hour(il, PRODUCTS, end))
    assert g == NP._round((8 * 25 + 25 * 30) / 16.5) == 58  # an ultra: over its 30–50 g/h, train the gut
    assert NP.carbs_note(g, 16.5) == ("Plus que le repère pour un ultra (30 à 50 g par heure) : habitue ton ventre à "
                                      "cette dose à l'entraînement.", "plain")
    # each row is one short line on a phone: the pouches, the opened pouch's prises only when there is room
    for r in rows:
        room = NP.ROW_CHARS - len(r["name"])
        assert len(NP.take_text(r["items"], PRODUCTS, room)) <= max(room, 23), r["name"]


def test_a_pf90_in_3_prises_takes_whole_pouches_and_the_opened_one_carries_on():
    # one prise every 20 min; ravitos at 0:50, 1:30, 2:10; the finish at 2:30 → 2, 2, 2 then 1 prise
    secs, cps = _toy([("A", 5.0, 50, "full"), ("B", 10.0, 90, "full"), ("C", 15.0, 130, "base")], 150)
    rows, _il, _end = _count([_line(2, 20)], secs, cps)
    assert [[(it["prises"], it["units"], it["left"]) for it in r["items"]] for r in rows] == [
        [(2, 1, 1)], [(2, 1, 2)], [(2, 0, 0)], [(1, 1, 2)]]
    assert _texts(rows) == ["1 PF 90 (il t'en reste 1 prise)", "1 PF 90 (il t'en reste 2 prises)", "rien à prendre",
                            "1 PF 90 (il t'en reste 2 prises)"]
    assert _texts(rows, room=20) == ["1 PF 90", "1 PF 90", "rien à prendre", "1 PF 90"]  # no room: the pouches only
    assert NP.shopping(rows) == {2: 3}  # 7 prises, 3 pouches
    # a pouch set to 1 prise in « Tes produits » is a whole unit each time
    one = {**PRODUCTS, 2: {**PF90, "servings": 1, "carbs_g": 30}}
    rows = NP.ravito_rows(NP.refill_points(secs, cps, 6 * 3600), 150 * 60, NP.intakes([_line(2, 20)], 150 * 60), one, [2])
    assert [NP.take_text(r["items"], one) for r in rows] == ["2 PF 90", "2 PF 90", "2 PF 90", "1 PF 90"]


def test_caffeine_against_the_24_h_cap():
    secs, _cps = _toy([], 600)  # 10 h, no ravito
    end = NP.race_end_s(secs, 6 * 3600)
    il = NP.intakes([_line(1, 20), _line(3, 60, 180)], end)
    assert NP.holds_caffeine([_line(3, 60, 180)], PRODUCTS) and not NP.holds_caffeine([_line(1, 20)], PRODUCTS)
    assert NP.caffeine_24h(il, PRODUCTS) == 600  # 4 h … 9 h: six gels of 100 mg
    assert N.caffeine_cap_mg(None) == 400 and N.caffeine_cap_mg(60) == 360
    # a rolling day: on a 30 h race a dose every 4 h is 6 doses in the worst 24 h, not 7
    il = NP.intakes([_line(3, 240)], 30 * 3600)
    assert len(il) == 7 and NP.caffeine_24h(il, PRODUCTS) == 600


def test_the_carbs_per_hour_read_against_the_range_of_the_race_duration():
    """2026-10-09 (owner, on the mockup: « la maquette est parfaite »): the range follows the race's duration —
    under 1 h eating is not needed, 30–60 g/h up to 2 h 30 (ACSM 2016), 60–90 g/h to 6 h (Jeukendrup 2014),
    30–50 g/h past 6 h, an ultra (ISSN 2019); over it, train the gut (plain), under it, the warning colour."""
    assert [N.carbs_zone(h) for h in (0.5, 1, 2.4, 2.5, 5.9, 6, 6.1, 30)] == [
        None, (30, 60), (30, 60), (60, 90), (60, 90), (60, 90), (30, 50), (30, 50)]
    assert NP.carbs_note(45, 16.5) == ("Dans le repère pour un ultra : 30 à 50 g par heure.", "ok")
    assert NP.carbs_note(20, 16.5) == ("Moins que le repère pour un ultra : 30 à 50 g par heure.", "warn")
    assert NP.carbs_note(70, 5) == ("Dans le repère pour cette durée : 60 à 90 g par heure.", "ok")
    assert NP.carbs_note(50, 5)[1] == "warn" and NP.carbs_note(95, 5)[1] == "plain"
    assert NP.carbs_note(40, 2) == ("Dans le repère pour cette durée : 30 à 60 g par heure.", "ok")
    assert NP.carbs_note(0, 0.75) == ("Sur moins d'une heure, manger n'est pas nécessaire.", "plain")
    # « Plan type » aims at the guideline for the duration: 50 g/h on an ultra, no longer 80
    assert [N.starter_carbs(h) for h in (2, 5, 6, 6.5, 16.5)] == [60, 75, 75, 50, 50]
    il = NP.intakes([_line(1, 20)], 6 * 3600)  # 25 g every 20 min = 75 g/h, the last one before the finish
    assert NP._round(NP.carbs_per_hour(il, PRODUCTS, 6 * 3600)) == NP._round(17 * 25 / 6) == 71


def test_plan_type_aims_at_the_guideline_for_the_race_duration():
    assert N.default_targets(10, None)["carbs_g_per_h"] == 80
    assert NP.starter_interval(N.GENERIC_BY_ID[-1], 80) == 20  # 25 g: every 20 min (75 g/h)
    assert NP.starter_interval(PF90, 80) == 20  # a prise of 30 g: every 20 min (90 g/h; 30 min would be 60)
    assert NP.starter_interval({"name": "Maurten Gel 160", "carbs_g": 40, "servings": 1}, 75) == 30  # 80 g/h
    assert NP.starter_interval({"name": "Sel", "carbs_g": 0, "sodium_mg": 300}, 75) == 60


def test_lines_are_read_safely_and_older_plans_map_when_they_can():
    km = {20.0: 3 * 3600 + 7 * 60}.get
    # v2, each line checked: unknown product out, an odd interval snapped, « à » before « de » = the finish
    nj = {"v": 2, "spare": True, "rhythms": [None, "x", {"product_id": "abc"}, {"product_id": 99, "every_min": 20},
                                             {"product_id": 1, "every_min": 7, "from_min": -5, "to_min": "inf"},
                                             {"product_id": "2", "every_min": "20", "from_min": "120", "to_min": "60"}]}
    assert NP.read_plan(nj, PRODUCTS) == {"rhythms": [_line(1, 15), _line(2, 20, 120)], "spare": True, "old": False}
    for junk in (None, [], "plan", 3, {"v": 2, "rhythms": "x"}, {"targets": {"carbs_g_per_h": 60}, "items": []}, {"v": 3, "picks": []}):
        assert NP.read_plan(junk, PRODUCTS)["rhythms"] == [] and NP.read_plan(junk, PRODUCTS)["old"] is False
    # a v3 plan with one product per phase: one line each, at its beep's interval, the switch at its ravito's time
    v3 = {"v": 3, "picks": [1, 2], "level": "normal", "rows": {}, "aid": {"20.0": {"-10": 1}},
          "phases": [{"km": 0.0, "mix": {"1": 1}}, {"km": 20.0, "mix": {"2": 1}}]}
    assert NP.read_plan(v3, PRODUCTS, time_at_km=km) == {
        "rhythms": [_line(1, 20, 0, 187), _line(2, 20, 187)], "spare": False, "old": False}
    # older shapes: a list of products with rates per hour, one product → one line
    legacy = {"targets": {"carbs_g_per_h": 75}, "items": [{"product_id": 1, "per_hour": 1.5}], "flask_capacity_ml": 1000}
    assert NP.read_plan(legacy, PRODUCTS)["rhythms"] == [_line(1, 40)]
    assert NP.read_plan({"v": 2, "picks": [2], "manual": {"2": "0.5"}}, PRODUCTS)["rhythms"] == [_line(2, 40)]  # 1,5 prise an hour
    assert NP.read_plan({"v": 3, "phases": "x", "picks": [1]}, PRODUCTS)["rhythms"] == [_line(1, 20)]  # no phase: his one gel all race
    # what does not map starts over, said once: two products in a phase, a stretch set by hand, salt per flask,
    # a phase at a ravito the race no longer has
    for old in ({**v3, "phases": [{"km": 0.0, "mix": {"1": 1, "2": 1}}]}, {**v3, "rows": {"20.0": {"1": 3}}},
                {**v3, "picks": [1, 2, -3]}, {**v3, "phases": [{"km": 0.0, "mix": {"1": 1}}, {"km": 44.0, "mix": {"2": 1}}]},
                {"items": [{"product_id": 1, "per_hour": 1}, {"product_id": 3, "per_hour": 0.5}]}, {"v": 2, "picks": [1, 3]}):
        assert NP.read_plan(old, PRODUCTS, time_at_km=km) == {"rhythms": [], "spare": False, "old": True}, old
    # the JSON written back
    assert NP.plan_json([_line(1, 60, 0, 480)], False, [12.5]) == {
        "v": 2, "rhythms": [{"product_id": 1, "every_min": 60, "from_min": 0, "to_min": 480}], "spare": False, "refills": [12.5]}


def test_no_aid_station_is_one_stretch_from_the_start_to_the_finish():
    secs, cps = _toy([("Eau", 8.0, 50, "water"), ("Photo", 12.0, 80, "none")], 240)
    rows, _, _ = _count([_line(1, 30)], secs, cps)
    assert [r["name"] for r in rows] == ["Départ"] and _texts(rows) == ["7 Maurten 100"]


# ── water and sodium (ISSN 2019; owner, 2026-10-09: « Oui vas-y ») ──────────────

def test_the_water_to_carry_to_the_next_ravito():
    """Each row but « Arrivée »: its stretch's predicted time (the one its food is for, its arrival to the next
    row's) × 0,5 L/h, 0,75 L/h on a hot race, rounded UP to half a litre, at least half a litre (H)."""
    mn = 60
    # 0,45 L → 0,5; exactly 0,5 L stays; 0,51 → 1 L; 0,55 → 1 L; 1 L; 1,11 → 1,5 L
    assert [NP.water_ml(0, m * mn) for m in (54, 60, 61, 66, 120, 133)] == [500, 500, 1000, 1000, 1000, 1500]
    assert NP.water_ml(0, 3600 + 1) == 1000  # one second past half a litre: up
    assert NP.water_ml(0, 10 * mn) == 500 and NP.water_ml(3600, 3600) == 500  # never less than half a litre
    # hot: 0,75 L/h — 40 min is 0,5 L, 41 min 1 L, 1 h 27 (1,09 L) 1,5 L, 2 h 13 (1,66 L) 2 L
    assert [NP.water_ml(0, m * mn, hot=True) for m in (40, 41, 87, 133)] == [500, 1000, 1500, 2000]
    assert [NP.litres(ml) for ml in (500, 1000, 1500, 2000, 750)] == ["0,5 L", "1 L", "1,5 L", "2 L", "0,75 L"]
    # across midnight: the time on the race's own clock (seconds after the start), never the hour the row shows
    secs, cps = _toy([("Col", 10.0, 150, "full"), ("Village", 20.0, 225, "full")], 300, start_h=21)
    rows, _il, end = _count([_line(1, 30)], secs, cps, start_h=21)
    assert [NP.clock_hm(r["clock_s"]) for r in rows] == ["21:00", "23:30", "00:45"] and end == 5 * 3600
    assert [NP.water_ml(r["t_s"], r["end_s"]) for r in rows] == [1500, 1000, 1000]  # 2 h 30, 1 h 15, 1 h 15 to 02:00
    assert [NP.water_ml(r["t_s"], r["end_s"], hot=True) for r in rows] == [2000, 1000, 1000]  # 1,875 L; 0,94 L
    # the line that goes with them: drink to thirst first, then the rate the rows are counted at
    assert NP.water_note(False) == "Eau : bois à ta soif. Je compte 0,5 L par heure jusqu'au ravito suivant."
    assert NP.water_note(True) == ("Eau : bois à ta soif. Il fera chaud : je compte 0,75 L par heure jusqu'au ravito "
                                   "suivant.")


def test_a_hot_race_is_25_c_or_more_on_average_over_its_predicted_hours():
    """Hot (H): the temperatures the simulator read in the race's saved forecast (each section's, at its arrival
    point and hour, as the passage table shows them), weighted by each section's time on the plan's clocks: 25 °C
    or more. No forecast saved, no temperature: not hot (no guess)."""
    secs, _cps = _toy([("A", 10.0, 60, "full")], 240)  # 06:00, A at 07:00, the finish at 10:00
    assert NP.mean_temp_c(secs, 6 * 3600) is None and not NP.is_hot(secs, 6 * 3600)

    def wx(a: float, b: float) -> list[dict]:
        return [{**secs[0], "temperature_c": a}, {**secs[1], "temperature_c": b}]
    assert NP.mean_temp_c(wx(37, 21), 6 * 3600) == 25.0 and NP.is_hot(wx(37, 21), 6 * 3600)  # (37 × 1 h + 21 × 3 h) / 4 h
    assert NP.mean_temp_c(wx(36, 21), 6 * 3600) == 24.75 and not NP.is_hot(wx(36, 21), 6 * 3600)
    assert not NP.is_hot([{**secs[0], "temperature_c": 40}, secs[1]], 6 * 3600)  # a section without one: no guess
    # the simulator's own: 25 °C all day at the start of a mountain race is 18 °C up there on average; 35 °C is hot
    course = long_course()
    aid = {cp["distance_km"] for cp in LONG_CPS if cp["kind"] != "none"}

    def race(temp_c: float) -> list[dict]:
        hourly = {"temps": [temp_c] * 48, "humidity": [50] * 48}
        return compute_passage_times(course, LONG_CPS, int(16.5 * 3600), 1.0, 6, 0, hourly, aid_kms=aid)
    assert all(s["temperature_c"] is not None for s in race(25))
    assert round(NP.mean_temp_c(race(25), 6 * 3600)) == 18 and not NP.is_hot(race(25), 6 * 3600)
    assert NP.is_hot(race(35), 6 * 3600)
    assert not NP.is_hot(long_sections()[1], 6 * 3600)  # the same race without a forecast


def test_the_sodium_per_hour_and_its_three_sentences():
    """Counted like the carbs (the plan's products' label values, per prise, the intakes over the race's hours);
    said only on a hot race, against ISSN 2019's 300 to 600 mg an hour in the heat."""
    il = NP.intakes([_line(1, 30)], 10 * 3600)  # a Maurten 100 (20 mg) every 30 min: 19 before the finish
    assert NP._round(NP.sodium_per_hour(il, PRODUCTS, 10 * 3600)) == 38  # 19 × 20 mg / 10 h
    salty = {**PRODUCTS, 5: {"id": 5, "name": "Gel salé", "carbs_g": 90, "sodium_mg": 300, "servings": 3}}
    il = NP.intakes([_line(5, 20)], 3 * 3600)  # one prise (100 mg) at 0:20 … 2:40: 8
    assert NP.sodium_per_hour(il, salty, 3 * 3600) == 8 * 100 / 3 and NP.sodium_per_hour([], PRODUCTS, 0) == 0.0
    warn = ("Il fera chaud : vise 300 à 600 mg de sodium par heure. Ajoute du sel à ton plan (pastilles ou boisson "
            "salée).", "warn")
    assert NP.sodium_note(38) == warn and NP.sodium_note(299) == warn
    ok = ("Dans le repère par temps chaud : 300 à 600 mg de sodium par heure.", "ok")
    assert NP.sodium_note(300) == ok and NP.sodium_note(450) == ok and NP.sodium_note(600) == ok
    assert NP.sodium_note(601) == ("Plus que le repère par temps chaud (300 à 600 mg par heure).", "plain")


# ── the card, the race page, the forms ──────────────────────────────────────

@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


async def _route(client: AsyncClient, target_s: int | None = 5 * 3600, cps: list[dict] = CPS, name: str = "Jeju test") -> int:
    r = await client.post("/api/simulator/routes", data={
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(cps), "name": name,
        **({"target_time_s": target_s} if target_s else {}), "race_date": "2099-10-02",
        "start_hour": 21, "start_minute": 0, "sport_type": "trail", "stop_minutes": 3,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _nj(db: AsyncSession, route_id: int):
    route = (await db.execute(select(Route).where(Route.id == route_id))).scalar_one()
    await db.refresh(route)
    return route.nutrition_json


async def _product(db: AsyncSession, user: User, **kw) -> int:
    p = NutritionProduct(user_id=user.id, **{"kind": "gel", "sodium_mg": 0, **kw})
    db.add(p)
    await db.flush()
    return p.id


def _text(html: str) -> str:
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&#39;", "'").replace("&amp;", "&").strip()


def _rows(html: str) -> list[str]:
    """« À chaque ravito », a row a string, as a phone shows it (a note past one line is for wider screens)."""
    block = html.split('<ol class="pf-nu-rows">')[1].split("</ol>")[0]
    block = re.sub(r'<span class="pf-nu-left is-wide">[^<]*</span>', "", block)
    return [_text(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", block, flags=re.S)]


def _selected(html: str, select_id: str) -> str:
    sel = html.split(f'id="{select_id}"')[1].split("</select>")[0]
    return re.search(r'<option value="([^"]*)" selected>', sel).group(1)


@pytest.mark.asyncio
async def test_an_empty_plan_offers_plan_type_and_the_products_are_open(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user)
    t = (await as_user.get(f"{P}/{rid}")).text
    assert 'id="nu-starter"' in t and ">Commencer avec un plan type<" in t and "pf-nu-rows" not in t and "Ta liste" not in t
    assert 'id="nu-produits" class="pf-nu-fold" open' in t  # « Tes produits » open while the plan is empty
    for gone in ("Fragile", "Bip", "bip", "phase", "sachet", "Sac au départ", "flasque de", "Tu transpires", "Réserve", "Marque"):
        assert gone not in t, gone
    assert await _nj(db_session, rid) is None  # reading never writes
    r = await as_user.post(f"{P}/{rid}/starter", headers=HX)
    nj = await _nj(db_session, rid)
    assert nj == {"v": 2, "rhythms": [{"product_id": -1, "every_min": 20, "from_min": 0, "to_min": None}], "spare": False}
    t = r.text
    assert _selected(t, "nu-l0-p") == "-1" and _selected(t, "nu-l0-e") == "20"
    # one product all race long: « toute la course », no « de … à … » (2026-10-09, the approved mockup)
    assert '<span class="pf-nu-grp pf-nu-whole">toute la course</span>' in t and 'id="nu-l0-f"' not in t
    assert 'id="nu-l0-t"' not in t
    # 14 gels before the finish of a 5 h race, against 60–90 g/h
    assert "≈ 70 g de glucides par heure Dans le repère pour cette durée : 60 à 90 g par heure." in _text(t)
    assert 'id="nu-produits" class="pf-nu-fold">' in t and 'data-focus="nu-l0-p"' in t  # closed now the plan has a line
    # after what to take, the water to the next ravito: 2 h 05 × 0,5 L/h = 1,04 L → 1,5 L; 1 h 21 → 1 L; across
    # midnight (00:25 → 02:00, 1 h 34) → 1 L; none at the finish
    assert _rows(t) == ["21:00 Départ : 6 gels · 1,5 L", "23:05 Col assistance : 4 gels · 1 L", "00:25 Village : 4 gels · 1 L",
                        "02:00 Arrivée"]
    assert t.count('<span class="pf-nu-water">· ') == 3  # muted after the products, never on « Arrivée »
    assert "Ce que tu prends en repartant, et l'eau pour aller au ravito suivant." in _text(t)
    assert "Eau : bois à ta soif. Je compte 0,5 L par heure jusqu'au ravito suivant." in _text(t)
    assert "remplis tes flasques" not in t and "mg de sodium par heure" not in t  # no forecast saved: not hot, no sodium
    # « Plan type » takes his first gel when he has one (a caffeinated gel is not « a gel »)
    rid2 = await _route(as_user, name="Deux")
    caf = await _product(db_session, as_user._transport.app.dependency_overrides[get_current_user](), name="Gel CAF", carbs_g=25, caffeine_mg=75)
    gel = await _product(db_session, as_user._transport.app.dependency_overrides[get_current_user](), name="Maurten Gel 160", carbs_g=40)
    await as_user.post(f"{P}/{rid2}/starter", headers=HX)
    assert (await _nj(db_session, rid2))["rhythms"] == [{"product_id": gel, "every_min": 30, "from_min": 0, "to_min": None}]
    assert caf != gel


@pytest.mark.asyncio
async def test_lines_add_change_and_go_and_the_rows_follow(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    rid = await _route(as_user)
    m = await _product(db_session, test_user, name="Maurten Gel 100", carbs_g=25)
    pf = await _product(db_session, test_user, name="Precision Fuel PF 90 Gel", carbs_g=90, servings=3)
    await as_user.post(f"{P}/{rid}/starter", headers=HX)  # Maurten every 20 min
    r = await as_user.post(f"{P}/{rid}/rhythms", headers=HX)  # the next product he has, every 60 min, all race
    assert (await _nj(db_session, rid))["rhythms"][1] == {"product_id": pf, "every_min": 60, "from_min": 0, "to_min": None}
    assert 'data-focus="nu-l1-p"' in r.text and "1 prise toutes les" in _text(r.text)
    # Maurten until 2 h, PF 90 from 2 h every 20 min
    await as_user.post(f"{P}/{rid}/rhythms/0", data={"product_id": str(m), "every_min": "20", "from_min": "0", "to_min": "120"}, headers=HX)
    r = await as_user.post(f"{P}/{rid}/rhythms/1", data={"product_id": str(pf), "every_min": "20", "from_min": "120", "to_min": ""}, headers=HX)
    t = r.text
    assert (await _nj(db_session, rid))["rhythms"] == [
        {"product_id": m, "every_min": 20, "from_min": 0, "to_min": 120}, {"product_id": pf, "every_min": 20, "from_min": 120, "to_min": None}]
    # « à » offers only hours after « de », « de » only hours before « à »
    assert "<option value=\"60\"" not in t.split('id="nu-l1-t"')[1].split("</select>")[0]
    assert "<option value=\"180\"" not in t.split('id="nu-l0-f"')[1].split("</select>")[0]
    # Départ → Col (23:05): Maurten at 0:20 … 2:00; PF 90 from 2:20: 4 prises to Village (2 pouches, 2 prises left),
    # 4 more to the finish (1 pouch with them); the opened pouch's prises only where the row has room
    assert _rows(t) == ["21:00 Départ : 6 Maurten 100 · 1,5 L", "23:05 Col assistance : 2 PF 90 · 1 L",
                        "00:25 Village : 1 PF 90 · 1 L", "02:00 Arrivée"]
    assert '2 PF 90<span class="pf-nu-left is-wide"> (il t&#39;en reste 2 prises)</span>' in t  # past one line on a phone: wider screens
    # (6 × 25 + 8 × 30) / 5 h; two lines: each with its « de … à … »
    assert "≈ 78 g de glucides par heure Dans le repère pour cette durée : 60 à 90 g par heure." in _text(t)
    assert t.count('class="pf-nu-grp pf-nu-whole"') == 0 and 'id="nu-l1-f"' in t
    assert [_text(li) for li in re.findall(r"<li><b>.*?</li>", t.split('class="pf-nu-list"')[1].split("</ul>")[0])] == [
        "6 Maurten Gel 100", "3 Precision Fuel PF 90 Gel"]
    # a stale card (a line that is gone), an unknown product, « à » before « de »: no change, never a 500
    before = await _nj(db_session, rid)
    for data in ({"product_id": "999"}, {"every_min": "abc"}, {"product_id": "x", "from_min": "inf"}):
        assert (await as_user.post(f"{P}/{rid}/rhythms/1", data=data, headers=HX)).status_code == 200
    assert (await as_user.post(f"{P}/{rid}/rhythms/7", data={"every_min": "30"}, headers=HX)).status_code == 200
    assert await _nj(db_session, rid) == before
    await as_user.post(f"{P}/{rid}/rhythms/0", data={"from_min": "180", "to_min": "120"}, headers=HX)
    assert (await _nj(db_session, rid))["rhythms"][0]["to_min"] is None  # « à » before « de »: to the finish
    # a line goes: the focus lands on « Ajouter »
    r = await as_user.post(f"{P}/{rid}/rhythms/1/delete", headers=HX)
    assert len((await _nj(db_session, rid))["rhythms"]) == 1 and 'data-focus="nu-add"' in r.text


@pytest.mark.asyncio
async def test_spare_and_copy(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user)
    await as_user.post(f"{P}/{rid}/starter", headers=HX)
    r = await as_user.post(f"{P}/{rid}/spare", data={"spare": "1"}, headers=HX)
    assert (await _nj(db_session, rid))["spare"] is True and 'id="nu-spare" class="pf-nu-toggle" aria-pressed="true"' in r.text
    assert _rows(r.text)[:3] == ["21:00 Départ : 7 gels · 1,5 L", "23:05 Col assistance : 5 gels · 1 L", "00:25 Village : 5 gels · 1 L"]
    r = await as_user.post(f"{P}/{rid}/spare", data={"spare": "1"}, headers=HX)  # a stale tap sets, never flips back
    assert (await _nj(db_session, rid))["spare"] is True
    copy = r.text.split('id="nu-copytext"')[1].split(">", 1)[1].split("</textarea>")[0]
    # « Copier » carries the water on its rows too
    assert copy.splitlines() == ["Jeju test : nutrition", "", "Ta liste", "17 gels", "", "À chaque ravito",
                                 "21:00 Départ : 7 gels · 1,5 L", "23:05 Col : 5 gels · 1 L", "00:25 Village : 5 gels · 1 L",
                                 "02:00 Arrivée"]


@pytest.mark.asyncio
async def test_caffeine_is_said_against_his_cap(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    rid = await _route(as_user, target_s=10 * 3600)
    caf = await _product(db_session, test_user, name="Maurten Gel 100 CAF 100", carbs_g=25, caffeine_mg=100)
    gel = await _product(db_session, test_user, name="Maurten Gel 100", carbs_g=25)
    route = await db_session.get(Route, rid)
    route.nutrition_json = NP.plan_json([_line(gel, 20), _line(caf, 120, 120)], False)
    await db_session.flush()
    test_user.weight_kg = None
    t = _text((await as_user.get(f"{P}/{rid}")).text)
    # one at 4, 6 and 8 h (10 h is the finish): 300 mg; without his weight the cap is 400 mg
    assert "caféine : 300 mg, limite 400 mg sur 24 h · indique ton poids dans Réglages pour l'ajuster" in t
    test_user.weight_kg = 45.0  # 6 mg/kg: 270 mg
    html = (await as_user.get(f"{P}/{rid}")).text
    assert "caféine : 300 mg, au-dessus de la limite de 270 mg sur 24 h" in _text(html) and 'class="pf-nu-caf is-warn"' in html
    assert "Réglages" not in _text(html).split("caféine")[1]
    route.nutrition_json = NP.plan_json([_line(gel, 20)], False)
    await db_session.flush()
    assert "pf-nu-caf" not in (await as_user.get(f"{P}/{rid}")).text  # no caffeine in the plan: no caffeine line


@pytest.mark.asyncio
async def test_a_hot_race_counts_more_water_and_says_its_sodium(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """The race's saved forecast at 32 °C (the temperatures the passage table shows): 0,75 L an hour on every row,
    the hot water line, and under the carbs the sodium per hour with its sentence; a mild one: none of it."""
    rid = await _route(as_user)
    await as_user.post(f"{P}/{rid}/starter", headers=HX)  # the generic gel (0 mg of sodium) every 20 min
    route = await db_session.get(Route, rid)
    route.weather_json = {"hourly": {"temps": [32] * 48, "humidity": [50] * 48}}
    await db_session.flush()
    t = (await as_user.get(f"{P}/{rid}")).text
    # 2 h 05 × 0,75 L/h = 1,57 L → 2 L; 1 h 20 min 38 s → 1,01 L → 1,5 L; 1 h 34 → 1,18 L → 1,5 L
    assert _rows(t) == ["21:00 Départ : 6 gels · 2 L", "23:05 Col assistance : 4 gels · 1,5 L",
                        "00:25 Village : 4 gels · 1,5 L", "02:00 Arrivée"]
    assert "Eau : bois à ta soif. Il fera chaud : je compte 0,75 L par heure jusqu'au ravito suivant." in _text(t)
    totals = t.split('<p class="pf-nu-sum">')[1].split("</p>")[0]
    assert _text(totals) == ("≈ 70 g de glucides par heure Dans le repère pour cette durée : 60 à 90 g par heure. "
                             "≈ 0 mg de sodium par heure Il fera chaud : vise 300 à 600 mg de sodium par heure. "
                             "Ajoute du sel à ton plan (pastilles ou boisson salée).")
    assert '<span class="pf-nu-na">≈ 0 mg de sodium par heure</span><span class="pf-nu-zone is-warn">Il fera chaud' in t
    copy = t.split('id="nu-copytext"')[1].split(">", 1)[1].split("</textarea>")[0]
    assert "21:00 Départ : 6 gels · 2 L\n23:05 Col : 4 gels · 1,5 L\n00:25 Village : 4 gels · 1,5 L\n02:00 Arrivée" in copy
    # salt in his plan: a PH 1000 (500 mg) every hour, 4 before the finish: 400 mg an hour, in the range
    salt = await _product(db_session, test_user, name="Precision Hydration PH 1000 (pastille, 500 ml)", kind="salt",
                          carbs_g=0, sodium_mg=500)
    route.nutrition_json = NP.plan_json([_line(-1, 20), _line(salt, 60)], False)
    await db_session.flush()
    t = (await as_user.get(f"{P}/{rid}")).text
    assert ('≈ 400 mg de sodium par heure</span><span class="pf-nu-zone is-ok">Dans le repère par temps chaud : 300 à '
            '600 mg de sodium par heure.</span>') in t
    route.nutrition_json = NP.plan_json([_line(-1, 20), _line(salt, 30)], False)  # 9 of them: 900 mg an hour
    await db_session.flush()
    t = (await as_user.get(f"{P}/{rid}")).text
    assert ('≈ 900 mg de sodium par heure</span><span class="pf-nu-zone is-plain">Plus que le repère par temps chaud '
            '(300 à 600 mg par heure).</span>') in t
    # without JS: the race page renders the same card
    page = (await as_user.get(f"/simulator/routes/{rid}?vue=nutrition")).text
    assert "≈ 900 mg de sodium par heure" in page and "je compte 0,75 L par heure" in _text(page)
    # a mild forecast: 0,5 L an hour, no sodium line at all
    route.weather_json = {"hourly": {"temps": [14] * 48, "humidity": [50] * 48}}
    await db_session.flush()
    t = (await as_user.get(f"{P}/{rid}")).text
    assert "sodium par heure" not in t and "pf-nu-na" not in t and "Il fera chaud" not in t
    assert "Eau : bois à ta soif. Je compte 0,5 L par heure jusqu'au ravito suivant." in _text(t)
    assert _rows(t)[0].endswith(" · 1,5 L")


@pytest.mark.asyncio
async def test_no_simulation_keeps_the_plan_editable(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user, target_s=None)
    route = await db_session.get(Route, rid)
    route.course_json = {**route.course_json, "segments": []}
    route.nutrition_json = NP.plan_json([_line(-1, 30)], False)
    route.weather_json = {"hourly": {"temps": [32] * 48, "humidity": [50] * 48}}  # a hot forecast, but no times
    await db_session.flush()
    r = await as_user.get(f"{P}/{rid}")
    assert r.status_code == 200 and "Lance une simulation pour avoir tes heures de passage." in r.text
    assert 'id="nu-l0-e"' in r.text and "pf-nu-rows" not in r.text and "g de glucides par heure" not in r.text
    # no predicted times: no litres, no water rate, no sodium
    assert "pf-nu-water" not in r.text and "compte 0," not in r.text and "sodium par heure" not in r.text
    assert (await as_user.post(f"{P}/{rid}/rhythms/0", data={"every_min": "45"}, headers=HX)).status_code == 200
    assert (await _nj(db_session, rid))["rhythms"][0]["every_min"] == 45


@pytest.mark.asyncio
async def test_a_race_without_aid_stations_takes_everything_at_the_start(as_user: AsyncClient):
    rid = await _route(as_user, cps=[])
    await as_user.post(f"{P}/{rid}/starter", headers=HX)
    assert _rows((await as_user.get(f"{P}/{rid}")).text) == ["21:00 Départ : 14 gels · 2,5 L", "02:00 Arrivée"]  # 5 h × 0,5 L/h


@pytest.mark.asyncio
async def test_every_form_works_without_js(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """No HX-Request: each post answers 303 to the race page, which renders the card open at the block."""
    rid = await _route(as_user)
    r = await as_user.post(f"{P}/{rid}/starter")
    assert r.status_code == 303 and r.headers["location"] == f"/simulator/routes/{rid}?vue=nutrition#nu-plan"
    page = (await as_user.get(r.headers["location"])).text
    tool = page.split('id="rpanel-tool"')[1][:40]
    assert "hidden" not in tool and 'id="rpanel-plan" class="pf-col hidden"' in page
    assert 'id="tool-title" class="pf-h1 mb-6" tabindex="-1">Nutrition<' in page and 'id="nutrition-card"' in page
    assert 'action="/partials/simulator/nutrition/%d/rhythms/0" hx-post' % rid in page and "<noscript>" in page
    # the water is the server's too: the litres on the rows, the lead and water lines, without a line of JS
    assert _rows(page)[0] == "21:00 Départ : 6 gels · 1,5 L" and "et l'eau pour aller au ravito suivant." in _text(page)
    assert "Eau : bois à ta soif. Je compte 0,5 L par heure jusqu'au ravito suivant." in _text(page)
    r = await as_user.post(f"{P}/{rid}/rhythms/0", data={"product_id": "-1", "every_min": "30", "from_min": "0", "to_min": ""})
    assert r.status_code == 303 and (await _nj(db_session, rid))["rhythms"][0]["every_min"] == 30
    assert _selected((await as_user.get(r.headers["location"])).text, "nu-l0-e") == "30"
    r = await as_user.post(f"{P}/{rid}/rhythms")
    assert r.status_code == 303 and len((await _nj(db_session, rid))["rhythms"]) == 2
    r = await as_user.post(f"{P}/{rid}/rhythms/1/delete")
    assert r.status_code == 303 and len((await _nj(db_session, rid))["rhythms"]) == 1
    r = await as_user.post(f"{P}/{rid}/spare", data={"spare": "1"})
    assert r.headers["location"].endswith("#nu-ravitos") and (await _nj(db_session, rid))["spare"] is True
    r = await as_user.post(f"{P}/{rid}/products", data={"name": "Gel maison", "carbs_g": "30", "sodium_mg": "", "caffeine_mg": "", "servings": "1"})
    assert r.status_code == 303 and r.headers["location"] == f"/simulator/routes/{rid}?vue=nutrition&open=produits#nu-produits"
    page = (await as_user.get(r.headers["location"])).text
    assert 'id="nu-produits" class="pf-nu-fold" open' in page and "Gel maison" in page
    r = await as_user.post(f"{P}/{rid}/products", data={"key": "pf-90-gel"})
    assert r.status_code == 303
    names = [p.name for p in (await db_session.execute(select(NutritionProduct).where(NutritionProduct.user_id == test_user.id))).scalars()]
    assert names == ["Gel maison", "Precision Fuel PF 90 Gel"]
    # the race page itself: « Nutrition » is a link to that server-rendered card
    page = (await as_user.get(f"/simulator/routes/{rid}")).text
    assert f'href="/simulator/routes/{rid}?vue=nutrition#nutrition"' in page and "Nutrition</span>" in page
    assert 'id="rpanel-tool" class="hidden"' in page  # htmx loads the card when he opens it


@pytest.mark.asyncio
async def test_products_are_his_for_every_race_and_a_used_one_asks_before_it_goes(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    rid, other = await _route(as_user), await _route(as_user, name="Autre")
    r = await as_user.post(f"{P}/{rid}/products", data={"key": "maurten-gel-100"}, headers=HX)
    pid = (await db_session.execute(select(NutritionProduct.id).where(NutritionProduct.user_id == test_user.id))).scalar_one()
    assert 'id="nu-produits" class="pf-nu-fold" open' in r.text and f'data-focus="nu-prod-{pid}"' in r.text
    await as_user.post(f"{P}/{rid}/products", data={"key": "maurten-gel-100"}, headers=HX)  # once in the pantry
    assert (await db_session.execute(select(NutritionProduct).where(NutritionProduct.user_id == test_user.id))).scalars().all().__len__() == 1
    # edit: prises, carbs; the kind it has stays
    r = await as_user.post(f"{P}/{rid}/products/{pid}", data={"name": "Maurten Gel 100", "carbs_g": "25", "sodium_mg": "20", "caffeine_mg": "", "servings": "1"}, headers=HX)
    assert r.status_code == 200 and "25 g de glucides · 20 mg de sodium" in _text(r.text)
    # « Un produit à toi » without a name keeps what was typed
    r = await as_user.post(f"{P}/{rid}/products", data={"name": "", "carbs_g": "40", "sodium_mg": "200"}, headers=HX)
    assert "Donne-lui un nom." in r.text and 'value="40"' in r.text and 'class="pf-nu-own" open' in r.text
    # used by this race and another one: deleting asks first
    await as_user.post(f"{P}/{rid}/starter", headers=HX)
    await as_user.post(f"{P}/{other}/starter", headers=HX)
    assert (await _nj(db_session, other))["rhythms"][0]["product_id"] == pid
    r = await as_user.post(f"{P}/{rid}/products/{pid}/delete", headers=HX)
    assert "Supprimer Maurten Gel 100 le retire aussi de ton plan et de celui d'une autre course." in _text(r.text)
    assert f'data-focus="nu-yes-{pid}"' in r.text and await db_session.get(NutritionProduct, pid) is not None
    page = (await as_user.get(f"/simulator/routes/{rid}?vue=nutrition&open=produits&confirm={pid}")).text  # the same question without JS
    assert "le retire aussi" in page
    r = await as_user.post(f"{P}/{rid}/products/{pid}/delete", data={"confirm": "1"}, headers=HX)
    assert r.status_code == 200 and "Maurten Gel 100" not in _text(r.text).split("Du catalogue")[0]
    assert (await _nj(db_session, rid))["rhythms"] == [] and (await _nj(db_session, other))["rhythms"] == []
    assert ">Commencer avec un plan type<" in r.text
    # a product no plan uses goes at once
    g = await _product(db_session, test_user, name="Gel maison", carbs_g=30)
    await as_user.post(f"{P}/{rid}/products/{g}/delete", headers=HX)
    db_session.expire_all()
    assert await db_session.get(NutritionProduct, g) is None


@pytest.mark.asyncio
async def test_an_old_plan_is_read_in_memory_and_stored_as_v2_on_the_first_change(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    rid = await _route(as_user)
    gel = await _product(db_session, test_user, name="Baouw Gel", carbs_g=30)
    pf = await _product(db_session, test_user, name="Precision Fuel PF 90 Gel", carbs_g=90, servings=3)
    route = await db_session.get(Route, rid)
    route.nutrition_json = {"v": 3, "picks": [gel, pf], "level": "normal", "rows": {}, "refills": [12.0], "carry_ml": 1500,
                            "phases": [{"km": 0.0, "mix": {str(gel): 1}}, {"km": 20.0, "mix": {str(pf): 1}}]}
    await db_session.flush()
    before = await _nj(db_session, rid)
    t = (await as_user.get(f"{P}/{rid}")).text
    assert await _nj(db_session, rid) == before  # reading never writes
    assert "ne peut pas s'afficher" not in t and _selected(t, "nu-l0-p") == str(gel) and _selected(t, "nu-l1-p") == str(pf)
    village = _rows(t)[2].split()[0]  # the switch at Village's time, the minute kept as its own option
    h, mn = map(int, village.split(":"))
    switch = ((h - 21) % 24) * 60 + mn
    assert _selected(t, "nu-l0-t") == str(switch) and _selected(t, "nu-l1-f") == str(switch)
    await as_user.post(f"{P}/{rid}/spare", data={"spare": "1"}, headers=HX)
    nj = await _nj(db_session, rid)
    assert nj["v"] == 2 and nj["spare"] is True and nj["refills"] == [12.0] and len(nj["rhythms"]) == 2 and "phases" not in nj
    # what does not map starts over, said once, and the read writes nothing
    other = await _route(as_user, name="Ancien")
    route = await db_session.get(Route, other)
    route.nutrition_json = {"v": 3, "picks": [gel, pf, -3], "phases": [{"km": 0.0, "mix": {str(gel): 1, str(pf): 2}}], "rows": {"20.0": {str(gel): 2}}}
    await db_session.flush()
    t = (await as_user.get(f"{P}/{other}")).text
    assert "Ton ancien plan ne peut pas s'afficher ici : refais-le, ça prend une minute." in t and ">Commencer avec un plan type<" in t
    assert (await _nj(db_session, other))["phases"]
    await as_user.post(f"{P}/{other}/starter", headers=HX)
    t = (await as_user.get(f"{P}/{other}")).text
    assert "ne peut pas s'afficher" not in t and (await _nj(db_session, other))["v"] == 2


@pytest.mark.asyncio
async def test_the_other_surfaces_follow_the_card(as_user: AsyncClient, db_session: AsyncSession):
    """The passage rows point to their ravito's row, the bib band prints the rows, the watch says what to take."""
    rid = await _route(as_user)
    table = {"checkpoints_json": json.dumps(CPS), "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0, "route_id": rid, "stop_minutes": 3}
    t = (await as_user.post("/partials/simulator/passage-times", data=table)).text
    assert "Ta nutrition jusqu'à" not in t  # no plan yet: nothing to point at
    await as_user.post(f"{P}/{rid}/starter", headers=HX)
    t = _text((await as_user.post("/partials/simulator/passage-times", data=table)).text)
    assert "Ta nutrition jusqu'à Village" in t and "Ta nutrition jusqu'à l'arrivée" in t and "Dès ici" not in t
    page = (await as_user.get(f"/simulator/routes/{rid}/print")).text
    band = _text(page.split(">Nutrition</p>")[1])
    assert band.startswith("Gel · 1 toutes les 20 min 21:00 Départ 6 gels 23:05 Col 4 gels 00:25 Village 4 gels 02:00 Arrivée")
    gpx = (await as_user.get(f"/api/simulator/routes/{rid}/pace-export?format=gpx")).text
    assert "Col · Prends 4 gels" not in gpx and "Prends 4 gels" in gpx and gpx.count("Prends ") == 2
    # a card from before v2 posting its old taps: nothing changes, the new card comes back
    before = await _nj(db_session, rid)
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "level", "v": "solide"}, headers=HX)
    assert r.status_code == 200 and 'id="nutrition-card"' in r.text and await _nj(db_session, rid) == before
    assert (await as_user.post(f"{P}/{rid}/catalog/pf-90-gel", headers=HX)).status_code == 200  # the old « + Marque » path


@pytest.mark.asyncio
async def test_the_card_morphs_in_place_and_says_where_the_focus_goes(as_user: AsyncClient):
    rid = await _route(as_user)
    t = (await as_user.get(f"{P}/{rid}")).text
    script = t.split("<script>")[1]
    assert "Idiomorph.morph(t, html, { morphStyle: 'outerHTML' })" in script and "data-focus" in script and "htmx:beforeSwap" in script
    assert 'hx-target="this" hx-swap="outerHTML" hx-sync="this:queue all"' in t
    page = (await as_user.get(f"/simulator/routes/{rid}")).text
    assert "idiomorph" in page and "?v=20" in page and "TOOL_TITLES = { nutrition: 'Nutrition'" in page


@pytest.mark.asyncio
async def test_another_users_race_and_products_are_out_of_reach(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user)
    stranger = User(email="other@example.com", email_verified=True)
    db_session.add(stranger)
    await db_session.flush()
    theirs = await _product(db_session, stranger, name="Gel à lui", carbs_g=20)
    assert (await as_user.post(f"{P}/{rid}/products/{theirs}/delete", data={"confirm": "1"}, headers=HX)).status_code == 200
    assert await db_session.get(NutritionProduct, theirs) is not None
    r = await as_user.post(f"{P}/{rid}/rhythms/0", data={"product_id": str(theirs)}, headers=HX)
    assert r.status_code == 200 and (await _nj(db_session, rid)) is None
    assert (await as_user.post(f"{P}/999999/starter", headers=HX)).status_code == 404


@pytest.mark.asyncio
async def test_back_from_nutrition_redoes_the_rows_and_its_links_land_on_the_card(as_user: AsyncClient, cycling_on):
    html = (await as_user.get(f"/simulator/routes/{await _route(as_user)}")).text
    script = html.split("function switchRouteTab")[1].split("</script>")[0]
    # any successful POST to the card marks the rows stale (their « Ta nutrition jusqu'à » links); back to the plan redoes them once
    assert "ravitoDirty = true" in script and "/partials\\/simulator\\/nutrition\\//" in script
    plan_branch = script.split("} else {")[1].split("// a tool reloads")[0]
    assert "ravitoDirty" in plan_branch and "recalc()" in plan_branch
    # a passage row's link: the card opens on that point's row (s-<km>), after the reload when there is one
    assert "'&open=' + encodeURIComponent(anchor)" in script and "htmx:afterSettle" in script and "var sid = 's-' +" in script
    assert "anchor === 'bags' ? 'bags' : tab" in script and "#nutrition-wrap #nu-ravitos" in script  # old #bags links
    assert html.count('hx-on::validation:halted="this.reportValidity()"') == 2
    from tests.test_simulator_routes import _create_bike_route

    bike_id = await _create_bike_route(as_user)
    bike = (await as_user.get(f"/simulator/routes/{bike_id}")).text
    assert "idiomorph@0.3.0/dist/idiomorph.min.js" in bike and "?v=20" in bike and 'id="rpanel-nutrition" class="hidden"' in bike
    bike = (await as_user.get(f"/simulator/routes/{bike_id}?vue=nutrition")).text  # its forms land there without JS
    assert 'id="rpanel-plan" class="hidden"' in bike and 'id="nutrition-card"' in bike
    r = await as_user.get(f"{P}/{bike_id}")
    assert r.status_code == 200 and ">Commencer avec un plan type<" in r.text and "Drop bag ici" not in r.text


@pytest.mark.asyncio
async def test_odd_addresses_still_give_the_card(as_user: AsyncClient):
    rid = await _route(as_user)
    for q in ("?confirm=abc", "?open=" + "x" * 80, "?confirm=99999&open=produits"):
        r = await as_user.get(f"{P}/{rid}{q}")
        assert r.status_code == 200 and 'id="nutrition-card"' in r.text, q
        page = await as_user.get(f"/simulator/routes/{rid}?vue=nutrition&{q[1:]}")
        assert page.status_code == 200 and 'id="nutrition-card"' in page.text, q
