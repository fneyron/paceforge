"""Deterministic race-plan services: checkpoints, pacing guide, scenarios,
nutrition per leg, race calibration, pace export, debrief, reference finisher."""

import math

from app.schemas.simulator import CourseProfile, CourseSegment
from app.services import checkpoints as cpsvc
from app.services.debrief import leg_debrief
from app.services.nutrition import compute_plan
from app.services.pace_export import (
    build_pace_csv,
    build_pace_gpx,
    build_pace_tcx,
    elapsed_at,
    time_curve,
)
from app.services.pacing_guide import build_pacing_guide
from app.services.race_calibration import (
    apply_race_calibration,
    effort_km,
    fit_effort_model,
    predict_total_s,
)
from app.services.race_simulator import build_scenarios, compute_passage_times
from app.services.reference import (
    align_reference,
    compare_to_plan,
    parse_pasted_splits,
    parse_strava_activity_id,
)

# ── fixtures: a synthetic 30 km course — flat, a 6 km climb with a 22 % km, a descent ──

def _course() -> CourseProfile:
    grades = [0, 1, -1, 0, 6, 8, 10, 22, 24, 6, -8, -10, -9, -7, -2, 0, 1, 0, -1, 0, 5, 7, 6, -6, -8, -4, 0, 1, 0, -1]
    segs, elev, pts, coords = [], 200.0, [], []
    for i, g in enumerate(grades):
        start_e = elev
        elev = elev + g * 10  # 1 km at g % = g*10 m
        segs.append(CourseSegment(
            index=i, start_km=float(i), end_km=float(i + 1), distance_m=1000.0,
            elevation_gain=max(0.0, elev - start_e), elevation_loss=max(0.0, start_e - elev),
            avg_gradient_pct=float(g), min_elevation=min(start_e, elev), max_elevation=max(start_e, elev),
        ))
        for k in range(4):  # 250 m resolution profile
            km = i + k / 4
            e = start_e + (elev - start_e) * k / 4
            pts.append({"distance_km": km, "elevation": e})
            coords.append([45.0 + km * 0.005, 6.0, km, e])
    pts.append({"distance_km": 30.0, "elevation": elev})
    coords.append([45.0 + 30 * 0.005, 6.0, 30.0, elev])
    course = CourseProfile(
        name="Test", total_distance_km=30.0,
        total_elevation_gain=sum(s.elevation_gain for s in segs),
        total_elevation_loss=sum(s.elevation_loss for s in segs),
        segments=segs, elevation_points=pts, route_coords=coords,
    )
    # a crude "prediction": 6 min/km flat, slower uphill, faster downhill
    cum = 0.0
    for s in course.segments:
        factor = 1 + max(0.0, s.avg_gradient_pct) * 0.12 - max(0.0, -s.avg_gradient_pct) * 0.03
        s.base_time_s = 360 * factor
        s.predicted_time_s = s.base_time_s * 1.05
        s.predicted_pace_s_per_km = s.predicted_time_s
        cum += s.predicted_time_s
        s.cumulative_time_s = cum
    course.predicted_total_time_s = int(cum)
    return course


CPS = [
    {"name": "Eau 1", "distance_km": 6.0, "elevation": 240, "kind": "water", "crew": False, "drop_bag": False, "cutoff_clock": None},
    {"name": "Col", "distance_km": 10.0, "elevation": 700, "kind": "full", "crew": True, "drop_bag": True, "cutoff_clock": "10:30"},
    {"name": "Village", "distance_km": 20.0, "elevation": 400, "kind": "full", "crew": False, "drop_bag": False, "cutoff_clock": "13:00"},
]


def _sections(target=None, start_hour=6, stop_min=0):
    course = _course()
    aid = cpsvc.RESUPPLY_KINDS and {cp["distance_km"] for cp in CPS if cp["kind"] != "none"}
    secs = compute_passage_times(course, CPS, target, 1.0, start_hour, 0, None, stop_s_per_aid=stop_min * 60, aid_kms=aid)
    return course, cpsvc.annotate_cutoffs(secs, CPS, start_hour * 3600)


# ── 5. checkpoint metadata ──

def test_normalize_checkpoint_rejects_bad_values():
    cp = cpsvc.normalize_checkpoint({"name": "X", "distance_km": "12.5", "kind": "buffet", "crew": 1, "cutoff_clock": "25:99"})
    assert cp["kind"] == "none" and cp["crew"] is True and cp["cutoff_clock"] is None and cp["distance_km"] == 12.5


def test_cutoff_rolls_over_midnight():
    # race starts 21:00, cutoff 04:30 → next day, 7h30 after the start
    assert cpsvc.cutoff_elapsed_s("04:30", 21 * 3600) == 7 * 3600 + 30 * 60
    # cutoff 23:00 the SECOND night of a 100-miler: plausible passage at 30 h
    assert cpsvc.cutoff_elapsed_s("23:00", 21 * 3600, min_elapsed_s=30 * 3600) == 26 * 3600


def test_autonomy_between_full_stations_only():
    course, secs = _sections()
    legs = cpsvc.autonomy_legs(CPS, course.total_distance_km, secs)
    # water at km 6 does not break a leg: Départ → Col (10 km), Col → Village, Village → Arrivée
    assert [(l["from_name"], l["to_name"], l["km"]) for l in legs] == [
        ("Départ", "Col", 10.0), ("Col", "Village", 10.0), ("Village", "Arrivée", 10.0)]
    assert all(l["time_s"] and l["time_s"] > 0 for l in legs)


def test_cutoff_margin_annotated_on_sections():
    _, secs = _sections(target=5 * 3600)  # 5h plan from 06:00 → Col well before 10:30
    col = next(s for s in secs if s["end_name"] == "Col")
    assert col["cutoff_elapsed_s"] == 4 * 3600 + 30 * 60
    assert col["cutoff_margin_s"] > 0 and col["kind"] == "full" and col["drop_bag"] is True


# ── 4. pacing guide ──

def test_pacing_guide_blocks_and_stairs_alert():
    course = _course()
    g = build_pacing_guide(course, 4 * 3600, hr_cap_climb=140, hr_cap_flat=136, hr_release_descent=130, walk_grade=18)
    classes = [b["cls"] for b in g["blocks"]]
    assert "stairs" in classes and "climb" in classes and "descent" in classes and "flat" in classes
    stairs = next(b for b in g["blocks"] if b["cls"] == "stairs")
    assert stairs["start_km"] == 7.0 and stairs["end_km"] == 9.0
    assert "marche" in stairs["instruction"].lower()
    assert g["alerts"] and g["alerts"][0]["start_km"] == 7.0 and g["alerts"][0]["max_grade"] >= 22
    climb = next(b for b in g["blocks"] if b["cls"] == "climb")
    assert climb["vam_m_per_h"] and climb["hr_cap"] == 135  # early phase: cap − 5
    last = g["blocks"][-1]
    assert last["hr_free"] and last["hr_cap"] is None
    # the plan's block times add up to the target
    assert abs(sum(b["time_s"] for b in g["blocks"]) - 4 * 3600) < 5


def test_pacing_guide_without_hr_data():
    g = build_pacing_guide(_course(), None)
    assert all(b["hr_cap"] is None for b in g["blocks"]) and g["blocks"]


# ── 6. scenarios ──

def test_scenarios_columns_and_switch_rule():
    _, secs = _sections(target=5 * 3600, stop_min=3)
    sc = build_scenarios(secs, 6 * 3600, 5 * 3600, fast_pct=5, safe_pct=10, total_distance_km=30)
    assert sc["basis"] == "target"
    fin = sc["rows"][-1]
    assert fin["fast_s"] < fin["target_s"] < fin["safe_s"]
    # stops are the same in all three columns: only the MOVING budget
    # (target − 3 stops × 3 min) is scaled
    moving = 5 * 3600 - 3 * 180
    assert abs((sc["totals"]["safe_s"] - sc["totals"]["target_s"]) - 0.10 * moving) < 5
    assert abs((sc["totals"]["target_s"] - sc["totals"]["fast_s"]) - 0.05 * moving) < 5
    assert abs(sc["totals"]["target_s"] - 5 * 3600) < 5
    # switch defaults to the CP nearest 60 % (km 18 → Village at km 20)
    assert sc["switch"]["name"] == "Village"
    # cutoffs are checked against the safety column
    col = next(r for r in sc["rows"] if r["name"] == "Col")
    assert col["safe_ok"] is True and "cutoff_clock_s" in col


def test_scenarios_flag_cutoff_breach():
    _, secs = _sections(target=12 * 3600)  # 12h plan → Village (13:00 cutoff, 7h after start) is breached by +10 %
    sc = build_scenarios(secs, 6 * 3600, 12 * 3600)
    village = next(r for r in sc["rows"] if r["name"] == "Village")
    assert village["safe_s"] > village["cutoff_clock_s"] and village["safe_ok"] is False
    assert any(r["name"] == "Village" for r in sc["cutoff_breach"])


# ── 3. nutrition per leg ──

PRODUCTS = {
    1: {"id": 1, "name": "Gel", "kind": "gel", "carbs_g": 25, "sodium_mg": 50, "caffeine_mg": 0, "volume_ml": None, "kcal": 100},
    2: {"id": 2, "name": "Gel caféiné", "kind": "gel", "carbs_g": 25, "sodium_mg": 0, "caffeine_mg": 50, "volume_ml": None, "kcal": 100},
    3: {"id": 3, "name": "Sel", "kind": "salt", "carbs_g": 0, "sodium_mg": 300, "caffeine_mg": 0, "volume_ml": None, "kcal": 0},
}


def test_nutrition_real_rates_packing_and_caffeine():
    _, secs = _sections(target=5 * 3600, start_hour=21)  # night start → dawn falls inside a 5 h race? (21:00 + 5h = 02:00: no)
    targets = {"carbs_g_per_h": 75, "fluid_ml_per_h": 500, "sodium_mg_per_h": 400}
    items = [{"product_id": 1, "per_hour": 3}, {"product_id": 3, "per_hour": 1}]
    plan = compute_plan(
        5 * 3600, targets, items, PRODUCTS, secs, flask_capacity_ml=1000, refill_kms={10.0, 20.0},
        resupply_points=[{"km": 10.0, "name": "Col"}], caffeine={"enabled": True, "from_h": 1, "every_h": 2, "dose_mg": 50, "boost_dawn": True},
        start_offset_s=21 * 3600, weight_kg=70,
    )
    legs = plan["schedule"]
    assert len(legs) == 4  # Eau 1, Col, Village, Arrivée
    # whole units → real g/h on a leg is ≥ the target (ceil), never silently under
    assert all(l["carbs_real_per_h"] >= 75 for l in legs)
    assert all(l["carbs_status"] in ("ok", "over") for l in legs)
    assert legs[0]["night"] is True
    # packing: start bag until the Col drop bag, then the drop bag until the finish
    assert [p["at"] for p in plan["packing"]] == ["Départ", "Col"]
    assert plan["packing"][0]["until"] == "Col" and plan["packing"][1]["until"] == "Arrivée"
    total_units_packed = sum(u["units"] for p in plan["packing"] for u in p["units"] if u["name"] == "Gel")
    assert total_units_packed == next(l["total_units"] for l in plan["lines"] if l["name"] == "Gel")
    # caffeine: doses at 1h, 3h (5h is inside the last 20 min guard? 5h == duration → excluded)
    cf = plan["caffeine"]
    assert [d["elapsed_s"] for d in cf["doses"]] == [3600, 3 * 3600]
    assert cf["total_mg"] == 100 and cf["over"] is False and cf["max_mg"] == 400


def test_caffeine_dawn_boost_and_cap():
    _, secs = _sections(target=10 * 3600, start_hour=21)
    targets = {"carbs_g_per_h": 60, "fluid_ml_per_h": 500, "sodium_mg_per_h": 400}
    plan = compute_plan(10 * 3600, targets, [{"product_id": 2, "per_hour": 2}], PRODUCTS, secs,
                        caffeine={"enabled": True, "from_h": 3, "every_h": 2, "dose_mg": 100, "boost_dawn": True},
                        start_offset_s=21 * 3600, weight_kg=60)
    cf = plan["caffeine"]
    dawn = [d for d in cf["doses"] if d["label"].startswith("dose pleine")]
    assert len(dawn) == 1 and dawn[0]["mg"] == 200 and 5 <= (dawn[0]["clock_s"] % 86400) / 3600 < 7.5
    assert cf["max_mg"] == 360 and cf["over"] is True
    assert cf["doses"][0]["product"] == "Gel caféiné"


# ── 1. race calibration ──

def test_effort_model_fit_and_prediction():
    # two races: 90 km/6000 m in 16 h (ekm 150 → 9.4/h) and 100 km/3000 m in 10 h (ekm 130 → 13/h)
    pts = [{"hours": 16, "ekm_h": 150 / 16, "weight": 1}, {"hours": 10, "ekm_h": 13.0, "weight": 1}]
    m = fit_effort_model(pts)
    assert m["fitted"] and 0.02 <= m["b"] <= 0.20
    # a 148 km / 5000 m course (ekm 198) lands between the two rates' implied durations
    t = predict_total_s(m, effort_km(148, 5000)) / 3600
    assert 15 < t < 24
    single = fit_effort_model(pts[:1])
    assert single["n"] == 1 and single["b"] == 0.08


def test_apply_race_calibration_relevels_keeping_shape():
    course = _course()
    before = [s.predicted_time_s for s in course.segments]
    total_before = course.predicted_total_time_s
    model = {"a": 12.0, "b": 0.08}  # 12 ekm/h at 1 h → a fast runner: total goes DOWN
    info = apply_race_calibration(course, model)
    assert info and info["total_s"] < total_before
    ratios = [s.predicted_time_s / b for s, b in zip(course.segments, before, strict=False)]
    assert max(ratios) - min(ratios) < 1e-3  # uniform re-levelling (segment times are rounded to 0.1 s)
    assert abs(course.segments[-1].cumulative_time_s - course.predicted_total_time_s) < 1.5


# ── 7. pace export ──

def test_time_curve_and_exports():
    course, secs = _sections(target=5 * 3600, stop_min=3)
    segs = [g.model_dump() for g in course.segments]
    curve = time_curve(segs, secs, True)
    ts = [t for _, t in curve]
    # the target INCLUDES the 3 × 3 min aid stops: the curve ends on the target
    assert ts == sorted(ts) and abs(ts[-1] - 5 * 3600) < 2
    # …and the stops show as time jumps at the aid-station kms
    assert any(k0 == k1 and t1 - t0 >= 179 for (k0, t0), (k1, t1) in zip(curve, curve[1:], strict=False))
    assert elapsed_at(curve, 0) == 0 and elapsed_at(curve, 15) > elapsed_at(curve, 10)
    gpx = build_pace_gpx("Test", course.route_coords, segs, secs, True, "2026-10-02", 6 * 3600)
    assert gpx.count("<wpt") == 4 and "<time>" in gpx and "CP2 " in gpx and "ARR " in gpx
    tcx = build_pace_tcx("Test", course.route_coords, segs, secs, True, "2026-10-02", 6 * 3600)
    assert tcx.count("<CoursePoint>") == 4 and "<PointType>Food</PointType>" in tcx
    csv_txt = build_pace_csv("Test", secs, True, 6 * 3600)
    assert csv_txt.splitlines()[0].startswith("point;km") and "Village" in csv_txt


# ── 8. debrief ──

def _splits(total_km, moving_per_km, stops_at_km=None, hr=140):
    stops_at_km = stops_at_km or {}
    out = []
    for k in range(int(total_km)):
        m = moving_per_km(k)
        out.append({"distance": 1000, "moving_time": m, "elapsed_time": m + stops_at_km.get(k + 1, 0), "average_heartrate": hr(k) if callable(hr) else hr})
    return out


def test_debrief_blames_stops_and_fast_start():
    course, secs = _sections(target=5 * 3600, stop_min=2)
    per_km = {s.index: (5 * 3600) * (s.base_time_s / sum(x.base_time_s for x in course.segments)) for s in course.segments}
    # ran exactly the plan, but a 12-min stop at the Col (km 10)
    splits = _splits(30, lambda k: per_km[k], stops_at_km={10: 12 * 60})
    d = leg_debrief(splits, secs, 30.0, use_target=True, stop_s_per_aid=120, hr_cap=140)
    col = next(l for l in d["legs"] if l["to_name"] == "Col")
    assert col["tag"] == "stops" and "arrêts" in col["reason"]
    # fast start with HR over the ceiling, then a fade
    splits2 = _splits(30, lambda k: per_km[k] * (0.85 if k < 10 else 1.20), hr=lambda k: 152 if k < 10 else 128)
    d2 = leg_debrief(splits2, secs, 30.0, use_target=True, stop_s_per_aid=120, hr_cap=140)
    first = d2["legs"][0]
    assert first["tag"] == "over" and first["hr_over"]
    assert d2["summary"]["fade"] > 0.08 and "début" in d2["summary"]["verdict"].lower()
    assert d2["summary"]["lost"] and d2["summary"]["lost"][0]["delta_s"] > 0


# ── 2. reference finisher ──

def test_parse_pasted_splits_and_strava_url():
    txt = "Ravito Seogwipo\tkm 32\t3:41:05\nHallasan sommet 97 km 12:08:30\nArrivée 148km 18:52:10 | 15:52\n"
    pts = parse_pasted_splits(txt)
    assert [p["km"] for p in pts] == [32.0, 97.0, 148.0]
    assert pts[0]["time_s"] == 3 * 3600 + 41 * 60 + 5 and pts[0]["name"] == "Ravito Seogwipo"
    assert pts[2]["time_s"] == 18 * 3600 + 52 * 60 + 10  # the clock time (15:52) is ignored
    assert parse_strava_activity_id("https://www.strava.com/activities/12345678901/overview") == 12345678901
    assert parse_strava_activity_id("no url") is None


def test_align_and_compare_reference():
    course, secs = _sections(target=5 * 3600)
    # reference finisher: 10 min/km cumulative points every 5 km, recorded as 30.6 km
    ref_pts = [{"name": "", "km": 5.1 * i, "time_s": 600 * 5 * i} for i in range(1, 7)]
    aligned = align_reference(ref_pts, CPS, 30.0, ref_total_km=30.6)
    assert [a["matched_by"] for a in aligned] == ["km", "km", "km"]
    assert abs(aligned[1]["time_s"] - 100 * 60) < 60  # Col at km 10 ≈ 100 min
    cmp = compare_to_plan(aligned, secs, True, ref_total_s=300 * 60, plan_total_s=5 * 3600)
    assert cmp["rows"][-1]["name"] == "Arrivée" and cmp["rows"][-1]["delta_s"] == 0
    assert len(cmp["faster"]) + len(cmp["slower"]) >= 1
    # names only → matched by name, then by order
    named = [{"name": "eau", "km": None, "time_s": 3000}, {"name": "col", "km": None, "time_s": 6000}, {"name": "xx", "km": None, "time_s": 9000}]
    al2 = align_reference(named, CPS, 30.0)
    assert al2[0]["matched_by"] == "nom" and al2[1]["matched_by"] == "nom" and al2[2]["matched_by"] == "ordre"


def test_effort_km():
    assert math.isclose(effort_km(148, 5000), 198.0)


# ── bike: passage sections at checkpoints, objective → power, FTP from streams ──

def test_bike_passage_sections_and_power_solver():
    from app.services.cycling_simulator import build_bike_passage_sections, predict_cycling_course, solve_power_for_time

    course = _course()

    def predict(w):
        return predict_cycling_course(course, target_power_watts=w, rider_weight_kg=70, bike_weight_kg=8)

    cycling = predict(200)
    secs = build_bike_passage_sections(cycling, CPS, 8 * 3600, target_time_s=None, stop_s_per_aid=120, aid_kms={6.0, 10.0, 20.0})
    assert [s["end_name"] for s in secs] == ["Eau 1", "Col", "Village", "Arrivee"]
    assert abs(secs[-1]["cumulative_time_s"] - cycling.predicted_total_time_s) < 2
    assert abs(secs[-1]["clock_time_s"] - (8 * 3600 + secs[-1]["cumulative_time_s"] + 3 * 120)) < 2  # 3 aid stops on the clock
    # with an objective every leg scales by the same factor
    secs_t = build_bike_passage_sections(cycling, CPS, 8 * 3600, target_time_s=int(cycling.predicted_total_time_s * 1.2))
    ratios = [s["adjusted_time_s"] / s["predicted_time_s"] for s in secs_t if s["predicted_time_s"]]
    assert max(ratios) - min(ratios) < 0.01
    # objective → required power: faster objective needs more watts, and lands on the objective
    target = int(cycling.predicted_total_time_s * 0.9)
    w = solve_power_for_time(predict, target)
    assert w and w > 200 and abs(predict(w).predicted_total_time_s - target) < 60
    assert solve_power_for_time(predict, 60) is None  # out of range


def test_ftp_from_streams():
    from app.services.power_calculator import ftp_from_mean_max, mean_max_power

    # 40 min ride: 20 min at 300 W then 20 min at 200 W, 1 Hz samples with a pause gap
    times = list(range(0, 1200)) + list(range(1500, 2700))
    watts = [300] * 1200 + [200] * 1200
    p20 = mean_max_power({"time": times, "watts": watts}, 1200)
    assert p20 == 300
    p5 = mean_max_power({"time": times, "watts": watts}, 300)
    assert p5 == 300
    assert mean_max_power({"time": times, "watts": watts}, 3600) is None  # ride shorter than the window (pause excluded)
    ftp, method = ftp_from_mean_max(p5=330, p20=300, p60=None)
    assert ftp == 290 and "critique" in method  # CP (290) beats 95 % of P20 (285)
    ftp2, method2 = ftp_from_mean_max(p5=None, p20=None, p60=270)
    assert ftp2 == 270 and method2 == "60 min"
    assert ftp_from_mean_max(None, None, None) == (None, "")
