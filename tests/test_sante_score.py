"""Santé v4's recovery (SANTE_V4_SPEC.md §2 and §8): effort classes from the
activities alone and their windows, the nights after a ≥ 6 h effort tagged and
left out quietly, the score without Ressenti nor races (components, weights,
caps: the alert 39, the effort windows, a red component 69, no VFC nor FC de
nuit 80, a short night 65), the state as the band of the score (WHOOP) and the
reason its sentence names, the 14-day history as each day computed it, and
the (H) heuristics pinned."""
import json
import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import nights as nt
from app.services import sante
from app.services import sante_score as sc
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
                   speed=2.8, hr=hr, hr_peak=160, suffer=None, workout_type=workout_type, temp=None,
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


def _day(rows, sessions=(), today=D):
    """What health_page computes for `today` (state, score) on these rows and activities (the efforts anchored on
    the nights, as health_page does)."""
    sessions = list(sessions)
    nights = _nights(rows, sessions, today)
    with nt.memo():
        nt.freeze(nights)
        return sante._assess(nights, sessions, nt.anchor_efforts(nights, st.efforts(sessions)), today)


def _history(rows, sessions=(), today=D):
    sessions = list(sessions)
    nights = _nights(rows, sessions, today)
    with nt.memo():
        nt.freeze(nights)
        return sante._history(nights, sessions, nt.anchor_efforts(nights, st.efforts(sessions)), today)


# ── effort classes, from the activities alone ───────────────────────────────

def test_effort_classes_by_their_own_time_stops_included():
    assert st.effort_of(_session(D, 170)) is None  # under 3 h, flat: no effort
    assert st.effort_of(_session(D, 170, dplus=1500)).kind == "long"  # the legs rule: 1 500 m D+ on foot
    assert st.effort_of(_session(D, 170, dplus=1500, sport="Ride")) is None  # D+ on a bike is no legs rule
    assert st.effort_of(_session(D, 150, elapsed=185)).kind == "long"  # 3 h stops included
    assert st.effort_of(_session(D, 300, elapsed=365)).kind == "very_long"
    assert st.effort_of(_session(D, 340, elapsed=360, sport="Ride")).kind == "very_long"  # any activity
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
    ("ultra", 600, {1: 40, 3: 40, 4: 65, 10: 65, 11: None}),
    ("very_long", 360, {1: 45, 2: 45, 3: 65, 5: 65, 6: None}),
    ("long", 180, {1: 65, 2: 65, 3: None}),
])
def test_each_class_opens_its_window(kind, elapsed, windows):
    """D+0, the day it ends (once it is uploaded), already reads D+1's cap: a big outing done today is never
    « pas de grosse sortie » while the Charge ring counts it (OWN-B / G)."""
    e = st.effort_of(_session(D, elapsed))
    w0 = st.effort_window([e], D)
    assert e.kind == kind and (w0["days"], w0["ago"], w0["cap"]) == (0, 0, windows[1])
    assert st.effort_window([e], D - timedelta(days=1)) is None  # the day before it: nothing
    for k, cap in windows.items():
        w = st.effort_window([e], D + timedelta(days=k))
        assert (w["cap"] if w else None) == cap, (kind, k)
    w = st.effort_window([e], D + timedelta(days=1))
    assert w["load"] == {"ultra": 20, "very_long": 30, "long": 50}[kind] and w["days"] == 1


def test_overlapping_windows_keep_the_lowest_cap_then_the_bigger_effort():
    ultra = st.effort_of(_session(D - timedelta(days=8), 700, sid=1))  # D+8: cap 65, Charge 20
    long = st.effort_of(_session(D - timedelta(days=1), 200, sid=2))  # D+1: cap 65, Charge 50
    w = st.effort_window([ultra, long], D)
    assert w["effort"].session_id == 1 and (w["cap"], w["load"]) == (65, 20)
    fresh = st.effort_of(_session(D - timedelta(days=2), 400, sid=3))  # very long D+2: cap 45
    assert st.effort_window([ultra, long, fresh], D)["effort"].session_id == 3


# ── the nights after a big effort ───────────────────────────────────────────

def test_the_three_nights_after_six_hours_are_tagged_and_left_out():
    rows = night_rows(range(0, 40))
    sessions = [_session(D - timedelta(days=5), 420, sid=5)]  # 7 h ending at 14:00 on D-5
    nights = _nights(rows, sessions)
    tagged = sorted(d for d, n in nights.items() if "big" in n.tags)
    assert tagged == [D - timedelta(days=4), D - timedelta(days=3), D - timedelta(days=2)]  # D+1 → D+3
    assert nt.TAG_WORDS["big"] == "après grosse sortie"
    assert not nights[D - timedelta(days=4)].usable("hr") and not nights[D - timedelta(days=4)].usable("hrv")
    assert nt.band(nights, "hr", D)["n"] == 36  # 39 nights before D, 3 of them out
    # a 3-h effort tags nothing (only the night after a session ≥ 90 min is « sortie longue »: Myllymäki 2012)
    long = _nights(rows, [_session(D - timedelta(days=5), 200, sid=5)])
    assert not any("big" in n.tags for n in long.values())


def test_a_sleep_right_after_an_ultra_finished_at_dawn_is_tagged():
    """An ultra ending at 05:00: the sleep that started after it, waking that same day, is the first night after
    (D+1: D is the day before), and exactly 3 nights are tagged (DAWN-FINISH); one ending at 06:00, after that
    morning's 05:00 sleep began, keeps D on its own day."""
    rows = {"sleep": {}}
    for k in range(20):
        d = D - timedelta(days=k)
        rows["sleep"][d] = (340, {"main_start": f"{d}T05:00", "main_end": f"{d}T11:00"}, "Garmin")  # 05:00 → 11:00
    s = _session(D - timedelta(days=4), 1080, hour=12)  # D-4 12:00 + 18 h → D-3 06:00
    nights = _nights(rows, [s])
    assert st.effort_of(s).day == D - timedelta(days=3)
    # D-3's sleep started at 05:00, before the finish at 06:00: not after it; D-2 → D are D+1 → D+3
    assert sorted(d for d, n in nights.items() if "big" in n.tags) == [D - timedelta(days=k) for k in (2, 1, 0)]
    s = _session(D - timedelta(days=4), 1020, hour=12)  # 17 h → D-3 05:00: the 05:00 sleep is after it
    nights = _nights(rows, [s])
    assert st.effort_of(s).day == D - timedelta(days=4)  # ended before 06:00: D is the day before (H)
    assert sorted(d for d, n in nights.items() if "big" in n.tags) == [D - timedelta(days=k) for k in (3, 2, 1)]


def test_tagged_nights_never_fire_the_illness_alert():
    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)  # the last 2 nights very high
    alert = _day(rows, _runs())
    assert alert["state"]["key"] == "ill"
    after = _day(rows, _runs() + [_session(D - timedelta(days=2), 500, sid=9)])  # ≥ 6 h on D-2: its nights tagged
    assert after["alert"] is None and after["state"]["key"] == "effort"


# ── the state, first match wins ─────────────────────────────────────────────

def test_rung_1_the_alert_comes_first_even_in_a_window():
    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)
    ultra = _session(D - timedelta(days=5), 610, sid=9)  # D+5 of an ultra: its 3 nights after are not the last 2
    day = _day(rows, _runs() + [ultra])
    assert day["window"]["days"] == 5
    assert (day["state"]["key"], day["state"]["word"]) == ("ill", "À ménager")
    assert day["state"]["text"] == ("FC de nuit nettement au-dessus de ta normale 2 nuits de suite : ça arrive avant "
                                    "un rhume, après de l'alcool ou une grosse journée.")
    assert 0 <= day["score"]["value"] <= 39 and day["score"]["tone"] == "danger"
    # a provisional band (13 nights) never fires it: specific, not sensitive (Quer 2021)
    young = night_rows(range(0, 15), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)
    assert _day(young, _runs())["alert"] is None


def test_rung_2_an_effort_window_names_the_activity_never_its_time():
    """The sentence names the activity and how long ago it started (its day on Activités and its Charge bar's),
    never its hours: this week's Charge ring may print those very hours (each number printed once)."""
    big = _session(D - timedelta(days=5), 935, elapsed=1013, sid=42, name="Trail des Glaciers")
    day = _day(_rich(), _runs() + [big])
    st_ = day["state"]
    assert (st_["key"], st_["tone"], st_["word"]) == ("effort", "warn", "Récupération en cours")
    assert st_["text"] == "Grosse sortie il y a 5 jours : Trail des Glaciers." and st_["href"] == "/activity/42"
    assert "16h53" not in st_["text"] and "16 heures" not in st_["aria"]
    yday = _day(_rich(), _runs() + [_session(D - timedelta(days=1), 190, sid=43, name="Boucle des crêtes")])
    assert yday["state"]["text"] == "Grosse sortie hier : Boucle des crêtes."
    # an ultra started at 21:00 ends the next day: its window counts from its end, the sentence from its start
    night = _session(D - timedelta(days=6), 935, elapsed=1013, hour=21, offset=32400, sid=44, name="Transjeju 100M")
    w = _day(_rich(), _runs() + [night])["window"]
    assert (w["days"], w["ago"]) == (5, 6) and td.effort_text(w) == "Grosse sortie il y a 6 jours : Transjeju 100M."
    long_name = replace(night, name="Ultra-Trail des Montagnes du Monde et de la Mer Intérieure")
    text = td.effort_text(_day(_rich(), _runs() + [long_name])["window"])
    assert len(text.split(" : ", 1)[1]) <= td.NAME_MAX + 1 and text.endswith("….")  # a long name: cut, never wrapped


def test_a_red_component_never_sits_under_a_green_ring():
    """VFC far under its band (sub-score 0): the others full make raw 70, but a component under 40 caps the score at
    69 (H): « Récupération en cours », the sentence names it. A VFC just under its band (sub-score ≥ 40) only lowers
    the score: still « Bien récupéré », no sentence; with the nightly HR 4 bpm up, lower again (no special rule)."""
    far = _day(_rich(hrv_last=50.0), _runs())
    vfc = next(p for p in far["score"]["parts"] if p["key"] == "hrv")
    assert vfc["sub"] == 0 and far["score"]["raw0"] == 70
    assert far["score"]["value"] == 69 == sc.CAP_RED and far["score"]["caps"] == ["red"]
    st_ = far["state"]
    assert (st_["key"], st_["tone"], st_["text"]) == ("hrv", "warn", "VFC basse sur 7 nuits.")
    near = _day(_rich(hrv_last=58.0), _runs())
    sub = next(p for p in near["score"]["parts"] if p["key"] == "hrv")["sub"]
    assert 40 <= sub < 70 and near["score"]["value"] == round(70 + 0.3 * sub)
    assert (near["state"]["key"], near["state"]["text"]) == ("ok", None)
    both = _day(_rich(hrv_last=58.0, hr_last=49.0), _runs())  # +4 bpm over a median of 45: FC de nuit 75
    assert both["state"]["key"] == "ok" and both["score"]["value"] < near["score"]["value"]
    # its bar is green (75): its word too, never « haute » on a green bar
    hr = {r["key"]: r for r in sc.contributors(both["score"], both["state"])["rows"]}["hr"]
    assert (hr["word"], hr["tone"], hr["sub"]) == ("un peu haute", "ok", 75)


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
    assert sc.reason("warn", [], [{"key": "load", "sub": 20}, sleep], day(window=w)) == "effort"
    assert sc.reason("warn", [], [{"key": "sleep", "sub": 30}], day(tst24=330)) == "short"


def test_rung_4_a_short_24_hour_total():
    rows = _rich()
    rows["sleep"].update(night_rows([0], asleep=330, start=(0, 30), end=(6, 30))["sleep"])  # 5h30, no nap
    day = _day(rows, _runs())
    assert (day["state"]["key"], day["state"]["text"]) == ("short", "Nuit courte.")
    assert day["score"]["caps"] == ["short"] and day["score"]["value"] == sc.CAP_SHORT == 65
    # a nap that ends today lifts the 24-h total over 6 h: no short night (Craven 2022: per 24 h)
    rows["nap"][D] = (60, {"windows": [[f"{D}T13:00", f"{D}T14:05"]]}, "Garmin")
    assert _day(rows, _runs())["state"]["key"] == "ok"


def test_rung_5_well_recovered_needs_a_nightly_signal():
    day = _day(_rich(), _runs())
    assert (day["state"]["key"], day["state"]["word"], day["state"]["text"]) == ("ok", "Bien récupéré", None)
    assert day["score"]["value"] == 100
    strava = _day({}, _runs())  # activities only: nothing measured
    assert strava["state"] is None and strava["score"]["value"] is None
    assert td.no_state_line(False) == "Connecte ta montre pour ta récupération."
    # without a nightly signal, a recovery window still makes the state, from the activities alone (OWN-1): the
    # score is the window's cap (nothing measured says otherwise), « Récupération en cours », the activity named
    ultra = _day({}, _runs() + [_session(D - timedelta(days=3), 700, sid=9, name="Ultra des Crêtes")])
    w, s, st_ = ultra["window"], ultra["score"], ultra["state"]
    assert (w["days"], w["cap"]) == (3, 40) and (s["value"], s["tone"], s["measured"]) == (40, "warn", False)
    assert s["caps"] == ["effort", "red", "no_heart"] and s["absent"] == ["hrv", "hr", "sleep"]
    assert (st_["key"], st_["word"], st_["text"]) == ("effort", "Récupération en cours",
                                                     "Grosse sortie il y a 3 jours : Ultra des Crêtes.")
    later = _day({}, _runs() + [_session(D - timedelta(days=6), 700, sid=9)])  # D+6: its 65 cap
    assert (later["score"]["value"], later["state"]["key"]) == (65, "effort")
    assert _day({}, _runs() + [_session(D - timedelta(days=11), 700, sid=9)])["state"] is None  # window over


# ── the score ───────────────────────────────────────────────────────────────

def test_components():
    band = {"center": 70.0, "sd": 0.1, "provisional": False}
    assert sc.hrv_sub(70 * math.exp(-0.05), band) == pytest.approx(100)  # z = −0.5: the band's floor
    assert sc.hrv_sub(70 * math.exp(-0.25), band) == pytest.approx(0)  # z = −2.5
    assert sc.hrv_sub(90, band) == 100  # above: never praised, never more
    assert round(sc.hrv_sub(70 * math.exp(-0.15), band)) == 50
    assert sc.hr_sub(47, {"center": 45}) == 100 and sc.hr_sub(55, {"center": 45}) == 0
    assert sc.hr_sub(51, {"center": 45}) == 50
    assert sc.sleep_sub(420) == sc.sleep_sub(520) == 100 and sc.sleep_sub(360) == 60 and sc.sleep_sub(240) == 0
    assert sc.sleep_sub(390) == 80 and sc.sleep_sub(300) == 30
    assert sc.sleep_sub(480, mean7=380, usual=470) == 40  # 90 min under the usual
    assert sc.load_sub(None) == 100 and sc.load_sub({"load": 20}) == 20


def test_weights_are_renormalised_and_charge_needs_activities_or_a_window():
    assert sc.WEIGHTS == {"hrv": 30, "hr": 25, "sleep": 25, "load": 20}  # (H)
    day = _day(_rich(hrv_last=58.0), [])  # no activity: no Charge, the three nightly signals share 100 %
    parts = {p["key"]: p for p in day["score"]["parts"]}
    assert set(parts) == {"hrv", "hr", "sleep"} and day["score"]["absent"] == ["load"]
    assert [round(parts[k]["weight"], 4) for k in ("hrv", "hr", "sleep")] == [0.375, 0.3125, 0.3125]
    few = _day(_rich(), _runs(n=5))  # 5 activities in 42 days: no Charge (H)
    assert "load" in few["score"]["absent"]
    window = _day(_rich(), [_session(D - timedelta(days=1), 200, sid=9)])  # a window alone brings it
    assert {p["key"]: p["sub"] for p in window["score"]["parts"]}["load"] == 50


@pytest.mark.parametrize("raw,score,tone", [(100, 100, "ok"), (70, 70, "ok"), (69.5, 70, "ok"), (69.49, 69, "warn"),
                                            (64.5, 65, "warn"), (64.4, 64, "warn"), (40, 40, "warn"),
                                            (39.5, 40, "warn"), (39.4, 39, "danger"), (0, 0, "danger")])
def test_the_state_is_the_band_of_the_score(raw, score, tone):
    """Like WHOOP, the state comes from the score (§8): the capped raw rounded half up on the exact value, then its
    band: ≥ 70 bien récupéré, 40–69 en cours, < 40 à ménager. No placement inside a band chosen first."""
    assert sc.rounded(raw) == score and sc.tone_of(score) == tone
    assert not hasattr(sc, "place") and sc.BANDS == {"ok": (70, 100), "warn": (40, 69), "danger": (0, 39)}


def test_the_effort_caps_bind_and_the_ultra_window_ends_after_10_days():
    big = _session(D - timedelta(days=1), 935, elapsed=1013, sid=42)  # D+1 after an ultra (it ended on D-1)
    early = _day(_rich(), _runs() + [big])
    assert early["window"]["cap"] == 40 and early["score"]["caps"] == ["effort", "red"]
    assert early["score"]["value"] == 40  # raw (30+25+25)·100 + 20·20 = 84 → capped at 40: the fold's « 40 »
    assert early["state"]["tone"] == "warn" and early["state"]["key"] == "effort"
    nine, ten = timedelta(days=9), timedelta(days=10)
    later = _day(_rich(), _runs() + [replace(big, start=big.start - nine, day=big.day - nine)])
    assert later["window"]["days"] == 10 and later["window"]["cap"] == 65
    assert later["score"]["value"] == 65  # the fold's « puis à 65 »
    done = _day(_rich(), _runs() + [replace(big, start=big.start - ten, day=big.day - ten)])
    assert done["window"] is None and done["state"]["key"] == "ok"


def test_owner_like_day_the_exact_score():
    """Sommeil 100 and Charge 20 (an ultra on D+5): raw 64,4 under the 65 cap, inside 40–69: 64."""
    rows = night_rows([0], asleep=516, start=(22, 42), end=(7, 30), source="COROS",
                      hr_method="coros_sleep_summary")
    day = _day(rows, [_session(D - timedelta(days=6), 935, elapsed=1013, hour=21, offset=32400, sid=8)])
    raw = (25 * 100 + 20 * 20) / 45
    assert day["window"]["days"] == 5 and day["score"]["raw"] == round(raw, 9)
    assert day["score"]["value"] == 64 == math.floor(raw + 0.5) and day["score"]["caps"] == []  # 65 is above
    assert (day["state"]["key"], day["state"]["tone"]) == ("effort", "warn")  # the lowest component: Charge (20)


def test_no_100_from_sleep_alone():
    """Neither VFC nor FC de nuit in the score (the watch worn some nights only, no normal yet): capped at 80 (H),
    so a long night alone never makes a rested 100; with them, a 100 stays possible."""
    rows = night_rows([0], asleep=588, start=(23, 52), end=(9, 54), source="COROS", hr_method="coros_sleep_summary")
    day = _day(rows, _runs())  # 9h48 → Sommeil 100, Charge 100: raw 100
    assert day["state"]["key"] == "ok" and day["score"]["raw0"] == 100
    assert day["score"]["value"] == 80 == sc.CAP_NO_HEART and day["score"]["caps"] == ["no_heart"]
    assert {p["key"] for p in day["score"]["parts"]} == {"sleep", "load"}
    assert _day(_rich(), _runs())["score"]["value"] == 100


def test_contributors_print_no_number_and_say_what_is_missing():
    big = _session(D - timedelta(days=2), 200, sid=42)
    day = _day(_rich(hrv_last=58.0), _runs(start=3) + [big])  # a long effort D+2 (state), the HRV low
    c = sc.contributors(day["score"], day["state"])
    words = {r["key"]: r["word"] for r in c["rows"]}
    assert words["load"] == "grosse sortie"  # the state line already says when
    hrv_only = _day(_rich(hrv_last=58.0), _runs())
    c = sc.contributors(hrv_only["score"], hrv_only["state"])
    assert {r["key"]: r["word"] for r in c["rows"]}["hrv"] == "basse" and c["absent"] is None
    # in an alert, the window is not the state: the Contributeurs say when
    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)
    ill = _day(rows, _runs(start=3) + [_session(D - timedelta(days=5), 610, sid=9)])
    assert ill["state"]["key"] == "ill"
    assert {r["key"]: r["word"] for r in sc.contributors(ill["score"], ill["state"])["rows"]}["load"] == \
        "grosse sortie il y a 5 j"
    for r in sc.contributors(day["score"], day["state"])["rows"]:
        assert not any(ch.isdigit() for ch in r["word"])


def test_a_contributors_bar_wears_the_colour_its_ring_wears():
    """The sleep row as the Sommeil ring (≥ 7 h, 6–7 h, < 6 h), whatever its sub-score: 6h30 is 80, still
    orange; Charge récente neutral, as the Charge ring (it describes, never warns); the others by their sub-score
    on the score's bands. A red VFC caps the ring at 69: never green over it."""
    assert [sc.sleep_tone(t) for t in (420, 419, 360, 359)] == ["ok", "warn", "warn", "danger"]
    day = _day(_rich(hrv_last=52.0, asleep=390), _runs())  # VFC far under its band, FC in it
    assert day["state"]["tone"] == "warn" and day["score"]["value"] <= sc.CAP_RED
    rows = {r["key"]: r for r in sc.contributors(day["score"], day["state"])["rows"]}
    assert rows["sleep"]["sub"] == 80 and rows["sleep"]["tone"] == "warn"
    assert rows["hrv"]["word"] == "nettement basse" and rows["hrv"]["tone"] == "danger" and rows["hr"]["tone"] == "ok"
    assert rows["load"]["sub"] == 100 and rows["load"]["tone"] == "accent"
    rested = _day(_rich(asleep=440), _runs())
    assert {r["key"]: r["tone"] for r in sc.contributors(rested["score"], rested["state"])["rows"]}["sleep"] == "ok"
    # a week short of the usual: the row as its word says it, never better than the ring
    short_week = {"key": "sleep", "tst24": 390, "sub": 76, "debt": True}
    assert sc._tone(short_week) == "warn" and sc._tone({**short_week, "tst24": 450, "sub": 35}) == "danger"
    window = {"key": "load", "sub": 20, "window": {"ago": 3}}
    assert sc._tone(window) == "accent"


def test_the_contributors_sleep_words_are_masculine():
    """« le sommeil »: suffisant, un peu court, plus court que d'habitude, court (never the feminine)."""
    def word(tst, debt=False):
        return sc._word({"key": "sleep", "tst24": tst, "debt": debt, "sub": 0}, None)
    assert [word(480), word(400), word(450, True), word(330)] == ["suffisant", "un peu court",
                                                                  "plus court que d'habitude", "court"]
    for w in (word(480), word(400), word(450, True), word(330)):
        assert not w.endswith("e") or w == "plus court que d'habitude", w


def test_provisional_bands_say_so_in_the_contributors():
    rows = night_rows(range(0, 16))  # 9 nights before the 7-night window: a « provisoire » normal (H)
    day = _day(rows, _runs())
    words = {r["key"]: r["word"] for r in sc.contributors(day["score"], day["state"])["rows"]}
    assert words["hrv"] == words["hr"] == "dans ta normale (provisoire)"
    assert words["sleep"] == "suffisant"  # the sleep words never carry it


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


def test_history_first_night_of_an_alert_episode_is_tagged_from_the_next_morning():
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


def test_the_history_card_rests_on_its_mean_and_shows_the_bands():
    """It rests on the mean of the days with a score (nothing selected: today's score is the ring's); a tap says
    the score, the state's glyph and word (its tone on the figure, never colour alone), the day and the reason;
    faint 40 and 70 lines labelled on the right; today on a disc."""
    rows = _rich()
    nights = _nights(rows, _runs())
    with nt.memo():
        nt.freeze(nights)
        day = sante._assess(nights, _runs(), [], D)
        hist = sante._history(nights, _runs(), [], D) + [(D, day["state"], day["score"])]
    c = sc.history_card(hist, D)
    d = json.loads(c["data"])
    assert c["read"] == ["100", "en moyenne", ""] and d["sel"] == 14 and d["back"] == 13
    assert d["r"][-1] == ["100", "● Bien récupéré", "jeu. 8 oct."] and d["t"][-1] == "ok"
    assert all(b["cls"] == "ok" for b in c["bars"]) and c["bars"][-1]["today"]
    assert [ln["label"] for ln in c["lines"]] == ["70", "40"] and c["lines"][0]["y"] < c["lines"][1]["y"]
    assert c["xt"][-1]["today"] and c["tone_now"] == ""


# ── the method and the heuristics ───────────────────────────────────────────

def test_the_method_fold_marks_every_choice_h_and_cites_the_brief():
    text = " ".join(sc.METHOD)
    assert text.count("(H)") >= 6 and "Les poids (H)" in text and "Plafonds (H)" in text
    assert "Grosses sorties (H)" in text and "Hynynen 2010" in text and "courses comprises" in text
    assert "Une différence de quelques points ne veut rien dire" in text
    assert "séance" not in text.lower() and "ressenti" not in text.lower()
    # short (« simple ») and true to the numbers: the state from the score, the caps as the score shows them
    assert len(sc.METHOD) <= 5
    assert "70 et plus, bien récupéré ; 40 à 69, récupération en cours ; moins de 40, à ménager" in text
    assert "plafonné à 40 dès la fin et les 3 jours qui suivent, puis à 65" in text and "sans VFC ni FC de nuit, 80" in text
    # OWN-4: provisional from 7 to 13 nights, full at 14 (nights.MIN_BAND_NIGHTS), as the sleep fold says
    assert "« provisoire » de 7 à 13 nuits (H), pleine à 14" in text and "7 à 14" not in text
    # OWN-E: how Charge récente is scored; OWN-1: the score without a night during a window
    assert "charge récente pleine sans grosse sortie, 50, 30 ou 20" in text
    assert "sans aucune nuit, pas de score, sauf pendant la récupération d'une grosse sortie" in text
    assert "2 nuits de suite, 39" in text and "un signal sous 40, 69" in text and "24 h, 65" in text
    assert "place" not in text and "plage" not in text  # no placement inside a band
    assert [name for name, _ in sc.REFS] == ["Plews 2013", "Johnston 2020", "Craven 2022", "Hynynen 2010",
                                             "Buchheit 2014", "Altini & Plews 2021", "Quer 2021", "Doherty 2025"]


def test_an_alert_episode_is_read_by_the_score_never_by_the_band():
    """Seven nights at 56 bpm over a median of 45: the alert episode's own nights make the HR component (the band
    stays the normal before them), so the red score shows the episode."""
    rows = night_rows(range(0, 60), hr=lambda k: 56.0 if k < 7 else 44.0 + k % 3)
    day = _day(rows, _runs())
    assert day["state"]["key"] == "ill" and day["stats"]["hr"]["normal"]["center"] == 45
    hr = next(p for p in day["score"]["parts"] if p["key"] == "hr")
    assert hr["sub"] == 0 and 0 <= day["score"]["value"] <= 39


# ── v4.1: the review's findings ─────────────────────────────────────────────

def test_dawn_finish_the_first_morning_after_is_in_its_window():
    """DAWN-FINISH: a 22-h 100-miler from 05:00 to 03:00, then asleep 04:00 → 11:00: that morning is D+1 (cap 40,
    « hier »), its sleep the first night « après grosse sortie »; finishing at 23:59 or at 00:01 gives the same
    morning after (two minutes never turn a 40 into a 100)."""
    rows = _rich()
    rows["sleep"][D] = (400, {"main_start": f"{D}T04:00", "main_end": f"{D}T11:00", "timeline": True}, "Garmin")
    race = _session(D - timedelta(days=1), 1320, hour=5, sid=50, name="100 miles des Crêtes")  # → D 03:00
    day = _day(rows, _runs() + [race])
    assert st.effort_of(race).day == D - timedelta(days=1)
    assert (day["window"]["days"], day["window"]["cap"], day["score"]["value"]) == (1, 40, 40)
    assert day["state"]["text"] == "Grosse sortie hier : 100 miles des Crêtes."
    nights = _nights(rows, _runs() + [race])
    assert "big" in nights[D].tags and "big" not in nights[D - timedelta(days=1)].tags
    words = {r["key"]: r["word"] for r in sc.contributors(day["score"], day["state"])["rows"]}
    assert words["load"] == "grosse sortie"
    for start in (datetime(2026, 10, 7, 4, 59), datetime(2026, 10, 7, 5, 1)):  # 19 h: 23:59 or 00:01
        run = _session(D - timedelta(days=1), 1140, hour=start.hour, sid=51)
        run = replace(run, start=start.replace(tzinfo=timezone.utc))
        w = _day(_rich(), _runs() + [run])["window"]
        assert (w["days"], w["cap"], w["ago"]) == (1, 40, 1), start


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
    assert e.day == D - timedelta(days=1) and "big" in nights[D].tags
    assert _day(rows, _runs() + [race])["window"]["days"] == 1


def test_two_windows_open_the_charge_is_the_lowest():
    """OVERLAP: an ultra 5 days ago (cap 65 now, Charge 20) and a 7-h outing 2 days ago (cap 45, Charge 30): the
    cap and the sentence are the outing's, Charge récente stays the ultra's 20 (a second effort never raises it)."""
    ultra = st.effort_of(_session(D - timedelta(days=5), 700, sid=1, name="Ultra"))
    hike = st.effort_of(_session(D - timedelta(days=2), 420, sid=2, name="Rando"))
    w = st.effort_window([ultra, hike], D)
    assert (w["effort"].session_id, w["cap"], w["load"]) == (2, 45, 20)
    assert st.effort_window([ultra], D)["load"] == 20 and st.effort_window([hike], D)["load"] == 30


def test_an_ultra_saved_as_two_activities_is_one_effort():
    """SPLIT-ULTRA: the Transjeju saved as 9h00 (21:00 → 06:00) then, 6 min later, 7h47 (→ 13:53): one ultra of
    16h53 (first start to last end, the stop included), named and linked after the first; its 10-day window
    (40 to D+3, 65 to D+10) as if it were one activity. Apart by more than 30 min (H): two efforts."""
    a = _session(date(2026, 10, 2), 540, hour=21, offset=32400, sid=81, name="Transjeju 100M")
    b = _session(date(2026, 10, 3), 467, hour=6, offset=32400, sid=82, name="Transjeju 100M (2)")
    b = replace(b, start=b.start + timedelta(minutes=6))
    [e] = st.efforts([a, b])
    assert (e.kind, e.session_id, e.name, e.minutes) == ("ultra", 81, "Transjeju 100M", 1013)
    assert (e.end, e.day, e.start_day) == (datetime(2026, 10, 3, 13, 53), date(2026, 10, 3), date(2026, 10, 2))
    assert [st.effort_window([e], date(2026, 10, k))["cap"] for k in (6, 9, 13)] == [40, 65, 65]
    far = replace(b, start=b.start + timedelta(minutes=40))
    assert [x.kind for x in st.efforts([a, far])] == ["very_long", "very_long"]


def test_the_day_after_an_alert_stops_firing_the_episode_is_still_read():
    """POST-ALERT-EPISODE: 56 bpm on 06/10 and 07/10 (alert line 50), 49 on 08/10: the alert stops, the episode
    is still open (49 is over the band's top, 48): the 7-night means read its nights, so FC de nuit is not « dans
    ta normale » at 100 the next morning (the band stays the normal before it)."""
    rows = night_rows(range(0, 60), hr=lambda k: 56.0 if k in (1, 2) else 49.0 if k == 0 else 44.0 + k % 3)
    assert _day(rows, _runs(), D - timedelta(days=1))["state"]["key"] == "ill"
    day = _day(rows, _runs())
    assert day["alert"] is None and day["stats"]["hr"]["normal"]["center"] == 45
    hr = next(p for p in day["score"]["parts"] if p["key"] == "hr")
    assert round(day["stats"]["hr"]["value"], 2) == 48.57 and round(hr["sub"]) == 80
    assert sc._word(hr, day["state"]) != "dans ta normale" and day["score"]["value"] == 95


def test_the_illness_alert_makes_the_nightly_hr_row_red():
    """UX1: under « À ménager » for the nightly-HR alert, the FC de nuit row reads the alert's 2 nights: « nettement
    haute », a red bar (≤ 39), never « dans ta normale » in green (5 normal nights dilute the 7-night mean)."""
    rows = night_rows(range(0, 40), hr=lambda k: 53.0 if k < 2 else 44.0 + k % 3)
    day = _day(rows, _runs())
    assert day["state"]["key"] == "ill" and day["score"]["value"] <= 39
    assert day["stats"]["hr"]["value"] - day["stats"]["hr"]["normal"]["center"] < td.HR_UP_BPM  # diluted
    row = {r["key"]: r for r in sc.contributors(day["score"], day["state"])["rows"]}["hr"]
    assert (row["word"], row["tone"]) == ("nettement haute", "danger") and row["sub"] <= 39


def test_the_contributors_words_follow_their_bars_band():
    """« haute » only on an orange bar, « nettement haute » on a red one; « un peu haute » on a green one; the
    same for VFC (« un peu basse », « basse », « nettement basse »)."""
    def word(key, sub, status="in", delta=0.0):
        return sc._word({"key": key, "sub": sub, "status": status, "delta": delta}, None)
    assert [word("hr", 100), word("hr", 80, "above", 3.6), word("hr", 55, "above", 5.6), word("hr", 20, "above", 8.4)] \
        == ["dans ta normale", "un peu haute", "haute", "nettement haute"]
    assert [word("hrv", 100), word("hrv", 85, "below"), word("hrv", 55, "below"), word("hrv", 10, "below"),
            word("hrv", 100, "above")] == ["dans ta normale", "un peu basse", "basse", "nettement basse", "au-dessus"]
    for sub, tone in ((80, "ok"), (55, "warn"), (20, "danger")):
        assert sc._tone({"key": "hr", "sub": sub}) == tone


def test_a_nap_yesterday_afternoon_counts_in_the_24_hours_before_the_wake():
    """SHORT-CAP: a 1h40 nap yesterday 16:00 → 17:45, then 5h00 (01:20 → 06:40): 6h40 asleep in the 24 h before
    the wake, so no « Nuit courte », no 65 cap; the ring, the sub-score and the word read that one figure. A
    « rendormi » nap of the day before (≤ 3 h after its wake) is the night before's: never counted twice."""
    rows = _rich()
    rows["sleep"][D] = (300, {"main_start": f"{D}T01:20", "main_end": f"{D}T06:40", "timeline": True}, "Garmin")
    y = D - timedelta(days=1)
    rows["nap"][y] = (100, {"windows": [[f"{y}T16:00", f"{y}T17:45"]]}, "Garmin")
    day = _day(rows, _runs())
    assert day["tst24"] == 400 and "short" not in day["score"]["caps"]
    assert (day["state"]["key"], day["score"]["value"]) == ("ok", 97)
    row = {r["key"]: r for r in sc.contributors(day["score"], day["state"])["rows"]}["sleep"]
    assert (row["word"], row["tone"]) == ("un peu court", "warn")
    nights = nt.build_nights(rows, D)
    assert nights[D].tst24 == 300 and nights[y].tst24 == 540  # each day's bar keeps its own nap
    # a « rendormi » nap of the day before (06:42, its wake 06:40): never counted again
    rows["nap"][y] = (100, {"windows": [[f"{y}T06:42", f"{y}T08:27"]]}, "Garmin")
    assert nt.day_tst24(nt.build_nights(rows, D), D) == 300
    # a nap partly before the 24 h (05:40 → 07:40, the wake at 06:40): only its part inside them
    rows["nap"][y] = (120, {"windows": [[f"{y}T05:40", f"{y}T07:40"]]}, "Garmin")
    n = nt.build_nights(rows, D)
    n[y].end = datetime.combine(y, datetime.min.time()).replace(hour=2)  # its own wake 02:00: not « rendormi »
    assert nt.day_tst24(n, D) == 300 + 60  # 06:40 → 07:40 of its 2 h


def test_history_reads_only_the_activities_finished_that_day():
    """HISTORY-OVERNIGHT-LEAK: a 9-h hike from 05/10 21:00 to 06/10 06:00 at 2 500 m is uploaded on 06/10: the
    05/10 bar is the page of 05/10 (the hike unknown: no « en altitude » on that night, no 6th activity)."""
    rows = _rich(hrv_last=58.0)
    hike = replace(_session(D - timedelta(days=3), 540, hour=21, sid=60, sport="Hike", name="Rando de nuit"),
                   elev_high=2500.0)
    sessions = _runs(n=5) + [hike]
    hist = {d: (state, score) for d, state, score in _history(rows, sessions)}
    for d, (state, score) in hist.items():
        then = _then(rows, sessions, d)
        assert score["value"] == then["score"]["value"], d
        assert (state or {}).get("key") == (then["state"] or {}).get("key"), d
    d5 = D - timedelta(days=3)
    assert hist[d5][1]["value"] == _then(rows, _runs(n=5), d5)["score"]["value"]


def test_the_day_a_big_outing_ends_it_already_counts():
    """OWN-B / G: an 11-h ultra today 05:00 → 16:00: that evening the state names it (« aujourd'hui »), the
    score is capped like D+1, and the Contributeurs never say « pas de grosse sortie »."""
    ultra = _session(D, 660, hour=5, sid=70, name="Grand Raid")
    day = _day(_rich(), _runs() + [ultra])
    assert (day["window"]["days"], day["window"]["cap"], day["score"]["value"]) == (0, 40, 40)
    assert day["state"]["text"] == "Grosse sortie aujourd'hui : Grand Raid."
    words = {r["key"]: r["word"] for r in sc.contributors(day["score"], day["state"])["rows"]}
    assert words["load"] == "grosse sortie"
    long = _session(D, 210, dplus=1600, hour=7, sid=71, name="Grand tour")  # a 3h30 outing this morning: long
    words = {r["key"]: r["word"] for r in sc.contributors(*(lambda x: (x["score"], x["state"]))(
        _day(_rich(), _runs() + [long])))["rows"]}
    assert words["load"] == "grosse sortie" and "pas de grosse sortie" not in words.values()
    rows = night_rows(range(0, 40), hr=lambda k: 58.0 if k < 2 else 44.0 + k % 3)  # an alert: the window not the state
    ill = _day(rows, _runs() + [ultra])
    assert ill["state"]["key"] == "ill"
    assert {r["key"]: r["word"] for r in sc.contributors(ill["score"], ill["state"])["rows"]}["load"] == \
        "grosse sortie aujourd'hui"


def test_a_past_day_is_said_as_its_readout_prints_it():
    """OWN-3: in the 14-day card a past day's spoken text never says « il y a » (it would count from that day, not
    today); today's bar keeps the state's sentence."""
    big = _session(D - timedelta(days=6), 935, elapsed=1013, hour=21, offset=32400, sid=44, name="Transjeju 100M")
    rows, sessions = _rich(), _runs() + [big]
    nights = _nights(rows, sessions)
    with nt.memo():
        nt.freeze(nights)
        efs = nt.anchor_efforts(nights, st.efforts(sessions))
        day = sante._assess(nights, sessions, efs, D)
        hist = sante._history(nights, sessions, efs, D) + [(D, day["state"], day["score"])]
    d = json.loads(sc.history_card(hist, D)["data"])
    past = [a for a in d["a"][:-1] if "Grosse sortie" in a]
    assert past and all("il y a" not in a and a.endswith("Grosse sortie : Transjeju 100M.") for a in past)
    assert d["a"][-1].endswith("Grosse sortie il y a 6 jours : Transjeju 100M.")
