"""Historical Santé scenarios checked against the current nightly-signal contract.

Cardiac series independently contribute warnings using the existing personal thresholds. Legacy effort windows
remain metadata; recorded-race context never sets the signal index. Acute-alert tags, source-specific baselines,
sleep totals, and missing-data requirements retain their existing rules. This is not physiological validation.
"""
import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import nights as nt
from app.services import sante_score as sc
from app.services import sante_training as st
from tests import test_coros  # the linked athlete's client fixture (as_user)
from tests.test_nights import night_rows
from tests.test_sante import rested  # noqa: F401 (a fixture the tests name: a no-op since the 8-h need, 2026-10-14)
from tests.test_sante_score import _day, _history, _rich, _runs, _session, _then
from tests.test_sante_score import _example as _score_example

as_user, no_commit = test_coros.as_user, test_coros.no_commit

D = date(2026, 10, 8)
ROOT = Path(__file__).resolve().parent.parent
HR_BAND = {"center": 45.0, "sd": 1.8}  # the example days' FC de nuit: its usual value and SD (bpm)


def _example(z=None, hr=None, tst=None, window=None) -> dict:
    """One day of research_recovery.md §3.4 from its numbers: the 7-night HRV `z` (ln units over the band's SD),
    the 7-night nightly HR over its median (bpm, over the band's SD 1,8: its z), the 24-h sleep (minutes), an
    effort window (owner, 2026-10-14: scientific basis — read by the rule-based state, tests.test_sante_score)."""
    x = _score_example(z_hrv=z, z_hr=None if hr is None else hr / HR_BAND["sd"], tst=tst, window=window)
    if z is None and hr is None:  # sleep alone: never a state (C8)
        x["day"]["night_measured"] = tst is not None
    return x


def _rows(x: dict) -> dict:
    """Each heart signal's part of the indice (s × 100, rounded) and the colour its row wears on the page
    (sante_score.row_tone: its place against its usual values), in the score's order."""
    trends = x["day"]["trends"]
    return {p["key"]: {"sub": round(100 * p["s"]), "tone": sc.row_tone(p["key"], trends[p["key"]])}
            for p in x["score"]["parts"]}


# ── §A: research_recovery.md §3.4, the example days ─────────────────────────

def test_the_example_days_of_the_research_report():
    """Personal cardiac thresholds and short sleep determine the signal index.
    An effort window cannot hide a cardiac warning or create one when the signals are usual."""
    normal = _example(z=0, hr=0, tst=450)
    assert (normal["score"]["value"], normal["score"]["tone"], normal["score"]["caps"]) == (100, "ok", [])
    # 2. HRV low, HR low (was 77, green: the « saturation » of Buchheit 2014 cannot be told from fatigue by one
    # athlete's 7 days, Plews 2013 reads the VFC first): the VFC 2 SD under, orange; (0,25 + 1) / 2 → 63
    sat = _example(z=-2.0, hr=-2, tst=450)
    assert (sat["score"]["value"], sat["score"]["tone"], sat["state"]["key"]) == (63, "warn", "hrv")
    assert (_rows(sat)["hrv"]["sub"], _rows(sat)["hrv"]["tone"]) == (25, "warn")  # never red
    # 3. HRV low, HR up: « accumulated fatigue » (was 64): the VFC's reason; (0,5 + 0,139) / 2 → 32; 6h30 of sleep
    # is no reason (from 6 h)
    tired = _example(z=-1.5, hr=4, tst=390)
    assert (tired["score"]["value"], tired["score"]["tone"]) == (32, "warn")
    assert [r["key"] for r in tired["score"]["reasons"]] == ["hrv", "hr"] and tired["state"]["text"] is None
    assert [(r["sub"], r["tone"]) for r in _rows(tired).values()] == [(50, "warn"), (14, "warn")]
    # 4. HR + 3 alone crosses its threshold: the index is held at 69 despite usual VFC.
    plus3 = _example(z=0, hr=3, tst=420)
    assert (plus3["score"]["value"], plus3["score"]["tone"]) == (69, "warn")
    assert (_rows(plus3)["hr"]["sub"], _rows(plus3)["hr"]["tone"]) == (42, "warn")
    # 5. HR + 6 with usual VFC: orange, index 50. Only the existing acute alert can make it red.
    plus6 = _example(z=-0.3, hr=6, tst=420)
    assert (plus6["score"]["value"], plus6["score"]["tone"], plus6["state"]["key"]) == (50, "warn", "hr")
    assert (_rows(plus6)["hr"]["sub"], _rows(plus6)["hr"]["tone"]) == (0, "warn")
    # 6. a short night (5h30), the rest normal: orange, the indice held to 69 (was 59)
    short = _example(z=0, hr=0, tst=330)
    assert (short["score"]["value"], short["score"]["tone"], short["state"]["key"]) == (69, "warn", "short")
    # 7. A long-effort window is context only: the measured signals remain usual.
    trail = _example(z=0, hr=0, tst=450, window={"tone": "warn", "text": "fenêtre indicative jusqu'à J+1"})
    assert (trail["score"]["value"], trail["state"]["key"], trail["score"]["caps"]) == (100, "ok", [])
    # 8. sleep only, 8 h: no state from sleep alone (was 80), a reason, never a number
    alone = _example(tst=480)
    assert alone["score"]["value"] is None and alone["score"]["missing"]["key"] == "no_heart"


def test_the_joint_cap_vfc_under_its_band_and_fc_up_3_bpm():
    """VFC and FC de nuit independently contribute their measured warning reasons.
    The existing mean of cardiac components sets the number; no extra effort penalty is added."""
    mild = _example(z=-0.8, hr=3, tst=450)
    assert (mild["score"]["value"], mild["score"]["caps"], mild["score"]["tone"]) == (63, [], "warn")
    assert (mild["state"]["key"], mild["state"]["text"]) == ("hrv", None)
    rows = _rows(mild)
    assert (rows["hrv"]["tone"], rows["hrv"]["sub"]) == ("warn", 85)
    assert (rows["hr"]["tone"], rows["hr"]["sub"]) == ("warn", 42)
    # HRV inside its threshold cannot suppress the independently elevated FC de nuit.
    inside = _example(z=-0.7, hr=3, tst=450)
    assert (inside["score"]["value"], inside["score"]["tone"]) == (66, "warn") and _rows(inside)["hr"]["tone"] == "warn"
    assert _example(z=-0.8, hr=0.5, tst=450)["state"]["key"] == "hrv"
    # An effort window adds no reason: only the two measured cardiac warnings remain.
    w = {"tone": "warn", "text": "fenêtre indicative jusqu'à J+3"}
    both = _example(z=-0.8, hr=3, tst=450, window=w)
    assert [r["key"] for r in both["score"]["reasons"]] == ["hrv", "hr"] and both["state"]["key"] == "hrv"
    assert both["state"]["text"] is None and both["score"]["value"] == 63


def test_a_low_vfc_counts_as_red_only_with_the_nightly_hr_over_2_bpm():
    """Was: a low VFC a red contributor only with the FC de nuit > + 2 bpm. Now (owner, 2026-10-14: scientific
    basis) a low VFC is an orange reason, never red (red: the existing illness alert), whatever
    the FC de nuit; without it the indice is the VFC's alone (25, was 66). A 5-h day is its own reason."""
    for hr in (None, -2, 2, 2.5):
        x = _example(z=-2.0, hr=hr, tst=450)
        assert (x["score"]["tone"], x["state"]["key"]) == ("warn", "hrv"), hr
        assert _rows(x)["hrv"]["tone"] == "warn", hr
    alone = _example(z=-2.0, tst=450)
    assert [p["key"] for p in alone["score"]["parts"]] == ["hrv"] and alone["score"]["value"] == 25
    assert alone["score"]["caps"] == []
    sleepy = _example(z=0, hr=0, tst=300)
    assert [r["key"] for r in sleepy["score"]["reasons"]] == ["short"] and sleepy["score"]["value"] == 69


def test_the_nightly_hr_scale():
    """Was: 100 / 40 / 0 at + 2 / + 5 / + 8 bpm. Now (owner, 2026-10-14: scientific basis) the FC de nuit's part
    of the indice is s = clip((2,5 − z)/2, 0, 1), z its 7-night mean over its usual values in SD (H): 100 up to
    z 0,5, 0 at 2,5 SD; its row orange from its threshold, + 0,70 SD for 7 nights against 30 (here 1,27 bpm),
    neutral under (never praised), red only under the illness alert."""
    subs = {z: sc.sub("hr", z) for z in (-2, 0, 0.5, 1, 1.5, 2, 2.5, 3)}
    assert subs == pytest.approx({-2: 1, 0: 1, 0.5: 1, 1: 0.75, 1.5: 0.5, 2: 0.25, 2.5: 0, 3: 0})
    assert sc.sub("hrv", -1.5) == 0.5 and sc.sub("hrv", -0.5) == 1 and sc.sub("hrv", -3) == 0
    tones = {d: _rows(_example(z=0, hr=d, tst=450))["hr"]["tone"] for d in (-4, 0, 0.8, 1, 1.3, 4, 5.5)}
    assert tones == {-4: "accent", 0: "ok", 0.8: "ok", 1: "ok", 1.3: "warn", 4: "warn", 5.5: "warn"}
    trend = _example(z=0, hr=5.5, tst=450)["day"]["trends"]["hr"]
    assert sc.row_tone("hr", trend, alert=True) == "danger"


def test_charge_is_no_component_any_more():
    """Neither training load nor an effort window enters the nightly index.
    Identical nights retain identical components and index despite a recorded long effort."""
    day = _day(_rich(), _runs(n=12))
    assert [p["key"] for p in day["score"]["parts"]] == ["hrv", "hr"] and day["score"]["absent"] == []
    young = _day(night_rows(range(0, 3), today=D), _runs(n=12))  # no band yet: VFC and FC missing, not Charge
    assert young["score"]["absent"] == ["hrv", "hr"] and young["score"]["building"] == ["hrv", "hr"]
    big = _day(_rich(), _runs(n=12) + [_session(D - timedelta(days=1), 200, sid=9)])
    assert [p["key"] for p in big["score"]["parts"]] == ["hrv", "hr"] and big["window"]["tone"] == "warn"
    assert (big["score"]["value"], big["state"]["key"], big["score"]["caps"]) == (100, "ok", [])
    assert big["score"] == day["score"]  # identical nights despite the open effort window
    assert "load" not in big["window"]
    assert not hasattr(sc, "WEIGHTS") and sc.HEART == ("hrv", "hr")


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
    # nightly HR: 14 nights at 50 ± 3 bpm (MAD 3, robust SD 4,45): σ0 = 2 bpm, the alert's robust σ̂
    # √((14·4,45² + 7·2²)/21) = 3,81, its line 50 + max(2 × 3,81, 5) = 57,6 (it was 58,9 unshrunk)
    noisy = nt.build_nights(night_rows(range(1, 15), today=D, hr=lambda k: 50.0 + (3 if k % 2 else -3)), D)
    h = nt.band(noisy, "hr", D)
    s = 1.4826 * 3
    assert h["n"] == 14 and h["robust_sd"] == pytest.approx(math.sqrt((14 * s * s + 7 * 4) / 21))
    assert h["alert"] == pytest.approx(50 + 2 * h["robust_sd"]) and round(h["alert"], 1) == 57.6
    # the trend's band: the mean ± 0,5 SD, the SD (3,11) shrunk to 2,79 (owner, 2026-10-14: scientific basis — it
    # was ± 3 bpm, a fixed width, then the median ± 0,5 robust σ̂)
    assert h["sd"] == pytest.approx(math.sqrt((14 * (14 * 9 / 13) + 7 * 4) / 21)) and round(h["sd"], 2) == 2.79
    assert (h["lo"], h["hi"]) == pytest.approx((50 - 0.5 * h["sd"], 50 + 0.5 * h["sd"]))
    # a very steady HR: the 5-bpm floor holds
    flat = nt.build_nights(night_rows(range(1, 15), today=D, hr=45.0), D)
    assert nt.band(flat, "hr", D)["alert"] == 50


# ── §B: recovery windows from activities (research_efforts.md §b, all H, « indicatives ») ──

def _caps(s):
    """(class, its nights' class, the last day of its window D+n) of one activity alone."""
    e = st.effort_of(s)
    return (e.kind, e.nights, e.last) if e else None


def test_the_classes_and_their_windows():
    """Owner, 2026-10-14: scientific basis — windows are reasons, no caps: Longue (≥ 3 h, or ≥ 1 500 m D+ on
    foot) orange through D+1, D+2 run like a race (Petersen 2007); Très longue D+3 (Fazackerley 2019); Ultra
    D+5 (Hoffman 2017), D+7 when ≥ 24 h (a night run through no longer extends it; it still counts for the
    sleep: sante_training.raced_nights); no Charge (2026-10-08)."""
    assert _caps(_session(D, 200)) == ("long", "long", 1)
    assert _caps(_session(D, 200, workout_type=1)) == ("long", "long", 2)  # a race (Strava)
    assert _caps(_session(D, 170, dplus=1600)) == ("long", "long", 1)  # the legs rule
    assert _caps(_session(D, 420)) == ("very_long", "very_long", 3)
    assert _caps(_session(D, 420, workout_type=1)) == ("very_long", "very_long", 3)
    assert _caps(_session(D, 660, hour=5)) == ("ultra", "ultra", 5)  # 05:00 → 16:00
    assert _caps(_session(D, 1500, hour=5)) == ("ultra", "ultra", 7)  # ≥ 24 h
    night = _session(D, 660, hour=20)  # 20:00 → 07:00: ran from 01:00 to 05:00
    assert _caps(night) == ("ultra", "ultra", 5) and st.through_night(st.local_start(night), st.local_end(night))
    assert not st.through_night(datetime(2026, 10, 8, 2), datetime(2026, 10, 8, 12))  # started after 01:00
    assert not st.through_night(datetime(2026, 10, 7, 22), datetime(2026, 10, 8, 4, 59))  # ended before 05:00
    e = st.effort_of(_session(D, 200, workout_type=1))
    assert [(st.effort_window([e], D + timedelta(days=k)) or {}).get("tone") for k in (0, 2, 3)] == [
        "warn", "warn", None]


def test_the_owner_transjeju_window_and_nights():
    """The owner (brief §E): the Transjeju started 02/10 21:00, 16h53, ending 03/10 13:53 local → D+0 = 03/10, an
    Ultra: red on D+0 and D+1 (03 → 04/10), orange to D+5 (08/10) (owner, 2026-10-14: scientific basis — it was
    35 to D+3, 65 to D+13: a night run through no longer extends it); nights D+1 → D+4 (04 → 07/10) never fire
    the alert, and count in the band like any night (2026-10-08)."""
    s = _session(date(2026, 10, 2), 935, elapsed=1013, hour=21, offset=32400, workout_type=1, name="Transjeju 100M")
    [e] = st.efforts([s])
    assert (e.kind, e.nights, e.day, e.end) == ("ultra", "ultra", date(2026, 10, 3), datetime(2026, 10, 3, 13, 53))
    assert e.last == 5 and not hasattr(e, "load") and not hasattr(e, "caps")
    tones = {k: (st.effort_window([e], date(2026, 10, k)) or {}).get("tone") for k in (3, 4, 5, 8, 9)}
    assert tones == {3: "danger", 4: "danger", 5: "warn", 8: "warn", 9: None}
    assert st.effort_window([e], date(2026, 10, 6))["until"] == date(2026, 10, 8)
    rows = night_rows(range(0, 12), today=D)
    nights = nt.build_nights(rows, D)
    nt.tag_efforts(nights, [e])
    assert sorted(d for d, n in nights.items() if "ultra" in n.tags) == [date(2026, 10, k) for k in (4, 5, 6, 7)]
    assert all(not nt.alert_night(nights, (), d) for d, n in nights.items() if "ultra" in n.tags)
    assert nt.mean7(nights, "hr", date(2026, 10, 7))["n"] == 7  # 04 → 07/10 among them


def test_the_nights_after_an_effort_by_its_class():
    """Longue: night D+1 « après une sortie longue » (every Longue, the D+ rule included: an 85-min climb the
    90-min rule misses); Très longue: D+1 → D+3 « après grosse sortie »; Ultra: D+1 → D+4 « après ultra », after
    ≥ 20 h too (no tail out of the band any more: every measured night counts, 2026-10-08). All counted in the
    24-h totals."""
    def tagged(s):
        nights = nt.build_nights(night_rows(range(0, 20), today=D), D)
        nt.tag_efforts(nights, st.efforts([s]))
        return {k: sorted(n.tags) for k in range(0, 9) if (n := nights.get(D - timedelta(days=12 - k))) and n.tags}
    climb = _session(D - timedelta(days=12), 85, dplus=1600, hour=8)  # 85 min, 1 600 m D+: a Longue by its legs
    assert tagged(climb) == {1: ["long"]}
    assert tagged(_session(D - timedelta(days=12), 420, hour=6)) == {1: ["big"], 2: ["big"], 3: ["big"]}
    assert tagged(_session(D - timedelta(days=12), 660, hour=5)) == {k: ["ultra"] for k in (1, 2, 3, 4)}
    tail = tagged(_session(D - timedelta(days=12), 1260, hour=0))  # 21 h, 00:00 → 21:00
    assert tail == {k: ["ultra"] for k in (1, 2, 3, 4)}
    assert "ultra_tail" not in nt.TAG_WORDS and "tail" not in st.Effort.__dataclass_fields__ and "ultra" in nt.CONTEXT
    # a night after an effort keeps its sleep in the 24-h totals
    nights = nt.build_nights(night_rows(range(0, 20), today=D), D)
    nt.tag_efforts(nights, st.efforts([_session(D - timedelta(days=12), 660, hour=5)]))
    assert nt.day_tst24(nights, D - timedelta(days=11)) == 440


def test_the_illness_alert_is_muted_then_rearms_against_the_band_before():
    """The alert is muted on the nights after an effort (Longue D+1, Très longue D+1 → D+3, Ultra D+1 → D+4)
    and re-arms by itself after them (Schwellnus 2016: symptoms more frequent in the 1–2 weeks after a race); those
    nights count in its band like any night (2026-10-08), their 52 bpm leaving its median at 45; after ≥ 20 h too,
    the nights D+5 → D+7 can fire it. Red: the alert is the one heart reason that makes the day red."""
    def rows(high):  # 60 nights at 44–46 bpm; `high`: days ago at 58 bpm; 52 bpm on the nights D+1 → D+4
        return night_rows(range(0, 60), today=D, hr=lambda k: 58.0 if k in high else 52.0 if 2 <= k <= 5 and
                          not high & {2, 3} else 44.0 + k % 3)
    ultra = _session(D - timedelta(days=6), 660, hour=5, sid=9)  # D+0 = D-6: nights D-5 → D-2 muted
    on = _day(rows({0, 1}), _runs() + [ultra])  # D+5 and D+6 at 58: it fires, against the band before
    assert on["alert"] and on["alert"]["days"] == [D - timedelta(days=1), D]
    assert on["alert"]["threshold"] == 50 and on["state"]["key"] == "ill"  # 52 bpm on D+1 → D+4: in the band
    muted = _day(rows({2, 3}), _runs() + [ultra], today=D - timedelta(days=2))  # D+3 and D+4 at 58: muted
    assert muted["alert"] is None and muted["state"]["key"] == "hr"
    long_ = _session(D - timedelta(days=6), 1260, hour=0, sid=9)  # 21 h: D+5 → D+7 fire it like any night
    tail = _day(rows({0, 1}), _runs() + [long_])
    assert tail["alert"] and tail["state"]["key"] == "ill" and tail["state"]["tone"] == "danger"
    assert tail["score"]["value"] <= 39  # the indice held to the red range
    trend = tail["trends"]["hr"]  # the episode's nights are read
    assert trend["status"] == "above" and sc.row_tone("hr", trend, alert=bool(tail["alert"])) == "danger"


def test_m4_cycling_is_one_class_lower_its_nights_follow_its_time():
    """M4: not on foot (ride, swim…) one class lower — ≥ 10 h Très longue, 6–10 h Longue, 3–6 h none — while its
    nights follow its time (Koller 1998; Shave 2007). A chain with a run in it is on foot (a triathlon)."""
    assert _caps(_session(D, 720, sport="Ride")) == ("very_long", "ultra", 3)
    assert _caps(_session(D, 420, sport="Ride")) == ("long", "very_long", 1)
    assert _caps(_session(D, 420, sport="Ride", workout_type=11)) == ("long", "very_long", 2)
    assert _caps(_session(D, 240, sport="Ride")) == (None, "long", None)
    four = st.effort_of(_session(D - timedelta(days=1), 240, sport="Ride"))
    assert st.effort_window([four], D) is None  # no window
    nights = nt.build_nights(night_rows(range(0, 5), today=D), D)
    nt.tag_efforts(nights, [four])
    assert nights[D].tags == {"long"}  # its night after: never fires the alert
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
    assert after["line"] == ("Tes 2 dernières sorties faciles : cœur plus haut que d'habitude, à la même allure, après "
                             "ultra.")
    old = S(30, minutes=700, km=100, dplus=4000, sport="TrailRun", id=99_998, name="Ultra")  # D+28: flagged again
    assert tv.footing(runs + [old], T, 185)["flagged"]
    long_ride = S(11, minutes=700, km=300, dplus=2000, sport="Ride", id=99_997, name="Vélo")  # Très longue (M4)
    assert tv.footing(runs + [long_ride], T, 185)["flagged"]
    html = (ROOT / "app/templates/partials/activity_training.html").read_text()
    assert '{% elif e.after_ultra %} <span class="pf-train-soft">· après ultra</span>' in html


async def test_a_rich_garmin_wearer_after_a_marathon_marked_as_a_race(db_session, test_user, rested):
    """A marked marathon appears as dated context at J+4 and J+2 without rewriting usual night signals.
    Intensity alone does not establish a confirmed race; an easy long run creates no race context either."""
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
    assert (st_["key"], st_["word"], st_["text"]) == ("ok", "Signaux habituels", None)
    assert [q["key"] for q in s["parts"]] == ["hrv", "hr"] and s["reasons"] == [] and s["value"] == 100
    assert page["synthesis"]["context"][0]["label"] == "Après Marathon de Lyon · J+4"
    assert {r["iso"]: r["marks"] for r in page["sleep"]["rows"]}[(d + timedelta(days=1)).isoformat()] == \
        "◇ après un gros effort"  # v4.3: Santé names no outing (its plain words)
    two = D - timedelta(days=2)
    act.start_date = datetime(two.year, two.month, two.day, 12, tzinfo=timezone.utc)
    act.name = "Marathon de Lyon 2"  # a change the sessions' cache sees
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=D)
    assert (page["state"]["key"], page["state"]["word"], page["score"]["value"]) == (
        "ok", "Signaux habituels", 100)
    assert page["state"]["reasons"] == []
    assert page["synthesis"]["context"][0]["label"] == "Après Marathon de Lyon 2 · J+2"
    act.raw_data = {"utc_offset": 7200, "workout_type": 0}  # not marked as a race on Strava
    act.name = "Marathon de Lyon "
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=D)
    # The old intensity classification is not sufficient evidence of a confirmed race.
    assert page["state"]["key"] == "ok" and page["state"]["reasons"] == []
    assert page["synthesis"]["context"] == []  # intense does not establish that this was a race
    act.average_heartrate = 140  # an easy long run: its window ended on D+1
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=D)
    assert page["state"]["key"] == "ok" and page["score"]["value"] >= 70


# ── §C: sleep (research_sleep.md §a–§b; the owner's requests win) ───────────

async def test_the_sommeil_dial_marks_a_short_day_only(db_session, test_user):
    """The Sommeil dial prints the last 24 h as a percentage of the need, at most 100 % (owner: « Mets des
    pourcentages plutôt que des valeurs »; 2026-10-08: « Fais comme WHOOP »): 8 h for everyone, whatever the week
    before (owner, 2026-10-14: scientific basis — it was 9 h after a week of short nights: the sleep owed is said
    apart, never added to the need); with a word from the hours: under 6 h « court », its arc in the warning
    colour (Craven 2022), from 6 h « un peu court », from 7 h « suffisant » (Watson 2015a), both in the sleep
    colour; a long night is never flagged, no ceiling (Watson 2015a: > 9 h « may be appropriate »); under 6 h is a
    reason of the state (« Sommeil court »), never more."""
    from app.services import sante
    from tests.test_sante import _seed_rows

    for asleep, value, word, tone in ((330, "69", "court", "warn"), (390, "81", "un peu court", "sleep"),
                                      (600, "100", "suffisant", "sleep")):
        # 30 nights: a full baseline (22 nights from day −67 to −8), so the day has a state
        rows = night_rows(range(0, 30), today=D, asleep=asleep, start=(21, 0), end=(9, 0))
        await _seed_rows(db_session, test_user, rows)
        page = await sante.health_page(db_session, test_user.id, today=D)
        dial = page["dials"][0]
        assert (dial["key"], dial["value"], dial["unit"], dial["sub"], dial["tone"], dial["href"]) == (
            "sommeil", value, "%", word, tone, "#sommeil"), asleep
        assert page["sleep"]["hero"]["need"] == "repère 8h00" and page["score"]["value"] is not None, asleep
        # the arc: the hours of the need, at most full
        assert dial["dash"] == pytest.approx(min(1, asleep / 480) * dial["c"], abs=0.01)
        assert ("short" in [r["key"] for r in page["score"]["reasons"]]) is (asleep < 360), asleep
        from sqlalchemy import delete

        from app.models.health import HealthMetric
        await db_session.execute(delete(HealthMetric).where(HealthMetric.user_id == test_user.id))
        await db_session.flush()


def test_a_late_nap_is_after_the_usual_bedtime_minus_7_h_or_16_00():
    """« après sieste tardive »: a nap ending after min(the usual bedtime − 7 h, 16:00) on the evening before
    (H; Mograss 2022 for the 7 h, Walsh 2021's 13:00–16:00 window); a word in the nights' table, nothing else."""
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
    assert "late_nap" in nights[D].tags and nt.alert_night(nights, (), D) and nt.mean7(nights, "hr", D)["n"] == 7


def test_stages_rounded_to_10_min_the_bar_keeps_its_shape():
    """Each phase rounded to 10 min (H) in the legend and the table (« 1h10 », « 10 min »); the bar keeps the raw
    minutes; no %, no target, no norm; « < 10 min » for a sliver, never « 0 min »."""
    from app.services import sante_sleep as sl

    assert [sl.stage_hm(m) for m in (49, 205, 96, 13, 15, 4, 125)] == [
        "50 min", "3h30", "1h40", "10 min", "20 min", "< 10 min", "2h10"]
    ph = sl.phases({"awake": 4, "light": 205, "deep": 49, "rem": 0})
    assert [(p["key"], p["min"], p["hm"]) for p in ph["parts"]] == [("awake", 4, "< 10 min"),
                                                                    ("light", 205, "3h30"), ("deep", 49, "50 min")]
    assert ph["aria"] == ("Phases estimées par ta montre : éveil moins de 10 minutes, léger 3 heures 30, profond "
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
    """Two overlapping efforts can leave a night both « ultra » and « big »: Santé's table says « après un gros
    effort » once (v4.3: Santé names no outing); the race page's words name each."""
    from app.services import sante_sleep as sl

    nights = nt.build_nights(night_rows(range(0, 3), today=D), D)
    nights[D].tags |= {"ultra", "big", "late_nap"}
    assert nt.tag_words(nights[D].tags) == ["après grosse sortie", "après sieste tardive", "après ultra"]
    assert {r["iso"]: r["marks"] for r in sl.rows(nights, D)}[D.isoformat()] == \
        "◇ après un gros effort · ◇ après une sieste tardive"


def test_a_big_climb_is_no_modifier_any_more():
    """v4.3 (owner: « Récupération morning trail run … je ne comprends pas »): no unusual-climb modifier (M1): a
    Longue or a Très longue keeps its class's window whatever the athlete's previous climbs."""
    before = [_session(D - timedelta(days=d), 120, dplus=1000 if d == 30 else 400, sid=d) for d in range(5, 50, 5)]
    climb = _session(D, 240, dplus=1600, sid=100)  # 1 600 m: 1,6 × the largest of the 8 weeks before
    [e] = [x for x in st.efforts(before + [climb]) if x.session_id == 100]
    assert (e.kind, e.last) == ("long", 1)
    assert [x.last for x in st.efforts(before + [_session(D, 420, dplus=2000, sid=101)]) if x.session_id == 101] == [3]
    for name in ("CLIMB_RATIO", "CLIMB_MIN_M", "CLIMB_DAYS", "CLIMB_EXTRA", "_climbs", "back_to_back"):
        assert not hasattr(st, name), name


def test_back_to_back_days_are_two_efforts_each_its_own_window():
    """Adjacent outings keep separate classes, tags and legacy windows; they are not merged into an ultra.
    Neither window changes the historical index when the measured nightly signals are identical."""
    d18 = _session(date(2026, 9, 18), 491.6, elapsed=544, hour=10, offset=7200, sid=1, dplus=3511,
                   name="Morning Trail Run")
    d19 = _session(date(2026, 9, 19), 399.9, elapsed=423.7, hour=9, offset=7200, sid=2, dplus=2847,
                   name="Morning Trail Run")
    a, b = st.efforts([d18, d19])
    assert (a.kind, a.day, a.last, b.kind, b.day, b.last) == ("very_long", date(2026, 9, 18), 3,
                                                             "very_long", date(2026, 9, 19), 3)
    tones = {k: (st.effort_window([a, b], date(2026, 9, k)) or {}).get("tone") for k in range(18, 27)}
    assert tones == {18: "warn", 19: "warn", 20: "warn", 21: "warn", 22: "warn", 23: None, 24: None, 25: None,
                     26: None}
    assert st.effort_window([a, b], date(2026, 9, 22))["effort"].session_id == 2
    # each past day of the 14-day history saw what it knew: the 18th alone on the 18th, both from the 19th
    rows, sessions = _rich(today=date(2026, 9, 26)), _runs(today=date(2026, 9, 26), start=5) + [d18, d19]
    hist = {d: score["value"] for d, _, score in _history(rows, sessions, today=date(2026, 9, 26))}
    assert [hist[date(2026, 9, k)] for k in (17, 18, 21, 22, 23, 25)] == [100, 100, 100, 100, 100, 100]


async def test_the_method_folds_and_their_sources(as_user, db_session, test_user, monkeypatch):
    """The reading help lives on a separate authenticated page, with optional detailed calculations
    and links to the scientific references; the dashboard stays focused on measurements."""
    import re

    from app.services import sante
    from app.services import sante_sleep as sl
    from tests.owner_v4 import D8, seed_owner_v4

    async def today(*a, **k):
        return D8
    monkeypatch.setattr(sante, "athlete_today", today)
    await seed_owner_v4(db_session, test_user)
    html = (await as_user.get("/sante")).text
    assert 'href="/sante/aide"' in html and "Comment je calcule" not in html
    help_page = await as_user.get("/sante/aide")
    assert help_page.status_code == 200
    folds = re.findall(r'<details class="pf-fold">(.*?)</details>', help_page.text, re.S)
    assert len(folds) == 2
    assert any("Quelles mesures améliorer ?" in fold for fold in folds)
    folds = [fold for fold in folds if "<summary>Calcul détaillé</summary>" in fold]
    assert len(folds) == 1
    fold = folds[0]
    assert "<summary>Calcul détaillé</summary>" in fold and len(re.findall(r"<li>", fold)) == len(sc.METHOD) == 5
    assert "(H)" not in fold and "pf-method-h" not in fold and "doi.org" not in fold
    assert not re.search(r"\(\w[\w ]* (19|20)\d\d", fold)  # no « (Kellmann 2018) »
    assert 'href="/sante/sources">Références scientifiques ›</a>' in fold
    r = await as_user.get("/sante/sources")
    assert r.status_code == 200
    page = r.text.split('id="main-content"', 1)[1]
    assert '<a href="/sante" class="pf-back">‹ Santé</a>' in page and '<h1 class="pf-sante-h1">Sources</h1>' in page
    assert re.findall(r'<h2 id="h-(\w+)" class="pf-sec-h">([^<]+)</h2>', page) == [("recuperation", "Récupération"),
                                                                                   ("sommeil", "Sommeil")]
    assert re.findall(r'<h3 class="pf-method-h">([^<]+)</h3>', page) == ["Recommandations officielles", "Études scientifiques"] * 2
    for refs in (sc.REFS, sl.REFS):
        for _, items in sc.linked(refs):
            for name, href in items:
                assert f'<a href="{href}" rel="noopener" target="_blank">{name}</a>' in page, name
    assert '<a href="https://doi.org/10.1123/ijspp.2017-0759" rel="noopener" target="_blank">Kellmann 2018</a>' in page
    assert 'href="https://westminsterresearch.westminster.ac.uk/item/wxx7y/' in page
    assert 'href="https://www.thensf.org/wp-content/uploads/2022/10/ANSI-CTA-NSF-2052.1-A-FINAL.pdf"' in page
    assert len(re.findall(r"<li>", page.split("</main>")[0])) == sum(len(i) for refs in (sc.REFS, sl.REFS)
                                                                 for _, i in refs)
    assert (await as_user.get("/sante/sources", follow_redirects=False)).status_code == 200
