"""Santé v4's recovery (SANTE_V4_SPEC.md §2 and §8): effort classes from the
activities alone and their windows, the nights after a ≥ 6 h effort tagged
(they never fire the illness alert, and count in the usual values like every
measured night: owner, 2026-10-08), the score without Ressenti nor races (components, weights,
caps: the alert 39, the effort windows, a red component 69, no VFC nor FC de
nuit 80, a short night 65), the state as the band of the score (WHOOP) and the
reason its sentence names, the 14-day history as each day computed it, and
the (H) heuristics pinned."""
import json
import math
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from unittest import mock

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


# These fixtures start with a 7h30 prior. The baseline may now adapt when their
# history is sufficient. The production 8h prior and learning guards are covered
# separately in test_sante_need and test_sante_calibration.
RESTED = 450


def _rested(base=RESTED):
    """The need's base for a test (sante_sleep.NEED_DEFAULT; None: the app's 8 h)."""
    return mock.patch.object(sl, "NEED_DEFAULT", base or sl.NEED_DEFAULT)


def _day(rows, sessions=(), today=D, base=RESTED):
    """What health_page computes for `today` (state, score) on these rows and activities (the efforts anchored on
    the nights, as health_page does)."""
    sessions = list(sessions)
    nights = _nights(rows, sessions, today)
    with nt.memo(), _rested(base):
        nt.freeze(nights)
        return sante._assess(nights, sessions, nt.anchor_efforts(nights, st.efforts(sessions)), today)


def _history(rows, sessions=(), today=D, base=RESTED):
    sessions = list(sessions)
    nights = _nights(rows, sessions, today)
    with nt.memo(), _rested(base):
        nt.freeze(nights)
        return sante._history(nights, sessions, nt.anchor_efforts(nights, st.efforts(sessions)), today)


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
    ("ultra", 600, {1: 35, 3: 55, 4: 65, 10: 95, 11: None}),  # v4.3: 35 the first 3 days (H)
    ("very_long", 360, {1: 45, 2: 55, 3: 65, 5: 265/3, 6: None}),
    ("long", 180, {1: 65, 2: 230/3, 3: 265/3, 4: None}),  # v4.2: 65 until D+3 (was D+2)
])
def test_each_class_opens_its_window(kind, elapsed, windows):
    """D+0, the day it ends (once it is uploaded), already reads D+1's cap: a big outing done today is never
    « pas de grosse sortie » (OWN-B / G)."""
    e = st.effort_of(_session(D, elapsed))
    w0 = st.effort_window([e], D)
    assert e.kind == kind and (w0["days"], w0["ago"], w0["cap"]) == (0, 0, windows[1])
    assert st.effort_window([e], D - timedelta(days=1)) is None  # the day before it: nothing
    for k, cap in windows.items():
        w = st.effort_window([e], D + timedelta(days=k))
        assert (w["cap"] if w else None) == (pytest.approx(cap) if cap is not None else None), (kind, k)
    w = st.effort_window([e], D + timedelta(days=1))
    assert w["days"] == 1 and "load" not in w  # 2026-10-08: no Charge récente any more


def test_overlapping_windows_keep_the_lowest_cap_then_the_bigger_effort():
    ultra = st.effort_of(_session(D - timedelta(days=8), 700, sid=1))  # D+8: cap 65
    long = st.effort_of(_session(D - timedelta(days=1), 200, sid=2))  # D+1: cap 65
    w = st.effort_window([ultra, long], D)
    assert w["effort"].session_id == 2 and w["cap"] == 65  # one cap: the bigger effort (its class) names it
    fresh = st.effort_of(_session(D - timedelta(days=2), 400, sid=3))  # very long D+2: cap 45
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
    assert nt.band(nights, "hr", D)["n"] == 39 and nt.normal(nights, "hr", D)["n"] == 40
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
    assert after["alert"] is None and after["state"]["key"] == "effort"


# ── the state, first match wins ─────────────────────────────────────────────

def test_rung_1_the_alert_comes_first_even_in_a_window():
    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)
    ultra = _session(D - timedelta(days=7), 610, sid=9)  # D+7 of an ultra: its 4 nights after are not the last 2
    day = _day(rows, _runs() + [ultra])
    assert day["window"]["days"] == 7
    assert (day["state"]["key"], day["state"]["word"]) == ("ill", "Récupération faible")
    # the one sentence the page still prints (v4.3): what the alert can mean
    assert day["state"]["text"] == ("Ta FC de nuit est nettement plus haute que d'habitude. Ça arrive avant un rhume, "
                                    "après de l'alcool ou une grosse journée.")  # its 2 nights: the FC de nuit row
    assert 0 <= day["score"]["value"] <= 39 and day["score"]["tone"] == "danger"
    # a provisional band (13 nights) never fires it: specific, not sensitive (Quer 2021)
    young = night_rows(range(0, 15), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)
    assert _day(young, _runs())["alert"] is None


def test_a_red_component_never_sits_under_a_green_ring():
    """A component under 40 caps the score at 69 (H): FC de nuit, Sommeil and Charge always, VFC only when FC de
    nuit is measured and its 7-night mean is over its median + 2 bpm (v4.2; Buchheit 2014, Table 2: a low VFC with
    a normal HR is the « saturation » of an athlete coping well). VFC far under its band (sub-score 0: a week at
    35 ms, −46 %, the band holding it), FC de nuit in it: nothing caps, raw (25·0 + 25·100 + 30·100) / 80 = 68,75 →
    69, its facts row orange (never a red row the ring does not show). With FC de nuit at + 2,5 bpm (sub-score 90) a
    VFC of 37,5 counts: raw 77,4 → 69, red."""
    far = _day(_rich(hrv_last=35.0), _runs())
    vfc = next(p for p in far["score"]["parts"] if p["key"] == "hrv")
    assert vfc["sub"] == 0 and not vfc["red"] and far["score"]["raw0"] == 68.75
    assert far["score"]["value"] == 69 and far["score"]["caps"] == [] and far["score"]["tone"] == "warn"
    st_ = far["state"]
    assert (st_["key"], st_["tone"], st_["text"]) == ("hrv", "warn", None)
    assert sc.row_tone(vfc) == "warn"
    up = _day(_rich(hrv_last=54.0, hr_last=47.5), _runs())  # VFC 37,5 (z −1,75), FC de nuit + 2,5 bpm → 90
    parts = {p["key"]: p for p in up["score"]["parts"]}
    assert round(parts["hrv"]["sub"], 1) == 37.5 and parts["hrv"]["red"] and parts["hr"]["sub"] == 90
    assert not parts["hrv"]["joint"]  # + 2,5 bpm is under the joint cap's + 3
    assert up["score"]["raw0"] == pytest.approx((25 * parts["hrv"]["sub"] + 25 * 90 + 30 * 100) / 80)
    assert up["score"]["value"] == 69 == sc.CAP_RED and up["score"]["caps"] == ["red"]
    assert (up["state"]["key"], up["state"]["text"]) == ("hrv", None)
    assert sc.row_tone(parts["hrv"]) == "danger"  # red under an orange ring
    assert sc.row_tone(parts["hr"]) == "ok"  # + 2,5 bpm: inside its normal (± 3 bpm)
    near = _day(_rich(hrv_last=60.0), _runs())  # VFC 64,9: lowers the score only
    sub = next(p for p in near["score"]["parts"] if p["key"] == "hrv")["sub"]
    assert 40 <= sub < 70 and near["score"]["value"] == sc.rounded((25 * sub + 25 * 100 + 30 * 100) / 80) == 89
    assert (near["state"]["key"], near["state"]["text"]) == ("ok", None)


def test_the_reason_is_the_binding_cap_else_the_lowest_component():
    """Under « Bien récupéré » the sentence names the main reason: the alert whenever it holds, else the cap that
    binds (the lowest; equal caps in the order ill, effort, short, red, no_heart), else the lowest component."""
    def day(**kw):
        base = {"alert": None, "window": None, "tst24": 450}
        return {**base, **kw}
    hrv, hr, sleep = ({"key": k, "sub": v} for k, v in (("hrv", 30), ("hr", 55), ("sleep", 90)))
    assert sc.reason("warn", ["red"], [hrv, hr, sleep], day()) == "hrv"  # the red component its cap is about
    assert sc.reason("warn", [], [{**hrv, "sub": 60}, hr, sleep], day()) == "hr"  # no cap binds: the lowest
    assert sc.reason("ok", [], [hrv], day()) is None  # green: no sentence
    w = {"cap": 65, "load": 20}
    assert sc.reason("warn", ["effort", "short"], [hrv], day(window=w, tst24=300)) == "effort"
    assert sc.reason("warn", ["short"], [hrv], day(tst24=300)) == "short"
    assert sc.reason("danger", [], [hrv], day(alert={"days": []})) == "ill"
    assert sc.reason("warn", ["effort", "no_heart"], [sleep], day(window=w)) == "effort"  # the window binds
    assert sc.reason("warn", [], [{"key": "sleep", "sub": 30}], day(tst24=330)) == "short"


def test_rung_4_a_short_24_hour_total():
    rows = _rich()
    rows["sleep"].update(night_rows([0], asleep=330, start=(23, 30), end=(6, 30))["sleep"])  # 5h30, no nap
    day = _day(rows, _runs())
    assert (day["state"]["key"], day["state"]["text"]) == ("short", None)  # the reason, never printed (v4.3)
    assert day["score"]["caps"] == ["short"] and day["score"]["value"] == sc.CAP_SHORT == 65
    # a nap in the 24 h before the wake (yesterday 13:00) lifts the total over 6 h: no short night (Craven 2022)
    y = D - timedelta(days=1)
    rows["nap"][y] = (60, {"windows": [[f"{y}T13:00", f"{y}T14:05"]]}, "Garmin")
    assert _day(rows, _runs())["state"]["key"] == "ok"
    # today's afternoon nap is tomorrow morning's 24 h (each nap once: owner's report 2026-10-09)
    rows["nap"] = {D: (60, {"windows": [[f"{D}T13:00", f"{D}T14:05"]]}, "Garmin")}
    assert _day(rows, _runs())["state"]["key"] == "short"


def test_rung_5_well_recovered_needs_a_nightly_signal():
    day = _day(_rich(), _runs())
    assert (day["state"]["key"], day["state"]["word"], day["state"]["text"]) == ("ok", "Bonne récupération", None)
    assert day["score"]["value"] == 100 and day["score"]["measured"]
    strava = _day({}, _runs())  # activities only: nothing measured
    assert strava["state"] is None and strava["score"]["value"] is None
    assert td.no_state_line(False) == "Connecte ta montre pour voir ta récupération."
    # without a nightly signal no score, a recovery window or not (owner, 2026-10-09, like WHOOP: « Mets un
    # cadran vide, oui »): the window is still there (the « Effort récent » row says it), never a score from it
    ultra = _day({}, _runs() + [_session(D - timedelta(days=3), 700, sid=9, name="Ultra des Crêtes")])
    w, s = ultra["window"], ultra["score"]
    assert (w["days"], w["cap"]) == (3, 55) and (s["value"], s["measured"], ultra["state"]) == (None, False, None)
    assert s["absent"] == ["hrv", "hr", "sleep"] and s["building"] == []  # nothing ever measured: not « built »
    assert "estimated" not in s and not hasattr(sc, "ESTIMATED") and not hasattr(sc, "EST_READ")


# ── the score ───────────────────────────────────────────────────────────────

def test_components():
    band = {"center": 70.0, "sd": 0.1, "provisional": False}
    assert sc.hrv_sub(70 * math.exp(-0.05), band) == pytest.approx(100)  # z = −0.5: the band's floor
    assert sc.hrv_sub(70 * math.exp(-0.25), band) == pytest.approx(0)  # z = −2.5
    assert sc.hrv_sub(90, band) == 100  # above: never praised, never more
    assert round(sc.hrv_sub(70 * math.exp(-0.15), band)) == 50
    # FC de nuit (v4.2, H): 100 up to + 2 bpm, 40 at + 5, 0 at + 8 (Alavi 2022: + 4 on 2 nights; Bosquet 2008: + 4,5)
    hr = {"center": 45}
    assert sc.hr_sub(47, hr) == 100 and sc.hr_sub(48, hr) == 80 and sc.hr_sub(49, hr) == 60
    assert sc.hr_sub(50, hr) == 40 and sc.hr_sub(51, hr) == pytest.approx(80 / 3) and sc.hr_sub(53, hr) == 0
    assert sc.hr_sub(56, hr) == 0 and sc.hr_sub(40, hr) == 100
    assert sc.sleep_sub(420) == sc.sleep_sub(520) == 100 and sc.sleep_sub(360) == 60 and sc.sleep_sub(240) == 0
    assert sc.sleep_sub(390) == 80 and sc.sleep_sub(300) == 30
    # the need moves the three points (2026-10-09): a 9-h need → 7h52 for 100, 6h45 for 60, 4h30 for 0
    assert sc.sleep_sub(472.5, 540) == 100 and sc.sleep_sub(405, 540) == 60 and sc.sleep_sub(270, 540) == 0
    assert sc.sleep_sub(420, 540) < 100  # 7 h is « suffisant » for an 8-h need only
    assert not hasattr(sc, "SLEEP_DEBT") and not hasattr(sc, "sleep_base")  # the debt lives in the need now
    assert not hasattr(sc, "load_sub")  # 2026-10-08: no Charge récente component


def test_weights_are_renormalised_and_a_window_only_caps():
    """VFC 25, FC de nuit 25, Sommeil 30 (H, v4.2), renormalised over the components present. 2026-10-08 (owner:
    « Fais comme WHOOP »: its recovery is body signals and sleep, the strain kept out): no Charge récente in the
    mean, in a recovery window or not; the window caps the score (owner: « Un effort récent, il faut le prendre
    en compte et afficher la fatigue quand même »)."""
    assert sc.WEIGHTS == {"hrv": 25, "hr": 25, "sleep": 30}  # (H)
    day = _day(_rich(hrv_last=60.0), _runs(n=20))  # plenty of activities, no window
    parts = {p["key"]: p for p in day["score"]["parts"]}
    assert set(parts) == {"hrv", "hr", "sleep"} and day["score"]["absent"] == []
    assert [parts[k]["weight"] for k in ("hrv", "hr", "sleep")] == [0.3125, 0.3125, 0.375]
    assert [p["key"] for p in day["score"]["parts"]] == ["hrv", "hr", "sleep"]
    window = _day(_rich(), [_session(D - timedelta(days=1), 200, sid=9)])  # a long effort: its 65
    parts = {p["key"]: p for p in window["score"]["parts"]}
    assert set(parts) == {"hrv", "hr", "sleep"} and parts["sleep"]["weight"] == 0.375
    assert window["score"]["raw0"] == 100 and window["score"]["value"] == 65  # (25+25+30)·100 / 80 → its cap
    assert window["score"]["caps"] == ["effort"] and window["state"]["key"] == "effort"


@pytest.mark.parametrize("raw,score,tone", [(100, 100, "ok"), (70, 70, "ok"), (69.5, 70, "ok"), (69.49, 69, "warn"),
                                            (64.5, 65, "warn"), (64.4, 64, "warn"), (40, 40, "warn"),
                                            (39.5, 40, "warn"), (39.4, 39, "danger"), (0, 0, "danger")])
def test_the_state_is_the_band_of_the_score(raw, score, tone):
    """Like WHOOP, the state comes from the score (§8): the capped raw rounded half up on the exact value, then its
    band: ≥ 70 « Bonne récupération », 40–69 « Récupération en cours », < 40 « Récupération faible » (v4.3, owner:
    « à ménager » is not good French). No placement inside a band chosen first."""
    assert sc.rounded(raw) == score and sc.tone_of(score) == tone
    assert not hasattr(sc, "place") and sc.BANDS == {"ok": (70, 100), "warn": (40, 69), "danger": (0, 39)}
    assert td.WORDS == {"ok": "Bonne récupération", "warn": "Récupération en cours", "danger": "Récupération faible"}


def test_the_effort_caps_bind_and_the_ultra_window_ends_after_10_days():
    big = _session(D - timedelta(days=1), 935, elapsed=1013, sid=42)  # D+1 after an ultra (it ended on D-1)
    early = _day(_rich(), _runs() + [big])
    assert early["window"]["cap"] == 35 and early["score"]["caps"] == ["effort"]
    assert early["score"]["value"] == 35  # raw 100 (no Charge in the mean, 2026-10-08) → capped at 35 (v4.3, H)
    assert early["state"]["tone"] == "danger" and early["state"]["key"] == "effort"
    assert early["state"]["word"] == "Récupération faible" and early["score"]["measured"]  # nights measured
    nine, ten = timedelta(days=9), timedelta(days=10)
    later = _day(_rich(), _runs() + [replace(big, start=big.start - nine, day=big.day - nine)])
    assert later["window"]["days"] == 10 and later["window"]["cap"] == 95
    assert later["score"]["value"] == 95
    done = _day(_rich(), _runs() + [replace(big, start=big.start - ten, day=big.day - ten)])
    assert done["window"] is None and done["state"]["key"] == "ok"


def test_owner_like_day_the_exact_score():
    """Sommeil 100 alone (an ultra on D+5; no Charge in the mean since 2026-10-08): raw 100, its window's 65
    binds (and the 80 without VFC nor FC de nuit): 65, as with v4.2's Charge (raw 68)."""
    rows = night_rows([0], asleep=516, start=(22, 42), end=(7, 30), source="COROS",
                      hr_method="coros_sleep_summary")
    day = _day(rows, [_session(D - timedelta(days=6), 935, elapsed=1013, hour=21, offset=32400, sid=8)])
    assert day["window"]["days"] == 5 and day["score"]["raw0"] == 100 and day["score"]["raw"] == 68.5
    assert day["score"]["value"] == 69 and day["score"]["caps"] == ["effort", "no_heart"]  # the window's cap binds
    assert (day["state"]["key"], day["state"]["tone"]) == ("effort", "warn")  # the cap that binds: the window


def test_no_100_from_sleep_alone():
    """Neither VFC nor FC de nuit in the score (the watch worn some nights only, no normal yet): capped at 80 (H),
    so a long night alone never makes a rested 100; with them, a 100 stays possible."""
    rows = night_rows([0], asleep=588, start=(23, 52), end=(9, 54), source="COROS", hr_method="coros_sleep_summary")
    day = _day(rows, _runs())  # 9h48 → Sommeil 100 (no window: no Charge): raw 100
    assert day["state"]["key"] == "ok" and day["score"]["raw0"] == 100
    assert day["score"]["value"] == 80 == sc.CAP_NO_HEART and day["score"]["caps"] == ["no_heart"]
    assert {p["key"] for p in day["score"]["parts"]} == {"sleep"}
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


def test_a_low_week_is_read_against_a_band_that_holds_it():
    """The usual values include the week they judge (owner, 2026-10-08: every measured night counts, last night
    included, like WHOOP's and Oura's rolling baselines): 7 nights at 50 ms (−26 %) over 53 at ≈ 70 ms lower the
    band's centre (67,6 ms) and widen it (SD 0,15), z −2,0: VFC 24 (it was 0 against the 60 days before the week),
    the score 76, « Bonne récupération »; a week at 35 ms (−46 %) still empties the bar (z −2,6)."""
    day = _day(_rich(hrv_last=50.0), _runs())
    b = day["stats"]["hrv"]["normal"]
    assert b["n"] == 60 and round(b["center"], 1) == 67.6 and round(b["sd"], 2) == 0.15
    vfc = next(p for p in day["score"]["parts"] if p["key"] == "hrv")
    assert round(vfc["sub"]) == 24 and (day["score"]["value"], day["state"]["word"]) == (76, "Bonne récupération")
    assert next(p for p in _day(_rich(hrv_last=35.0), _runs())["score"]["parts"] if p["key"] == "hrv")["sub"] == 0


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
    with nt.memo(), _rested():
        nt.freeze(nights)
        day = sante._assess(nights, _runs(), [], D)
        hist = sante._history(nights, _runs(), [], D) + [(D, day["state"], day["score"])]
    c = sc.history_card(hist, D)
    d = json.loads(c["data"])
    assert c["read"] == d["rest"] == ["", "Récupération · 14 jours", ""] and d["sel"] == 14 and d["back"] == 13
    assert c["title"] == "Récupération · 14 jours" and "moyenne" not in c["data"]
    assert d["r"][-1] == ["100\u00a0%", "● Bonne récupération", "jeu. 8 oct."] and d["t"][-1] == "ok"
    assert d["a"][-1] == "jeudi 8 octobre : 100\u00a0%, bonne récupération."
    assert all(b["cls"] == "ok" and not b["est"] for b in c["bars"]) and c["bars"][-1]["today"]
    assert c["hatched"] == []  # every day measured: no hatched bar, no legend for it
    assert [ln["label"] for ln in c["lines"]] == ["70\u00a0%", "40\u00a0%"] and c["lines"][0]["y"] < c["lines"][1]["y"]
    assert c["xt"][-1]["today"] and c["tone_now"] == ""


# ── the method and the heuristics ───────────────────────────────────────────

def test_an_alert_episode_is_read_by_the_score():
    """Seven nights at 56 bpm over a median of 45: the alert episode's own nights make the HR component; they count
    in the band too (owner, 2026-10-08), whose median (robust) stays 45, so the red score shows the episode."""
    rows = night_rows(range(0, 60), hr=lambda k: 56.0 if k < 7 else 44.0 + k % 3)
    day = _day(rows, _runs())
    assert day["state"]["key"] == "ill" and day["stats"]["hr"]["normal"]["center"] == 45
    hr = next(p for p in day["score"]["parts"] if p["key"] == "hr")
    assert hr["sub"] == 0 and 0 <= day["score"]["value"] <= 39


# ── v4.1: the review's findings ─────────────────────────────────────────────

def test_dawn_finish_the_first_morning_after_is_in_its_window():
    """DAWN-FINISH: a 22-h 100-miler from 05:00 to 03:00, then asleep 04:00 → 11:00: that morning is D+1 (cap 35),
    its sleep the first night « après ultra »; finishing at 23:59 or at 00:01 gives the same morning after (two
    minutes never turn a 35 into a 100)."""
    rows = _rich()
    rows["sleep"][D] = (400, {"main_start": f"{D}T04:00", "main_end": f"{D}T11:00", "timeline": True}, "Garmin")
    race = _session(D - timedelta(days=1), 1320, hour=5, sid=50, name="100 miles des Crêtes")  # → D 03:00
    day = _day(rows, _runs() + [race])
    assert st.effort_of(race).day == D - timedelta(days=1)
    assert (day["window"]["days"], day["window"]["cap"], day["score"]["value"]) == (1, 35, 35)
    assert day["state"]["key"] == "effort" and day["state"]["text"] is None
    nights = _nights(rows, _runs() + [race])
    assert "ultra" in nights[D].tags and "ultra" not in nights[D - timedelta(days=1)].tags  # 22 h: an ultra
    assert {p["key"] for p in day["score"]["parts"]} == {"hrv", "hr", "sleep"} and day["score"]["caps"] == ["effort", "short"]
    for start in (datetime(2026, 10, 7, 4, 59), datetime(2026, 10, 7, 5, 1)):  # 19 h: 23:59 or 00:01
        run = _session(D - timedelta(days=1), 1140, hour=start.hour, sid=51)
        run = replace(run, start=start.replace(tzinfo=timezone.utc))
        w = _day(_rich(), _runs() + [run])["window"]
        assert (w["days"], w["cap"], w["ago"]) == (1, 35, 1), start


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


def test_two_windows_open_the_lowest_cap_holds():
    """OVERLAP: an ultra 5 days ago (cap 65 now) and a 7-h outing 2 days ago (cap 45): the cap and the reason are
    the outing's (the lowest holds the day)."""
    ultra = st.effort_of(_session(D - timedelta(days=5), 700, sid=1, name="Ultra"))
    hike = st.effort_of(_session(D - timedelta(days=2), 420, sid=2, name="Rando"))
    w = st.effort_window([ultra, hike], D)
    assert (w["effort"].session_id, w["cap"]) == (2, 55)
    assert st.effort_window([ultra], D)["cap"] == 70 and st.effort_window([hike], D)["cap"] == 55


def test_an_ultra_saved_as_two_activities_is_one_effort():
    """SPLIT-ULTRA: the Transjeju saved as 9h00 (21:00 → 06:00) then, 6 min later, 7h47 (→ 13:53): one ultra of
    16h53 (first start to last end, the stop included), named and linked after the first; its window (35 to D+3,
    65 to D+13: it ran through the night) as if it were one activity. Apart by more than 30 min (H): two
    activities, two efforts (v4.3: no back-to-back merge, M3, any more): each its own window."""
    a = _session(date(2026, 10, 2), 540, hour=21, offset=32400, sid=81, name="Transjeju 100M")
    b = _session(date(2026, 10, 3), 467, hour=6, offset=32400, sid=82, name="Transjeju 100M (2)")
    b = replace(b, start=b.start + timedelta(minutes=6))
    [e] = st.efforts([a, b])
    assert (e.kind, e.session_id, e.name, e.minutes) == ("ultra", 81, "Transjeju 100M", 1013)
    assert (e.end, e.day, e.start_day) == (datetime(2026, 10, 3, 13, 53), date(2026, 10, 3), date(2026, 10, 2))
    assert [st.effort_window([e], date(2026, 10, k))["cap"] for k in (6, 9, 13, 16)] == [55, 72, 86, 96.5]
    assert st.effort_window([e], date(2026, 10, 17)) is None
    far = replace(b, start=b.start + timedelta(minutes=40))
    assert [(x.kind, x.minutes, x.session_id) for x in st.efforts([a, far])] == [("very_long", 540, 81),
                                                                                ("very_long", 467, 82)]


def test_the_day_after_an_alert_stops_firing_the_episode_is_still_read():
    """POST-ALERT-EPISODE: 56 bpm on 06/10 and 07/10 (alert line 50), 49 on 08/10: the alert stops, the episode
    is still open (49 is over the band's top, 48): the 7-night means read its nights, so FC de nuit is not « dans
    ta normale » at 100 the next morning (its nights count in the band too, whose median stays 45)."""
    rows = night_rows(range(0, 60), hr=lambda k: 56.0 if k in (1, 2) else 49.0 if k == 0 else 44.0 + k % 3)
    assert _day(rows, _runs(), D - timedelta(days=1))["state"]["key"] == "ill"
    day = _day(rows, _runs())
    assert day["alert"] is None and day["stats"]["hr"]["normal"]["center"] == 45
    hr = next(p for p in day["score"]["parts"] if p["key"] == "hr")
    assert round(day["stats"]["hr"]["value"], 2) == 48.57 and hr["sub"] == pytest.approx(100 - 60 * (3.5714 - 2) / 3,
                                                                                         abs=0.01)
    assert sc.row_tone(hr) == "warn"  # + 3,6 bpm, over its normal: orange (never « dans ta normale »)
    nights = _nights(rows, _runs())
    with nt.memo():
        nt.freeze(nights)
        card = sante._night_card(nights, "hr", D, day)
    assert card["status"] == {"key": "above", "value": "+8\u00a0%", "word": "plus haute que d'habitude",
                              "text": "+8\u00a0% plus haute que d'habitude", "tone": "warn",
                              "meaning": "Ça arrive avec la fatigue, la chaleur, l'alcool ou un début de maladie."}
    assert 100 * (day["stats"]["hr"]["value"] - 45) / 45 == pytest.approx(7.94, abs=0.01)  # 48,57 against 45
    assert day["score"]["value"] == sc.rounded((25 * 100 + 25 * hr["sub"] + 30 * 100) / 80) == 90


def test_the_illness_alert_makes_the_nightly_hr_row_red():
    """UX1: under « Récupération faible » for the nightly-HR alert, FC de nuit reads the alert's 2 nights (≤ 39),
    never green (5 normal nights dilute the 7-night mean): its card says « au-dessus » in red, its facts row
    « nettement au-dessus, 2 nuits » in red (v4.4)."""
    rows = night_rows(range(0, 40), hr=lambda k: 53.0 if k < 2 else 44.0 + k % 3)
    day = _day(rows, _runs())
    assert day["state"]["key"] == "ill" and day["score"]["value"] <= 39
    assert day["stats"]["hr"]["value"] - day["stats"]["hr"]["normal"]["center"] < 3  # diluted
    hr = next(p for p in day["score"]["parts"] if p["key"] == "hr")
    assert sc.row_tone(hr) == "danger" and hr["sub"] <= 39
    nights = _nights(rows, _runs())
    with nt.memo():
        nt.freeze(nights)
        card = sante._night_card(nights, "hr", D, day)
    # its 2 nights (53 bpm) against the usual value (45): + 18 %, the same on the card and on its facts row
    assert day["stats"]["hr"]["normal"]["center"] == 45 and day["alert"]["values"] == [53.0, 53.0]
    assert card["status"] == {"key": "above", "value": "+18\u00a0%", "word": "nettement plus haute depuis 2 nuits",
                              "text": "+18\u00a0% nettement plus haute depuis 2 nuits", "tone": "danger",
                              "meaning": "Ça arrive avec la fatigue, la chaleur, l'alcool ou un début de maladie."}
    row = sante.night_row(card, "hr")
    assert (row["name"], row["qual"], row["value"], row["word"], row["tone"]) == (
        "FC de nuit", "2 nuits", "+18\u00a0%", "nettement plus haute depuis 2 nuits", "danger")


def test_a_nap_yesterday_afternoon_counts_in_the_24_hours_before_the_wake():
    """A 1h40 nap yesterday 16:00 → 17:45, then 5h00 (01:20 → 06:40): 6h40 asleep in the 24 h before
    the wake, a less restrictive continuous cap; the Sommeil row and score read that one figure. A
    « rendormi » nap of the day before (≤ 3 h after its wake) is the night before's: never counted twice."""
    rows = _rich()
    rows["sleep"][D] = (300, {"main_start": f"{D}T01:20", "main_end": f"{D}T06:40", "timeline": True}, "Garmin")
    y = D - timedelta(days=1)
    rows["nap"][y] = (100, {"windows": [[f"{y}T16:00", f"{y}T17:45"]]}, "Garmin")
    day = _day(rows, _runs())
    assert day["tst24"] == 400 and "short" in day["score"]["caps"]
    # Its sufficient stable history adapts the base to 7h20; no debt remains.
    assert day["need"] == {"total": 440, "base": 440, "effort": 0, "debt": 0}
    assert (day["state"]["key"], day["score"]["value"]) == ("ok", 92)  # continuous sleep precaution
    assert round(next(p for p in day["score"]["parts"] if p["key"] == "sleep")["sub"]) == 100
    dial = sante.sleep_dial(day["tst24"], day["need"]["total"], "#sommeil")
    assert (dial["value"], dial["sub"], dial["tone"]) == ("91", "un peu court", "sleep")  # 6h40 of 7h50: its own hue
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
    """OWN-B / G: an 11-h ultra today 05:00 → 16:00: that evening the score is capped like D+1 (never « pas de
    grosse sortie »)."""
    ultra = _session(D, 660, hour=5, sid=70, name="Grand Raid")
    day = _day(_rich(), _runs() + [ultra])
    assert (day["window"]["days"], day["window"]["cap"], day["score"]["value"]) == (0, 35, 35)
    assert day["state"]["key"] == "effort" and day["state"]["text"] is None and day["score"]["caps"] == ["effort"]
    long = _session(D, 210, dplus=1600, hour=7, sid=71, name="Grand tour")  # a 3h30 outing this morning: long
    assert _day(_rich(), _runs() + [long])["score"]["value"] == 65
    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)  # an alert: the window not the state
    ill = _day(rows, _runs() + [ultra])
    assert ill["state"]["key"] == "ill" and ill["window"]["cap"] == 35 and ill["score"]["value"] <= 35


def test_rung_2_an_effort_window_is_the_reason_never_printed():
    """v4.3 (owner: « Ne mentionne pas les sorties dans la partie Santé, ça complexifie : mets juste les scores »):
    the window is the state's reason, as data; the page prints the state's word only, never the activity, its
    day or its time. The window still knows how long ago it started and ended (an ultra started at 21:00 ends
    the next day: its window counts from its end)."""
    big = _session(D - timedelta(days=5), 935, elapsed=1013, sid=42, name="Trail des Glaciers")
    day = _day(_rich(), _runs() + [big])
    st_ = day["state"]
    assert (st_["key"], st_["tone"], st_["word"], st_["text"]) == ("ok", "ok", "Bonne récupération", None)
    assert "href" not in st_ and st_["aria"] == "Bonne récupération."
    night = _session(D - timedelta(days=6), 935, elapsed=1013, hour=21, offset=32400, sid=44, name="Transjeju 100M")
    w = _day(_rich(), _runs() + [night])["window"]
    assert (w["days"], w["ago"]) == (5, 6)
    assert not hasattr(td, "effort_text") and not hasattr(td, "short_text") and not hasattr(td, "TEXTS")


def test_the_parts_and_what_is_missing():
    """The score's components in their order and the ones missing, as data (v4.4: no sub-score on the page, no
    « Pas encore dans le score »): the heart signals measured lately whose normal does not exist yet are
    `building` (their cards say so), a watch that never measures HRV just misses it."""
    big = _session(D - timedelta(days=2), 200, sid=42)
    day = _day(_rich(hrv_last=58.0), _runs(start=3) + [big])  # a long effort D+2, the HRV low
    assert [p["key"] for p in day["score"]["parts"]] == ["hrv", "hr", "sleep"]  # no Charge (2026-10-08)
    assert day["score"]["absent"] == [] and day["score"]["building"] == []
    young = _day(night_rows(range(0, 6)), _runs())  # 6 nights: their normal is still being built (7, H)
    assert young["score"]["absent"] == ["hrv", "hr"] and young["score"]["building"] == ["hrv", "hr"]
    assert _day(night_rows(range(0, 7)), _runs())["score"]["absent"] == []  # the 7th, last night, makes it
    # a watch that never measures HRV: VFC is just missing, nothing is being built for it
    no_hrv = night_rows(range(0, 6))
    no_hrv.pop("hrv")
    nothing = _day(no_hrv, _runs())
    assert nothing["score"]["absent"] == ["hrv", "hr"] and nothing["score"]["building"] == ["hr"]
    # no night this morning: Sommeil is missing too, and no score even in a window (like WHOOP)
    gone = _day(night_rows(range(1, 6)), [_session(D - timedelta(days=1), 200, sid=9)])  # in a window
    assert gone["score"]["absent"] == ["hrv", "hr", "sleep"] and gone["score"]["value"] is None
    assert gone["window"] and gone["state"] is None


def test_a_row_wears_the_colour_its_card_wears():
    """The Sommeil dial's word from its 24 h — under 6 h « court » (its arc warm: Craven 2022), 6 to 7 h « un peu
    court », 7 h and more « suffisant » (Watson 2015a), in the sleep hue, never a flag on a long night; a heart
    row as its card's status line (v4.3): VFC far under its band with FC de nuit normal caps nothing: orange,
    never red; FC de nuit in its normal: green; no Effort récent row outside a window."""
    words = {t: (sante.sleep_dial(t, 480, "#sommeil")["sub"], sante.sleep_dial(t, 480, "#sommeil")["tone"])
             for t in (600, 420, 419, 360, 359)}
    assert words == {600: ("suffisant", "sleep"), 420: ("suffisant", "sleep"), 419: ("un peu court", "sleep"),
                     360: ("un peu court", "sleep"), 359: ("court", "warn")}
    assert sante.sleep_dial(None, 480, "#sommeil")["sub"] == "pas de données"
    assert sante.sleep_dial(400, 480, None)["href"] is None
    day = _day(_rich(hrv_last=35.0, asleep=390), _runs())  # VFC far under its band, FC in it, 6h30 every night
    assert day["need"]["total"] == 510  # 7h30 + 1 h owed (a quarter of the 7 h short over 7 days, at most 1 h)
    sleep = 60 + 40 * (390 - 0.75 * 510) / (0.125 * 510)  # between ¾ and ⅞ of 8h30
    assert day["score"]["raw0"] == pytest.approx((25 * 0 + 25 * 100 + 30 * sleep) / 80) and day["score"]["value"] == 56
    parts = {p["key"]: p for p in day["score"]["parts"]}
    assert (sc.row_tone(parts["hrv"]), sc.row_tone(parts["hr"])) == ("warn", "ok")
    assert "load" not in parts and sante.effort_row([], day) is None  # no window: no Effort récent row


def test_provisional_bands_say_so_on_the_cards():
    """A « provisoire » normal (7 to 13 nights, H): the night cards' readouts say « (provisoire) » after the band's
    numbers, the status line keeps its plain words."""
    rows = night_rows(range(0, 12))  # 12 nights, last night included: a « provisoire » normal (H)
    day = _day(rows, _runs())
    assert all(p["prov"] for p in day["score"]["parts"] if p["key"] in ("hrv", "hr"))
    nights = _nights(rows, _runs())
    with nt.memo():
        nt.freeze(nights)
        card = sante._night_card(nights, "hrv", D, day)
    assert card["read"][2].endswith(" (provisoire)")
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
    assert "base part de 8 h" in sc.METHOD[0] and "nuits comparables" in sc.METHOD[0]
    assert "diminue chaque jour" in sc.METHOD[1] and "ne prédit pas" in sc.METHOD[1]
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
    """v4.3 (owner: « est-ce que c'est bien ou pas bien ? »): green in its normal; orange out of it on the side
    that matters (VFC under, FC de nuit over), red when its note is under 40 and counts for the 69 cap; neutral on
    the other side (never praised); at least orange under the joint cap; red under the alert."""
    assert sc.heart_tone("hrv", "in", 100) == sc.heart_tone("hr", "in", 90) == "ok"
    assert sc.heart_tone("hrv", "below", 95) == sc.heart_tone("hr", "above", 76) == "warn"
    assert sc.heart_tone("hr", "above", 20) == "danger"
    assert sc.heart_tone("hrv", "below", 10, red=False) == "warn" and sc.heart_tone("hrv", "below", 10) == "danger"
    assert sc.heart_tone("hrv", "above", 100) == sc.heart_tone("hr", "below", 100) == "accent"
    assert sc.heart_tone("hr", "in", 80, joint=True) == "warn" and sc.heart_tone("hr", "in", 100, alert=True) == "danger"


def test_a_past_day_says_its_date_its_score_and_its_state_only():
    """OWN-3, v4.3 (owner: « Ne mentionne pas les sorties dans la partie Santé »): in the 14-day card a day says its
    date, its score and its state, never an activity, never « il y a »; a day without a night measured has no
    score, its recovery window or not (owner, 2026-10-09, like WHOOP: « Mets un cadran vide, oui »): no bar, a tap
    says « pas de mesure ce jour-là »."""
    big = _session(D - timedelta(days=6), 935, elapsed=1013, hour=21, offset=32400, sid=44, name="Transjeju 100M")
    rows, sessions = night_rows([0, 1, 2, 9, 10, 11, 12, 13]), _runs() + [big]  # no night D-8 → D-3, no band
    nights = _nights(rows, sessions)
    with nt.memo(), _rested():
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
