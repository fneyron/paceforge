"""Santé v4's recovery (SANTE_V4_SPEC.md §2): effort classes from the
activities alone and their windows, the nights after a ≥ 6 h effort tagged and
left out quietly, the state ladder rung by rung, the score without Ressenti
nor races (components, weights, caps, its place inside the state's band), the
14-day history as each day computed it, and the (H) heuristics pinned."""
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
    """What health_page computes for `today` (state, score) on these rows and activities."""
    sessions = list(sessions)
    nights = _nights(rows, sessions, today)
    with nt.memo():
        nt.freeze(nights)
        return sante._assess(nights, sessions, st.efforts(sessions), today)


def _history(rows, sessions=(), today=D):
    sessions = list(sessions)
    nights = _nights(rows, sessions, today)
    with nt.memo():
        nt.freeze(nights)
        return sante._history(nights, sessions, st.efforts(sessions), today)


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
    e = st.effort_of(_session(D, elapsed))
    assert e.kind == kind and st.effort_window([e], D) is None  # D+0, its own day: nothing yet
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
    """An ultra ending at 03:00: the sleep that started after it, waking that same day, is the first night after;
    the sleep that ended before it started (that morning) is not."""
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
    assert sorted(d for d, n in nights.items() if "big" in n.tags) == [D - timedelta(days=k) for k in (3, 2, 1, 0)]


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


def test_rung_2_an_effort_window_names_the_activity():
    big = _session(D - timedelta(days=5), 935, elapsed=1013, sid=42)
    day = _day(_rich(), _runs() + [big])
    st_ = day["state"]
    assert (st_["key"], st_["tone"], st_["word"]) == ("effort", "warn", "Récupération en cours")
    assert st_["text"] == "Grosse sortie de 16h53 il y a 5 jours." and st_["href"] == "/activity/42"
    yday = _day(_rich(), _runs() + [_session(D - timedelta(days=1), 190, sid=43)])
    assert yday["state"]["text"] == "Grosse sortie de 3h10 hier."


def test_rung_3_low_hrv_with_nightly_hr_up():
    day = _day(_rich(hrv_last=58.0, hr_last=49.0), _runs())  # +4 bpm over a median of 45: ≥ +3 (H)
    assert (day["state"]["key"], day["state"]["text"]) == ("hrv", "VFC basse et FC de nuit haute sur 7 nuits.")
    assert day["score"]["caps"] == ["VFC basse et FC de nuit haute"] and day["score"]["value"] <= sc.place("warn", 60)
    alone = _day(_rich(hrv_last=58.0), _runs())  # a low HRV alone never moves the state (H)
    assert alone["state"]["key"] == "ok"
    hr_alone = _day(_rich(hr_last=49.0), _runs())
    assert hr_alone["state"]["key"] == "ok"


def test_rung_4_a_short_24_hour_total():
    rows = _rich()
    rows["sleep"].update(night_rows([0], asleep=330, start=(0, 30), end=(6, 30))["sleep"])  # 5h30, no nap
    day = _day(rows, _runs())
    assert (day["state"]["key"], day["state"]["text"]) == ("short", "Nuit courte.")
    assert "nuit courte" in day["score"]["caps"]
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
    # an effort window still says so without a watch, with no number (Charge alone says nothing of recovery)
    ultra = _day({}, _runs() + [_session(D - timedelta(days=3), 700, sid=9)])
    assert ultra["state"]["key"] == "effort" and ultra["score"]["value"] is None


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


@pytest.mark.parametrize("tone,raw,score", [("ok", 100, 100), ("ok", 0, 70), ("ok", 50, 85), ("warn", 100, 69),
                                            ("warn", 0, 40), ("warn", 65, 59), ("danger", 100, 39),
                                            ("danger", 0, 0), ("danger", 50, 20)])
def test_the_score_sits_inside_the_band_of_the_state(tone, raw, score):
    assert sc.place(tone, raw) == score


def test_the_effort_caps_bind_and_the_ultra_window_ends_after_10_days():
    big = _session(D - timedelta(days=1), 935, elapsed=1013, sid=42)  # D+1 after an ultra (it ended on D-1)
    early = _day(_rich(), _runs() + [big])
    assert early["window"]["cap"] == 40 and early["score"]["caps"] == ["grosse sortie"]
    assert early["score"]["value"] == sc.place("warn", 40) == 52  # raw (30+25+25)·100 + 20·20 = 84 → 40
    nine, ten = timedelta(days=9), timedelta(days=10)
    later = _day(_rich(), _runs() + [replace(big, start=big.start - nine, day=big.day - nine)])
    assert later["window"]["days"] == 10 and later["window"]["cap"] == 65
    assert later["score"]["value"] == sc.place("warn", 65) == 59
    done = _day(_rich(), _runs() + [replace(big, start=big.start - ten, day=big.day - ten)])
    assert done["window"] is None and done["state"]["key"] == "ok"


def test_owner_like_day_the_exact_score():
    """Sommeil 100 and Charge 20 (an ultra on D+5): raw 64,4 under the 65 cap, placed at 59."""
    rows = night_rows([0], asleep=516, start=(22, 42), end=(7, 30), source="COROS",
                      hr_method="coros_sleep_summary")
    day = _day(rows, [_session(D - timedelta(days=6), 935, elapsed=1013, hour=21, offset=32400, sid=8)])
    raw = (25 * 100 + 20 * 20) / 45
    assert day["window"]["days"] == 5 and day["score"]["raw"] == round(raw, 9)
    assert day["score"]["value"] == 59 == math.floor(40 + 29 * raw / 100 + 0.5)


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
    orange; the others by their sub-score on the score's bands."""
    assert [sc.sleep_tone(t) for t in (420, 419, 360, 359)] == ["ok", "warn", "warn", "danger"]
    day = _day(_rich(hrv_last=52.0, asleep=390), _runs())  # VFC far under its band, FC in it: « Bien récupéré »
    assert day["state"]["key"] == "ok"
    rows = {r["key"]: r for r in sc.contributors(day["score"], day["state"])["rows"]}
    assert rows["sleep"]["sub"] == 80 and rows["sleep"]["tone"] == "warn"
    assert rows["hrv"]["word"] == "basse" and rows["hrv"]["tone"] == "danger" and rows["hr"]["tone"] == "ok"
    assert rows["load"]["sub"] == 100 and rows["load"]["tone"] == "ok"
    rested = _day(_rich(asleep=440), _runs())
    assert {r["key"]: r["tone"] for r in sc.contributors(rested["score"], rested["state"])["rows"]}["sleep"] == "ok"


def test_provisional_bands_say_so_in_the_contributors():
    rows = night_rows(range(0, 16))  # 9 nights before the 7-night window: a « provisoire » normal (H)
    day = _day(rows, _runs())
    words = {r["key"]: r["word"] for r in sc.contributors(day["score"], day["state"])["rows"]}
    assert words["hrv"] == words["hr"] == "dans ta normale (provisoire)"
    assert words["sleep"] == "suffisante"  # the sleep words never carry it


# ── the 14-day history: what each day knew ──────────────────────────────────

def _then(rows, sessions, d):
    """The page as computed on day `d`: from the rows and activities stored by then only."""
    past_rows = {m: {x: v for x, v in per.items() if x <= d} for m, per in rows.items()}
    return _day(past_rows, [s for s in sessions if s.day <= d], d)


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


def test_the_history_card_rests_on_a_hint_and_colours_each_day_by_its_state():
    rows = _rich()
    nights = _nights(rows, _runs())
    with nt.memo():
        nt.freeze(nights)
        day = sante._assess(nights, _runs(), [], D)
        hist = sante._history(nights, _runs(), [], D) + [(D, day["state"], day["score"])]
    c = sc.history_card(hist, D)
    d = json.loads(c["data"])
    assert c["read"] == ["", "", "Touche un jour pour son score et son état."] and d["sel"] == 14
    assert d["r"][-1] == ["jeu. 8 oct.", "100 · Bien récupéré", ""]
    assert all(b["cls"] == "ok" for b in c["bars"]) and c["bars"][-1]["today"]


# ── the method and the heuristics ───────────────────────────────────────────

def test_the_method_fold_marks_every_choice_h_and_cites_the_brief():
    text = " ".join(sc.METHOD)
    assert text.count("(H)") >= 6 and "Les poids (H)" in text and "Plafonds (H)" in text
    assert "Grosses sorties (H)" in text and "Hynynen 2010" in text and "courses comprises" in text
    assert "Une différence de quelques points ne veut rien dire" in text
    assert "séance" not in text.lower() and "ressenti" not in text.lower()
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
