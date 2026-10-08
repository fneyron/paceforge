"""Santé v4.2, « une approche scientifique » (owner, 2026-10-08: « Base-toi sur plus d'études officielles pour
optimiser »): each rule as V42_BRIEF.md sets it from the three research reports, with exact values from the
formulas.

- §A the score: weights VFC 25 · FC de nuit 25 · Sommeil 30 · Charge 20, Charge only in a recovery window;
  HR and HRV read together (a low VFC alone caps nothing, the joint cap 69; Buchheit 2014, Table 2); the FC de
  nuit scale 100 / 40 / 0 at + 2 / + 5 / + 8 bpm (Alavi 2022; Bosquet 2008); the bands' SDs shrunk towards a
  prior (Kellmann 2018); research_recovery.md §3.4's example days.
- §B the recovery windows from activities (research_efforts.md §b, « indicatives », all H): Longue D+3 (D+5
  for a race), Ultra D+13 after ≥ 24 h or a night run through; the nights after an effort by its class; the
  alert muted then re-armed; M1 an unusual climb, M3 back-to-back days, M4 low-impact sports; the owner's
  Transjeju; Activités' « après ultra ».
"""
import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import nights as nt
from app.services import sante_score as sc
from app.services import sante_today as td
from app.services import sante_training as st
from tests import test_coros  # the linked athlete's client fixture (as_user)
from tests.test_nights import night_rows
from tests.test_sante_score import _day, _history, _rich, _runs, _session, _then

as_user, no_commit = test_coros.as_user, test_coros.no_commit
D = date(2026, 10, 8)
ROOT = Path(__file__).resolve().parent.parent
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


# ── §B: recovery windows from activities (research_efforts.md §b, all H, « indicatives ») ──

def _caps(s, history=()):
    e = st.effort_of(s, history)
    return (e.kind, e.nights, e.caps, e.load, e.tail) if e else None


def test_the_classes_and_their_windows():
    """Longue (≥ 3 h, or ≥ 1 500 m D+ on foot) 65 until D+3 (was D+2), D+5 when Strava marks it a race; Très
    longue 45 to D+2, 65 to D+5; Ultra 40 to D+3, 65 to D+10, D+13 when ≥ 24 h or run through a night (01:00 →
    05:00 local); Charge 50 / 30 / 20."""
    assert _caps(_session(D, 200)) == ("long", "long", ((3, 65),), 50, False)
    assert _caps(_session(D, 200, workout_type=1)) == ("long", "long", ((5, 65),), 50, False)  # a race (Strava)
    assert _caps(_session(D, 170, dplus=1600)) == ("long", "long", ((3, 65),), 50, False)  # the legs rule
    assert _caps(_session(D, 420)) == ("very_long", "very_long", ((2, 45), (5, 65)), 30, False)
    assert _caps(_session(D, 420, workout_type=1)) == ("very_long", "very_long", ((2, 45), (5, 65)), 30, False)
    assert _caps(_session(D, 660, hour=5)) == ("ultra", "ultra", ((3, 40), (10, 65)), 20, False)  # 05:00 → 16:00
    assert _caps(_session(D, 1500, hour=5)) == ("ultra", "ultra", ((3, 40), (13, 65)), 20, True)  # ≥ 24 h, ≥ 20 h
    night = _session(D, 660, hour=20)  # 20:00 → 07:00: ran from 01:00 to 05:00
    assert _caps(night) == ("ultra", "ultra", ((3, 40), (13, 65)), 20, False) and st.through_night(
        st.local_start(night), st.local_end(night))
    assert not st.through_night(datetime(2026, 10, 8, 2), datetime(2026, 10, 8, 12))  # started after 01:00
    assert not st.through_night(datetime(2026, 10, 7, 22), datetime(2026, 10, 8, 4, 59))  # ended before 05:00
    e = st.effort_of(_session(D, 200, workout_type=1))
    assert [(st.effort_window([e], D + timedelta(days=k)) or {}).get("cap") for k in (0, 3, 5, 6)] == [65, 65, 65, None]


def test_the_owner_transjeju_window_and_nights():
    """The owner (brief §E): the Transjeju started 02/10 21:00, 16h53, ending 03/10 13:53 local → D+0 = 03/10, an
    Ultra: cap 40 to D+3 (06/10); it ran through the night, so 65 to D+13 (16/10); nights D+1 → D+4 (04 → 07/10)
    out of the band and the alert; 16h53 is under 20 h, so no D+5 → D+7."""
    s = _session(date(2026, 10, 2), 935, elapsed=1013, hour=21, offset=32400, workout_type=1, name="Transjeju 100M")
    [e] = st.efforts([s])
    assert (e.kind, e.nights, e.day, e.end, e.tail) == ("ultra", "ultra", date(2026, 10, 3),
                                                       datetime(2026, 10, 3, 13, 53), False)
    assert e.caps == ((3, 40), (13, 65)) and e.load == 20
    caps = {k: (st.effort_window([e], date(2026, 10, k)) or {}).get("cap") for k in (3, 6, 7, 16, 17)}
    assert caps == {3: 40, 6: 40, 7: 65, 16: 65, 17: None}
    assert st.effort_window([e], date(2026, 10, 8))["until"] == date(2026, 10, 16)
    rows = night_rows(range(0, 12), today=D)
    nights = nt.build_nights(rows, D)
    nt.tag_efforts(nights, [e])
    assert sorted(d for d, n in nights.items() if "ultra" in n.tags) == [date(2026, 10, k) for k in (4, 5, 6, 7)]
    assert not any("ultra_tail" in n.tags for n in nights.values())
    assert all(not n.usable("hr") and not nt.alert_night(nights, (), d) for d, n in nights.items() if "ultra" in n.tags)


def test_the_nights_after_an_effort_by_its_class():
    """Longue: night D+1 « après une sortie longue » (every Longue, the D+ rule included: an 85-min climb the
    90-min rule misses); Très longue: D+1 → D+3 « après grosse sortie »; Ultra: D+1 → D+4 « après ultra »; after
    ≥ 20 h also D+5 → D+7, out of the band only. All counted in the 24-h totals."""
    def tagged(s):
        nights = nt.build_nights(night_rows(range(0, 20), today=D), D)
        nt.tag_efforts(nights, st.efforts([s]))
        return {k: sorted(n.tags) for k in range(0, 9) if (n := nights.get(D - timedelta(days=12 - k))) and n.tags}
    climb = _session(D - timedelta(days=12), 85, dplus=1600, hour=8)  # 85 min, 1 600 m D+: a Longue by its legs
    assert tagged(climb) == {1: ["long"]}
    assert tagged(_session(D - timedelta(days=12), 420, hour=6)) == {1: ["big"], 2: ["big"], 3: ["big"]}
    assert tagged(_session(D - timedelta(days=12), 660, hour=5)) == {k: ["ultra"] for k in (1, 2, 3, 4)}
    tail = tagged(_session(D - timedelta(days=12), 1260, hour=0))  # 21 h, 00:00 → 21:00
    assert tail == {**{k: ["ultra"] for k in (1, 2, 3, 4)}, **{k: ["ultra_tail"] for k in (5, 6, 7)}}
    assert nt.TAG_WORDS["ultra"] == nt.TAG_WORDS["ultra_tail"] == "après ultra"
    assert "ultra_tail" in nt.EXCLUDING and "ultra_tail" not in nt.CONTEXT and "ultra" in nt.CONTEXT
    # a night out of the band keeps its sleep in the 24-h totals
    nights = nt.build_nights(night_rows(range(0, 20), today=D), D)
    nt.tag_efforts(nights, st.efforts([_session(D - timedelta(days=12), 660, hour=5)]))
    assert nights[D - timedelta(days=11)].tst24 == 440 and nt.day_tst24(nights, D - timedelta(days=11)) == 440


def test_the_illness_alert_is_muted_then_rearms_against_the_band_before():
    """The alert is muted on the nights out of the alert (Longue D+1, Très longue D+1 → D+3, Ultra D+1 → D+4)
    and re-arms by itself after them, against the band before the effort (Schwellnus 2016: symptoms more frequent
    in the 1–2 weeks after a race); after ≥ 20 h the nights D+5 → D+7 can fire it (out of the band only)."""
    def rows(high):  # 60 nights at 44–46 bpm; `high`: days ago at 58 bpm; 52 bpm on the nights D+1 → D+4
        return night_rows(range(0, 60), today=D, hr=lambda k: 58.0 if k in high else 52.0 if 2 <= k <= 5 and
                          not high & {2, 3} else 44.0 + k % 3)
    ultra = _session(D - timedelta(days=6), 660, hour=5, sid=9)  # D+0 = D-6: nights D-5 → D-2 muted
    on = _day(rows({0, 1}), _runs() + [ultra])  # D+5 and D+6 at 58: it fires, against the band before
    assert on["alert"] and on["alert"]["days"] == [D - timedelta(days=1), D]
    assert on["alert"]["threshold"] == 50 and on["state"]["key"] == "ill"  # 52 bpm on D+1 → D+4: out of the band
    muted = _day(rows({2, 3}), _runs() + [ultra], today=D - timedelta(days=2))  # D+3 and D+4 at 58: muted
    assert muted["alert"] is None and muted["state"]["key"] == "effort"
    long_ = _session(D - timedelta(days=6), 1260, hour=0, sid=9)  # 21 h: D+5 → D+7 are out of the band only
    tail = _day(rows({0, 1}), _runs() + [long_])
    assert tail["alert"] and tail["state"]["key"] == "ill"
    hr = next(p for p in tail["score"]["parts"] if p["key"] == "hr")  # the episode's nights are read
    assert hr["alert"] and hr["sub"] < sc.RED_SUB


def test_m1_an_unusual_climb_lengthens_the_65_window_by_2_days():
    """M1: a Longue or Très longue on foot whose D+ is ≥ 1,5 × the largest single-activity D+ on foot of the 8
    weeks before (that largest ≥ 200 m) → its 65 window 2 days longer (Bontemps 2020; D+ for D−: Koller 1998)."""
    before = [_session(D - timedelta(days=d), 120, dplus=1000 if d == 30 else 400, sid=d) for d in range(5, 50, 5)]
    climb = _session(D, 240, dplus=1600, sid=100)  # 1 600 ≥ 1,5 × 1 000
    assert _caps(climb, before) == ("long", "long", ((5, 65),), 50, False)
    assert _caps(replace(climb, dplus=1400), before) == ("long", "long", ((3, 65),), 50, False)  # 1,4 × only
    assert _caps(replace(climb, workout_type=1), before)[2] == ((7, 65),)  # a race too: D+5 + 2
    assert _caps(_session(D, 420, dplus=2000, sid=101), before)[2] == ((2, 45), (7, 65))  # a Très longue
    assert _caps(_session(D, 700, dplus=5000, sid=102), before)[2] == ((3, 40), (10, 65))  # never an Ultra
    old = [replace(x, start=x.start - timedelta(days=30), day=x.day - timedelta(days=30)) if x.dplus == 1000 else x
           for x in before]  # the 1 000 m now 60 days before: the largest of the 8 weeks is 400
    assert _caps(climb, old)[2] == ((5, 65),) and _caps(replace(climb, dplus=500), old)[2] == ((3, 65),)
    flat = [replace(x, dplus=150) for x in before]  # the largest under 200 m (H): no M1
    assert _caps(climb, flat)[2] == ((3, 65),)
    ride = _session(D, 420, dplus=3000, sport="Ride", sid=103)  # not on foot: no M1 (and one class lower)
    assert _caps(ride, before)[:3] == ("long", "very_long", ((3, 65),))
    # in efforts(), each activity is read against its own 8 weeks
    [e] = [x for x in st.efforts(before + [climb]) if x.session_id == 100]
    assert e.caps == ((5, 65),)


def test_m3_back_to_back_days_are_one_effort():
    """M3: consecutive days that each hold an activity ≥ 3 h are one effort (summed times; D+0 the last day's
    end; named after its longest activity; Besson 2020); no extra days on top. The state line stays one line:
    the longest activity, « il y a N jours » from its day."""
    d1 = _session(D - timedelta(days=3), 240, sid=1, name="Jour 1", hour=7)  # 4 h
    d2 = _session(D - timedelta(days=2), 210, sid=2, name="Jour 2", hour=7)  # 3h30
    [e] = st.efforts([d1, d2])
    assert (e.kind, e.minutes, e.day, e.session_id, e.name, e.start_day) == (
        "very_long", 450, D - timedelta(days=2), 1, "Jour 1", D - timedelta(days=3))
    assert e.caps == ((2, 45), (5, 65)) and e.end == datetime.combine(D - timedelta(days=2), datetime.min.time()) \
        + timedelta(hours=10, minutes=30)
    w = st.effort_window([e], D)
    assert (w["days"], w["ago"], w["cap"]) == (2, 3, 45) and td.effort_text(w) == "Grosse sortie il y a 3 jours : Jour 1."
    shake = _session(D - timedelta(days=2), 30, sid=3, hour=5)  # a short run between them changes nothing
    assert [(x.kind, x.minutes) for x in st.efforts([d1, shake, d2])] == [("very_long", 450)]
    gap = replace(d2, start=d2.start + timedelta(days=1), day=d2.day + timedelta(days=1))  # a day between them
    assert [x.kind for x in st.efforts([d1, gap])] == ["long", "long"]
    same = replace(d2, start=d1.start + timedelta(hours=6), day=d1.day)  # both on one day: not « consecutive »
    assert [x.kind for x in st.efforts([d1, same])] == ["long", "long"]
    three = st.efforts([d1, d2, _session(D - timedelta(days=1), 400, sid=4, name="Jour 3")])  # 4 h, 3h30, 6h40
    assert [(x.kind, x.minutes, x.name) for x in three] == [("ultra", 850, "Jour 3")]
    # each past day of the history saw what it knew: on D-3 a Longue (Jour 1 alone), on D-2 the two days
    rows, sessions = _rich(), _runs(start=5) + [d1, d2]
    hist = {d: (state, score) for d, state, score in _history(rows, sessions)}
    for d, (state, score) in hist.items():
        then = _then(rows, sessions, d)
        assert score["value"] == then["score"]["value"] and (state or {}).get("key") == (then["state"] or {}).get(
            "key"), d
    assert _then(rows, sessions, D - timedelta(days=3))["window"]["effort"].kind == "long"
    assert _then(rows, sessions, D - timedelta(days=2))["window"]["effort"].kind == "very_long"


def test_m4_cycling_is_one_class_lower_its_nights_follow_its_time():
    """M4: not on foot (ride, swim…) one class lower — ≥ 10 h Très longue, 6–10 h Longue, 3–6 h none — while its
    nights follow its time (Koller 1998; Shave 2007). A chain with a run in it is on foot (a triathlon)."""
    assert _caps(_session(D, 720, sport="Ride")) == ("very_long", "ultra", ((2, 45), (5, 65)), 30, False)
    assert _caps(_session(D, 420, sport="Ride")) == ("long", "very_long", ((3, 65),), 50, False)
    assert _caps(_session(D, 420, sport="Ride", workout_type=11)) == ("long", "very_long", ((5, 65),), 50, False)
    assert _caps(_session(D, 240, sport="Ride")) == (None, "long", (), None, False)
    four = st.effort_of(_session(D - timedelta(days=1), 240, sport="Ride"))
    assert st.effort_window([four], D) is None  # no window, no Charge
    nights = nt.build_nights(night_rows(range(0, 5), today=D), D)
    nt.tag_efforts(nights, [four])
    assert nights[D].tags == {"long"}  # its night after: out of the band and the alert
    bike = _session(D - timedelta(days=2), 300, sport="Ride", sid=1, hour=6)  # 06:00 → 11:00
    run = _session(D - timedelta(days=2), 100, sid=2, hour=11)  # 11:00 → 12:40, no stop: one effort on foot
    assert [(x.kind, x.nights) for x in st.efforts([bike, run])] == [("very_long", "very_long")]
    assert st.effort_of(_session(D, 170, dplus=1500, sport="Ride")) is None  # D+ on a bike is no legs rule


def test_activites_a_raised_easy_pace_hr_after_an_ultra_is_annotated_not_flagged():
    """Activités › FC en footing: for 21 days after an ultra a raised easy-pace HR is « après ultra » instead of
    « à surveiller » (Chambers 1998, n = 8: HR at fixed speeds higher to day 25; H); the fold stays closed."""
    from app.services import training_view as tv
    from tests.test_training_view import S, T, easy

    runs = [easy(k, 140) for k in range(10, 200, 3)] + [easy(5, 144), easy(2, 146)]
    flagged = tv.footing(runs, T, 185)
    assert flagged["flagged"] and not flagged["after_ultra"] and flagged["line"] == tv.FLAG_LINE
    ultra = S(11, minutes=700, km=100, dplus=4000, sport="TrailRun", id=99_999, name="Ultra")  # D = T-11
    after = tv.footing(runs + [ultra], T, 185)
    assert not after["flagged"] and after["after_ultra"]
    assert after["line"] == "Tes 2 dernières sorties faciles : cœur au-dessus de ta normale, à même allure, après ultra."
    old = S(30, minutes=700, km=100, dplus=4000, sport="TrailRun", id=99_998, name="Ultra")  # D+28: flagged again
    assert tv.footing(runs + [old], T, 185)["flagged"]
    long_ride = S(11, minutes=700, km=300, dplus=2000, sport="Ride", id=99_997, name="Vélo")  # Très longue (M4)
    assert tv.footing(runs + [long_ride], T, 185)["flagged"]
    html = (ROOT / "app/templates/partials/activity_training.html").read_text()
    assert '{% elif e.after_ultra %} <span class="pf-train-soft">· après ultra</span>' in html


async def test_a_rich_garmin_wearer_after_a_marathon_marked_as_a_race(db_session, test_user):
    """A Garmin athlete every night, a 3h30 marathon marked as a race on Strava 4 days ago: a Longue, 65 until D+5
    (v4.2; an unmarked one ends at D+3); its night D+1 out of the normal. Raw ≈ (25·100 + 25·100 + 30·100 +
    20·50) / 100 = 90 (the VFC just under 100 without that night), capped at 65: « Récupération en cours », the
    marathon named."""
    from app.models.activity import Activity
    from app.services import sante
    from tests.test_sante import _garmin_rows, _seed_rows
    from tests.test_sante import _runs as _db_runs

    await _seed_rows(db_session, test_user, _garmin_rows(D))
    await _db_runs(db_session, test_user, D)
    d = D - timedelta(days=4)
    act = Activity(user_id=test_user.id, strava_activity_id=8850, sport_type="Run", name="Marathon de Lyon",
                   start_date=datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc), distance=42_195,
                   moving_time=12_400, elapsed_time=12_600, total_elevation_gain=120, average_heartrate=162,
                   raw_data={"utc_offset": 7200, "workout_type": 1})
    db_session.add(act)
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=D)
    st_, s = page["state"], page["score"]
    assert (st_["key"], st_["text"]) == ("effort", "Grosse sortie il y a 4 jours : Marathon de Lyon.")
    subs = {q["key"]: q["sub"] for q in s["parts"]}
    assert subs["sleep"] == 100 and subs["load"] == 50 and subs["hr"] == 100 and subs["hrv"] > 95
    assert s["raw0"] == pytest.approx((25 * subs["hrv"] + 25 * 100 + 30 * 100 + 20 * 50) / 100)
    assert s["value"] == 65 and s["caps"] == ["effort"]
    assert {r["iso"]: r["marks"] for r in page["sleep"]["rows"]}[(d + timedelta(days=1)).isoformat()] == \
        "◇ après une sortie longue"
    act.raw_data = {"utc_offset": 7200, "workout_type": 0}  # not a race: its window ended on D+3
    act.name = "Marathon de Lyon "  # a change the sessions' cache sees
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=D)
    assert page["state"]["key"] == "ok" and page["score"]["value"] == 100


# ── §C: sleep (research_sleep.md §a–§b; the owner's requests win) ───────────

async def test_the_sommeil_ring_marks_one_day_only_under_6_hours(db_session, test_user):
    """The ring's value is the last 24 h, full at 8 h (a scale, not a goal, H); one day is marked only under 6 h
    (warm, the word « court »: Craven 2022), else the neutral sleep hue (the 7 h is habitual sleep: Watson 2015a);
    a long night is never flagged, no ceiling (Watson 2015a: > 9 h « may be appropriate »)."""
    from app.services import sante
    from tests.test_sante import _seed_rows

    for asleep, tone, note in ((330, "warn", "court"), (390, "accent", None), (600, "accent", None)):
        rows = night_rows(range(0, 20), today=D, asleep=asleep, start=(21, 0), end=(9, 0))
        await _seed_rows(db_session, test_user, rows)
        page = await sante.health_page(db_session, test_user.id, today=D)
        ring = page["rings"][1]
        assert (ring["tone"], ring["note"]) == (tone, note), asleep
        assert ring["aria"].endswith(("court." if note else "siestes comprises.") + " Ouvre la section Sommeil.")
        row = next(r for r in page["contrib"]["rows"] if r["key"] == "sleep")
        assert row["tone"] == tone and (row["word"] == "suffisant") is (asleep >= 420), asleep
        if asleep == 600:
            assert row["sub"] == 100 and ring["dash"] == ring["c"]  # 10 h: full, never « too long »
        from sqlalchemy import delete

        from app.models.health import HealthMetric
        await db_session.execute(delete(HealthMetric).where(HealthMetric.user_id == test_user.id))
        await db_session.flush()


def test_a_late_nap_is_after_the_usual_bedtime_minus_7_h_or_16_00():
    """« après sieste tardive »: a nap ending after min(the usual bedtime − 7 h, 16:00) on the evening before
    (H; Mograss 2022 for the 7 h, Walsh 2021's 13:00–16:00 window); annotated, never out of the normal."""
    def tagged(start, nap_end, days=range(0, 10)):
        rows = night_rows(days, today=D, start=start, end=(7, 0))
        y = D - timedelta(days=1)
        rows["nap"][y] = (50, {"windows": [[f"{y}T{nap_end[0] - 1:02d}:{nap_end[1]:02d}",
                                            f"{y}T{nap_end[0]:02d}:{nap_end[1]:02d}"]]}, "Garmin")
        nights = nt.build_nights(rows, D)
        nt.tag_nights(nights)
        return "late_nap" in nights[D].tags, nt.nap_cutoff(nights, D)
    assert tagged((23, 0), (15, 50)) == (False, datetime(2026, 10, 7, 16, 0))  # 23:00 − 7 h = 16:00
    assert tagged((23, 0), (16, 10))[0]
    assert tagged((21, 30), (15, 0)) == (True, datetime(2026, 10, 7, 14, 30))  # 21:30 − 7 h = 14:30
    assert tagged((21, 30), (14, 20))[0] is False
    # a bedtime after midnight (00:30): 00:30 − 7 h = 17:30, so 16:00 comes first
    rows = night_rows(range(0, 10), today=D, end=(8, 0))
    for d in list(rows["sleep"]):
        v, det, src = rows["sleep"][d]
        rows["sleep"][d] = (v, {**det, "main_start": f"{d}T00:30"}, src)
    y = D - timedelta(days=1)
    rows["nap"][y] = (50, {"windows": [[f"{y}T15:15", f"{y}T16:05"]]}, "Garmin")
    nights = nt.build_nights(rows, D)
    nt.tag_nights(nights)
    assert "late_nap" in nights[D].tags and nt.nap_cutoff(nights, D) == datetime(2026, 10, 7, 16, 0)
    # under 5 nights in 28 days: the night's own onset
    assert tagged((21, 30), (15, 0), days=range(0, 3)) == (True, datetime(2026, 10, 7, 14, 30))
    rows = night_rows(range(0, 10), today=D)
    y = D - timedelta(days=1)
    rows["nap"][y] = (50, {"windows": [[f"{y}T16:30", f"{y}T17:20"]]}, "Garmin")
    nights = nt.build_nights(rows, D)
    nt.tag_nights(nights)
    assert "late_nap" in nights[D].tags and not nights[D].excluded and nights[D].usable("hr")


def test_stages_rounded_to_10_min_the_bar_keeps_its_shape():
    """Each phase rounded to 10 min (H) in the legend and the table (« 1h10 », « 10 min »); the bar keeps the raw
    minutes; no %, no target, no norm; « < 10 min » for a sliver, never « 0 min »."""
    from app.services import sante_sleep as sl

    assert [sl.stage_hm(m) for m in (49, 205, 96, 13, 15, 4, 125)] == [
        "50 min", "3h30", "1h40", "10 min", "20 min", "< 10 min", "2h10"]
    ph = sl.phases({"awake": 4, "light": 205, "deep": 49, "rem": 0})
    assert [(p["key"], p["min"], p["hm"]) for p in ph["parts"]] == [("awake", 4, "< 10 min"),
                                                                    ("light", 205, "3h30"), ("deep", 49, "50 min")]
    assert ph["aria"] == ("Phases estimées par la montre : éveil moins de 10 minutes, léger 3 heures 30, profond "
                          "50 minutes.")
    for word in ("%", "objectif", "idéal", "norme"):
        assert word not in str(ph)


def test_the_race_page_banks_sleep_the_last_week_and_reassures_on_the_eve():
    """The sleep-banking target and line only J-7 → J-1 (Walsh 2021: « even just 1 week »), + 30–60 min kept
    (H); never on the race eve; Walsh's race-eve reassurance once, J-7 → J0."""
    import json as _json

    from app.services import race_prep as rp

    nights = nt.build_nights(night_rows(range(0, 90), today=D, asleep=440), D)
    for rd in (D + timedelta(days=10), D + timedelta(days=3)):  # D is J-10, then J-3
        c = rp.night_bars(nights, rd, D)
        d = _json.loads(c["data"].replace("<\\/", "</"))
        assert [d["r"][i][2].split(" · ")[0] for i in range(14) if "cible" in d["r"][i][2]] == [
            "J‑7", "J‑6", "J‑5", "J‑4", "J‑3", "J‑2"], rd  # J-14 → J-8 and the race eve: none
        slot = rp.X1 / 14
        assert c["goal"]["x"] == round(slot * 7, 1) and c["goal"]["w"] == round(slot * 6, 1)  # J-7 → J-2
        assert "par jour de J-7 à J-2" in c["summary"] and "par jour de J-7 à J-2" in c["aria_now"]
    assert rp.EVE_LINE == ("Une nuit agitée avant une course est courante : si tu as bien dormi la semaine "
                           "d'avant, elle ne devrait pas peser sur ta course.")
    html = (ROOT / "app/templates/partials/race_prep.html").read_text()
    assert "{% if p.bank %}" in html and "{% if p.eve %}<p class=\"pf-rp-line\">{{ p.eve }}</p>{% endif %}" in html


def test_no_8_to_10_hour_norm_anywhere():
    """The IOC 2026 dropped the « 9–10 h » of 2019 (Reardon 2026): never an 8–10 h norm for athletes, never a
    sleep drop worded as overreaching (Walsh 2021)."""
    for path in list((ROOT / "app/services").glob("*.py")) + list((ROOT / "app/templates").rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for norm in ("8 à 10", "9 à 10", "8–10 h", "8-10 h", "9–10 h", "surentraînement", "surmenage"):
            assert norm not in text, (path.name, norm)


# ── §D: the method folds on the page ────────────────────────────────────────

async def test_the_method_folds_on_the_page(as_user, db_session, test_user, monkeypatch):
    """Both folds, closed: their groups titled, then « Textes officiels » and « Études », each reference a link (a
    DOI, or the text's own address for BASES 2023 and CTA/NSF 2052.1-A)."""
    import re

    from app.services import sante
    from tests.owner_v4 import D8, seed_owner_v4

    async def today(*a, **k):
        return D8
    monkeypatch.setattr(sante, "athlete_today", today)
    await seed_owner_v4(db_session, test_user)
    html = (await as_user.get("/sante")).text
    folds = re.findall(r'<details class="pf-fold pf-method">(.*?)</details>', html, re.S)
    assert len(folds) == 2
    recup, nuits = folds
    assert "<summary>Comment je calcule ta récupération</summary>" in recup
    assert re.findall(r'<h3 class="pf-method-h">([^<]+)</h3>', recup) == [
        "Ton score", "Ce qui vient des textes officiels", "Ce qui est notre choix (H)", "Ce que le score ne sait pas",
        "Textes officiels", "Études"]
    assert re.findall(r'<h3 class="pf-method-h">([^<]+)</h3>', nuits) == [
        "Ce qui vient des textes officiels", "Ce qui est notre choix (H)", "Ce que la montre ne sait pas",
        "Textes officiels", "Études"]
    assert '<a href="https://doi.org/10.1123/ijspp.2017-0759" rel="noopener" target="_blank">Kellmann 2018</a>' in recup
    assert 'href="https://westminsterresearch.westminster.ac.uk/item/wxx7y/' in recup
    assert 'href="https://www.thensf.org/wp-content/uploads/2022/10/ANSI-CTA-NSF-2052.1-A-FINAL.pdf"' in nuits
    assert '<ul class="pf-refs" aria-label="Textes officiels">' in recup and '<ul class="pf-refs" aria-label="Études">' \
        in nuits


def test_the_history_sees_a_chain_across_midnight_as_each_day_knew_it():
    """A 2-h run 21:00 → 23:00, then another from 23:20 to 01:00: one 4-h Longue (a chain), uploaded after
    midnight. The 14-day card's day before saw only the first run (no effort: no window, no Charge), the day
    after the whole Longue; today's efforts are reused on the other days (no recomputation, no leak)."""
    d1 = D - timedelta(days=4)
    a = _session(d1, 120, sid=11, hour=21, name="Sortie du soir")
    b = _session(d1, 100, sid=12, hour=23, name="Suite")
    b = replace(b, start=b.start + timedelta(minutes=20))
    [e] = st.efforts([a, b])
    assert (e.kind, e.ids, e.day) == ("long", frozenset({11, 12}), d1)  # ended 01:00: D is the day before
    rows, sessions = _rich(), _runs(start=5) + [a, b]
    hist = {d: (state, score) for d, state, score in _history(rows, sessions)}
    for d, (state, score) in hist.items():
        then = _then(rows, sessions, d)
        assert score["value"] == then["score"]["value"], d
        assert (state or {}).get("key") == (then["state"] or {}).get("key"), d
    assert _then(rows, sessions, d1)["window"] is None
    assert _then(rows, sessions, d1 + timedelta(days=1))["window"]["effort"].ids == frozenset({11, 12})


def test_a_night_says_each_word_once():
    """Two overlapping ultras can leave a night both « ultra » and « ultra_tail »: « après ultra » once."""
    from app.services import sante_sleep as sl

    nights = nt.build_nights(night_rows(range(0, 3), today=D), D)
    nights[D].tags |= {"ultra", "ultra_tail", "late_nap", "race"}
    assert nt.tag_words(nights[D].tags) == ["après sieste tardive", "après ultra"]
    assert {r["iso"]: r["marks"] for r in sl.rows(nights, D)}[D.isoformat()] == \
        "◇ après sieste tardive · ◇ après ultra"
