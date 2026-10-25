"""Santé's daily state (owner, 2026-10-14: scientific basis — it replaced a weighted 0–100 index and its caps):
effort classes from the activities alone and their windows (a reason: red on D+0/D+1 after an Ultra, orange to
D+1 / D+2 raced / D+3 / D+5 / D+7 ≥ 24 h), the nights after a ≥ 6 h effort tagged (they never fire the illness
alert, and count in the usual values like every measured night: owner, 2026-10-08), the state from its reasons
(the alert; the primary marker's trend outside its smallest worthwhile change; the breathing flag; under 6 h of
sleep; an effort window) and its « indice nocturne » held to the state's range, the 14-day history as each day
computed it, and the (H) heuristics pinned."""
import json
import math
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import nights as nt
from app.services import sante
from app.services import sante_score as sc
from app.services import sante_sleep as sl
from app.services import sante_today as td
from app.services import sante_training as st
from app.services.sante_training import Session
from tests.test_nights import night_rows as _night_rows

D = date(2026, 10, 8)


def night_rows(days, **kw):
    """test_nights.night_rows, offsets before D (8 Oct) unless told otherwise."""
    kw.setdefault("today", D)
    return _night_rows(days, **kw)


def _session(day, minutes, dplus=0, sid=7, sport="Run", hr=125, elapsed=None, hour=7, offset=0, workout_type=0,
             name="Sortie"):
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=hour) \
        - timedelta(seconds=offset)
    return Session(id=sid, start=start, day=day, sport=sport, minutes=minutes, dplus=dplus, km=minutes / 6,
                   speed=2.8, hr=hr, hr_peak=160, workout_type=workout_type, temp=None,
                   elapsed=elapsed if elapsed is not None else minutes, offset=offset, name=name)


def _runs(n=8, today=D, start=2):
    """`n` easy runs of 50 min every 4 days, the latest `start` days ago: ≥ 6 activities in 42 days."""
    return [_session(today - timedelta(days=start + 4 * i), 50, sid=100 + i) for i in range(n)]


def _rich(hrv_last=None, hr_last=None, days=60, today=D, asleep=440):
    """A Garmin athlete every night: HRV ≈ 70 ms (ln spread ≈ 0.11), nightly HR 44–46, 7h20 asleep."""
    return _night_rows(range(0, days), today=today, asleep=asleep,
                      hr=lambda k: hr_last if hr_last is not None and k < 7 else 44.0 + k % 3,
                      hrv=lambda k: hrv_last if hrv_last is not None and k < 7 else 70 * math.exp(0.08 * (k % 5 - 2)))


def _nights(rows, sessions=(), today=D):
    nights = nt.build_nights(rows, today)
    nt.tag_activities(nights, list(sessions), st.efforts(list(sessions)), nt.rest_hr(nights, today), 190)
    nt.tag_alerts(nights, today)
    return nights


def _day(rows, sessions=(), today=D, base=None):
    """What health_page computes for `today` (state, score) on these rows and activities (the efforts anchored on
    the nights, as health_page does). `base`: kept for the callers; the need is 8 h for everyone (2026-10-14)."""
    sessions = list(sessions)
    nights = _nights(rows, sessions, today)
    with nt.memo():
        nt.freeze(nights)
        return sante._assess(nights, sessions, nt.anchor_efforts(nights, st.efforts(sessions)), today)


def _history(rows, sessions=(), today=D, base=None):
    sessions = list(sessions)
    nights = _nights(rows, sessions, today)
    with nt.memo():
        nt.freeze(nights)
        return sante._history(nights, sessions, nt.anchor_efforts(nights, st.efforts(sessions)), today)


def _example(z_hrv=None, z_hr=None, tst=450, window=None, alert=None, resp=None, n7=7) -> dict:
    """One synthetic day from its trends' z (a full baseline of 30 nights, last night measured, n7 nights in the
    week), its 24 h, its effort window, alert and breathing flag: what sante_score.score_of reads."""
    def trend(z):
        if z is None:
            return None
        t = nt.threshold(range(1 - n7, 1), 30)  # n7 nights in a row up to last night, a baseline of 30
        return {"source": "Garmin", "n": n7, "last": True, "z": z, "T": t, "value": 1.0, "baseline_n": 30,
                "baseline": {"n": 30, "provisional": False, "center": 1.0, "sd": 0.1},
                "status": "below" if z <= -t else "above" if z >= t else "in"}
    day = {"day": D, "night_measured": True, "trends": {"hrv": trend(z_hrv), "hr": trend(z_hr)}, "tst24": tst,
           "window": window, "alert": alert, "resp": resp}
    score = sc.score_of(day)
    return {"score": score, "state": td.state(score, day), "day": day}


# ── effort classes, from the activities alone ───────────────────────────────

def test_effort_classes_by_their_own_time_stops_included():
    assert st.effort_of(_session(D, 170)) is None  # under 3 h, flat: no effort
    assert st.effort_of(_session(D, 170, dplus=1500)).kind == "long"  # the legs rule: 1 500 m D+ on foot
    assert st.effort_of(_session(D, 170, dplus=1500, sport="Ride")) is None  # D+ on a bike is no legs rule
    assert st.effort_of(_session(D, 150, elapsed=185)).kind == "long"  # 3 h stops included
    assert st.effort_of(_session(D, 300, elapsed=365)).kind == "very_long"
    # any activity, but a low-impact one is one class lower (M4, v4.2): its nights still follow its time
    ride = st.effort_of(_session(D, 340, elapsed=360, sport="Ride"))
    assert (ride.kind, ride.nights) == ("long", "very_long")
    assert st.effort_of(_session(D, 560, elapsed=600)).kind == "ultra"
    # a race is just an activity: the same classes by its own size
    assert st.effort_of(_session(D, 170, workout_type=1)) is None
    assert st.effort_of(_session(D, 935, elapsed=1013, workout_type=1)).kind == "ultra"
    # a watch left running (elapsed over twice the moving time, H): the moving time counts
    assert st.effort_of(_session(D, 60, elapsed=400)) is None
    assert st.effort_minutes(_session(D, 60, elapsed=400)) == 60


def test_an_effort_ends_on_its_own_local_day():
    """The Transjeju: 02/10 21:00 in Korea + 16h53 → ended 03/10 13:53: D is 03/10."""
    s = _session(date(2026, 10, 2), 935, elapsed=1013, hour=21, offset=32400, workout_type=1)
    e = st.effort_of(s)
    assert (e.kind, e.day, e.end) == ("ultra", date(2026, 10, 3), datetime(2026, 10, 3, 13, 53))
    assert e.big and not st.effort_of(_session(D, 200)).big


@pytest.mark.parametrize("kind,elapsed,windows", [
    # owner, 2026-10-14: scientific basis — a reason, no cap (was 35 → 65 → 95 to D+10, 45 → 65 to D+5, 65 to D+3)
    ("ultra", 600, {1: "danger", 2: "warn", 5: "warn", 6: None}),  # red D+0/D+1, orange to D+5
    ("very_long", 360, {1: "warn", 3: "warn", 4: None}),  # orange to D+3 (Fazackerley 2019)
    ("long", 180, {1: "warn", 2: None}),  # orange to D+1 (raced: D+2, Petersen 2007)
])
def test_each_class_opens_its_window(kind, elapsed, windows):
    """D+0, the day it ends (once it is uploaded), is already in its window, with D+1's tone: a big outing done
    today is never « pas de grosse sortie » (OWN-B / G); « fenêtre indicative jusqu'à J+n »."""
    e = st.effort_of(_session(D, elapsed))
    w0 = st.effort_window([e], D)
    assert e.kind == kind and (w0["days"], w0["ago"], w0["tone"]) == (0, 0, windows[1])
    assert w0["text"] == f"fenêtre indicative jusqu'à J+{e.last}" and w0["until"] == D + timedelta(days=e.last)
    assert st.effort_window([e], D - timedelta(days=1)) is None  # the day before it: nothing
    for k, tone in windows.items():
        w = st.effort_window([e], D + timedelta(days=k))
        assert (w["tone"] if w else None) == tone, (kind, k)
    w = st.effort_window([e], D + timedelta(days=1))
    assert w["days"] == 1 and "load" not in w and "cap" not in w  # 2026-10-08: no Charge; 2026-10-14: no cap


def test_overlapping_windows_keep_the_strongest_then_the_bigger_effort():
    """Two windows open: red first, then the bigger effort (its class), then the latest (it was the lowest
    cap)."""
    ultra = st.effort_of(_session(D - timedelta(days=4), 700, sid=1))  # D+4: orange
    long = st.effort_of(_session(D - timedelta(days=1), 200, sid=2))  # D+1: orange
    w = st.effort_window([ultra, long], D)
    assert w["effort"].session_id == 1 and w["tone"] == "warn"  # both orange: the bigger effort names it
    fresh = st.effort_of(_session(D - timedelta(days=1), 700, sid=3))  # an Ultra D+1: red
    assert st.effort_window([ultra, long, fresh], D)["effort"].session_id == 3


# ── the nights after a big effort ───────────────────────────────────────────

def test_the_three_nights_after_six_hours_are_tagged_and_never_fire_the_alert():
    rows = night_rows(range(0, 40))
    sessions = [_session(D - timedelta(days=5), 420, sid=5)]  # 7 h ending at 14:00 on D-5
    nights = _nights(rows, sessions)
    tagged = sorted(d for d, n in nights.items() if "big" in n.tags)
    assert tagged == [D - timedelta(days=4), D - timedelta(days=3), D - timedelta(days=2)]  # D+1 → D+3
    assert nt.TAG_WORDS["big"] == "après grosse sortie"
    assert not any(nt.alert_night(nights, (), d) for d in tagged) and nt.alert_night(nights, (), D)
    # they count in the bands and the means like any night (owner, 2026-10-08)
    assert nt.band(nights, "hr", D)["n"] == 39 and nt.normal(nights, "hr", D)["n"] == 32  # days −39 → −8
    assert nt.mean7(nights, "hrv", D)["n"] == 7
    # a 3-h effort tags nothing (only the night after a session ≥ 90 min is « sortie longue »: Myllymäki 2012)
    long = _nights(rows, [_session(D - timedelta(days=5), 200, sid=5)])
    assert not any("big" in n.tags for n in long.values())


def test_a_sleep_right_after_an_ultra_finished_at_dawn_is_tagged():
    """An ultra ending at 05:00: the sleep that started after it, waking that same day, is the first night after
    (D+1: D is the day before), and exactly 4 nights are tagged (DAWN-FINISH; v4.2: D+1 → D+4); one ending at
    06:00, after that morning's 05:00 sleep began, keeps D on its own day."""
    rows = {"sleep": {}}
    for k in range(20):
        d = D - timedelta(days=k)
        rows["sleep"][d] = (340, {"main_start": f"{d}T05:00", "main_end": f"{d}T11:00"}, "Garmin")  # 05:00 → 11:00
    s = _session(D - timedelta(days=4), 1080, hour=12)  # D-4 12:00 + 18 h → D-3 06:00
    nights = _nights(rows, [s])
    assert st.effort_of(s).day == D - timedelta(days=3)
    # D-3's sleep started at 05:00, before the finish at 06:00: not after it; D-2 → D are D+1 → D+3 (D+4 is
    # tomorrow): « après ultra » (an ultra: D+1 → D+4, v4.2)
    assert sorted(d for d, n in nights.items() if "ultra" in n.tags) == [D - timedelta(days=k) for k in (2, 1, 0)]
    s = _session(D - timedelta(days=4), 1020, hour=12)  # 17 h → D-3 05:00: the 05:00 sleep is after it
    nights = _nights(rows, [s])
    assert st.effort_of(s).day == D - timedelta(days=4)  # ended before 06:00: D is the day before (H)
    assert sorted(d for d, n in nights.items() if "ultra" in n.tags) == [D - timedelta(days=k)
                                                                         for k in (3, 2, 1, 0)]


def test_tagged_nights_never_fire_the_illness_alert():
    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)  # the last 2 nights very high
    alert = _day(rows, _runs())
    assert alert["state"]["key"] == "ill"
    after = _day(rows, _runs() + [_session(D - timedelta(days=2), 500, sid=9)])  # ≥ 6 h on D-2: its nights tagged
    assert after["alert"] is None and after["state"]["key"] == "hr"
    assert [r["key"] for r in after["score"]["reasons"]] == ["hr"]


# ── the state: its reasons, red first ───────────────────────────────────────

def test_rung_1_the_alert_comes_first_even_in_a_window():
    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)
    ultra = _session(D - timedelta(days=7), 1500, sid=9)  # D+7 of a 25-h ultra: its 4 nights after are not the last 2
    day = _day(rows, _runs() + [ultra])
    assert (day["window"]["days"], day["window"]["tone"]) == (6, "warn")  # it ended on D-6
    assert (day["state"]["key"], day["state"]["word"]) == ("ill", "Signaux inhabituels")
    assert [(r["key"], r["tone"]) for r in day["state"]["reasons"]] == [("ill", "danger")]
    # the one sentence the page still prints (v4.3): what the alert can mean
    assert day["state"]["text"] == ("Ta FC de nuit est nettement plus haute que d'habitude. Ça arrive avant un rhume, "
                                    "après de l'alcool ou une grosse journée.")  # its 2 nights: the FC de nuit card
    assert 0 <= day["score"]["value"] <= 39 and day["score"]["tone"] == "danger"
    # a provisional baseline (7 nights from day −14 to −8) never fires it: specific, not sensitive (Quer 2021)
    young = night_rows(range(0, 15), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)
    assert _day(young, _runs())["alert"] is None


def test_the_indice_never_contradicts_the_state():
    """The indice nocturne: 100 × the mean of s_VFC = clip((z + 2.5)/2, 0, 1) and s_FC = clip((2.5 − z)/2, 0, 1),
    held to ≤ 69 when orange, ≤ 39 when red, ≥ 70 when green (owner, 2026-10-14: scientific basis — it replaced a
    weighted mean of sub-scores and a « red component » cap of 69). A week at 35 ms (z −6,2 against days −67 → −8):
    the VFC primary marker below, orange, s_VFC 0 and s_FC 1: 50. VFC 54 ms with FC + 2,5 bpm: z −2,3 and + 2,5
    (the FC de nuit's spread is its SD around its mean, 1,0 bpm here: owner, 2026-10-14: scientific basis — it was
    the robust 1,5 around the median, z + 1,6, 26), both cardiac series give independent orange reasons: 4. A VFC at 60 ms (z −1,4): orange, its mean 77 held to 69."""
    far = _day(_rich(hrv_last=35.0), _runs())
    parts = {p["key"]: p for p in far["score"]["parts"]}
    assert parts["hrv"]["s"] == 0 and parts["hr"]["s"] == 1 and far["score"]["value"] == 50
    assert (far["state"]["key"], far["state"]["tone"], far["state"]["text"]) == ("hrv", "warn", None)
    up = _day(_rich(hrv_last=54.0, hr_last=47.5), _runs())
    parts = {p["key"]: p for p in up["score"]["parts"]}
    assert (parts["hrv"]["status"], parts["hr"]["status"]) == ("below", "above")
    assert [r["key"] for r in up["score"]["reasons"]] == ["hrv", "hr"]
    assert up["score"]["value"] == sc.rounded(100 * (parts["hrv"]["s"] + parts["hr"]["s"]) / 2) == 4
    near = _day(_rich(hrv_last=60.0), _runs())
    s_hrv = next(p for p in near["score"]["parts"] if p["key"] == "hrv")["s"]
    assert sc.rounded(100 * (s_hrv + 1) / 2) == 77 and near["score"]["value"] == 69  # held to the orange range
    assert (near["state"]["key"], near["state"]["tone"]) == ("hrv", "warn")


def test_the_reasons_are_listed_red_first():
    """Measured signal reasons are ordered red first, then cardiac trends, respiration and short sleep."""
    ultra = {"tone": "danger", "text": "fenêtre indicative jusqu'à J+5", "days": 1}
    long = {"tone": "warn", "text": "fenêtre indicative jusqu'à J+1", "days": 1}
    x = _example(z_hrv=-1.0, z_hr=0, tst=330, window=long, resp={"mean": 17})
    assert [(r["key"], r["tone"]) for r in x["score"]["reasons"]] == [("hrv", "warn"), ("resp", "warn"),
                                                                      ("short", "warn")]
    assert x["state"]["key"] == "hrv" and x["score"]["tone"] == "warn" and x["score"]["value"] <= 69
    assert all(r["key"] != "effort" for r in x["score"]["reasons"])
    red = _example(z_hrv=0, z_hr=0, window=ultra, alert={"values": [58, 58], "resp_up": None})
    assert [r["key"] for r in red["score"]["reasons"]] == ["ill"] and red["score"]["value"] == 39
    assert _example(z_hrv=0, z_hr=0)["score"]["reasons"] == [] and _example(z_hrv=0)["state"]["key"] == "ok"
    # The FC de nuit above its reference is a reason even when VFC is inside its own.
    assert [r["key"] for r in _example(z_hrv=0, z_hr=2.0)["score"]["reasons"]] == ["hr"]
    assert [r["key"] for r in _example(z_hr=2.0)["score"]["reasons"]] == ["hr"]
    assert sc.LABELS["resp"] == "Respiration plus rapide depuis 2 nuits" and sc.TEXTS["short"]


def test_rung_4_a_short_24_hour_total():
    rows = _rich()
    rows["sleep"].update(night_rows([0], asleep=330, start=(23, 30), end=(6, 30))["sleep"])  # 5h30, no nap
    day = _day(rows, _runs())
    assert (day["state"]["key"], day["state"]["text"]) == ("short", None)  # the reason, never printed (v4.3)
    assert day["score"]["tone"] == "warn" and day["score"]["value"] == 69  # its heart 100, held to orange (was 65)
    # a nap in the 24 h before the wake (yesterday 13:00) lifts the total over 6 h: no short night (Craven 2022)
    y = D - timedelta(days=1)
    rows["nap"][y] = (60, {"windows": [[f"{y}T13:00", f"{y}T14:05"]]}, "Garmin")
    assert _day(rows, _runs())["state"]["key"] == "ok"
    # today's afternoon nap is tomorrow morning's 24 h (each nap once: owner's report 2026-10-09)
    rows["nap"] = {D: (60, {"windows": [[f"{D}T13:00", f"{D}T14:05"]]}, "Garmin")}
    assert _day(rows, _runs())["state"]["key"] == "short"


def test_rung_5_well_recovered_needs_a_nightly_signal():
    day = _day(_rich(), _runs())
    assert (day["state"]["key"], day["state"]["word"], day["state"]["text"]) == ("ok", "Signaux habituels", None)
    assert day["score"]["value"] == 100 and day["score"]["measured"] and day["state"]["reasons"] == []
    strava = _day({}, _runs())  # activities only: nothing measured
    assert strava["state"] is None and strava["score"]["value"] is None
    assert td.no_state_line(False) == "Connecte ta montre pour voir tes signaux nocturnes."
    # without a nightly signal no state, a window or not (owner, 2026-10-09, like WHOOP: « Mets un cadran vide,
    # oui »): the window is still there, never a state from activities alone
    ultra = _day({}, _runs() + [_session(D - timedelta(days=3), 700, sid=9, name="Ultra des Crêtes")])
    w, s = ultra["window"], ultra["score"]
    assert (w["days"], w["tone"]) == (3, "warn") and (s["value"], s["measured"], ultra["state"]) == (None, False, None)
    assert s["absent"] == ["hrv", "hr"] and s["building"] == []  # nothing ever measured: not « built »
    assert s["missing"] == {"key": "no_night", "label": "pas de bilan"}
    assert "estimated" not in s and not hasattr(sc, "ESTIMATED") and not hasattr(sc, "EST_READ")


# ── the indice nocturne ─────────────────────────────────────────────────────

def test_components():
    """s_VFC = clip((z + 2.5)/2, 0, 1): 100 from z −0.5, 0 at −2.5; s_FC = clip((2.5 − z)/2, 0, 1): 100 up to + 0.5,
    0 at + 2.5 (owner, 2026-10-14: scientific basis — the same construction as the VFC, it was 100 / 40 / 0 at + 2 /
    + 5 / + 8 bpm); never praised above (100 at most). No sleep component: under 6 h is a reason."""
    assert sc.sub("hrv", -0.5) == 1 and sc.sub("hrv", -2.5) == 0 and sc.sub("hrv", 2) == 1
    assert sc.sub("hrv", -1.5) == pytest.approx(0.5) and sc.sub("hrv", -3) == 0
    assert sc.sub("hr", 0.5) == 1 and sc.sub("hr", 2.5) == 0 and sc.sub("hr", 1.5) == pytest.approx(0.5)
    assert sc.sub("hr", -3) == 1
    for name in ("hrv_sub", "hr_sub", "sleep_sub", "WEIGHTS", "caps", "sleep_limit", "load_sub", "SLEEP_DEBT"):
        assert not hasattr(sc, name), name


def test_two_heart_series_average_and_a_window_stays_context():
    """The two cardiac series average into the index; a recorded effort cannot rewrite the measured signals."""
    day = _day(_rich(hrv_last=60.0), _runs(n=20))  # plenty of activities, no window
    assert [p["key"] for p in day["score"]["parts"]] == ["hrv", "hr"] and day["score"]["absent"] == []
    assert day["score"]["primary"] == "hrv"
    window = _day(_rich(), [_session(D - timedelta(days=1), 200, sid=9)])  # a long effort, D+1
    assert window["score"]["value"] == 100 and window["score"]["caps"] == []
    assert window["score"]["reasons"] == [] and window["state"]["key"] == "ok"


@pytest.mark.parametrize("z_hrv,z_hr,reasons,value,tone", [
    (0.0, 0.0, {}, 100, "ok"), (-0.4, 0.0, {}, 100, "ok"),
    (-0.4, 2.0, {}, 63, "warn"),  # the high FC is visible even when HRV is usual
    (-0.6, 0.0, {}, 98, "ok"),  # inside T = 0,70 (7 nights against 30, 2026-10-14; was orange under 0,5): 97,5
    (-0.75, 0.0, {}, 69, "warn"),  # orange: (0,875 + 1)/2 = 94 held down to 69
    (-2.0, 0.0, {}, 63, "warn"),  # (0,25 + 1)/2 = 62,5, half up
    (0.0, 1.0, {}, 69, "warn"),  # the high FC holds the index in the existing warning range
    (0.0, 0.0, {"tst": 330}, 69, "warn"), (0.0, 0.0, {"alert": {"values": [55, 56], "resp_up": None}}, 39, "danger"),
    (0.0, 0.0, {"window": {"tone": "danger", "text": "fenêtre indicative jusqu'à J+5"}}, 100, "ok"),  # context only
    (-2.6, 2.6, {"alert": {"values": [55, 56], "resp_up": None}}, 0, "danger")])
def test_the_state_comes_from_its_reasons(z_hrv, z_hr, reasons, value, tone):
    """The state from its reasons (owner, 2026-10-14: scientific basis — it was the band of a weighted score, ≥ 70,
    40–69, < 40): none green, an orange one « Signaux à surveiller », a red one « Signaux inhabituels »; the indice
    held to the state's range; rounded half up on the exact value."""
    x = _example(z_hrv=z_hrv, z_hr=z_hr, **reasons)
    assert (x["score"]["value"], x["score"]["tone"]) == (value, tone)
    assert x["state"]["word"] == td.WORDS[tone]
    assert sc.rounded(69.5) == 70 and sc.rounded(64.4) == 64 and sc.rounded(39.5) == 40
    assert not hasattr(sc, "place") and not hasattr(sc, "BANDS") and sc.HOLD == {"ok": (70, 100), "warn": (0, 69),
                                                                                 "danger": (0, 39)}
    assert td.WORDS == {"ok": "Signaux habituels", "warn": "Signaux à surveiller", "danger": "Signaux inhabituels"}


def test_ultra_windows_do_not_rewrite_the_nightly_index():
    """Legacy windows remain available for context; their tones never set the nightly index."""
    big = _session(D - timedelta(days=1), 935, elapsed=1013, sid=42)  # D+1 after an ultra (it ended on D-1)
    early = _day(_rich(), _runs() + [big])
    assert (early["window"]["tone"], early["score"]["value"], early["score"]["tone"]) == ("danger", 100, "ok")
    assert early["state"]["key"] == "ok" and early["state"]["word"] == "Signaux habituels"
    assert early["score"]["measured"]  # nights measured
    four, five = timedelta(days=4), timedelta(days=5)
    later = _day(_rich(), _runs() + [replace(big, start=big.start - four, day=big.day - four)])
    assert (later["window"]["days"], later["window"]["tone"], later["score"]["value"]) == (5, "warn", 100)
    done = _day(_rich(), _runs() + [replace(big, start=big.start - five, day=big.day - five)])
    assert done["window"] is None and done["state"]["key"] == "ok"


def test_owner_like_day_no_state_without_a_heart_baseline():
    """One COROS night, 8h36 (an ultra on D+5): no heart baseline, so no state and no number (it was 65, the
    window's cap over Sommeil alone): the reason says the baselines are building; the window is still open."""
    rows = night_rows([0], asleep=516, start=(22, 42), end=(7, 30), source="COROS",
                      hr_method="coros_sleep_summary")
    day = _day(rows, [_session(D - timedelta(days=6), 935, elapsed=1013, hour=21, offset=32400, sid=8)])
    assert (day["window"]["days"], day["window"]["tone"]) == (5, "warn")
    assert day["score"]["value"] is None and day["state"] is None
    assert day["score"]["missing"]["key"] == "building" and day["score"]["missing"]["label"] == \
        "repères en construction (0/7)"


def test_no_state_from_sleep_alone():
    """Neither VFC nor FC de nuit with a full baseline (the watch worn some nights only): no state, never a number
    from sleep alone (it was capped at 80); with them, a 100 stays possible."""
    rows = night_rows([0], asleep=588, start=(23, 52), end=(9, 54), source="COROS", hr_method="coros_sleep_summary")
    day = _day(rows, _runs())  # 9h48
    assert day["state"] is None and day["score"]["value"] is None and day["score"]["parts"] == []
    sleep_only = {**night_rows(range(0, 40)), "hrv": {}, "hr_night": {}}
    assert _day(sleep_only, _runs())["score"]["missing"] == {"key": "no_heart", "label": "pas de bilan"}
    assert _day(_rich(), _runs())["score"]["value"] == 100


# ── the 14-day history: what each day knew ──────────────────────────────────

def _then(rows, sessions, d):
    """The page as computed on day `d`: from the rows stored by then, and the activities finished (uploaded) by
    the end of that day only."""
    past_rows = {m: {x: v for x, v in per.items() if x <= d} for m, per in rows.items()}
    midnight = datetime.combine(d + timedelta(days=1), datetime.min.time())
    return _day(past_rows, [s for s in sessions if st.local_end(s) <= midnight], d)


def test_the_history_is_the_score_each_day_had():
    rows = _rich(hrv_last=58.0, hr_last=49.0)
    sessions = _runs() + [_session(D - timedelta(days=6), 420, sid=9)]  # very long 6 days ago
    hist = _history(rows, sessions)
    assert len(hist) == 13
    for d, state, score in hist:
        then = _then(rows, sessions, d)
        assert score["value"] == then["score"]["value"], d
        assert (state or {}).get("key") == (then["state"] or {}).get("key"), d


def test_the_history_reads_the_series_as_far_back_as_the_live_page():
    """Garmin alone, then COROS and Garmin in strict turns for 190 days, longer than the 160 days of nights the
    history copies: each past day reads the series the live page read that day — the tie decided back to the last
    day the counts decided (Garmin), never the fixed order at the edge of the copy (COROS). On a COROS night the
    Garmin series has no last night: no state, the other watch's line (the second statistics review, 2026-10-14:
    the history read COROS on every day where the live page read Garmin)."""
    def watch(k):
        return "Garmin" if k >= 190 or k % 2 else "COROS"
    rows = {"sleep": {}, "hrv": {}, "hr_night": {}, "nap": {}}
    for k in range(330):
        one = _night_rows([k], source=watch(k), hr=48.0 if watch(k) == "Garmin" else 53.0,
                          hrv=60 * math.exp(0.08 * (k % 5 - 2)) if watch(k) == "Garmin" else 42.0)
        for metric in rows:
            rows[metric].update(one[metric])
    hist = {d: (score["value"], (state or {}).get("key"), (score.get("missing") or {}).get("key"))
            for d, state, score in _history(rows)}
    assert len(hist) == 13
    for d, past in hist.items():
        then = _then(rows, [], d)
        live = (then["score"]["value"], (then["state"] or {}).get("key"),
                (then["score"].get("missing") or {}).get("key"))
        assert past == live, d
        assert nt.device(then["trends"]["hrv"]["source"]) == "Garmin", d
    assert {v[2] for v in hist.values()} == {None, "elsewhere"}  # Garmin nights read, COROS nights « elsewhere »


def test_a_low_week_is_read_against_the_weeks_before_it():
    """The usual values stop a week before the day (owner, 2026-10-14: scientific basis — they held the week they
    judge, owner, 2026-10-08: its own 7 nights at 50 ms lowered the centre to 67,6 ms and widened the SD to 0,15,
    so a −26 % week read z −2,0 and « Signaux habituels »): against days −67 → −8 (52 nights, centre 70,3 ms, σ̂
    0,113) the same week reads z −3,0, the VFC below its usual values: orange, s_VFC 0, the indice 50."""
    day = _day(_rich(hrv_last=50.0), _runs())
    b = day["stats"]["hrv"]["normal"]
    assert b["n"] == 52 and round(b["center"], 1) == 70.3 and round(b["sd"], 3) == 0.113
    vfc = next(p for p in day["score"]["parts"] if p["key"] == "hrv")
    assert round(vfc["z"], 1) == -3.0 and vfc["s"] == 0 and vfc["status"] == "below"
    assert (day["score"]["value"], day["state"]["word"]) == (50, "Signaux à surveiller")
    assert next(p for p in _day(_rich(hrv_last=35.0), _runs())["score"]["parts"] if p["key"] == "hrv")["s"] == 0


def test_history_the_alert_fires_from_its_second_night_as_it_did_then():
    """The alert fires on the second night: the day of the first night still read it as it was then."""
    def hr(k):
        return 56.0 if k in (2, 3) else 47.5 if 4 <= k <= 9 else 44.0 + k % 3
    rows = night_rows(range(0, 70), hr=hr, hrv=lambda k: 35.0 if k in (2, 3) else 70 * math.exp(0.08 * (k % 5 - 2)))
    first = D - timedelta(days=3)
    then = _then(rows, _runs(), first)
    assert then["state"]["key"] != "ill" and _then(rows, _runs(), first + timedelta(days=1))["state"]["key"] == "ill"
    hist = {d: (s or {}).get("key") for d, s, _ in _history(rows, _runs())}
    assert hist[first] == then["state"]["key"] and hist[first + timedelta(days=1)] == "ill"


def test_history_computes_its_bands_and_alerts_once(monkeypatch):
    rows = _rich()
    calls = []
    real = nt._band

    def spy(*a, **k):
        calls.append((id(a[0]), a[1:], tuple(sorted(k.items()))))
        return real(*a, **k)
    monkeypatch.setattr(nt, "_band", spy)
    _history(rows, _runs())
    assert len(calls) == len(set(calls))  # each band of each nights dict once


def test_the_history_card_rests_on_its_title_and_shows_the_bands():
    """Nothing selected, no number (the audit, 2026-10-09: the 14 days' mean decided nothing and looked like the
    dial's, today's score is the ring's): its title rests in the readout's place; a tap says the score, the
    state's glyph and word (its tone on the figure, never colour alone) and the day, nothing else (v4.3); faint 40
    and 70 lines labelled on the right; today on a disc."""
    rows = _rich()
    nights = _nights(rows, _runs())
    with nt.memo():
        nt.freeze(nights)
        day = sante._assess(nights, _runs(), [], D)
        hist = sante._history(nights, _runs(), [], D) + [(D, day["state"], day["score"])]
    c = sc.history_card(hist, D)
    d = json.loads(c["data"])
    assert c["read"] == d["rest"] == ["100/100", "en moyenne", ""] and d["sel"] == 14 and d["back"] == 13
    assert c["title"] == "Signaux nocturnes · 14 jours" and "en moyenne" in c["data"]
    assert d["r"][-1] == ["100/100", "● Signaux habituels", "jeu. 8 oct."] and d["t"][-1] == "ok"
    assert d["a"][-1] == "jeudi 8 octobre : indice 100 sur 100, signaux habituels."
    assert all(b["cls"] == "ok" and not b["est"] for b in c["bars"]) and c["bars"][-1]["today"]
    assert c["hatched"] == []  # every day measured: no hatched bar, no legend for it
    assert [ln["label"] for ln in c["lines"]] == ["100", "70", "40", "0"] and c["lines"][0]["y"] < c["lines"][1]["y"]
    assert c["xt"][-1]["today"] and c["tone_now"] == ""


# ── the method and the heuristics ───────────────────────────────────────────

def test_an_alert_episode_is_read_by_the_score():
    """Seven nights at 56 bpm over a median of 45: the alert is red (its indice 39); the FC de nuit trend is far
    above (s_FC 0); the baseline (days −59 → −8) does not hold the episode: its median (the alert's) stays 45, its
    mean (the trend's, owner, 2026-10-14: scientific basis) 45,02."""
    rows = night_rows(range(0, 60), hr=lambda k: 56.0 if k < 7 else 44.0 + k % 3)
    day = _day(rows, _runs())
    normal = day["stats"]["hr"]["normal"]
    assert day["state"]["key"] == "ill" and normal["median"] == 45 and round(normal["center"], 2) == 45.02
    hr = next(p for p in day["score"]["parts"] if p["key"] == "hr")
    assert hr["s"] == 0 and day["score"]["value"] == 39 and day["score"]["tone"] == "danger"


# ── v4.1: the review's findings ─────────────────────────────────────────────

def test_dawn_finish_the_first_morning_after_is_in_its_window():
    """The first sleep after a dawn finish is tagged consistently; context does not set the index tone."""
    rows = _rich()
    rows["sleep"][D] = (400, {"main_start": f"{D}T04:00", "main_end": f"{D}T11:00", "timeline": True}, "Garmin")
    race = _session(D - timedelta(days=1), 1320, hour=5, sid=50, name="100 miles des Crêtes")  # → D 03:00
    day = _day(rows, _runs() + [race])
    assert st.effort_of(race).day == D - timedelta(days=1)
    assert (day["window"]["days"], day["window"]["tone"], day["score"]["value"]) == (1, "danger", 100)
    assert day["state"]["key"] == "ok" and day["state"]["text"] is None
    nights = _nights(rows, _runs() + [race])
    assert "ultra" in nights[D].tags and "ultra" not in nights[D - timedelta(days=1)].tags  # 22 h: an ultra
    assert [p["key"] for p in day["score"]["parts"]] == ["hrv", "hr"]
    assert day["state"]["reasons"] == []  # 6h40: not a short night
    for start in (datetime(2026, 10, 7, 4, 59), datetime(2026, 10, 7, 5, 1)):  # 19 h: 23:59 or 00:01
        run = _session(D - timedelta(days=1), 1140, hour=start.hour, sid=51)
        run = replace(run, start=start.replace(tzinfo=timezone.utc))
        w = _day(_rich(), _runs() + [run])["window"]
        assert (w["days"], w["tone"], w["ago"]) == (1, "danger", 1), start


def test_a_dawn_finish_then_a_daytime_sleep_is_anchored_on_the_nights():
    """An ultra ending at 06:30 (after 06:00), then asleep 07:00 → 13:00: that sleep is its first night after
    (nights.anchor_efforts), so D is the day before and that morning is D+1."""
    rows = _rich()
    rows["sleep"][D] = (340, {"main_start": f"{D}T07:00", "main_end": f"{D}T13:00", "timeline": True}, "Garmin")
    race = _session(D - timedelta(days=1), 1320, hour=8, sid=52)  # D-1 08:00 + 22 h → D 06:00 … 06:30 below
    race = replace(race, start=race.start + timedelta(minutes=30))
    assert st.effort_of(race).day == D  # 06:30: the proxy alone keeps it on D
    nights = _nights(rows, _runs() + [race])
    [e] = nt.anchor_efforts(nights, [st.effort_of(race)])
    assert e.day == D - timedelta(days=1) and "ultra" in nights[D].tags
    assert _day(rows, _runs() + [race])["window"]["days"] == 1


def test_two_windows_open_the_strongest_holds():
    """OVERLAP: an ultra 5 days ago (orange, its last day) and a 7-h outing 2 days ago (orange): the bigger effort
    names the window (it was the lowest cap's, the outing's 55); an ultra yesterday (red) would."""
    ultra = st.effort_of(_session(D - timedelta(days=5), 700, sid=1, name="Ultra"))
    hike = st.effort_of(_session(D - timedelta(days=2), 420, sid=2, name="Rando"))
    w = st.effort_window([ultra, hike], D)
    assert (w["effort"].session_id, w["tone"], w["text"]) == (1, "warn", "fenêtre indicative jusqu'à J+5")
    assert st.effort_window([hike], D)["text"] == "fenêtre indicative jusqu'à J+3"
    fresh = st.effort_of(_session(D - timedelta(days=1), 700, sid=3))
    assert st.effort_window([ultra, hike, fresh], D)["effort"].session_id == 3


def test_an_ultra_saved_as_two_activities_is_one_effort():
    """SPLIT-ULTRA: the Transjeju saved as 9h00 (21:00 → 06:00) then, 6 min later, 7h47 (→ 13:53): one ultra of
    16h53 (first start to last end, the stop included), named and linked after the first; its window (red to D+1,
    orange to D+5: under 24 h, its night run through no longer extends it) as if it were one activity. Apart by
    more than 30 min (H): two activities, two efforts (v4.3: no back-to-back merge, M3, any more): each its own
    window."""
    a = _session(date(2026, 10, 2), 540, hour=21, offset=32400, sid=81, name="Transjeju 100M")
    b = _session(date(2026, 10, 3), 467, hour=6, offset=32400, sid=82, name="Transjeju 100M (2)")
    b = replace(b, start=b.start + timedelta(minutes=6))
    [e] = st.efforts([a, b])
    assert (e.kind, e.session_id, e.name, e.minutes) == ("ultra", 81, "Transjeju 100M", 1013)
    assert (e.end, e.day, e.start_day) == (datetime(2026, 10, 3, 13, 53), date(2026, 10, 3), date(2026, 10, 2))
    assert [st.effort_window([e], date(2026, 10, k))["tone"] for k in (4, 5, 8)] == ["danger", "warn", "warn"]
    assert st.effort_window([e], date(2026, 10, 9)) is None  # was capped to 16/10
    far = replace(b, start=b.start + timedelta(minutes=40))
    assert [(x.kind, x.minutes, x.session_id) for x in st.efforts([a, far])] == [("very_long", 540, 81),
                                                                                ("very_long", 467, 82)]


def test_a_past_day_never_reads_a_later_morning_for_its_naps():
    """An afternoon nap no next morning counts (the watch was not worn the next night) goes back to its own day once
    later days are in the data — while that day was the latest, it was « pending », counted by no morning. A past
    day of the 14-day card read its 24 h with the later nights there, so its total and its state could change
    (owner, 2026-10-14: scientific basis — 42 of 1170 days, 5 states). Each day reads the nights up to itself."""
    rows = night_rows(range(0, 40), asleep=330)  # 5h30 every night: « Sommeil court »
    x = D - timedelta(days=2)
    for metric in rows:
        rows[metric].pop(D - timedelta(days=1), None)  # the next night not worn
    rows["nap"][x] = (60, {"windows": [[f"{x}T14:00", f"{x}T15:00"]]}, "Garmin")  # the afternoon after its wake
    nights = _nights(rows)
    assert nt.day_tst24(nights, x) == 390  # with the later days: the nap went back to it
    with nt.memo():
        nt.freeze(nights)
        past = sante._assess(nights, [], [], x)
    assert past["tst24"] == 330 and "short" in [r["key"] for r in past["score"]["reasons"]]  # as that day saw it
    hist = {d: score for d, _, score in _history(rows)}
    assert (hist[x]["value"], hist[x]["tone"], hist[x]["reason"]) == (
        past["score"]["value"], past["score"]["tone"], past["score"]["reason"])


def test_last_night_on_another_watch_says_so_rather_than_no_night():
    """COROS five nights a week, Garmin the other two, last night on Garmin: the series stays COROS's (the most
    nights), so last night is not on it and there is no state; the line says why — it read « il manque des
    données de nuit » though a night was measured (owner, 2026-10-14). One Garmin night of another calculation:
    the same, « avec un autre calcul »."""
    coros = night_rows([k for k in range(0, 60) if k % 7 not in (0, 3)], source="COROS",
                       hr_method="coros_sleep_summary")
    garmin = night_rows([k for k in range(0, 60) if k % 7 in (0, 3)], source="Garmin")
    rows = {m: {**coros[m], **garmin[m]} for m in coros}
    day = _day(rows)
    assert day["score"]["value"] is None and day["trends"]["hrv"]["source"] == "COROS"
    assert day["score"]["missing"] == {"key": "elsewhere", "kind": "device",
                                       "label": "dernière nuit mesurée par une autre montre que d'habitude"}
    assert td.no_state_line(True, day["score"]["missing"]) == (
        "Pas de score ce matin : dernière nuit mesurée par une autre montre que d'habitude.")
    staged = night_rows(range(1, 60))
    for metric in ("hrv", "hr_night"):
        for d, (v, det, src) in list(staged[metric].items()):
            staged[metric][d] = (v, {**det, "scope": "sleep_stages_v1"}, src)
    window = night_rows([0])
    for metric in ("hrv", "hr_night"):
        v, det, src = window[metric][D]
        window[metric][D] = (v, {**det, "scope": "main_window_v1"}, src)
    other = _day({m: {**staged[m], **window[m]} for m in staged})
    assert other["score"]["missing"]["key"] == "elsewhere" and other["score"]["missing"]["kind"] == "method"


def test_the_day_after_an_alert_stops_firing_the_episode_is_still_read():
    """POST-ALERT-EPISODE: 56 bpm on 06/10 and 07/10 (alert line 50), 49 on 08/10: the alert stops, the 7-night
    mean still reads the episode's nights (48,57 against a median of 45, its baseline without them): the FC de nuit
    card is above its usual values in orange, never « dans tes valeurs habituelles »; the VFC (the primary marker)
    is in them, so the state is green and the FC shows on its card only (owner, 2026-10-14: scientific basis — the
    score was 90 with an orange FC row), its indice held to 70."""
    rows = night_rows(range(0, 60), hr=lambda k: 56.0 if k in (1, 2) else 49.0 if k == 0 else 44.0 + k % 3)
    assert _day(rows, _runs(), D - timedelta(days=1))["state"]["key"] == "ill"
    day = _day(rows, _runs())
    assert day["alert"] is None and day["stats"]["hr"]["normal"]["median"] == 45
    hr = next(p for p in day["score"]["parts"] if p["key"] == "hr")
    assert round(day["stats"]["hr"]["value"], 2) == 48.57 and hr["status"] == "above"
    assert day["state"]["key"] == "hr" and day["score"]["value"] == 50
    assert [r["key"] for r in day["score"]["reasons"]] == ["hr"]
    nights = _nights(rows, _runs())
    with nt.memo():
        nt.freeze(nights)
        card = sante._night_card(nights, "hr", D, day)
    assert card["status"] == {"key": "above", "value": "+8\u00a0%", "word": "plus haute que d'habitude",
                              "text": "+8\u00a0% plus haute que d'habitude", "tone": "warn",
                              "meaning": "Ça arrive avec la fatigue, la chaleur, l'alcool ou un début de maladie."}
    assert 100 * (day["stats"]["hr"]["value"] - 45) / 45 == pytest.approx(7.94, abs=0.01)  # 48,57 against 45


def test_the_illness_alert_makes_the_nightly_hr_card_red():
    """UX1: under « Signaux inhabituels » for the nightly-HR alert, FC de nuit reads the alert's 2 nights, never
    green (5 normal nights dilute the 7-night mean): its card says « nettement plus haute depuis 2 nuits » in red,
    and the alert is the state's first reason (its sentence printed under the dials)."""
    rows = night_rows(range(0, 40), hr=lambda k: 53.0 if k < 2 else 44.0 + k % 3)
    day = _day(rows, _runs())
    assert day["state"]["key"] == "ill" and day["score"]["value"] <= 39
    assert day["stats"]["hr"]["value"] - day["stats"]["hr"]["normal"]["center"] < 3  # diluted
    assert day["state"]["reasons"][0] == {"key": "ill", "tone": "danger", "label": sc.LABELS["ill"],
                                          "text": td.ILL}
    nights = _nights(rows, _runs())
    with nt.memo():
        nt.freeze(nights)
        card = sante._night_card(nights, "hr", D, day)
    # its 2 nights (53 bpm) against the usual value (45): + 18 %
    assert day["stats"]["hr"]["normal"]["center"] == 45 and day["alert"]["values"] == [53.0, 53.0]
    assert card["status"] == {"key": "above", "value": "+18\u00a0%", "word": "nettement plus haute depuis 2 nuits",
                              "text": "+18\u00a0% nettement plus haute depuis 2 nuits", "tone": "danger",
                              "meaning": "Ça arrive avec la fatigue, la chaleur, l'alcool ou un début de maladie."}
    assert (card["period"], card["status_short"]) == ("2 nuits", "Élevée sur 2 nuits")


def test_a_nap_yesterday_afternoon_counts_in_the_24_hours_before_the_wake():
    """A 1h40 nap yesterday 16:00 → 17:45, then 5h00 (01:20 → 06:40): 6h40 asleep in the 24 h before
    the wake, over the 6 h of a short night; the Sommeil dial and the state read that one figure. A
    « rendormi » nap of the day before (≤ 3 h after its wake) is the night before's: never counted twice."""
    rows = _rich()
    rows["sleep"][D] = (300, {"main_start": f"{D}T01:20", "main_end": f"{D}T06:40", "timeline": True}, "Garmin")
    y = D - timedelta(days=1)
    rows["nap"][y] = (100, {"windows": [[f"{y}T16:00", f"{y}T17:45"]]}, "Garmin")
    day = _day(rows, _runs())
    assert day["tst24"] == 400 and "need" not in day  # 6h40: over 6 h, no short-sleep reason
    assert (day["state"]["key"], day["score"]["value"]) == ("ok", 100)  # was 92: a sleep precaution under 7h20
    dial = sante.sleep_dial(day["tst24"], 480, "#sommeil")
    assert (dial["value"], dial["sub"], dial["tone"]) == ("83", "un peu court", "sleep")  # 6h40 of 8 h: its own hue
    nights = nt.build_nights(rows, D)
    # each day's bar reads that figure: the nap on D's, never on y's too (owner's report 2026-10-09)
    assert nt.day_tst24(nights, D) == 400 and nt.day_tst24(nights, y) == 440 and nt.bar_nap_min(nights, y) == 0
    # a « rendormi » nap of the day before (06:42, its wake 06:40): never counted again
    rows["nap"][y] = (100, {"windows": [[f"{y}T06:42", f"{y}T08:27"]]}, "Garmin")
    assert nt.day_tst24(nt.build_nights(rows, D), D) == 300
    # a nap partly before the 24 h (05:40 → 07:40, the wake at 06:40): only its part inside them
    rows["nap"][y] = (120, {"windows": [[f"{y}T05:40", f"{y}T07:40"]]}, "Garmin")
    v, det, src = rows["sleep"][y]
    rows["sleep"][y] = (v, {**det, "main_end": f"{y}T02:00"}, src)  # its own wake 02:00: not « rendormi »
    assert nt.day_tst24(nt.build_nights(rows, D), D) == 300 + 60  # 06:40 → 07:40 of its 2 h


def test_history_reads_only_the_activities_finished_that_day():
    """HISTORY-OVERNIGHT-LEAK: a 9-h hike from 05/10 21:00 to 06/10 06:00 at 2 500 m is uploaded on 06/10: the
    05/10 bar is the page of 05/10 (the hike unknown: no « en altitude » on that night, no 6th activity)."""
    rows = _rich(hrv_last=58.0)
    hike = replace(_session(D - timedelta(days=3), 540, hour=21, sid=60, sport="Hike", name="Rando de nuit"),
                   alt=2500.0, located=True)  # ended at 2 500 m (v4.4: the night after it is « en altitude »)
    sessions = _runs(n=5) + [hike]
    hist = {d: (state, score) for d, state, score in _history(rows, sessions)}
    for d, (state, score) in hist.items():
        then = _then(rows, sessions, d)
        assert score["value"] == then["score"]["value"], d
        assert (state or {}).get("key") == (then["state"] or {}).get("key"), d
    d5 = D - timedelta(days=3)
    assert hist[d5][1]["value"] == _then(rows, _runs(n=5), d5)["score"]["value"]


def test_the_day_a_big_outing_ends_it_already_counts():
    """A completed ultra is recorded on its finish day without rewriting that morning’s signals."""
    ultra = _session(D, 660, hour=5, sid=70, name="Grand Raid")
    day = _day(_rich(), _runs() + [ultra])
    assert (day["window"]["days"], day["window"]["tone"], day["score"]["value"]) == (0, "danger", 100)
    assert day["state"]["key"] == "ok" and day["state"]["text"] is None
    long = _session(D, 210, dplus=1600, hour=7, sid=71, name="Grand tour")  # a 3h30 outing this morning: long
    assert _day(_rich(), _runs() + [long])["score"]["value"] == 100
    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)  # an alert: the window too
    ill = _day(rows, _runs() + [ultra])
    assert ill["state"]["key"] == "ill" and ill["window"]["tone"] == "danger" and ill["score"]["value"] <= 39
    assert [r["key"] for r in ill["state"]["reasons"]] == ["ill"]


def test_an_effort_window_does_not_create_a_signal_reason():
    """The recorded effort keeps its local finish and window metadata, separately from the nightly index."""
    big = _session(D - timedelta(days=5), 935, elapsed=1013, sid=42, name="Trail des Glaciers")
    day = _day(_rich(), _runs() + [big])
    st_ = day["state"]
    assert (st_["key"], st_["tone"], st_["word"], st_["text"]) == ("ok", "ok", "Signaux habituels", None)
    assert "href" not in st_ and st_["aria"] == "Signaux habituels."
    assert st_["reasons"] == []
    night = _session(D - timedelta(days=6), 935, elapsed=1013, hour=21, offset=32400, sid=44, name="Transjeju 100M")
    w = _day(_rich(), _runs() + [night])["window"]
    assert (w["days"], w["ago"]) == (5, 6)
    assert not hasattr(td, "effort_text") and not hasattr(td, "short_text") and not hasattr(td, "TEXTS")


def test_the_parts_and_what_is_missing():
    """The heart series read in their order and the ones missing, as data (v4.4: no sub-score on the page): the
    heart signals with no baseline yet are `building` (under 7 nights before the assessed week), with a reason and
    never a number; a provisional baseline of 7 to 13 nights in that same window makes a score that
    says « Repères provisoires » (owner, 2026-10-14: « il y a bien un score »); a watch that never measures HRV just
    misses it."""
    big = _session(D - timedelta(days=2), 200, sid=42)
    day = _day(_rich(hrv_last=58.0), _runs(start=3) + [big])  # a long effort D+2: over, the HRV low
    assert [p["key"] for p in day["score"]["parts"]] == ["hrv", "hr"]
    assert day["score"]["absent"] == [] and day["score"]["building"] == []
    young = _day(night_rows(range(0, 6)), _runs())  # 6 nights: no baseline yet
    assert young["score"]["absent"] == ["hrv", "hr"] and young["score"]["building"] == ["hrv", "hr"]
    assert young["score"]["value"] is None and young["score"]["missing"]["label"] == "repères en construction (0/7)"
    week = _day(night_rows(range(0, 21)), _runs())  # 13 nights from day −20 to −8: a provisional score
    assert week["score"]["missing"] is None and week["state"] is not None and week["score"]["building"] == []
    assert sc.quality(week)["provisional"] and sc.quality(week)["label"] == "Repères provisoires"
    assert _day(night_rows(range(0, 22)), _runs())["score"]["absent"] == []  # the 14th, 21 days ago, makes it
    # a watch that never measures HRV: VFC is just missing, nothing is being built for it
    no_hrv = night_rows(range(0, 6))
    no_hrv.pop("hrv")
    nothing = _day(no_hrv, _runs())
    assert nothing["score"]["absent"] == ["hrv", "hr"] and nothing["score"]["building"] == ["hr"]
    # no night this morning: no state even in a window (like WHOOP)
    gone = _day(night_rows(range(1, 30)), [_session(D - timedelta(days=1), 200, sid=9)])  # in a window
    assert gone["score"]["value"] is None and gone["score"]["missing"] == {"key": "no_night", "label": "pas de bilan"}
    assert gone["window"] and gone["state"] is None
    # under 3 nights in the week: the reason says how many
    thin = _day(night_rows([0, 4] + list(range(8, 40))), _runs())
    assert thin["score"]["missing"]["label"] == "pas assez de nuits cette semaine (2/3)"


def test_a_card_wears_its_place_against_the_usual_values():
    """The Sommeil dial's word from its 24 h — under 6 h « court » (its arc warm: Craven 2022), 6 to 7 h « un peu
    court », 7 h and more « suffisant » (Watson 2015a), in the sleep hue, never a flag on a long night; a heart
    card's colour its trend's place (v4.3): VFC far under its usual values orange, FC de nuit in them green; the
    7 nights' sleep owed said apart, never added to the 8-h need (owner, 2026-10-14: scientific basis)."""
    words = {t: (sante.sleep_dial(t, 480, "#sommeil")["sub"], sante.sleep_dial(t, 480, "#sommeil")["tone"])
             for t in (600, 420, 419, 360, 359)}
    assert words == {600: ("suffisant", "sleep"), 420: ("suffisant", "sleep"), 419: ("un peu court", "sleep"),
                     360: ("un peu court", "sleep"), 359: ("court", "warn")}
    assert sante.sleep_dial(None, 480, "#sommeil")["sub"] == "pas de données"
    assert sante.sleep_dial(400, 480, None)["href"] is None
    rows = _rich(hrv_last=35.0, asleep=390)  # VFC far under its usual values, FC in them, 6h30 every night
    day = _day(rows, _runs())
    nights = _nights(rows, _runs())
    with nt.memo():
        nt.freeze(nights)
        vfc, fc = sante._night_card(nights, "hrv", D, day), sante._night_card(nights, "hr", D, day)
        owed = sl.deficit(nights, D)
    assert (vfc["status"]["tone"], fc["status"]["tone"]) == ("warn", "ok")
    assert {k: owed[k] for k in ("minutes", "n", "raced", "days", "text")} == {  # 7 × 1h30
        "minutes": 630, "n": 7, "raced": 0, "days": 7, "text": "écart estimé au repère sur 7 jours\u00a0: 10h30"}
    assert owed["raw"] > owed["floor"]  # over its noise floor (1,28·σ_T·7/√7, σ_T 15 min here: 52 min)
    assert day["score"]["value"] == 50 and [r["key"] for r in day["state"]["reasons"]] == ["hrv"]  # 6h30: no reason


def test_provisional_bands_say_so_on_the_cards():
    """A « provisoire » baseline (7 to 13 nights from day −67 to −8, H): drawn dashed, read by the state, which
    says so (« Repères provisoires »); the cards' readouts say « (provisoire) » after the band's numbers, the
    status line keeps its plain words."""
    rows = night_rows(range(0, 18))  # 10 nights in the baseline window: provisional
    day = _day(rows, _runs())
    assert day["stats"]["hrv"]["normal"]["provisional"] and [p["key"] for p in day["score"]["parts"]] == ["hrv", "hr"]
    assert day["score"]["missing"] is None and sc.quality(day)["provisional"]
    nights = _nights(rows, _runs())
    with nt.memo():
        nt.freeze(nights)
        card = sante._night_card(nights, "hrv", D, day)
        assert card["read"][3].endswith(" (provisoire)")
    assert (card["status"]["value"], card["status"]["word"]) == (None, "comme d'habitude")  # every night the same


def test_the_method_fold_is_five_plain_bullets():
    """« Comment je calcule », the page's one method fold (the audit, 2026-10-09: « Il y a deux explications à
    déplier, dont 8 points pour la première »): 5 bullets for the three dials in their order, the stages and the
    margin, without what the page already says (the 70 / 40 bands: the chart's lines, the dial's word). v4.3
    (owner: « trop d'explication, simplifie et synthétise, ne mets pas les citations pour gagner de la place »):
    plain words, no citation, no « (H) »; the references stay in the code, listed on /sante/sources, each a link
    (a DOI, else the text's own address). v4.4 (owner: « les explications en français ne sont pas claires »):
    sentences of 15 words at most, VFC and FC de nuit each said once in one plain sentence, « tes valeurs
    habituelles », never « ta normale »."""
    assert len(sc.METHOD) == 5
    # owner, 2026-10-14: scientific basis — the need no longer adapts, the efforts no longer accumulate in it
    assert "repère de 8 h" in sc.METHOD[0] and "7 jours" in sc.METHOD[0]
    assert "sans la dernière semaine" in sc.METHOD[1] and "ne prédit pas" in sc.METHOD[1]
    assert "efforts accumulés" not in sc.flat(sc.METHOD) and "s’adapte" not in sc.flat(sc.METHOD)
    assert sum(len(b.split()) for b in sc.METHOD) <= 155  # one concise explanation, explicitly estimated
    for sentence in re.split(r"(?<=[.!?])\s+", sc.flat(sc.METHOD)):  # « 3 h » is one word, « : » none
        assert len([w for w in re.sub(r"\d+ h\b", "N", sentence).split() if re.search(r"\w", w)]) <= 15, sentence
    assert "normale" not in sc.flat(sc.METHOD)
    text = sc.flat(sc.METHOD)
    assert "(H)" not in text and not re.search(r"\(\w+ (19|20)\d\d", text) and "séance" not in text.lower()
    assert not any(n in text for _, items in sc.REFS for n, _ in items)  # no citation in the fold
    assert all(" :" in b or ":" not in b for b in sc.typo(sc.METHOD))  # « : » never alone at a line start
    assert [g for g, _ in sc.REFS] == ["Recommandations officielles", "Études scientifiques"]
    links = dict(n_l for _, items in sc.linked(sc.REFS) for n_l in items)
    assert links["Kellmann 2018"] == "https://doi.org/10.1123/ijspp.2017-0759"
    assert links["BASES 2023"].startswith("https://westminsterresearch.westminster.ac.uk/")  # no DOI: its URL


def test_a_heart_signal_wears_its_place_against_the_normal():
    """v4.3 (owner: « est-ce que c'est bien ou pas bien ? »): green in its usual values; orange out of them on the
    side that matters (VFC under, FC de nuit over), the secondary marker too (shown, it changes nothing); neutral
    on the other side (never praised: Plews 2013); red under the alert; nothing without a comparison."""
    assert sc.row_tone("hrv", {"status": "in"}) == sc.row_tone("hr", {"status": "in"}) == "ok"
    assert sc.row_tone("hrv", {"status": "below"}) == sc.row_tone("hr", {"status": "above"}) == "warn"
    assert sc.row_tone("hrv", {"status": "above"}) == sc.row_tone("hr", {"status": "below"}) == "accent"
    assert sc.row_tone("hr", {"status": "in"}, alert=True) == "danger" and sc.row_tone("hrv", None) is None
    assert sc.row_tone("hrv", {"status": None}) is None and not hasattr(sc, "heart_tone")


def test_a_past_day_says_its_date_its_score_and_its_state_only():
    """OWN-3, v4.3 (owner: « Ne mentionne pas les sorties dans la partie Santé »): in the 14-day card a day says its
    date, its score and its state, never an activity, never « il y a »; a day without a night measured has no
    score, its recovery window or not (owner, 2026-10-09, like WHOOP: « Mets un cadran vide, oui »): no bar, a tap
    says « pas de mesure ce jour-là »."""
    big = _session(D - timedelta(days=6), 935, elapsed=1013, hour=21, offset=32400, sid=44, name="Transjeju 100M")
    rows, sessions = night_rows([0, 1, 2] + list(range(9, 60))), _runs() + [big]  # no night D-8 → D-3
    nights = _nights(rows, sessions)
    with nt.memo():
        nt.freeze(nights)
        efs = nt.anchor_efforts(nights, st.efforts(sessions))
        day = sante._assess(nights, sessions, efs, D)
        hist = sante._history(nights, sessions, efs, D) + [(D, day["state"], day["score"])]
    c = sc.history_card(hist, D)
    d = json.loads(c["data"])
    for word in ("Transjeju", "sortie", "il y a"):
        assert word not in c["data"], word
    assert not any(b["est"] for b in c["bars"]) and c["hatched"] == []
    gaps = [i for i, b in enumerate(c["bars"]) if b.get("miss")]
    assert {8, 9, 10} <= set(gaps)  # D-5 → D-3: no night, its window open, no score
    assert all(d["r"][i][2].endswith(" · pas de mesure ce jour-là") for i in (8, 9, 10))
    assert all(d["a"][i].endswith(" : pas de mesure ce jour-là.") for i in (8, 9, 10))
