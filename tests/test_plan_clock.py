"""The plan's clock across a daylight-saving change (services/plan_clock.py): the rows show the wall clock of the
race's zone, cutoffs and margins are read on it, and the model's seconds after the start stay the currency."""

import json
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.route import Route
from app.models.user import User
from app.services import checkpoints as cpsvc
from app.services import race_match
from app.services import race_simulator as rs
from app.services.pace_export import build_pace_csv, waypoint_rows
from app.services.plan_clock import PlanClock
from app.services.plan_view import build_plan_data, night_bands_km
from app.services.race_simulator import build_scenarios, compute_passage_times, plan_total_s, replan_from_passage
from tests.test_race_plan_services import CPS, _course

PARIS = ZoneInfo("Europe/Paris")
AUTUMN = "2026-10-24"  # Saturday; 03:00 → 02:00 on the 25th, five hours after a 22:00 start
SPRING = "2026-03-28"  # Saturday; 02:00 → 03:00 on the 29th
START_22 = 22 * 3600
VILLAGE_MIN = 5 * 60 + 39  # the 8 h plan passes Village (km 20) 5 h 39 after the start (the clock is printed to the minute)
CUTOFF_0430_AUTUMN = 7 * 3600 + 30 * 60  # 04:30 CET on the 25th: 7 h 30 after 22:00 CEST (the old arithmetic said 6 h 30)


def _hhmm(clock_s: int) -> str:
    return f"{clock_s % 86400 // 3600:02d}:{clock_s % 3600 // 60:02d}"


def _wall(race_date: str, elapsed_s: int, zone=PARIS, start_hour: int = 22) -> datetime:
    """The real wall clock: the start instant plus the elapsed seconds, read in the zone."""
    start = datetime.fromisoformat(race_date).replace(hour=start_hour, tzinfo=zone)
    return (start.astimezone(timezone.utc) + timedelta(seconds=elapsed_s)).astimezone(zone)


def _cps(cutoff: str | None = "04:30", **pins) -> list[dict]:
    out = [dict(CPS[0], cutoff_clock=None), dict(CPS[1], cutoff_clock=None), dict(CPS[2], cutoff_clock=cutoff)]
    for cp in out:
        if cp["name"] in pins:
            cp["target_s"] = pins[cp["name"]]
    return out


def _by(secs, name):
    return next(s for s in secs if s["end_name"] == name)


def _plan(race_date, tz="Europe/Paris", target=8 * 3600, cps=None, stops=None):
    clock = PlanClock(START_22, race_date, tz)
    cps = cps if cps is not None else _cps()
    secs = compute_passage_times(_course(), cps, target, 1.0, 22, 0, None, aid_stops=stops, clock=clock)
    return clock, cpsvc.annotate_cutoffs(secs, cps, START_22, clock=clock)


# ── the clock itself ──

def test_plan_clock_reads_the_wall_clock_and_its_first_occurrence():
    c = PlanClock(START_22, AUTUMN, "Europe/Paris")
    assert c.zoned and c.clock_s(0) == START_22
    assert _hhmm(c.clock_s(5 * 3600 - 60)) == "02:59" and _hhmm(c.clock_s(5 * 3600)) == "02:00"  # 03:00 → 02:00
    assert c.clock_s(VILLAGE_MIN * 60) == 86400 + 2 * 3600 + 39 * 60 and c.clock_s(8 * 3600) == 86400 + 5 * 3600
    assert c.elapsed_s(86400 + 4 * 3600 + 30 * 60) == CUTOFF_0430_AUTUMN
    # the repeated hour reads as its first occurrence (summer time): documented, deterministic
    assert c.elapsed_s(86400 + 2 * 3600 + 30 * 60) == 4 * 3600 + 30 * 60
    s = PlanClock(START_22, SPRING, "Europe/Paris")
    assert _hhmm(s.clock_s(4 * 3600)) == "03:00" and _hhmm(s.clock_s(9 * 3600)) == "08:00"  # 02:00 → 03:00
    assert s.elapsed_s(86400 + 4 * 3600 + 30 * 60) == 5 * 3600 + 30 * 60
    assert s.elapsed_s(86400 + 2 * 3600 + 30 * 60) == 4 * 3600 + 30 * 60  # inside the gap: the offset before the change
    # the rows keep their own seconds; a row built without them falls back on its clock
    assert c.elapsed_of({"adjusted_elapsed_s": 20354, "adjusted_clock_time_s": c.clock_s(20354)}, plan=True) == 20354
    assert c.elapsed_of({"clock_time_s": 86400 + 4 * 3600}, plan=False) == 7 * 3600
    assert c.elapsed_of({"clock_time_s": 1}, plan=True) is None


def test_without_a_known_zone_the_clock_is_the_start_offset_plus_the_elapsed():
    for c in (PlanClock(START_22), PlanClock(START_22, AUTUMN, None), PlanClock(START_22, AUTUMN, "Mars/Olympus"),
              PlanClock(START_22, None, "Europe/Paris"), PlanClock(START_22, "pas une date", "Europe/Paris")):
        assert not c.zoned and c.clock_s(20354) == START_22 + 20354 and c.elapsed_s(86400 + 1800) == 86400 + 1800 - START_22


# ── the plan across the change ──

def test_autumn_night_rows_show_the_real_wall_clock_and_the_real_margin():
    clock, secs = _plan(AUTUMN)
    for s in secs:
        for key in ("", "adjusted_"):
            real = _wall(AUTUMN, s[f"{key}elapsed_s"])
            assert _hhmm(s[f"{key}clock_time_s"]) == real.strftime("%H:%M")
            assert s[f"{key}clock_time_s"] // 86400 == (real.date() - date(2026, 10, 24)).days
    village, fin = _by(secs, "Village"), secs[-1]
    assert village["adjusted_elapsed_s"] // 60 == VILLAGE_MIN and _hhmm(village["adjusted_clock_time_s"]) == "02:39"  # not 03:39
    assert fin["adjusted_elapsed_s"] == 8 * 3600 and _hhmm(fin["adjusted_clock_time_s"]) == "05:00"  # not 06:00
    assert village["cutoff_elapsed_s"] == CUTOFF_0430_AUTUMN and village["cutoff_margin_s"] == 111 * 60
    # the same plan without a zone: what the page showed before (an hour off the wall clock past the change)
    old = cpsvc.annotate_cutoffs(compute_passage_times(_course(), _cps(), 8 * 3600, 1.0, 22, 0, None), _cps(), START_22)
    assert _hhmm(_by(old, "Village")["adjusted_clock_time_s"]) == "03:39" and _by(old, "Village")["cutoff_margin_s"] == 51 * 60
    assert [s["adjusted_elapsed_s"] for s in old] == [s["adjusted_elapsed_s"] for s in secs]  # the model's seconds, unchanged


def test_spring_night_skips_the_hour_and_the_cutoff_comes_an_hour_sooner():
    clock, secs = _plan(SPRING)
    village, fin = _by(secs, "Village"), secs[-1]
    assert _hhmm(village["adjusted_clock_time_s"]) == "04:39" and _hhmm(fin["adjusted_clock_time_s"]) == "07:00"
    assert village["cutoff_elapsed_s"] == 5 * 3600 + 30 * 60  # 04:30 CEST, not 6 h 30 after the start
    assert village["cutoff_margin_s"] == 5 * 3600 + 30 * 60 - VILLAGE_MIN * 60 == -9 * 60  # late, where the old margin read +51 min


def test_a_zone_without_daylight_saving_is_unchanged():
    clock, secs = _plan(AUTUMN, tz="Asia/Seoul")
    plain = cpsvc.annotate_cutoffs(compute_passage_times(_course(), _cps(), 8 * 3600, 1.0, 22, 0, None), _cps(), START_22)
    assert secs == plain


def test_a_cutoff_in_the_repeated_hour_is_the_occurrence_next_to_the_plan():
    clock = PlanClock(START_22, AUTUMN, "Europe/Paris")
    assert cpsvc.cutoff_elapsed_s("02:45", START_22, clock=clock) == 4 * 3600 + 45 * 60  # no passage known: 02:45 CEST, the first
    assert cpsvc.cutoff_elapsed_s("02:45", START_22, min_elapsed_s=VILLAGE_MIN * 60, clock=clock) == 5 * 3600 + 45 * 60  # beside a 02:39 CET row
    assert cpsvc.cutoff_elapsed_s("02:45", START_22, min_elapsed_s=3 * 3600 + 50 * 60, clock=clock) == 4 * 3600 + 45 * 60  # beside a 01:50 CEST row
    assert cpsvc.cutoff_elapsed_s("01:00", START_22, min_elapsed_s=30 * 3600, clock=clock) == 28 * 3600  # the second night, CET (27 h nominal)
    assert cpsvc.cutoff_elapsed_s("04:30", 21 * 3600) == 7 * 3600 + 30 * 60  # no zone: as before
    # 02:30 beside the plan's 02:39 CET at Village: the second 02:30, 9 min late (its first reading said 69)
    cps = _cps(cutoff="02:30")
    secs = cpsvc.annotate_cutoffs(compute_passage_times(_course(), cps, 8 * 3600, 1.0, 22, 0, None, clock=clock), cps, START_22, clock=clock)
    assert _by(secs, "Village")["cutoff_elapsed_s"] == 5 * 3600 + 30 * 60 and _by(secs, "Village")["cutoff_margin_s"] == -9 * 60


def test_a_typed_time_is_read_on_the_side_of_the_switch_it_names():
    """A passage typed on race day (and a pinned time: the page's sheet, below) names a wall clock: the occurrence
    closest to the plan's own time there, on either side of the switch."""
    clock, secs = _plan(AUTUMN)
    assert clock.switches(8 * 3600 + 86400) == [{"at_elapsed_s": 5 * 3600, "shift_s": -3600}]
    assert PlanClock(START_22, SPRING, "Europe/Paris").switches(12 * 3600) == [{"at_elapsed_s": 4 * 3600, "shift_s": 3600}]
    assert PlanClock(START_22, AUTUMN, "Asia/Seoul").switches(48 * 3600) == [] and PlanClock(START_22).switches(48 * 3600) == []
    assert clock.occurrences(86400 + 2 * 3600 + 30 * 60) == [4 * 3600 + 30 * 60, 5 * 3600 + 30 * 60]  # the repeated 02:30
    assert clock.occurrences(86400 + 3 * 3600 + 10 * 60) == [6 * 3600 + 10 * 60] and clock.occurrences(3600) == [-21 * 3600]
    assert PlanClock(START_22, SPRING, "Europe/Paris").occurrences(86400 + 2 * 3600 + 30 * 60) == [4 * 3600 + 30 * 60]  # the gap: fold 0
    assert PlanClock(START_22).occurrences(86400 + 2 * 3600 + 30 * 60) == [86400 + 2 * 3600 + 30 * 60 - START_22]
    # Col is planned at 01:50 CEST: « 03:10 » typed there is 03:10 CET, 6 h 10 in, and the row shows 03:10
    assert _hhmm(_by(secs, "Col")["adjusted_clock_time_s"]) == "01:50"
    out, rp = replan_from_passage(secs, START_22, 8 * 3600, 10.0, 3 * 3600 + 10 * 60, clock=clock)
    assert _by(out, "Col")["adjusted_elapsed_s"] == 6 * 3600 + 10 * 60 and _hhmm(_by(out, "Col")["adjusted_clock_time_s"]) == "03:10"
    # Village is planned at 02:39 CET: « 01:30 » is 01:30 CEST, 3 h 30 in; « 02:30 » the second 02:30, 9 min behind
    out, rp = replan_from_passage(secs, START_22, 8 * 3600, 20.0, 1 * 3600 + 30 * 60, clock=clock)
    assert _by(out, "Village")["adjusted_elapsed_s"] == 3 * 3600 + 30 * 60 and _hhmm(_by(out, "Village")["adjusted_clock_time_s"]) == "01:30"
    out, rp = replan_from_passage(secs, START_22, 8 * 3600, 20.0, 2 * 3600 + 30 * 60, clock=clock)
    assert _by(out, "Village")["adjusted_elapsed_s"] == 5 * 3600 + 30 * 60 and rp["delta_s"] == -9 * 60


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node runs the page's pin sheet")
def test_the_pin_sheet_reads_a_typed_clock_as_the_server_does(tmp_path):
    """The page's own functions (gpx_result.html), fed the plan data the server sends: a typed clock lands on the
    side of the switch it names, closest to the row's own time, and the sheet shows it back as typed."""
    tpl = (Path(__file__).resolve().parents[1] / "app/templates/partials/gpx_result.html").read_text(encoding="utf-8")
    block = re.search(r"// -- the plan's clock.*?// -- end of the plan's clock --", tpl, re.S).group(0)
    clock, secs = _plan(AUTUMN)
    data = build_plan_data(secs, START_22, True, 30.0, clock=clock)
    col, village = _by(secs, "Col")["adjusted_elapsed_s"], _by(secs, "Village")["adjusted_elapsed_s"]
    script = f"""var planData = {json.dumps({'clock_switches': data['clock_switches']})};
function startOffset() {{ return {START_22}; }}
{block}
console.log(JSON.stringify({{col_0310: pinElapsed({3 * 3600 + 10 * 60}, {col}), village_0130: pinElapsed({1 * 3600 + 30 * 60}, {village}),
  village_0230: pinElapsed({2 * 3600 + 30 * 60}, {village}), col_0230: pinElapsed({2 * 3600 + 30 * 60}, {col}),
  before: clockShiftAt({3 * 3600}), after: clockShiftAt({6 * 3600})}}));
planData.clock_switches = [{{at_elapsed_s: {4 * 3600}, shift_s: 3600}}];
console.log(JSON.stringify({{gap: elapsedAtClock({86400 + 2 * 3600 + 30 * 60}), spring_0430: pinElapsed({4 * 3600 + 30 * 60}, {village})}}));
planData.clock_switches = [];
console.log(JSON.stringify({{nominal: pinElapsed({3 * 3600 + 10 * 60}, {col})}}));
"""
    (tmp_path / "pin.js").write_text(script, encoding="utf-8")
    lines = subprocess.run([NODE, str(tmp_path / "pin.js")], capture_output=True, text=True, check=True).stdout.splitlines()
    autumn, spring, nominal = (json.loads(line) for line in lines)
    assert autumn["col_0310"] == 6 * 3600 + 10 * 60 and clock.clock_s(autumn["col_0310"]) == 86400 + 3 * 3600 + 10 * 60  # shown 03:10
    assert autumn["village_0130"] == 3 * 3600 + 30 * 60 and clock.clock_s(autumn["village_0130"]) == 86400 + 1 * 3600 + 30 * 60  # shown 01:30
    assert autumn["village_0230"] == 5 * 3600 + 30 * 60 and autumn["col_0230"] == 4 * 3600 + 30 * 60  # the 02:30 next to each row
    assert autumn["before"] == 0 and autumn["after"] == -3600
    assert spring["gap"] == [4 * 3600 + 30 * 60] and spring["spring_0430"] == 5 * 3600 + 30 * 60  # as PlanClock reads them
    assert nominal["nominal"] == 5 * 3600 + 10 * 60  # no switch: the old reading (03:10 the next day)


def test_pins_and_the_automatic_clock_sit_on_the_wall_clock():
    cps = _cps(Village=6 * 3600 + 30 * 60)
    clock, secs = _plan(AUTUMN, cps=cps)
    village = _by(secs, "Village")
    assert village["pinned"] and village["adjusted_elapsed_s"] == 6 * 3600 + 30 * 60
    assert _hhmm(village["adjusted_clock_time_s"]) == "03:30" and village["pin_clock_s"] == village["adjusted_clock_time_s"]  # 22:00 + 6 h 30
    assert _hhmm(village["auto_clock_s"]) == "02:39"  # where the plan passed without the pin
    assert plan_total_s(secs, START_22, 8 * 3600, clock=clock) == 8 * 3600
    assert plan_total_s(secs, START_22, None, clock=clock) == secs[-1]["adjusted_elapsed_s"]


def test_replan_on_race_day_reads_the_typed_clock_on_the_race_clock():
    clock, secs = _plan(AUTUMN)
    # at Village at 03:10 (CET, after the change): 6 h 10 after the start, 31 min behind the plan's 02:39
    out, rp = replan_from_passage(secs, START_22, 8 * 3600, 20.0, 3 * 3600 + 10 * 60, clock=clock)
    assert rp is not None and rp["delta_s"] == 31 * 60 and rp["anchor_clock_s"] == 3 * 3600 + 10 * 60
    anchor = _by(out, "Village")
    assert anchor["is_anchor"] and anchor["adjusted_elapsed_s"] == 6 * 3600 + 10 * 60 and _hhmm(anchor["adjusted_clock_time_s"]) == "03:10"
    assert anchor["adjusted_clock_time_s"] // 86400 == 1
    for s in out[out.index(anchor):]:
        assert _hhmm(s["adjusted_clock_time_s"]) == _wall(AUTUMN, s["adjusted_elapsed_s"]).strftime("%H:%M")
    assert rp["finish_clock_s"] == out[-1]["adjusted_clock_time_s"] and _hhmm(rp["target_clock_s"]) == "05:00"
    assert rp["projected_finish_clock_s"] == out[-1]["adjusted_clock_time_s"]  # the objective held: the finish is the plan's
    # margins re-read after the replan: the cutoff is 7 h 30 in, the passage 6 h 10
    after = cpsvc.annotate_cutoffs(out, _cps(), START_22, clock=clock)
    assert _by(after, "Village")["cutoff_margin_s"] == 80 * 60
    # the old nominal arithmetic read 03:10 as 5 h 10 after the start: 29 min ahead of the plan instead of 31 behind
    out0, rp0 = replan_from_passage(secs, START_22, 8 * 3600, 20.0, 3 * 3600 + 10 * 60)
    assert rp0["delta_s"] == -29 * 60


def test_scenario_columns_and_cutoff_checks_are_in_seconds_after_the_start():
    clock, secs = _plan(AUTUMN)
    sc = build_scenarios(secs, START_22, 8 * 3600, fast_pct=5, safe_pct=10, total_distance_km=30, clock=clock)
    row = next(r for r in sc["rows"] if r["name"] == "Village")
    village_s = _by(secs, "Village")["adjusted_elapsed_s"]
    assert row["target_elapsed_s"] == village_s and _hhmm(row["target_s"]) == "02:39"
    assert row["safe_elapsed_s"] == round(village_s * 1.1) and _hhmm(row["safe_s"]) == _wall(AUTUMN, row["safe_elapsed_s"]).strftime("%H:%M")
    assert row["fast_elapsed_s"] == round(village_s * 0.95) and _hhmm(row["fast_s"]) == _wall(AUTUMN, row["fast_elapsed_s"]).strftime("%H:%M")
    assert row["safe_ok"] is True and row["target_ok"] is True and _hhmm(row["cutoff_clock_s"]) == "04:30"
    assert sc["totals"] == {"target_s": 8 * 3600, "fast_s": round(8 * 3600 * 0.95), "safe_s": round(8 * 3600 * 1.1)}
    assert sc["cutoff_breach"] == []
    # a cutoff read on the wall clock alone would mislead past the change: it is compared in seconds after the start
    cps = _cps(cutoff=None)
    cps[1]["cutoff_clock"] = "02:10"
    secs2 = cpsvc.annotate_cutoffs(compute_passage_times(_course(), cps, 8 * 3600, 1.0, 22, 0, None, clock=clock), cps, START_22, clock=clock)
    col = _by(secs2, "Col")
    assert col["cutoff_elapsed_s"] == 4 * 3600 + 10 * 60  # 02:10 CEST, its first occurrence
    sc2 = build_scenarios(secs2, START_22, 8 * 3600, fast_pct=5, safe_pct=10, total_distance_km=30, clock=clock)
    col_row = next(r for r in sc2["rows"] if r["name"] == "Col")
    assert col_row["target_ok"] == (col_row["target_elapsed_s"] <= 4 * 3600 + 10 * 60)
    assert col_row["safe_ok"] == (col_row["safe_elapsed_s"] <= 4 * 3600 + 10 * 60)


def test_plan_data_and_exports_carry_the_elapsed_and_the_wall_clock():
    clock, secs = _plan(AUTUMN, stops={10.0: 300, 20.0: 900})
    data = build_plan_data(secs, START_22, True, 30.0, clock=clock)
    assert [p["elapsed_s"] for p in data["points"]] == [s["adjusted_elapsed_s"] for s in secs]
    # the stops accumulated so far (to the second's rounding), not an hour short past the change
    assert all(abs(p["stops_s"] - want) <= 1 for p, want in zip(data["points"], [0, 0, 300, 1200], strict=True))
    assert data["plan_total_s"] == 8 * 3600 and data["points"][-1]["clock_s"] == 86400 + 5 * 3600
    assert data["points"][2]["pin_clock_s"] is None
    rows = waypoint_rows(secs, True, START_22, clock=clock)
    assert rows[-1]["elapsed"] == "8h00" and rows[-1]["clock"] == "05:00"
    village = next(r for r in rows if r["name"] == "Village")
    assert village["elapsed_s"] == _by(secs, "Village")["adjusted_elapsed_s"]
    assert village["clock"] == _wall(AUTUMN, village["elapsed_s"]).strftime("%H:%M")
    csv = build_pace_csv("x", secs, True, START_22, clock=clock)
    assert any(line.startswith("Arrivée;30.0;") and ";05:00;8h00;" in line for line in csv.splitlines())
    # the fixed night window (undated plans) follows the race's clock too: 06:00 CET is 9 h after this start
    clock9, secs9 = _plan(AUTUMN, target=9 * 3600)
    assert night_bands_km(secs9, START_22, True, clock=clock9) == [{"start_km": 0.0, "end_km": 30.0}]
    assert night_bands_km(secs9, START_22, True)[-1]["end_km"] < 30.0


def test_race_clock_names_the_zone_when_the_clock_is_a_zones():
    paris = ZoneInfo("Europe/Paris")
    assert rs.race_clock_at("2026-10-25", 2.35, None, paris, start_zone="Europe/Paris", start_hour=1) == (7200, "Europe/Paris")
    assert rs.race_clock_at("2026-08-28", 6.9, weather_offset_s=7200) == (7200, None)  # the forecast's offset alone
    assert rs.race_clock_at("2026-08-28", 6.9, zone=paris) == (7200, "Europe/Paris")  # his zone fits the course
    assert rs.race_clock_at("2026-10-02", 126.5, zone=paris) == (8 * 3600, None)  # lon / 15
    assert rs.race_clock_at(None, 6.9) == (None, None)
    assert rs.race_utc_offset_s("2026-10-02", 126.5, zone=paris) == 8 * 3600


@pytest.mark.asyncio
async def test_race_clock_carries_the_zone_to_the_page(db_session: AsyncSession, test_user: User):
    route = Route(user_id=test_user.id, name="MIUT", total_distance_km=115, total_elevation_gain=7000, race_date="2026-04-25",
                  start_hour=0, start_minute=0, course_json={"route_coords": [[32.86, -16.90], [32.70, -16.95]]},
                  weather_json={"date": "2026-04-25", "tz": "Atlantic/Madeira"})
    db_session.add(route)
    await db_session.flush()
    clk = await race_match.race_clock(db_session, test_user.id, route)
    assert clk == {"race_date": "2026-04-25", "utc_offset_s": 3600, "tz": "Atlantic/Madeira"}
    route.weather_json = None  # lon / 15: an offset, no zone
    assert (await race_match.race_clock(db_session, test_user.id, route))["tz"] is None


# ── through the page ──

@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
async def test_the_table_and_the_phone_copy_show_the_wall_clock(as_user: AsyncClient, db_session: AsyncSession):
    """The page's own plan (his profile, the stop model): every row past the change is on the wall clock."""
    course = _course()
    r = await as_user.post("/partials/simulator/passage-times", data={
        "course_json": course.model_dump_json(), "checkpoints_json": json.dumps(_cps()), "target_time_s": 8 * 3600,
        "start_hour": 22, "start_minute": 0, "race_date": AUTUMN, "wx_tz": "Europe/Paris",
    })
    assert r.status_code == 200, r.text
    data = json.loads(r.text.split('id="plan-data">')[1].split("</script>")[0])
    for p in data["points"]:
        assert _hhmm(p["clock_s"]) == _wall(AUTUMN, p["elapsed_s"]).strftime("%H:%M")
    village = next(p for p in data["points"] if p["name"] == "Village")
    assert village["elapsed_s"] > 5 * 3600 and village["clock_s"] == START_22 + village["elapsed_s"] - 3600  # past the change
    assert village["cutoff_margin_s"] == CUTOFF_0430_AUTUMN - village["elapsed_s"] // 60 * 60
    assert data["points"][-1]["clock_s"] == 86400 + 5 * 3600 and data["plan_total_s"] == 8 * 3600
    assert f'data-clock="{village["clock_s"]}"' in r.text and f'data-clock="{START_22 + village["elapsed_s"]}"' not in r.text
    assert ">05:00<" in r.text
    # the saved route reads the zone its weather carries: the printed band and the phone's copy agree with the table
    r = await as_user.post("/api/simulator/routes", data={
        "course_json": course.model_dump_json(), "checkpoints_json": json.dumps(_cps()), "name": "Nuit d'octobre",
        "target_time_s": 8 * 3600, "race_date": AUTUMN, "start_hour": 22, "start_minute": 0, "sport_type": "trail",
    })
    assert r.status_code == 200, r.text
    route = await db_session.get(Route, r.json()["id"])
    route.weather_json = {"date": AUTUMN, "tz": "Europe/Paris"}
    await db_session.flush()
    plan = (await as_user.get(f"/api/simulator/routes/{route.id}/offline")).json()
    village = next(p for p in plan["points"] if p["name"] == "Village")
    planned_min = CUTOFF_0430_AUTUMN - village["cutoff_margin_s"]  # the passage, to the minute, from the margin
    assert 5 * 3600 < planned_min < 7 * 3600 and village["clock_s"] // 60 * 60 == START_22 + planned_min - 3600
    assert plan["points"][-1]["clock_s"] == 86400 + 5 * 3600 and plan["start_s"] == START_22
    csv = (await as_user.get(f"/api/simulator/routes/{route.id}/pace-export?format=csv")).text
    assert ";05:00;8h00;" in csv and ";06:00;8h00;" not in csv
