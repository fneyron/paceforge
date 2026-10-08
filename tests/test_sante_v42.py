"""Santé v4.2, « une approche scientifique » (owner, 2026-10-08: « Base-toi sur plus d'études officielles pour
optimiser »): each rule as V42_BRIEF.md sets it from the three research reports, with exact values from the
formulas.

- §A the score: weights VFC 25 · FC de nuit 25 · Sommeil 30 · Charge 20, Charge only in a recovery window;
  HR and HRV read together (a low VFC alone caps nothing, the joint cap 69; Buchheit 2014, Table 2); the FC de
  nuit scale 100 / 40 / 0 at + 2 / + 5 / + 8 bpm (Alavi 2022; Bosquet 2008); the bands' SDs shrunk towards a
  prior (Kellmann 2018); research_recovery.md §3.4's example days.
"""
import math
from datetime import date, timedelta

import pytest

from app.services import nights as nt
from app.services import sante_score as sc
from app.services import sante_today as td
from tests.test_nights import night_rows
from tests.test_sante_score import _day, _rich, _runs, _session

D = date(2026, 10, 8)
HRV_BAND = {"center": 70.0, "sd": 0.1, "provisional": False, "lo": 70 * math.exp(-0.05), "hi": 70 * math.exp(0.05)}
HR_BAND = {"center": 45.0, "sd": 1.8, "provisional": False, "lo": 42.0, "hi": 48.0}


class _Ef:  # what a window's sentence reads of its effort
    name, session_id = "Trail des Crêtes", 1


def _example(z=None, hr=None, tst=None, window=None) -> dict:
    """One day of research_recovery.md §3.4 from its numbers: the 7-night HRV `z` (ln units over the band's SD),
    the 7-night nightly HR over its median (bpm), the 24-h sleep (minutes), an effort window."""
    stats = {k: {"value": None, "normal": None, "status": None} for k in ("hrv", "hr")}
    if z is not None:
        v = 70 * math.exp(0.1 * z)
        stats["hrv"] = {"value": v, "normal": HRV_BAND, "status": nt.status(v, HRV_BAND)}
    if hr is not None:
        v = 45.0 + hr
        stats["hr"] = {"value": v, "normal": HR_BAND, "status": nt.status(v, HR_BAND)}
    day = {"day": D, "stats": stats, "tst24": tst, "sleep": {"mean7": None, "usual": None, "prov": False},
           "window": window, "alert": None}
    score = sc.score_of(day)
    return {"score": score, "state": td.state(score, day), "day": day}


def _rows(x: dict) -> dict:
    return {r["key"]: r for r in sc.contributors(x["score"], x["state"])["rows"]}


# ── §A: research_recovery.md §3.4, the example days ─────────────────────────

def test_the_example_days_of_the_research_report():
    """§3.4: what v4 said and what v4.2 says, from the formulas (no Charge outside a window)."""
    normal = _example(z=0, hr=0, tst=450)
    assert (normal["score"]["value"], normal["score"]["caps"]) == (100, [])
    # 2. HRV low, HR low: the « saturation » of a well-trained athlete (Buchheit 2014, Table 2) — 69 in v4 (a red
    # VFC capped it), now the mean: (25·25 + 25·100 + 30·100) / 80 = 76,6 → 77, green; the row still « basse »
    sat = _example(z=-2.0, hr=-2, tst=450)
    assert sat["score"]["raw0"] == (25 * 25 + 25 * 100 + 30 * 100) / 80 == 76.5625
    assert (sat["score"]["value"], sat["score"]["tone"], sat["score"]["caps"]) == (77, "ok", [])
    assert sat["state"]["key"] == "ok" and sat["state"]["text"] is None
    assert (_rows(sat)["hrv"]["word"], _rows(sat)["hrv"]["tone"]) == ("basse", "warn")  # never red under green
    # 3. HRV low, HR up: « accumulated fatigue » — 74 (green) in v4, now (25·50 + 25·60 + 30·80) / 80 = 64,4 → 64,
    # under the joint cap (69): orange, the sentence names both
    tired = _example(z=-1.5, hr=4, tst=390)
    assert tired["score"]["raw0"] == (25 * 50 + 25 * 60 + 30 * 80) / 80 == 64.375
    assert (tired["score"]["value"], tired["score"]["tone"], tired["score"]["caps"]) == (64, "warn", [])
    assert (tired["state"]["key"], tired["state"]["text"]) == ("joint", "VFC basse et FC de nuit haute sur 7 nuits.")
    assert [(r["word"], r["tone"]) for r in _rows(tired).values()] == [("basse", "warn"), ("haute", "warn"),
                                                                     ("un peu court", "accent")]
    # 4. HR + 3 alone: 97 in v4, now (25·100 + 25·80 + 30·100) / 80 = 93,75 → 94
    plus3 = _example(z=0, hr=3, tst=420)
    assert (plus3["score"]["raw0"], plus3["score"]["value"]) == (93.75, 94)
    assert _rows(plus3)["hr"]["word"] == "un peu haute" and _rows(plus3)["hr"]["tone"] == "ok"
    # 5. HR + 6 for a week, HRV in its band: 88 (green) in v4, now FC de nuit 26,7 is red: capped at 69
    plus6 = _example(z=-0.3, hr=6, tst=420)
    assert plus6["score"]["raw0"] == pytest.approx((25 * 100 + 25 * 80 / 3 + 30 * 100) / 80)  # 77,1
    assert (plus6["score"]["value"], plus6["score"]["caps"]) == (69, ["red"])
    assert (plus6["state"]["key"], plus6["state"]["text"]) == ("hr", "FC de nuit haute sur 7 nuits.")
    assert (_rows(plus6)["hr"]["word"], _rows(plus6)["hr"]["tone"]) == ("nettement haute", "danger")
    # 6. a short night (5h30), the rest normal: 65 both times
    short = _example(z=0, hr=0, tst=330)
    assert (short["score"]["value"], short["score"]["caps"], short["state"]["text"]) == (65, ["short"],
                                                                                       "Nuit courte.")
    # 7. the day after a 4-h trail (a Longue: cap 65, Charge 50): 65 both times
    trail = _example(z=0, hr=0, tst=450, window={"cap": 65, "load": 50, "ago": 1, "effort": _Ef()})
    assert trail["score"]["raw0"] == (25 * 100 + 25 * 100 + 30 * 100 + 20 * 50) / 100 == 90
    assert (trail["score"]["value"], trail["score"]["caps"]) == (65, ["effort"])
    # 8. sleep only, 8 h: 80 both times
    alone = _example(tst=480)
    assert (alone["score"]["value"], alone["score"]["caps"]) == (80, ["no_heart"])


def test_the_joint_cap_vfc_under_its_band_and_fc_up_3_bpm():
    """VFC under its band (z < −0.5) and FC de nuit ≥ median + 3 bpm → 69 (H; Buchheit 2014, Table 2: rMSSD down
    with HR up, outside a taper, « accumulated fatigue »). Mild on each row (VFC 90, FC 80) the mean is 90,6:
    the cap binds, both rows read « basse » / « haute » in orange, as the sentence says."""
    mild = _example(z=-0.7, hr=3, tst=450)
    assert mild["score"]["raw0"] == (25 * 90 + 25 * 80 + 30 * 100) / 80 == 90.625
    assert (mild["score"]["value"], mild["score"]["caps"], mild["score"]["tone"]) == (69, ["joint"], "warn")
    assert (mild["state"]["key"], mild["state"]["text"]) == ("joint", "VFC basse et FC de nuit haute sur 7 nuits.")
    rows = _rows(mild)
    assert (rows["hrv"]["word"], rows["hrv"]["tone"], rows["hrv"]["sub"]) == ("basse", "warn", 90)
    assert (rows["hr"]["word"], rows["hr"]["tone"], rows["hr"]["sub"]) == ("haute", "warn", 80)
    # just inside the band (z −0.5) or FC + 2,9: no joint cap
    assert _example(z=-0.5, hr=3, tst=450)["score"]["caps"] == []
    assert _example(z=-0.7, hr=2.9, tst=450)["score"]["caps"] == []
    # a past day's readout says it on one line at 358 px
    assert td.short_text(mild["state"], mild["day"]) == "VFC basse, FC de nuit haute"
    assert len("mer. 30 sept. · " + td.SHORT["joint"]) <= 44
    # an effort's window that binds lower names the effort; the joint pattern still caps
    w = {"cap": 45, "load": 30, "ago": 2, "effort": _Ef()}  # Charge 30 is a red component too
    both = _example(z=-0.7, hr=3, tst=450, window=w)
    assert both["score"]["caps"] == ["effort", "joint", "red"] and both["state"]["key"] == "effort"
    assert both["state"]["text"] == "Grosse sortie il y a 2 jours : Trail des Crêtes."


def test_a_low_vfc_counts_as_red_only_with_the_nightly_hr_over_2_bpm():
    """« VFC counts as a red contributor only when FC de nuit is measured and its 7-night mean is > + 2 bpm » (§A2):
    FC de nuit, Sommeil and Charge always count. A VFC under 40 with no FC de nuit caps nothing either."""
    for hr, red in ((None, False), (-2, False), (2, False), (2.5, True)):
        x = _example(z=-2.0, hr=hr, tst=450)
        vfc = next(p for p in x["score"]["parts"] if p["key"] == "hrv")
        assert vfc["red"] is red and ("red" in x["score"]["caps"]) is red, hr
        assert _rows(x)["hrv"]["tone"] == ("danger" if red else "warn"), hr
    # no FC de nuit: (25·25 + 30·100) / 55 = 65,9 → 66; VFC is a heart signal, so no 80 cap either
    alone = _example(z=-2.0, tst=450)
    assert alone["score"]["raw0"] == pytest.approx((25 * 25 + 30 * 100) / 55) and alone["score"]["value"] == 66
    assert alone["score"]["caps"] == []
    # a sleep sub-score under 40 (5 h → 30) always counts: (25·100 + 25·100 + 30·30) / 80 = 73,75, capped at 65
    sleepy = _example(z=0, hr=0, tst=300)
    assert sleepy["score"]["raw0"] == 73.75 and sleepy["score"]["caps"] == ["short", "red"]
    assert next(p for p in sleepy["score"]["parts"] if p["key"] == "sleep")["red"]


def test_the_nightly_hr_scale():
    """100 at ≤ + 2 bpm, 40 at + 5, 0 at + 8, linear between (H; Alavi 2022: + 4 bpm on 2 nights; Bosquet 2008:
    ≈ + 4,5 bpm in a short overload; Altini & Plews 2021: ≈ + 6 % when sick). Its words follow its bar's band."""
    subs = {d: sc.hr_sub(45 + d, HR_BAND) for d in (-3, 0, 2, 2.5, 3, 3.5, 4, 5, 6, 7, 8, 9)}
    assert subs == pytest.approx({-3: 100, 0: 100, 2: 100, 2.5: 90, 3: 80, 3.5: 70, 4: 60, 5: 40, 6: 80 / 3,
                                  7: 40 / 3, 8: 0, 9: 0})
    words = {d: _rows(_example(z=0, hr=d, tst=450))["hr"]["word"] for d in (0, 2.5, 3, 4, 5.5)}
    assert words == {0: "dans ta normale", 2.5: "dans ta normale", 3: "un peu haute", 4: "haute",
                     5.5: "nettement haute"}


def test_charge_is_no_component_outside_a_window():
    """Outside a recovery window: no Charge row in the Contributeurs and never « Pas encore dans le score :
    … Charge récente » (§A1); the Charge ring still shows the week (sante._top)."""
    day = _day(_rich(), _runs(n=12))
    c = sc.contributors(day["score"], day["state"])
    assert [r["key"] for r in c["rows"]] == ["hrv", "hr", "sleep"] and c["absent"] is None
    young = _day(night_rows(range(0, 3), today=D), _runs(n=12))  # no band yet: VFC and FC missing, not Charge
    assert sc.contributors(young["score"], young["state"])["absent"] == "Pas encore dans le score : VFC, FC de nuit."
    big = _day(_rich(), _runs(n=12) + [_session(D - timedelta(days=1), 200, sid=9)])
    assert [r["key"] for r in sc.contributors(big["score"], big["state"])["rows"]] == ["hrv", "hr", "sleep", "load"]


# ── §A4: the bands' SDs shrunk towards a prior ──────────────────────────────

def test_the_band_sd_is_shrunk_towards_a_prior():
    """SD = √((n·s² + 7·σ0²) / (n + 7)), σ0 = 0,10 ln units for HRV (it replaces the 0,05 floor), 4 % of the
    median for the robust HR SD behind the alert line (H; Kellmann 2018's Bayesian individualisation; Mishica
    2022's night-to-night CVs). It matters at 7–13 nights; at 60 nights it is nearly the athlete's own."""
    assert nt.shrunk_sd(0.2, 7, 0.1) == pytest.approx(math.sqrt((7 * 0.04 + 7 * 0.01) / 14))  # 0,158
    assert nt.shrunk_sd(0.0, 7, 0.1) == pytest.approx(0.1 / math.sqrt(2))
    assert not hasattr(nt, "HRV_SD_FLOOR")
    # 7 nights of the same HRV: the band is not a hairline (it was the 0,05 floor), ± 0,5 × 0,0707
    steady = nt.build_nights(night_rows(range(1, 8), today=D, hrv=60.0), D)
    b = nt.band(steady, "hrv", D)
    assert b["provisional"] and b["sd"] == pytest.approx(0.1 * math.sqrt(7 / 14))
    assert (b["lo"], b["hi"]) == pytest.approx((60 * math.exp(-0.5 * b["sd"]), 60 * math.exp(0.5 * b["sd"])))
    # nightly HR: 14 nights at 50 ± 3 bpm (MAD 3, robust SD 4,45): σ0 = 2 bpm, SD √((14·4,45² + 7·2²)/21) = 3,81;
    # the alert line 50 + max(2 × 3,81, 5) = 57,6 (it was 58,9 unshrunk)
    noisy = nt.build_nights(night_rows(range(1, 15), today=D, hr=lambda k: 50.0 + (3 if k % 2 else -3)), D)
    h = nt.band(noisy, "hr", D)
    s = 1.4826 * 3
    assert h["n"] == 14 and h["sd"] == pytest.approx(math.sqrt((14 * s * s + 7 * 4) / 21))
    assert h["alert"] == pytest.approx(50 + 2 * h["sd"]) and round(h["alert"], 1) == 57.6
    assert (h["lo"], h["hi"]) == (47.0, 53.0)  # the band itself: median ± 3 bpm, unchanged
    # a very steady HR: the 5-bpm floor holds
    flat = nt.build_nights(night_rows(range(1, 15), today=D, hr=45.0), D)
    assert nt.band(flat, "hr", D)["alert"] == 50
