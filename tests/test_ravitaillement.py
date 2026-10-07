"""« Ravitaillement » : tu prépares par tronçon, tu manges au bip. One row per
stretch between food ravitos (a sachet of whole units), one watch beep, products
by phase, the bags and the list as the sums of the rows; Pilotage folded into
the passage rows. A complete plan with zero input, older plans read in memory,
reading never writes, the same numbers on every surface."""

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
from app.services import nutrition_plan as NP
from app.services.pacing_guide import build_pacing_guide, leg_instructions, resolve_hr_caps
from tests.test_race_plan_services import CPS, _course, _sections

P = "/partials/simulator/nutrition"


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


def _text(html: str) -> str:
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&#39;", "'").replace("&amp;", "&")


def _pressed(html: str, title: str) -> bool:
    return f'aria-pressed="true" title="{title}"' in html


def _phases(html: str) -> list[str]:
    """The header's phase chips, as read."""
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m)).strip() for m in re.findall(r'class="pf-rv-phase"[^>]*>(.*?)</button>', html, flags=re.S)]


def _rows(html: str) -> dict:
    """{stretch key: the row's HTML}."""
    out = {}
    for m in re.finditer(r'<details class="pf-rv-row[^"]*" name="rv-s" id="s-([\d.]+)"', html):
        end = html.find('<details class="pf-rv-row', m.end())
        out[float(m.group(1))] = html[m.start(): end if end > 0 else len(html)]
    return out


# ── older plans: read per stretch, never rewritten by a read ───────────────

LEGACY_PRODUCTS = {
    # the servings migration set 3 on the PF 90 rows already in a pantry
    11: {"id": 11, "name": "Precision Fuel PF 90 Gel", "kind": "gel", "carbs_g": 90, "sodium_mg": 0, "kcal": 360, "caffeine_mg": None, "volume_ml": None, "servings": 3},
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


async def _seed_legacy(db: AsyncSession, test_user: User, rid: int, plan: dict = LEGACY_PLAN) -> dict:
    ids = {}
    for p in LEGACY_PRODUCTS.values():
        prod = NutritionProduct(user_id=test_user.id, **{k: v for k, v in p.items() if k != "id"})
        db.add(prod)
        await db.flush()
        ids[p["id"]] = prod.id
    route = await db.get(Route, rid)
    route.nutrition_json = {**plan, "items": [{"product_id": ids[it["product_id"]], "per_hour": it["per_hour"]} for it in plan["items"]]}
    await db.flush()
    return ids


@pytest.mark.asyncio
async def test_the_seeded_transjeju_plan_reads_per_stretch_and_the_read_writes_nothing(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    rid = await _route(as_user, target_s=10 * 3600)
    ids = await _seed_legacy(db_session, test_user, rid)
    before = await _nj(db_session, rid)
    t = (await as_user.get(f"{P}/{rid}")).text
    assert '"v":"normal"}\' aria-pressed="true"' in t  # 75 g/h = Normal
    assert _phases(t) == ["PF 90 · Baouw · Maurten 160"]  # one phase: his three gels, in his order
    rows = _rows(t)
    assert len(rows) == 3 and "Bip toutes les" in t  # Départ → Col → Village → Arrivée, one beep
    assert "prises PF 90" in t and "Maurten 100 CAF" in t and "PH 1500" in t  # PF 90 in prises, the caffeinated gel at its times, salt per flask
    assert "0,5 gel" not in t and not re.search(r"\d+,5 (gels?|Maurten|Baouw|PF)", t)
    assert "Ton plan est maintenant par tronçon" in t
    assert await _nj(db_session, rid) == before  # reading never writes (in-memory upgrade)
    # the first tap stores v3, from what was shown; the note goes with it
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "level", "v": "normal"})
    assert "Ton plan est maintenant par tronçon" not in r.text
    nj = await _nj(db_session, rid)
    assert nj["v"] == 3 and nj["picks"] == [ids[11], ids[12], ids[13], ids[14], ids[15]]
    # the old rates count pouches, the stored shares count prises: PF 90 keeps its 45 g/h (0,5 pouch = 1,5 prises an hour)
    assert nj["phases"][0]["mix"] == {str(ids[11]): 1.5, str(ids[12]): 0.5, str(ids[13]): 0.5}
    assert nj["items"] and nj["targets"]["carbs_g_per_h"] == 75  # the echo an older reader understands


@pytest.mark.asyncio
async def test_a_v2_plan_reads_per_stretch_without_a_write(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user, target_s=8 * 3600)
    route = await db_session.get(Route, rid)
    route.nutrition_json = {"v": 2, "picks": [-1, -3], "manual": {"-1": 3.5}, "level": "solide", "carry_ml": 1500, "custom": {}, "refills": []}
    await db_session.flush()
    before = await _nj(db_session, rid)
    t = (await as_user.get(f"{P}/{rid}")).text
    assert '"v":"solide"}\' aria-pressed="true"' in t and _phases(t) == ["Gel"] and "1,5 L portés" in t
    assert await _nj(db_session, rid) == before


# ── zero input: a complete plan ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_race_without_a_plan_shows_a_complete_default_and_the_read_writes_nothing(as_user: AsyncClient, db_session: AsyncSession):
    short = await _route(as_user, target_s=5 * 3600)
    t = (await as_user.get(f"{P}/{short}")).text
    text = _text(t)
    assert _phases(t) == ["Gel"] and "Bip toutes les 20 min" in text  # 25 g gels at 75 g/h
    assert "Le rythme que tu as tenu à l'entraînement." in text
    assert "Alerte nutrition de ta montre, auto-pause coupée. Raté un bip ? Ne double pas, reprends au suivant." in text
    assert "Tu prépares par tronçon, tu manges au bip ; pas de demi-gel, le reste passe au tronçon suivant." in text
    assert "Sac au départ" in t and "Drop bag · Col" in t and "en réserve" in t  # two load points: the bags say what they hold
    assert "pastilles de sel" in t and "caféiné" not in _text(t.split('id="rv-brands"')[0])  # under 8 h no caffeine
    assert "Ce sont des produits génériques" in text
    assert 'class="pf-rv-strip" aria-hidden="true"' in t
    assert "pf-rv-warn is-warn" not in t and 'class="pf-rv-st"' not in t  # nothing out of band
    assert await _nj(db_session, short) is None
    # the passage rows point at the sachets, never list them again
    r = await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(CPS), "target_time_s": 5 * 3600, "start_hour": 21, "start_minute": 0, "route_id": short, "stop_minutes": 3,
    })
    assert "Ton sachet jusqu'à Village" in r.text.replace("&#39;", "'") and "À prendre en route" not in r.text and "reprends" not in r.text
    # from 8 h, a caffeinated gel joins the default, placed at its times
    long_ = await _route(as_user, target_s=9 * 3600, name="Long")
    t = (await as_user.get(f"{P}/{long_}")).text
    assert "gel caféiné" in _text(t.split('id="rv-brands"')[0])


@pytest.mark.asyncio
async def test_a_new_race_starts_from_the_newest_plan_of_another_race(as_user: AsyncClient, db_session: AsyncSession):
    first = await _route(as_user, name="CCC 2025")
    r = await as_user.post(f"{P}/{first}/catalog/pf-90-gel")
    assert r.status_code == 200
    r = await as_user.post(f"{P}/{first}/plan", data={"op": "level", "v": "solide"})
    second = await _route(as_user, name="UTMB")
    t = (await as_user.get(f"{P}/{second}")).text
    assert _phases(t) == ["PF 90"] and "Repris de « CCC 2025 »" in t
    assert '"v":"solide"}\' aria-pressed="true"' in t
    assert await _nj(db_session, second) is None


# ── the taps ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_taps_write_a_v3_plan_and_change_the_rows_the_bags_and_the_list(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user, target_s=6 * 3600)
    t0 = (await as_user.get(f"{P}/{rid}")).text
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "level", "v": "solide"})
    nj = await _nj(db_session, rid)
    assert nj["v"] == 3 and nj["level"] == "solide" and nj["picks"] == [-1, -3] and nj["items"] and nj["targets"]["carbs_g_per_h"] == 90
    buy = lambda html, name: int(re.search(rf'<b title="[^"]*">{name}</b>.*?<span class="pf-rv-buy"><b>(\d+)</b>', html, flags=re.S).group(1))  # noqa: E731
    assert buy(r.text, "Gel") > buy(t0, "Gel")
    keys = list(_rows(r.text))
    k = keys[1]
    # a stepper freezes the whole row (« à la main »), the next rows catch up
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "n", "km": str(k), "pid": "-1", "d": "-1", "open": f"s:{k}"})
    nj = await _nj(db_session, rid)
    assert str(k) in nj["rows"] and set(nj["rows"][str(k)]) >= {"-1", "-3"}
    row = _rows(r.text)[k]
    assert "à la main" in row and "Remettre en auto" in row and " open>" in row.split("<summary>")[0] + ">"
    assert 'id="s-' + str(k) + '-m-1"' in row  # stable ids: the morph keeps the focused stepper
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "auto", "km": str(k)})
    assert str(k) not in (await _nj(db_session, rid))["rows"] and "à la main" not in _rows(r.text)[k]
    # « + Produit »: this row only
    await as_user.post(f"{P}/{rid}/plan", data={"op": "pick", "v": "-5"})
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "add", "km": str(k), "pid": "-2"})
    nj = await _nj(db_session, rid)
    assert nj["rows"][str(k)]["-2"] == 1 and all("-2" not in v for kk, v in nj["rows"].items() if kk != str(k))
    # « J'en ai » lowers what to buy; reserve in minutes
    gel_need = buy(r.text, "Gel")
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "have", "pid": "-1", "d": "3", "open": "list"})
    assert buy(r.text, "Gel") == gel_need - 3 and (await _nj(db_session, rid))["have"] == {"-1": 3}
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "reserve", "v": "0", "open": "settings"})
    assert "en réserve" not in r.text and (await _nj(db_session, rid))["reserve_min"] == 0 and "réserve aucune" in r.text
    # sweat and flasks
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "sweat", "v": "beaucoup", "open": "settings"})
    assert "beaucoup de transpiration" in r.text and 'id="rv-settings" class="pf-rv-disc" open' in r.text
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "carry", "v": "1500"})
    assert (await _nj(db_session, rid))["carry_ml"] == 1500 and "1,5 L portés" in r.text
    # a stale card's per-hour stepper changes nothing
    before = await _nj(db_session, rid)
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "step", "pid": "-1", "step": "0.5"})
    assert r.status_code == 200 and await _nj(db_session, rid) == before
    # a malformed tap is ignored
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "n", "km": "x", "pid": "-1", "d": "1"})
    assert r.status_code == 200 and await _nj(db_session, rid) == before
    # « Revenir au calcul auto »
    await as_user.post(f"{P}/{rid}/plan", data={"op": "reset"})
    assert (await _nj(db_session, rid))["rows"] == {}


@pytest.mark.asyncio
async def test_a_product_switch_from_a_ravito_with_confirm_and_undo(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    rid = await _route(as_user, target_s=8 * 3600)
    await as_user.post(f"{P}/{rid}/catalog/maurten-gel-160")
    keys = list(_rows((await as_user.get(f"{P}/{rid}")).text))
    col, village = keys[1], keys[2]
    # the row's action opens the « à partir d'ici » editor (a GET: nothing written)
    before = await _nj(db_session, rid)
    t = (await as_user.get(f"{P}/{rid}?open=ph:{col}")).text
    assert "À partir de Col, jusqu'à l'arrivée" in _text(t) and await _nj(db_session, rid) == before
    # PF 90 from Col on, from the editor's « + Marque »
    r = await as_user.post(f"{P}/{rid}/catalog/pf-90-gel", data={"km": str(col)})
    assert "À partir de Col" in _text(r.text)  # the editor stays open
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "mix", "km": str(col), "pid": str(_pid(r.text, "Maurten 160")), "open": f"ph:{col}"})
    assert _phases(r.text) == ["Maurten 160", "PF 90 dès Col"]
    rows = _rows(r.text)
    assert "Maurten 160" in rows[keys[0]] and "PF 90" not in rows[keys[0]].split("<summary>")[1].split("</summary>")[0]
    assert "prises PF 90" in rows[col] and "Maurten 160" not in rows[col].split("</summary>")[0]
    # a hand-set row holding PF 90, then PF 90 leaves from Col: the chip asks first, an « Annuler » follows
    pf = _pid(r.text, "PF 90")
    await as_user.post(f"{P}/{rid}/plan", data={"op": "n", "km": str(village), "pid": str(pf), "d": "1"})
    t = (await as_user.get(f"{P}/{rid}?open=ph:{col}")).text
    chip = re.search(rf'id="ed-{col}-{pf}"[^>]*>', t).group(0)
    assert "hx-confirm=" in chip and "réglé à la main repasse en auto" in chip
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "mix", "km": str(col), "pid": str(pf)})
    nj = await _nj(db_session, rid)
    assert str(village) not in nj["rows"] and nj["undo"]["cleared"] == 1 and "1 tronçon remis en auto." in _text(r.text) and "Annuler" in r.text
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "undo"})
    nj = await _nj(db_session, rid)
    assert str(village) in nj["rows"] and "undo" not in nj and _phases(r.text) == ["Maurten 160", "PF 90 dès Col"]
    # « Supprimer ce changement »
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "unswitch", "km": str(col)})
    assert len(_phases(r.text)) == 1
    # the switch reaches the passage row and the watch
    t = (await as_user.post("/partials/simulator/passage-times", data={"checkpoints_json": json.dumps(CPS), "target_time_s": 8 * 3600, "start_hour": 21,
                                                                       "start_minute": 0, "route_id": rid, "stop_minutes": 3})).text
    assert "Dès ici" not in t
    await as_user.post(f"{P}/{rid}/plan", data={"op": "undo"})
    t = (await as_user.post("/partials/simulator/passage-times", data={"checkpoints_json": json.dumps(CPS), "target_time_s": 8 * 3600, "start_hour": 21,
                                                                       "start_minute": 0, "route_id": rid, "stop_minutes": 3})).text
    assert t.count("Dès ici : PF 90") == 1
    tcx = (await as_user.get(f"/api/simulator/routes/{rid}/pace-export?format=tcx")).text
    gpx = (await as_user.get(f"/api/simulator/routes/{rid}/pace-export?format=gpx")).text
    assert re.search(r"<Notes>km 10\.0 · \d\d:\d\d · Col · Dès ici : PF 90", tcx) and "Dès ici : PF 90</desc>" in gpx


def _pid(html: str, label: str) -> int:
    m = re.search(rf'"pid":"(-?\d+)"[^>]*>(?:(?!</button>).)*?<span class="pf-chip-lbl">{re.escape(label)}</span>', html, flags=re.S)
    return int(m.group(1))


@pytest.mark.asyncio
async def test_ravito_food_is_off_by_default_and_counts_where_eaten(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user, target_s=8 * 3600)
    t = (await as_user.get(f"{P}/{rid}")).text
    rows = _rows(t)
    keys = list(rows)
    assert "Au ravito" not in rows[keys[0]] and "Au ravito de Col" in _text(rows[keys[1]]) and "≈ Coca" not in t
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "aid", "km": str(keys[1]), "aid": "-10", "d": "2"})
    assert "≈ Coca 2" in r.text and (await _nj(db_session, rid))["aid"] == {str(keys[1]): {"-10": 2}}
    assert "Coca" not in r.text.split('id="rv-shop"')[1].split('id="rv-brands"')[0]  # never on the list


@pytest.mark.asyncio
async def test_catalogue_pick_replaces_the_generic_of_the_same_role(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user)
    r = await as_user.post(f"{P}/{rid}/catalog/pf-90-gel")
    t = r.text
    assert _phases(t) == ["PF 90"] and 'open' not in t.split('id="rv-brands"')[1][:40]  # the panel closes
    nj = await _nj(db_session, rid)
    assert -1 not in nj["picks"] and -3 in nj["picks"]
    pid = nj["picks"][0]
    # off the list: off every stretch; the quick pick « Gel » is offered again
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "toggle", "v": str(pid)})
    assert pid not in (await _nj(db_session, rid))["picks"] and 'title="Gel" hx-post' in r.text
    assert (await as_user.post("/api/nutrition/products", data={"name": "x"})).status_code in (404, 405)


@pytest.mark.asyncio
async def test_products_carry_their_prises(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    rid = await _route(as_user, target_s=8 * 3600)
    r = await as_user.post(f"{P}/{rid}/products", data={"name": "Gros gel", "kind": "gel", "carbs_g": "60", "sodium_mg": "0", "servings": "2"})
    assert r.status_code == 200 and "prises Gros gel" in r.text and "60 g · 0 mg sodium · 2 prises" in r.text
    prod = (await db_session.execute(select(NutritionProduct).where(NutritionProduct.user_id == test_user.id))).scalars().one()
    assert prod.servings == 2
    r = await as_user.post(f"{P}/{rid}/catalog/pf-90-gel")
    pf = (await db_session.execute(select(NutritionProduct).where(NutritionProduct.name == "Precision Fuel PF 90 Gel"))).scalar_one()
    assert pf.servings == 3
    # a row with a pouch to open says what to pack and what stays in it
    assert re.search(r"prises · prends \d+ poches?(, il en restera \d)?", _text(r.text)) or "finis la poche entamée" in r.text


# ── the engine rules, through the resolver ─────────────────────────────────

def _plan_for(nj, pantry, target_h, weight=68, stop_min=0, cps=CPS):
    _, secs = _sections(target=target_h * 3600, start_hour=21, stop_min=stop_min)
    inp = N.resolve_inputs(nj, pantry, target_h * 3600, None, weight, moving_s=NP.moving_cum(secs[-1]), start_offset_s=21 * 3600)
    inp["mean_temp"] = None
    st = NP.food_stretches(secs, cps, 21 * 3600)
    return inp, NP.build_plan(st, inp, sections=secs, start_offset_s=21 * 3600, refill_kms={6.0, 10.0, 20.0})


def _catalog_pantry(key):
    c = N.CATALOG_BY_KEY[key]
    return {100: {"id": 100, **{k: c.get(k) for k in ("name", "kind", "carbs_g", "sodium_mg", "kcal", "caffeine_mg", "volume_ml", "servings")}}}


def test_the_automatic_plan_is_never_out_of_band_nor_too_much_for_the_stomach():
    keys = [None] + [c["key"] for c in N.PRODUCT_CATALOG if N.role(c) in ("gel", "drink")]
    for target_h in (9, 19, 30):
        for level in ("fragile", "normal", "solide"):
            for key in keys:
                pantry = _catalog_pantry(key) if key else {}
                picks = N.pick_into([-1, -3, -4], 100, N.with_generics(pantry)) if key else [-1, -3, -4]
                inp, plan = _plan_for({"v": 3, "picks": picks, "level": level}, pantry, target_h)
                for r in plan["stretches"]:
                    assert r["status"] == "ok" and not r["stomach"], (target_h, level, key, r["to_name"], r["balance_g"], r["carbs_g"], r["need_g"])
                assert plan["beep"] and plan["beep"]["interval"] in NP.BEEPS


def test_a_drink_never_exceeds_the_water_and_salt_follows_the_flasks_of_water():
    inp, plan = _plan_for({"v": 3, "picks": [-2, -3]}, {}, 6)
    for r in plan["stretches"]:
        drink = sum(it["n"] for it in r["items"] if it["pid"] == -2)
        assert drink * 500 <= r["fluid_ml"] + 500  # at most the water of the stretch (whole doses, carried over)
    total_drink_ml = sum(it["n"] * 500 for r in plan["stretches"] for it in r["items"] if it["pid"] == -2)
    total_water = sum(r["fluid_ml"] for r in plan["stretches"]) - total_drink_ml
    salt = sum(it["n"] for r in plan["stretches"] for it in r["items"] if it["pid"] == -3)
    assert abs(salt - total_water / 500) <= 0.5 + 1e-9  # one tablet per 500 ml flask of water, never the drink's


def test_gels_of_one_phase_are_handed_out_in_turns():
    inp, plan = _plan_for({"v": 3, "picks": [12, 13, -3]}, LEGACY_PRODUCTS, 19)
    n12 = sum(it["n"] for r in plan["stretches"] for it in r["items"] if it["pid"] == 12)
    n13 = sum(it["n"] for r in plan["stretches"] for it in r["items"] if it["pid"] == 13)
    assert abs(n12 - n13) <= 1 and n12 > 3
    for r in plan["stretches"]:
        if r["d_s"] >= 2 * 3600:
            assert {12, 13} <= {it["pid"] for it in r["items"]}, r["to_name"]


def test_a_caffeinated_gel_is_only_taken_at_caffeine_times_and_covers_the_race():
    caf = {9: {"id": 9, "name": "Maurten Gel 100 CAF 100", "kind": "gel", "carbs_g": 25, "sodium_mg": 20, "caffeine_mg": 100, "volume_ml": None}}
    inp, plan = _plan_for({"v": 3, "picks": [-1, 9]}, caf, 19, weight=70)
    cf = plan["caffeine"]
    assert cf["total_mg"] <= cf["max_mg"] and len(cf["doses"]) == 4
    assert [round(d["elapsed_s"] / 3600) for d in cf["doses"]] == [3, 8, 13, 18]
    assert sum(it["n"] for r in plan["stretches"] for it in r["items"] if it["pid"] == 9) == 4


def test_no_caffeine_dose_after_the_finish_even_with_long_stops():
    caf = _catalog_pantry("maurten-gel-100-caf")
    inp, plan = _plan_for({"v": 3, "picks": [-1, 100, -3]}, caf, 19, stop_min=60)
    end = plan["stretches"][-1]["end_clock_s"]
    assert plan["caffeine"]["doses"] and all(d["clock_s"] <= end - 30 * 60 for d in plan["caffeine"]["doses"])
    assert sum(it["n"] for r in plan["stretches"] for it in r["items"] if it["pid"] == 100) == len(plan["caffeine"]["doses"])


def test_a_switched_off_legacy_caffeine_block_is_not_a_setting():
    legacy = {"targets": {"carbs_g_per_h": 75, "fluid_ml_per_h": 500, "sodium_mg_per_h": 400},
              "items": [{"product_id": -1, "per_hour": 3}, {"product_id": -3, "per_hour": 1.5}], "flask_capacity_ml": 1000,
              "caffeine": {"enabled": False, "from_h": 3.0, "every_h": 2.5, "dose_mg": 50, "boost_dawn": False}}
    st = N.upgrade_legacy(legacy)
    assert "caffeine" not in st["custom"]
    pantry = _catalog_pantry("maurten-gel-100-caf")
    st = {**st, "phases": []}
    st = N.apply_op(st, "pick", 100, N.with_generics(pantry))
    inp, plan = _plan_for(N.state_json(st), pantry, 19)
    assert inp["caffeine"].get("by_unit") and inp["caffeine"]["every_h"] >= 4  # auto spacing: the doses cover the race
    assert plan["caffeine"]["doses"][-1]["elapsed_s"] > 14 * 3600
    on = {**legacy, "caffeine": {**legacy["caffeine"], "enabled": True}}
    assert N.upgrade_legacy(on)["custom"]["caffeine"]["enabled"] is True


def test_unit_and_pill_labels():
    assert N.unit_label(1, N.GENERIC_BY_ID[-1]) == "1 gel" and N.unit_label(3, N.GENERIC_BY_ID[-1]) == "3 gels"
    assert N.unit_label(2, N.GENERIC_BY_ID[-3]) == "2 pastilles de sel"
    assert N.unit_label(4, {"name": "Precision Fuel PF 90 Gel"}) == "4 PF 90"
    assert NP.pill_label(10, {"name": "Precision Fuel PF 90 Gel"}) == "10 prises PF 90" and NP.pill_label(1, {"name": "Precision Fuel PF 90 Gel"}) == "1 prise PF 90"
    assert NP.pill_label(5, {"name": "Maurten Gel 160"}) == "5 Maurten 160"


def test_generic_products_survive_a_non_empty_pantry():
    pantry = {7: {"id": 7, "name": "Mon gel", "kind": "gel", "carbs_g": 30, "sodium_mg": 0, "caffeine_mg": None, "volume_ml": None}}
    inp = N.resolve_inputs({"targets": {"carbs_g_per_h": 75}, "items": [{"product_id": -1, "per_hour": 3}, {"product_id": -3, "per_hour": 1}]}, pantry, 5 * 3600, None, None)
    assert inp["picks"] == [-1, -3] and inp["phases"][0]["mix"] == {-1: 3.0}
    assert -1 in inp["products_by_id"] and 7 in inp["products_by_id"] and -10 in inp["products_by_id"]


# ── water ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_water_is_what_to_carry_and_a_short_flask_is_named(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    from tests.nutri_course import LONG_CPS, long_course

    r = await as_user.post("/api/simulator/routes", data={
        "course_json": long_course().model_dump_json(), "checkpoints_json": json.dumps(LONG_CPS), "name": "Long", "target_time_s": 27 * 3600,
        "race_date": "2099-10-02", "start_hour": 21, "start_minute": 0, "sport_type": "trail"})
    rid = r.json()["id"]
    t = (await as_user.get(f"{P}/{rid}")).text
    assert "pf-rv-water is-warn" not in t and re.search(r"[\d,]+ L<span class=\"sr-only\"> d'eau</span></span>", t.replace("&#39;", "'"))  # auto: each row says what to leave with, no amber
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "carry", "v": "1000"})
    t = r.text
    assert "pf-rv-water is-warn" in t and "Tes flasques font 1 L" in _text(t)
    # a water-only point where a long dry stretch starts says it in its passage row (a ravito's water is in its row)
    from app.routers.simulator import _passage_table_context

    route = await db_session.get(Route, rid)
    await db_session.refresh(route)
    from app.schemas.simulator import CourseProfile

    ctx = await _passage_table_context(db_session, test_user.id, CourseProfile(**route.course_json), LONG_CPS, 27 * 3600, 1.0, 21, 0, None,
                                       route, None, None, None, route_id=rid)
    names = [s["end_name"] for s in ctx["sections"]]
    noted = [names[i] for i, lg in enumerate(ctx["legs"]) if lg["water_note"]]
    kinds = {cp["name"]: cp["kind"] for cp in LONG_CPS}
    assert noted and all(kinds[n] == "water" for n in noted)
    assert all(ctx["legs"][names.index(n)]["water_alert"] for n in noted)


@pytest.mark.asyncio
async def test_rows_use_the_forecast_the_page_has_not_saved_yet(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """The forecast arrives, the rows are redone at once, the save comes after:
    the plan already reads that heat, as Ravitaillement will once it is saved."""
    rid = await _route(as_user, target_s=6 * 3600)
    from app.routers.simulator import _nutrition_plan, _plan_bundle

    route = await db_session.get(Route, rid)
    b = await _plan_bundle(route, db_session, test_user)

    async def water(temp=None):
        _inp, plan = await _nutrition_plan(db_session, test_user.id, None, route, b["sections"], b["checkpoints"], b["aid_kms"], b["start_offset_s"], 6 * 3600, temp)
        return [r["fluid_ml"] for r in plan["stretches"]]

    cold, hot = await water(), await water(32.0)
    assert sum(hot) > sum(cold)
    route.weather_json = {"temperature_c": 32.0}
    await db_session.flush()
    assert await water() == hot  # saved: the same plan without the override
    r = await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(CPS), "target_time_s": 6 * 3600, "start_hour": 21, "start_minute": 0, "route_id": rid,
        "stop_minutes": 3, "weather_temp_c": "32",
    })
    assert r.status_code == 200 and "Ton sachet jusqu" in r.text


# ── review fixes ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_too_much_caffeine_set_by_hand_is_said(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    test_user.weight_kg = 50
    await db_session.flush()
    rid = await _route(as_user, target_s=19 * 3600)
    await as_user.post(f"{P}/{rid}/catalog/maurten-gel-100-caf")
    k = list(_rows((await as_user.get(f"{P}/{rid}")).text))[0]
    pid = (await _nj(db_session, rid))["picks"][-1]
    for _ in range(3):
        r = await as_user.post(f"{P}/{rid}/plan", data={"op": "n", "km": str(k), "pid": str(pid), "d": "1"})
    assert re.search(r"Trop de caféine : \d+ mg en 24 h, au-dessus des 300 mg conseillés pour toi\.", _text(r.text))


@pytest.mark.asyncio
async def test_a_drop_bag_tap_keeps_the_default_so_another_race_cannot_rewrite_it(as_user: AsyncClient, db_session: AsyncSession):
    first = await _route(as_user, name="Transjeju", target_s=19 * 3600)
    other = await _route(as_user, name="SaintéLyon", target_s=9 * 3600)
    await as_user.post(f"{P}/{other}/plan", data={"op": "level", "v": "solide"})
    t = (await as_user.get(f"{P}/{first}")).text
    assert "Repris de « SaintéLyon »" in t and await _nj(db_session, first) is None
    r = await as_user.post(f"{P}/{first}/plan", data={"op": "keep"})
    nj = await _nj(db_session, first)
    assert nj["v"] == 3 and nj["level"] == "solide" and nj["picks"] == [-1, -3, -4] and "Repris de" not in r.text
    await as_user.post(f"{P}/{other}/plan", data={"op": "toggle", "v": "-4"})
    assert (await _nj(db_session, first))["picks"] == [-1, -3, -4]
    await as_user.post(f"{P}/{first}/plan", data={"op": "keep"})
    assert await _nj(db_session, first) == nj


@pytest.mark.asyncio
async def test_bare_points_ask_where_to_eat_and_the_race_runs_in_blocks(as_user: AsyncClient):
    bare = [{**cp, "kind": "none", "drop_bag": False, "crew": False} for cp in CPS]
    rid = await _route(as_user, cps=bare, target_s=9 * 3600)
    t = (await as_user.get(f"{P}/{rid}")).text
    chips = t.split("Où peux-tu manger ?")[1].split('class="pf-rv-table"')[0]
    for cp in CPS:
        assert cp["name"] in chips
    assert "pfRvPoint(this, 'food')" in chips and "Par tranche de 3 h" in t and len(_rows(t)) == 3
    t = (await as_user.get(f"{P}/{await _route(as_user, name='Typed')}")).text
    assert "Où peux-tu manger ?" not in t and "Entre les ravitos" in t


@pytest.mark.asyncio
async def test_no_predicted_time_says_how_to_get_one(as_user: AsyncClient, db_session: AsyncSession):
    rid = await _route(as_user)
    route = await db_session.get(Route, rid)
    route.target_time_s = None
    route.course_json = {**route.course_json, "segments": []}
    await db_session.flush()
    r = await as_user.get(f"{P}/{rid}")
    assert r.status_code == 200 and "Indique un objectif de temps, ou garde l'estimation, pour calculer ton plan." in r.text.replace("&#39;", "'")
    assert await _nj(db_session, rid) is None


@pytest.mark.asyncio
async def test_the_card_morphs_in_place_and_keeps_the_open_row(as_user: AsyncClient):
    t = (await as_user.get(f"{P}/{await _route(as_user)}")).text
    script = t.split("<script>")[1]
    assert "Idiomorph.morph(t, html, { morphStyle: 'outerHTML' })" in script and "htmx:beforeSwap" in script
    assert "htmx:configRequest" in script and "details.pf-rv-row[open]" in script
    assert 'hx-target="this" hx-swap="outerHTML" hx-sync="this:queue all"' in t
    page = (await as_user.get(f"/simulator/routes/{await _route(as_user, name='Page')}")).text
    assert "idiomorph" in page and "?v=19" in page


@pytest.mark.asyncio
async def test_print_band_has_one_line_per_stretch_and_the_beep(as_user: AsyncClient):
    rid = await _route(as_user, target_s=6 * 3600)
    t = (await as_user.get(f"/simulator/routes/{rid}/print")).text
    nut = t.split(">Nutrition")[1]
    assert "bip toutes les 20 min" in nut
    rows = re.findall(r"<tr>(.*?)</tr>", nut, flags=re.S)
    assert len(rows) == 3 and "gels" in nut
    cells = re.findall(r"<td[^>]*>(.*?)</td>", nut, flags=re.S)
    assert not any(re.fullmatch(r"\s*(\d+h\d\d|\d+ min)\s*", c) for c in cells), cells


def test_an_old_plans_flask_is_not_spread_to_a_new_race():
    src = {"name": "Transjeju", "nutrition_json": LEGACY_PLAN}
    inp = N.resolve_inputs(None, LEGACY_PRODUCTS, 20 * 3600, None, 68, default_from=src)
    assert inp["source"] == "Transjeju" and inp["picks"] == [11, 12, 13, 14, 15] and inp["carry_ml"] is None
    v2 = {"name": "CCC", "nutrition_json": {"v": 2, "picks": [-1, -3], "carry_ml": 1500}}
    assert N.resolve_inputs(None, {}, 20 * 3600, None, 68, default_from=v2)["carry_ml"] == 1500


@pytest.mark.asyncio
async def test_an_own_product_without_a_name_keeps_the_form(as_user: AsyncClient):
    rid = await _route(as_user)
    r = await as_user.post(f"/partials/simulator/nutrition/{rid}/products", data={"name": "", "kind": "drink", "carbs_g": "40", "sodium_mg": "200"})
    t = r.text
    assert 'id="rv-brands" class="pf-rv-more" open' in t and 'class="pf-rv-own" open' in t
    assert "Donne-lui un nom." in t and 'value="40"' in t and 'value="200"' in t and 'data-kind="drink" aria-pressed="true"' in t
    assert 'name="name" type="text" maxlength="100" required' in t and "novalidate" not in t
    assert "par flasque de 500 ml" in t


@pytest.mark.asyncio
async def test_back_from_ravitaillement_redoes_the_rows_and_the_bags_link_stays(as_user: AsyncClient):
    html = (await as_user.get(f"/simulator/routes/{await _route(as_user)}")).text
    script = html.split("function switchRouteTab")[1].split("</script>")[0]
    # any successful POST to the nutrition card marks the rows stale; back to the plan redoes them once
    assert "ravitoDirty = true" in script and "/partials\\/simulator\\/nutrition\\//" in script
    plan_branch = script.split("} else {")[1].split("// a tool reloads")[0]
    assert "ravitoDirty" in plan_branch and "recalc()" in plan_branch
    assert "anchor === 'bags' ? 'bags' : tab" in script  # #bags is kept in the address
    # an out-of-range number in Réglages du plan says why it is not saved
    assert html.count('hx-on::validation:halted="this.reportValidity()"') == 2


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

    assert effort_sentence("flat", 129) == "<b>Cardio sous 129</b>"  # running steady on the flat goes without saying
    assert effort_sentence("flat", None) is None
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
    row_caps = [int(x) for x in re.findall(r"Cardio (?:sous|vers) (\d+)", t)]  # « vers » on a descent (a target, not a ceiling)
    csv = (await as_user.get(f"/api/simulator/routes/{rid}/pace-export?format=csv")).text
    codes = [int(x) for x in re.findall(r"FC(\d+)", csv)]
    assert row_caps and row_caps == codes[:len(row_caps)]
    assert row_caps[0] <= 150 and len(set(row_caps)) > 1  # falls along the race, not one repeated number


@pytest.mark.asyncio
async def test_a_short_race_follows_the_duration_ladder(as_user: AsyncClient):
    t = (await as_user.get(f"{P}/{await _route(as_user, target_s=int(2.5 * 3600), name='Court')}")).text
    assert "Course courte : 60 g/h suffisent." in t and '"v":"normal"}\' aria-pressed="true"' in t


# ── second review ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_undo_shows_where_the_tap_was_made_and_only_on_that_render(as_user: AsyncClient, db_session: AsyncSession):
    """UX2 + UX3: « Annuler » in the editor he tapped in (not 1 170 px above), gone on
    a reload; « Supprimer ce changement » says « Changement supprimé. », never « 0 tronçon »."""
    rid = await _route(as_user, target_s=8 * 3600)
    await as_user.post(f"{P}/{rid}/catalog/maurten-gel-160")
    keys = list(_rows((await as_user.get(f"{P}/{rid}")).text))
    col, village = keys[1], keys[2]
    r = await as_user.post(f"{P}/{rid}/catalog/pf-90-gel", data={"km": str(col)})
    pf = _pid(r.text, "PF 90")
    await as_user.post(f"{P}/{rid}/plan", data={"op": "n", "km": str(village), "pid": str(pf), "d": "1"})
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "mix", "km": str(col), "pid": str(pf), "on": "0", "open": f"ph:{col}"})
    head = r.text.split('id="rv-table"')[0]
    editor = r.text.split(f'id="ed-{col}"')[1].split('<details class="pf-rv-row')[0]
    assert "pf-rv-undo" not in head and "1 tronçon remis en auto." in _text(editor) and '"op":"undo"' in editor
    assert 'role="status"' in editor
    t = (await as_user.get(f"{P}/{rid}")).text  # a reload or a later visit: no old « Annuler »
    assert "pf-rv-undo" not in t and "remis en auto" not in _text(t)
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "undo"})
    assert str(village) in (await _nj(db_session, rid))["rows"]
    # « Supprimer ce changement » (row open, editor closed): the line is in that row and says what happened
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "unswitch", "km": str(col), "open": f"s:{col}"})
    row = _rows(r.text)[col]
    assert "Changement supprimé." in _text(row) and "0 tronçon" not in r.text and "pf-rv-undo" not in r.text.split('id="rv-table"')[0]


@pytest.mark.asyncio
async def test_a_catalogue_product_replacing_gel_in_a_hand_set_row_says_so_in_the_list(as_user: AsyncClient, db_session: AsyncSession):
    """E5 through the page: the hand-set sachet goes back to auto with the new gel, an « Annuler » in the list."""
    rid = await _route(as_user, target_s=8 * 3600)
    k = list(_rows((await as_user.get(f"{P}/{rid}")).text))[1]
    await as_user.post(f"{P}/{rid}/plan", data={"op": "n", "km": str(k), "pid": "-1", "d": "1"})
    r = await as_user.post(f"{P}/{rid}/catalog/maurten-gel-160")
    nj = await _nj(db_session, rid)
    assert str(k) not in nj["rows"] and "à la main" not in _rows(r.text)[k] and "Maurten 160" in _rows(r.text)[k]
    panel = r.text.split('id="rv-brands"')[1].split('id="rv-settings"')[0]
    assert panel.startswith(' class="pf-rv-more" open') and "1 tronçon remis en auto." in _text(panel)
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "undo"})
    nj = await _nj(db_session, rid)
    assert nj["picks"][0] == -1 and nj["rows"][str(k)]["-1"] >= 1


@pytest.mark.asyncio
async def test_removing_a_product_is_idempotent_and_phase_chips_say_on_or_off(as_user: AsyncClient, db_session: AsyncSession):
    """stale-toggle-readds: a stale card (or a second device) cannot put back a product just removed."""
    rid = await _route(as_user, target_s=8 * 3600)
    r = await as_user.post(f"{P}/{rid}/catalog/maurten-gel-160")
    pid = (await _nj(db_session, rid))["picks"][0]
    chip = re.search(rf'hx-vals=\'\{{"op":"(\w+)","v":"{pid}","open":"brands"\}}\'', r.text)
    assert chip and chip.group(1) == "unpick"
    for _ in range(2):  # the same tap from two tabs
        await as_user.post(f"{P}/{rid}/plan", data={"op": "unpick", "v": str(pid), "open": "brands"})
    nj = await _nj(db_session, rid)
    assert pid not in nj["picks"] and all(str(pid) not in p["mix"] for p in nj["phases"])
    # « à partir d'ici » chips send what they want, twice is still on
    await as_user.post(f"{P}/{rid}/catalog/maurten-gel-160")
    keys = list(_rows((await as_user.get(f"{P}/{rid}")).text))
    t = (await as_user.get(f"{P}/{rid}?open=ph:{keys[1]}")).text
    assert re.search(rf'"pid":"{pid}","on":"0"', t)  # on in this phase: the chip turns it off
    for _ in range(2):
        await as_user.post(f"{P}/{rid}/plan", data={"op": "mix", "km": str(keys[1]), "pid": "-5", "on": "1"})
    nj = await _nj(db_session, rid)
    assert [p["km"] for p in nj["phases"]] == [0.0, keys[1]] and "-5" in nj["phases"][1]["mix"]


@pytest.mark.asyncio
async def test_odd_numbers_never_give_a_500_and_never_reset_the_flask(as_user: AsyncClient, db_session: AsyncSession):
    """servings-overflow-500: « inf » in a form or a tap keeps the page; « abc » keeps the flask."""
    rid = await _route(as_user)
    for v in ("inf", "1e999", "nan"):
        r = await as_user.post(f"{P}/{rid}/products", data={"name": f"G {v}", "kind": "gel", "carbs_g": "30", "servings": v})
        assert r.status_code == 200
    prods = (await db_session.execute(select(NutritionProduct))).scalars().all()
    assert {p.servings for p in prods} == {1}
    r = await as_user.post(f"{P}/{rid}/products/{prods[0].id}", data={"name": "G", "carbs_g": "inf", "servings": "inf"})
    assert r.status_code == 200
    await as_user.post(f"{P}/{rid}/plan", data={"op": "carry", "v": "1500"})
    for v in ("inf", "1e999", "abc", "-3"):
        r = await as_user.post(f"{P}/{rid}/plan", data={"op": "carry", "v": v})
        assert r.status_code == 200 and (await _nj(db_session, rid))["carry_ml"] == 1500
    await as_user.post(f"{P}/{rid}/plan", data={"op": "carry", "v": "auto"})
    assert (await _nj(db_session, rid))["carry_ml"] is None


@pytest.mark.asyncio
async def test_a_pf90_set_to_one_prise_stays_at_one(as_user: AsyncClient, db_session: AsyncSession):
    """servings-one-ignored: the pantry row's value is trusted, on the card and in the form."""
    rid = await _route(as_user, target_s=8 * 3600)
    await as_user.post(f"{P}/{rid}/catalog/pf-90-gel")
    pf = (await db_session.execute(select(NutritionProduct).where(NutritionProduct.name == "Precision Fuel PF 90 Gel"))).scalar_one()
    r = await as_user.post(f"{P}/{rid}/products/{pf.id}", data={"name": "Precision Fuel PF 90 Gel", "kind": "gel", "carbs_g": "90", "sodium_mg": "0", "servings": "1"})
    await db_session.refresh(pf)
    form = r.text.split(f'id="rv-edit-{pf.id}"')[1].split("</form>")[0]
    assert pf.servings == 1 and "3 prises" not in r.text and "prises PF 90" not in r.text and 'name="servings" type="number"' in form
    assert re.search(r'name="servings"[^>]*value="1"', form)


@pytest.mark.asyncio
async def test_the_old_form_quantities_are_pouches(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """legacy-shares-in-prises: an older client posting PF 90 at 0,5 an hour keeps 1,5 prises an hour."""
    rid = await _route(as_user, target_s=8 * 3600)
    ids = await _seed_legacy(db_session, test_user, rid)
    await as_user.post(f"{P}/{rid}/plan", data={"carbs_g_per_h": "75", f"use_{ids[11]}": "1", f"qty_{ids[11]}": "0.5",
                                                   f"use_{ids[12]}": "1", f"qty_{ids[12]}": "0.5"})
    nj = await _nj(db_session, rid)
    assert nj["phases"][0]["mix"] == {str(ids[11]): 1.5, str(ids[12]): 0.5}


@pytest.mark.asyncio
async def test_a_second_point_at_the_same_km_makes_one_row(as_user: AsyncClient, db_session: AsyncSession):
    """duplicate-stretch-keys: « Col » and « Col assistance » at km 10: one row id, one row frozen by a tap."""
    cps = CPS[:2] + [{**CPS[1], "name": "Col assistance", "kind": "none", "crew": True, "drop_bag": False, "cutoff_clock": None}] + CPS[2:]
    rid = await _route(as_user, cps=cps, target_s=8 * 3600)
    t = (await as_user.get(f"{P}/{rid}")).text
    ids = re.findall(r'<details class="pf-rv-row[^"]*" name="rv-s" id="(s-[\d.]+)"', t)
    assert len(ids) == len(set(ids)) == 3 and "s-10.0" in ids
    await as_user.post(f"{P}/{rid}/plan", data={"op": "n", "km": "10.0", "pid": "-1", "d": "1"})
    r = await as_user.get(f"{P}/{rid}")
    assert sum("à la main" in v for v in _rows(r.text).values()) == 1


@pytest.mark.asyncio
async def test_food_point_chips_stay_while_he_picks_and_say_which_are_food(as_user: AsyncClient, db_session: AsyncSession):
    """UX5: after the first ravito is marked, the chips are still there (pressed on it), and a second tap can unset it."""
    from app.models.route import RouteCheckpoint

    bare = [{**cp, "kind": "none", "drop_bag": False, "crew": False} for cp in CPS]
    rid = await _route(as_user, cps=bare, target_s=9 * 3600)
    t = (await as_user.get(f"{P}/{rid}")).text
    assert t.count("pfRvPoint(this, 'food')") == 3 and 'aria-pressed="true" data-ci' not in t
    cp = (await db_session.execute(select(RouteCheckpoint).where(RouteCheckpoint.route_id == rid, RouteCheckpoint.name == "Col"))).scalar_one()
    cp.kind = "full"
    await db_session.flush()
    r = await as_user.post(f"{P}/{rid}/plan", data={"op": "keep", "open": "points"})  # what the chip's tap sends after the save
    chips = r.text.split("Où peux-tu manger ?")[1].split('class="pf-rv-table"')[0]
    assert chips.count("pfRvPoint(this, 'food')") == 3
    assert re.search(r'id="pt-\d+" aria-pressed="true" data-ci="\d+" data-km="10.0"', chips) and chips.count('aria-pressed="true"') == 1
    assert "Où peux-tu manger ?" not in (await as_user.get(f"{P}/{rid}")).text  # a later visit: the typed points speak


@pytest.mark.asyncio
async def test_the_card_says_scopes_counts_and_water_in_words(as_user: AsyncClient):
    """UX6 scoped labels, UX8 counts once in an open row, UX11 water and counts for a screen
    reader, UX13 the editor's own button closes it, UX9 stable ids, UX10 44 px targets."""
    rid = await _route(as_user, target_s=8 * 3600)
    await as_user.post(f"{P}/{rid}/plan", data={"op": "carry", "v": "1000"})
    keys = list(_rows((await as_user.get(f"{P}/{rid}")).text))
    k = keys[1]
    t = (await as_user.get(f"{P}/{rid}?open=s:{k}")).text
    row = _rows(t)[k]
    assert "Produit sur ce tronçon</summary>" in row and 'aria-label="Ajouter un produit' not in row
    assert "Produit pour toute la course</summary>" in t
    assert re.search(rf'class="pf-chip-lg" id="s-{k}-a-?\d+"', row)  # « + Produit » chips have ids the focus can follow
    assert f'id="s-{k}-bag"' in row
    assert re.search(r'<b aria-live="polite" aria-atomic="true">\d+</b>', row)
    over = next(v for v in _rows(t).values() if "pf-rv-water is-warn" in v)
    assert "d'eau, plus que tes flasques" in over and "pf-rv-water is-warn" in over and 'class="w-3.5 h-3.5' in over.split("pf-rv-water is-warn")[1][:900]
    assert f'"open":"ph:{k}"' in row and "aria-controls" not in row.split("-ph")[1][:200]
    t = (await as_user.get(f"{P}/{rid}?open=ph:{k}")).text
    btn = re.search(rf'id="s-{k}-ph"[^>]*>', t).group(0)
    assert f'"open":"s:{k}"' in btn and 'aria-expanded="true"' in btn and f'aria-controls="ed-{k}"' in btn
    assert 'class="pf-rv-scope" tabindex="-1"' in t
    # the open row's pills are hidden (the steppers carry the counts), and rows and editor clear the sticky bars
    import pathlib

    css = (pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "interface.css").read_text()
    assert ".pf-rv-row[open] > summary .pf-rv-r2 { display: none; }" in css
    assert ".pf-rv-row, .pf-rv-row > summary, .pf-rv-editor { scroll-margin-top: 72px; scroll-margin-bottom: 80px; }" in css
    script = t.split("<script>")[1]
    assert "el.scrollIntoView({ block: 'nearest'" in script and "querySelector('.pf-rv-scope')" in script and "keepFocus(actId" in script
    # 44 px: « Ton sachet jusqu'à » and « Ouvrir le plan »
    pt = (await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(CPS), "target_time_s": 8 * 3600, "start_hour": 21, "start_minute": 0, "route_id": rid, "stop_minutes": 3})).text
    assert 'class="pf-link pf-rv-hit" onclick="window.switchRouteTab && window.switchRouteTab(\'nutrition\'' in pt
    none = await _route(as_user, cps=[], name="Sans ravitos", target_s=9 * 3600)
    assert 'class="pf-link pf-rv-hit" onclick="window.switchRouteTab && window.switchRouteTab(\'plan\')">Ouvrir le plan' in (await as_user.get(f"{P}/{none}")).text


@pytest.mark.asyncio
async def test_the_strip_is_coloured_by_phase(as_user: AsyncClient):
    """UX7: one phase, one colour on the strip, whatever product leads a stretch."""
    rid = await _route(as_user, target_s=8 * 3600)
    await as_user.post(f"{P}/{rid}/catalog/maurten-gel-160")
    await as_user.post(f"{P}/{rid}/catalog/baouw-gel")
    t = (await as_user.get(f"{P}/{rid}")).text
    strip = re.findall(r'<i data-cat="(\d)"', t.split('class="pf-rv-strip"')[1].split("</div>")[0])
    chip = re.findall(r'class="pf-rv-phase".*?data-cat="(\d)"', t, flags=re.S)
    assert len(chip) == 1 and strip and set(strip) == set(chip)


@pytest.mark.asyncio
async def test_the_sachet_link_opens_its_row_after_a_reload_and_the_bike_page_morphs(as_user: AsyncClient, cycling_on):
    """UX1: the anchor rides the reload (open=s:…) and the row is opened once the new card is in.
    UX14: the bike page loads idiomorph like the run page."""
    page = (await as_user.get(f"/simulator/routes/{await _route(as_user)}")).text
    script = page.split("function switchRouteTab")[1].split("</script>")[0]
    assert "'&open=' + encodeURIComponent(anchor)" in script and "htmx:afterSettle" in script
    assert script.index("var reload =") < script.index("if (rowAnchor)") or script.index("var reload =") < script.index("else if (rowAnchor)")
    from tests.test_simulator_routes import _create_bike_route

    bike = (await as_user.get(f"/simulator/routes/{await _create_bike_route(as_user)}")).text
    assert "idiomorph@0.3.0/dist/idiomorph.min.js" in bike
    # the card answers the open row the reload asks for
    rid = await _route(as_user, target_s=8 * 3600, name="Ancre")
    k = list(_rows((await as_user.get(f"{P}/{rid}")).text))[1]
    t = (await as_user.get(f"{P}/{rid}?v=19&open=s:{k}")).text
    assert " open>" in _rows(t)[k].split("<summary>")[0] + ">"
