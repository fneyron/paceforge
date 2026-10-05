"""The race dataset keeps solo running races only, and the public model figures stay readable."""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "race_data"))

import dataset  # noqa: E402


def test_running_filter_drops_bikes_relays_teams_and_stage_races():
    keep = [("UTMB®", "utmb.json.gz", "utmb_2025"), ("Jeju100M", "100m.json.gz", "transjeju_2026"),
            ("Marathon de Deauville individuel", "mid2019.json.gz", "mid-breizhchrono_2019"),
            ("100km Challenge", "100km.json.gz", "london2brighton_2022"), ("MB55", "MB55.json.gz", "chamonix_2024")]
    drop = [("Double 8 VTT AE COMPLET", "d8VAE4.json.gz", "super-huit_2023"), ("Triathlon L", "L.json.gz", "frenchman-breizhchrono_2019"),
            ("SAINTÉLYON RELAIS 2", "rel2.json.gz", "saintelyon_2024"), ("Trail du Barlatay en duo", "duotrai.json.gz", "barla_2024"),
            ("MDS 100", "hmds100.json.gz", "mdsjordan_2024"), ("Full 100K - 2Days", "2day.json.gz", "peakdistrict_2023"),
            ("PTL®", "ptl.json.gz", "utmb_2024"), ("Backyard Ultra Barjots", "Backyar.json.gz", "bub24_2024"),
            ("MB ULTRA SOMFY 100", "MB100.json.gz", "mbrace_2024"), ("L'Originale - Cyclo 100 km", "L.json.gz", "megevemontblanccycling_2022"),
            ("RELEVOS GRAN VUELTA VALLE DEL GENAL", "relevos.json.gz", "granvueltavalledelgenal_2018"),
            ("OXFAM 100K", "100k.json.gz", "oxfamtrail_2022"), ("OTW 50K", "50k.json.gz", "oxfamtrail_2023"),
            ("3 días Ultra", "ultra.json.gz", "3diastrailibiza_2024"), ("E51 Couples", "E51cou.json.gz", "eigerultratrail_2024"),
            ("Marathon", "maratho.json.gz", "londonhike_2018")]
    assert all(dataset.is_running(*x) for x in keep)
    assert not any(dataset.is_running(*x) for x in drop)


def _race(km, dplus_cp, hours):
    return {"cps": [{"km": 0.0}, {"km": km, "km_official": km, "dplus": dplus_cp}],
            "runners": [{"time_s": h * 3600} for h in hours]}


def test_median_effort_speed_screens_out_bikes():
    assert dataset.plausible_running(_race(100, 5000, [14, 18, 22]), 5000)      # 150 effort-km in 18 h
    assert not dataset.plausible_running(_race(100, 5000, [6, 7, 8]), 5000)     # 21 effort-km/h: a bike


def test_model_stats_keep_the_published_keys():
    s = json.loads((ROOT / "app" / "data" / "model_stats.json").read_text())
    for k in ("races", "events", "finisher_results", "checkpoint_passages", "sources"):
        assert k in s["dataset"]
    for k in ("races", "finisher_results", "note"):
        assert k in s["fit"]
    for k in ("held_out_races", "held_out_events", "mean_abs_gap_min_before", "mean_abs_gap_min_after", "races_improved"):
        assert k in s["validation"]
    assert s["dataset"]["races"] == sum(s["dataset"]["sources"].values())
    assert s["validation"]["transjeju_2026"]["actual_s"] == 16 * 3600 + 53 * 60 + 17
    # the public page reads these: a race-level gap says how many finishers it covers (one
    # runner's gap lives under bib_*), and a before / after comparison is never "0 races better"
    tj = s["validation"]["transjeju_2026"]
    assert tj["runners"] > 1 and "bib_mean_abs_gap_min" in tj
    assert s["validation"]["mean_abs_gap_min_before"] != s["validation"]["mean_abs_gap_min_after"]
    assert s["validation"]["races_improved"] > 0
