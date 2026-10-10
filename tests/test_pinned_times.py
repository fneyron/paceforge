"""Pinned passage times (« Heure de passage visée »): the plan hits every pinned
checkpoint to the minute, keeps the planned stops, survives inconsistent pins,
and is unchanged when nothing is pinned."""

import hashlib
import importlib.util
import json
import pathlib

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.dependencies import get_current_user
from app.models.user import User
from app.services import checkpoints as cpsvc
from app.services.pace_export import build_pace_csv, waypoint_rows
from app.services.pacing_guide import _seg_times_from_sections, build_pacing_guide
from app.services.plan_view import build_plan_data
from app.services.race_simulator import (
    MIN_PIN_RATIO,
    build_scenarios,
    compute_passage_times,
    plan_total_s,
    replan_from_passage,
)
from tests.test_race_plan_services import CPS, _course

STOPS = {6.0: 120, 10.0: 300, 20.0: 900}
START_21 = 21 * 3600


def _cps(**pins):
    """CPS with target_s set by name (« Col »=7200)."""
    out = [dict(cp) for cp in CPS]
    for cp in out:
        if cp["name"] in pins:
            cp["target_s"] = pins[cp["name"]]
    return out


def _plan(cps, target=None, start_hour=21, stops=STOPS, hourly=None):
    return compute_passage_times(_course(), cps, target, 1.0, start_hour, 0, hourly, aid_stops=stops)


def _by(secs, name):
    return next(s for s in secs if s["end_name"] == name)


# ── no pins: byte-for-byte what the plan gave before the feature ──

HOURLY = {"temps": [12 + (h % 24) * 0.5 for h in range(48)], "humidity": [70] * 48, "codes": [3] * 48, "wind": [10] * 48}
LEGACY = {  # sha256 of {sections, replan at Col +2h, scenarios}, computed before pins existed
    "no_target": (dict(target=None, sh=6, stops=None, hourly=None), "7dd306c737ec235847f0ae303581ce29024e5ab40caac8220d35cdeb57dbeb12"),
    "target": (dict(target=5 * 3600, sh=6, stops=None, hourly=None), "af0d2a5ad451b40f6fc644565b1933d749d84c440634343502b20038fa6150cb"),
    "target_stops_night": (dict(target=5 * 3600 + 600, sh=21, stops=STOPS, hourly=None), "9ba200d49a1c8fdfb7688edda6e7dc1f84c58a436a52c5e2cc96eb853fd71b9b"),
    "target_hourly": (dict(target=4 * 3600, sh=9, stops={10.0: 300}, hourly=HOURLY), "dd7bc6b76345d155bae4200af5734a5241cec73f3030a41ef42b289c7b7d4628"),
}


@pytest.mark.parametrize("key", sorted(LEGACY))
def test_no_pins_output_is_unchanged(key):
    cfg, digest = LEGACY[key]
    for cps in (CPS, [{**cp, "target_s": None} for cp in CPS]):
        s = compute_passage_times(_course(), cps, cfg["target"], 1.0, cfg["sh"], 0, cfg["hourly"], aid_stops=cfg["stops"])
        rp = replan_from_passage(s, cfg["sh"] * 3600, cfg["target"], 10.0, (cfg["sh"] * 3600 + 2 * 3600) % 86400, aid_stops=cfg["stops"])
        sc = build_scenarios(s, cfg["sh"] * 3600, cfg["target"])
        blob = json.dumps({"s": s, "rp": rp, "sc": sc}, sort_keys=True, default=str)
        assert hashlib.sha256(blob.encode()).hexdigest() == digest
        assert all("pinned" not in x for x in s)


# ── the pins are hit ──

def test_pins_hit_to_the_second_with_an_objective_and_stops():
    target = 5 * 3600
    secs = _plan(_cps(Col=7200, Village=3 * 3600 + 20 * 60), target=target)
    col, village, fin = _by(secs, "Col"), _by(secs, "Village"), secs[-1]
    assert col["adjusted_clock_time_s"] == START_21 + 7200 and col["pinned"] and not col["pin_clamped"]
    assert village["adjusted_clock_time_s"] == START_21 + 3 * 3600 + 20 * 60
    assert fin["adjusted_clock_time_s"] == START_21 + target and fin["objective_s"] == target
    # the stops are kept: next arrival = this arrival + its stop + the moving time of the leg
    for prev, cur in zip(secs, secs[1:], strict=False):
        assert abs(cur["adjusted_clock_time_s"] - prev["adjusted_clock_time_s"] - prev["stop_s"] - cur["adjusted_time_s"]) <= 1
    # the unpinned leg before the first pin still follows the effort basis (not an even split)
    eau = _by(secs, "Eau 1")
    assert START_21 < eau["adjusted_clock_time_s"] < START_21 + 7200


def test_pin_past_midnight_and_on_the_last_checkpoint_without_objective():
    # 21:00 start, Village (last checkpoint) pinned at 00:40 the next day
    secs = _plan(_cps(Village=3 * 3600 + 40 * 60), target=None)
    village = _by(secs, "Village")
    assert village["adjusted_clock_time_s"] == 86400 + 40 * 60  # carries its day
    assert village["adjusted_clock_time_s"] % 86400 // 60 == 40
    # no objective: after the last pin the model's own pace carries on
    fin = secs[-1]
    assert fin["objective_s"] is None
    assert abs(fin["adjusted_time_s"] - fin["predicted_time_s"]) <= 1
    assert plan_total_s(secs, START_21, None) == fin["adjusted_clock_time_s"] - START_21


def test_the_estimate_sent_as_target_is_not_an_objective():
    course = _course()
    est = course.predicted_total_time_s + 20  # the page sends the estimate, rounded to the minute
    pinned = _plan(_cps(Col=7200), target=est)
    free = _plan(_cps(Col=7200), target=None)
    assert pinned[-1]["objective_s"] is None
    assert [s["adjusted_clock_time_s"] for s in pinned] == [s["adjusted_clock_time_s"] for s in free]


def test_auto_clock_is_where_the_plan_would_pass_without_the_pin():
    base = _plan(CPS, target=None)
    secs = _plan(_cps(Col=7200), target=None)
    assert _by(secs, "Col")["auto_clock_s"] == _by(base, "Col")["clock_time_s"]
    assert _by(secs, "Eau 1")["auto_clock_s"] is None


# ── inconsistent pins: clamped and flagged, never a crash ──

def test_pins_out_of_order_are_clamped_and_flagged():
    secs = _plan(_cps(Col=3 * 3600, Village=2 * 3600), target=5 * 3600)  # Village before Col
    col, village = _by(secs, "Col"), _by(secs, "Village")
    assert col["adjusted_clock_time_s"] == START_21 + 3 * 3600 and not col["pin_clamped"]
    assert village["pin_clamped"] and village["adjusted_clock_time_s"] > col["adjusted_clock_time_s"] + col["stop_s"]
    assert village["adjusted_clock_time_s"] % 60 == 0  # moved to a whole minute
    clocks = [s["adjusted_clock_time_s"] for s in secs]
    assert clocks == sorted(clocks) and all(s["adjusted_time_s"] > 0 for s in secs)


def test_pin_impossible_after_the_stop_is_moved_to_the_fastest_possible():
    # Col pinned at 2h, then Village 5 min later: the 5 min base-vie stop alone eats it
    secs = _plan(_cps(Col=7200, Village=7200 + 300), target=None)
    col, village = _by(secs, "Col"), _by(secs, "Village")
    assert village["pin_clamped"]
    leg = village["adjusted_clock_time_s"] - col["adjusted_clock_time_s"] - col["stop_s"]
    assert leg >= MIN_PIN_RATIO * village["predicted_time_s"] - 1
    assert leg < MIN_PIN_RATIO * village["predicted_time_s"] + 60


def test_objective_too_short_after_the_pins_flags_the_finish():
    secs = _plan(_cps(Village=4 * 3600), target=4 * 3600 + 60)  # 1 min for the last 10 km
    fin = secs[-1]
    assert fin["pin_clamped"] and fin["adjusted_clock_time_s"] > START_21 + 4 * 3600 + 60
    assert _by(secs, "Village")["adjusted_clock_time_s"] == START_21 + 4 * 3600


# ── race day: the real passage beats the pins before it, the next pin holds when it can ──

def test_replan_keeps_the_next_pin_and_the_plan_after_it():
    target = 5 * 3600
    secs = _plan(_cps(Village=3 * 3600 + 30 * 60), target=target)
    col = _by(secs, "Col")
    late = (col["adjusted_clock_time_s"] + 5 * 60) % 86400  # 5 min behind at Col
    out, rp = replan_from_passage(secs, START_21, target, 10.0, late, aid_stops=STOPS)
    assert rp["mode"] == "pin" and rp["pin_name"] == "Village"
    assert _by(out, "Village")["adjusted_clock_time_s"] == _by(secs, "Village")["adjusted_clock_time_s"]
    assert out[-1]["adjusted_clock_time_s"] == secs[-1]["adjusted_clock_time_s"]
    # leg durations after the pin are the pinned plan's
    assert out[-1]["adjusted_cumulative_time_s"] - _by(out, "Village")["adjusted_cumulative_time_s"] == \
        secs[-1]["adjusted_cumulative_time_s"] - _by(secs, "Village")["adjusted_cumulative_time_s"]


def test_replan_drops_a_pin_out_of_reach_and_the_anchor_pin():
    secs = _plan(_cps(Col=7200, Village=3 * 3600), target=None)
    # 1h late at Col: Village at 3h would need the next leg far faster → rhythm, pins dropped
    out, rp = replan_from_passage(secs, START_21, None, 10.0, (START_21 + 3 * 3600) % 86400, aid_stops=STOPS)
    assert rp["mode"] == "rhythm"
    assert not _by(out, "Col")["pinned"] and not _by(out, "Village")["pinned"]
    assert _by(out, "Col")["is_anchor"]


def test_scenarios_target_column_is_the_pinned_plan():
    secs = _plan(_cps(Col=7200), target=None)
    sc = build_scenarios(secs, START_21, None)
    assert sc["basis"] == "target"
    row = next(r for r in sc["rows"] if r["name"] == "Col")
    assert row["target_s"] == START_21 + 7200 and row["fast_s"] < row["target_s"] < row["safe_s"]


# ── everything downstream reads the pinned plan ──

def test_exports_plan_view_and_pacing_guide_follow_the_pins():
    course = _course()
    secs = compute_passage_times(course, _cps(Col=7200), None, 1.0, 21, 0, None, aid_stops=STOPS)
    secs = cpsvc.annotate_cutoffs(secs, _cps(Col=7200), START_21)
    rows = waypoint_rows(secs, True, START_21)
    assert next(r for r in rows if r["name"] == "Col")["clock"] == "23:00"
    assert ";23:00;2h00;" in build_pace_csv("t", secs, True, START_21)
    data = build_plan_data(secs, START_21, True, 30.0)
    col = next(p for p in data["points"] if p["name"] == "Col")
    assert data["pins"] == 1 and col["pinned"] and col["clock_s"] == START_21 + 7200
    assert data["plan_total_s"] == secs[-1]["adjusted_clock_time_s"] - START_21
    guide = build_pacing_guide(course, None, plan_sections=secs)
    moving = sum(s["adjusted_time_s"] for s in secs)
    assert abs(sum(b["time_s"] for b in guide["blocks"]) - moving) <= len(guide["blocks"])
    # the km up to the Col (km 10) take the 2 h minus the stop at Eau 1
    per_km = _seg_times_from_sections(course.segments, secs)
    before_col = sum(per_km[id(g)] for g in course.segments if g.end_km <= 10.0)
    assert abs(before_col - (7200 - 120)) <= 1


# ── normalisation ──

def test_normalize_checkpoint_target_s():
    n = cpsvc.normalize_checkpoint
    assert n({"name": "A", "distance_km": 5})["target_s"] is None
    assert n({"name": "A", "distance_km": 5, "target_s": "37620"})["target_s"] == 37620
    assert n({"name": "A", "distance_km": 5, "target_s": 3600.7})["target_s"] == 3600
    for bad in ("", "abc", "inf", "nan", 0, -60, 8 * 86400, None):
        assert n({"name": "A", "distance_km": 5, "target_s": bad})["target_s"] is None


# ── HTTP: saved, reloaded, shown in the table, the print and the exports ──

@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, db_session, monkeypatch):
    # Endpoint commits are replaced with flushes to keep each test rollback-isolated.
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


async def _save(client: AsyncClient, cps, route_id=None) -> int:
    data = {
        "course_json": _course().model_dump_json(), "checkpoints_json": json.dumps(cps),
        "name": "Jeju pins", "target_time_s": 5 * 3600, "race_date": "2026-10-02",
        "start_hour": 21, "start_minute": 0, "sport_type": "trail",
    }
    if route_id:
        data["route_id"] = route_id
    r = await client.post("/api/simulator/routes", data=data)
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_pinned_time_round_trips_through_the_endpoints(as_user: AsyncClient):
    rid = await _save(as_user, _cps(Col=7200))
    page = (await as_user.get(f"/simulator/routes/{rid}")).text
    assert '"target_s": 7200' in page  # back to the page's checkpoint objects
    table = page.split('id="passage-times-result">')[1].split('id="page-error"')[0]
    row = table.split("data-pinned")[1].split("data-row")[0]
    assert ">23:00<" in row and "heure fixée par toi" in row
    assert table.count("data-pinned") == 1

    # the recalculation endpoint reads it from the posted checkpoints
    r = await as_user.post("/partials/simulator/passage-times", data={
        "checkpoints_json": json.dumps(_cps(Village=3 * 3600 + 40 * 60)), "target_time_s": 5 * 3600,
        "start_hour": 21, "start_minute": 0, "route_id": rid,
    })
    assert r.status_code == 200 and "data-pinned" in r.text
    plan = json.loads(r.text.split('id="plan-data">')[1].split("</script>")[0])
    village = next(p for p in plan["points"] if p["name"] == "Village")
    assert village["pinned"] and village["clock_s"] == 86400 + 40 * 60

    pr = (await as_user.get(f"/simulator/routes/{rid}/print")).text
    assert "heure fixée par toi" in pr and "23:00" in pr
    csv = (await as_user.get(f"/api/simulator/routes/{rid}/pace-export?format=csv")).text
    assert "Col;10.0;700;23:00;2h00;" in csv

    # « Automatique »: saved without the pin, the model time is back
    await _save(as_user, _cps(), route_id=rid)
    page = (await as_user.get(f"/simulator/routes/{rid}")).text
    assert '"target_s": null' in page and "data-pinned" not in page.split('id="passage-times-result">')[1].split('id="page-error"')[0]


# ── migration ──

MIG = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "r2a3b4c5d6e7_add_checkpoint_target_s.py"


def test_migration_adds_and_drops_target_s_and_is_the_head():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    spec = importlib.util.spec_from_file_location("mig_target_s", MIG)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "q1f2a3b4c5d6"
    root = pathlib.Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    # in the chain (later migrations stack on it), with a single head overall
    script = ScriptDirectory.from_config(cfg)
    assert mig.revision in {s.revision for s in script.walk_revisions()}
    assert len(script.get_heads()) == 1

    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE route_checkpoints (id INTEGER PRIMARY KEY, route_id INTEGER, name VARCHAR(100), distance_km FLOAT, stop_s INTEGER)"))
        c.execute(sa.text("INSERT INTO route_checkpoints (route_id, name, distance_km) VALUES (1, 'Gasiri', 88.4)"))
        with Operations.context(MigrationContext.configure(c)):
            mig.upgrade()
        assert "target_s" in [col["name"] for col in sa.inspect(c).get_columns("route_checkpoints")]
        c.execute(sa.text("UPDATE route_checkpoints SET target_s = 37620"))
        assert c.execute(sa.text("SELECT target_s FROM route_checkpoints")).scalar() == 37620
        with Operations.context(MigrationContext.configure(c)):
            mig.downgrade()
        assert "target_s" not in [col["name"] for col in sa.inspect(c).get_columns("route_checkpoints")]
