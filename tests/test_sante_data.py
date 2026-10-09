"""Santé v3 data layer, on the owner's real COROS answers (tests/owner_coros.py):
PaceForge's own nightly HRV clipped to the main window (France, Korea and the
autumn clock change), the « Sleep HR » line kept only for main sleeps, naps on
local ISO windows with their guards, the 24-h total, and the readers that still
accept the rows written before."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from app.services import coros
from app.services.health import (
    aggregate_days,
    feel_of,
    main_episode,
    main_window,
    nap_daily,
    nap_guard,
    nap_windows,
)
from tests import owner_coros as oc

D = date(2026, 10, 7)


def _inside(points, a, b):
    return [v for ts, tz, v in points if a <= coros.local_time(ts, tz) <= b]


def _mean(xs):
    return round(sum(xs) / len(xs), 1)


# ── nightly HRV: the readings inside the main window, nothing else ──────────

def test_owner_7_oct_hrv_is_the_main_window_only():
    points = coros.parse_hrv_points(oc.HRV_2026_10_07)
    assert len(points) == 87 and {tz for _, tz, _ in points} == {36}
    main = (datetime(2026, 10, 6, 23, 35), datetime(2026, 10, 7, 5, 38))
    nap = (datetime(2026, 10, 7, 6, 42), datetime(2026, 10, 7, 9, 7))
    # the night spans two local-day groups: 3 readings after 23:35 on the 6th, 29 on the 7th
    v = coros.night_hrv(points, *main)
    assert v == {"value": 95.1, "n": 32, "tz": 36}
    assert len(_inside(points, *nap)) == 14  # the nap's readings stay out of the night
    # COROS's own « HRV Avg » (97) is the arithmetic mean of the main window, nap left out: never read
    assert _mean(_inside(points, *main)) == 97.7 and _mean(_inside(points, main[0], nap[1])) == 99.1


def test_france_night_tz_4_matches_the_window():
    points = coros.parse_hrv_points(oc.HRV_2025_11_05)
    main = (datetime(2025, 11, 4, 22, 41), datetime(2025, 11, 5, 7, 30))
    v = coros.night_hrv(points, *main)
    assert v["n"] == 50 and v["tz"] == 4 and v["value"] == 79.8
    assert _mean(_inside(points, *main)) == 83.2  # COROS's 83: UTC+1 is the right reading
    # read at UTC instead, the night would lose its first readings (and gain the afternoon's)
    wrong = [v for ts, _, v in points if main[0] <= coros.local_time(ts, 0) <= main[1]]
    assert len(wrong) == 43


def test_france_nap_day_hrv_leaves_the_nap_out():
    points = coros.parse_hrv_points(oc.HRV_2025_10_31)
    main = (datetime(2025, 10, 30, 23, 2), datetime(2025, 10, 31, 4, 40))
    nap = (datetime(2025, 10, 31, 5, 58), datetime(2025, 10, 31, 7, 30))
    assert coros.night_hrv(points, *main) == {"value": 94.6, "n": 26, "tz": 4}
    assert len(_inside(points, *nap)) == 8
    # COROS's 98 = the main window's mean (98.2); with the nap it would be 102.3
    assert _mean(_inside(points, *main)) == 98.2
    assert _mean(_inside(points, *main) + _inside(points, *nap)) == 102.3


def test_autumn_clock_change_night_keeps_one_offset():
    points = coros.parse_hrv_points(oc.HRV_2025_DST)
    by_day = {}
    for ts, tz, _ in points:
        by_day.setdefault(coros.local_time(ts, tz).date(), set()).add(tz)
    # the clocks went back at 01:00 UTC on the 26th, yet the whole night stays on timezone 12 (UTC+3),
    # the frame of COROS's window (22:32 → 05:35); the next night is on timezone 8 (UTC+2)
    assert by_day[date(2025, 10, 26)] == {12} and by_day[date(2025, 10, 28)] == {8}
    dst = coros.night_hrv(points, datetime(2025, 10, 25, 22, 32), datetime(2025, 10, 26, 5, 35))
    assert dst["n"] == 20 and dst["tz"] == 12
    after = coros.night_hrv(points, datetime(2025, 10, 28, 0, 56), datetime(2025, 10, 28, 7, 4))
    assert after["n"] == 25 and after["tz"] == 8
    assert _mean(_inside(points, datetime(2025, 10, 28, 0, 56), datetime(2025, 10, 28, 7, 4))) == 91.4  # COROS 91


def test_hrv_needs_twelve_readings_and_both_days_read():
    points = coros.parse_hrv_points(oc.HRV_2026_10_07)
    ov = coros.parse_sleep_overview(oc.OVERVIEW_2026)
    rows = {r.day: r for r in coros.hrv_dailies(points, ov, {D, D - timedelta(days=1)})}
    assert rows[D].value == 95.1 and rows[D].details == {"n": 32, "tz": 540, "method": "ln_mean_main"}
    assert D - timedelta(days=1) not in rows  # the 6th: its evening (the 5th) was not read
    assert not coros.hrv_dailies(points, ov, {D})  # half a night would overwrite a whole one
    assert coros.night_hrv(points[:20], datetime(2026, 10, 6, 23, 35), datetime(2026, 10, 7, 5, 38)) is None


# ── the main sleep, the Sleep HR line, the naps ─────────────────────────────

def test_owner_nights_and_the_24h_total():
    ov = coros.parse_sleep_overview(oc.OVERVIEW_2026)
    assert date(2026, 9, 25) not in ov  # the nap-only day is never a night
    naps = coros.parse_naps(oc.OVERVIEW_2026)
    sleep = {r.day: r for r in coros.sleep_dailies(ov, {D: 36})}
    nap = {r.day: r for r in coros.nap_dailies(naps, ov)}
    assert sleep[D].value == 350 and sleep[D].details == {
        "main_start": "2026-10-06T23:35", "main_end": "2026-10-07T05:38", "period": 363, "bedtime": "23:35",
        "wake": "05:38", "timeline": False, "tz": 540, "daily": 490, "naps_folded": True}
    assert nap[D].value == 140 and nap[D].details == {"period": 145,
                                                       "windows": [["2026-10-07T06:42", "2026-10-07T09:07"]]}
    assert sleep[D].value + nap[D].value == 490 == sleep[D].details["daily"]  # 5h50 + 2h20 = 8h10, COROS agrees
    assert nap[date(2026, 9, 25)].value == 82 and date(2026, 9, 25) not in sleep


def test_older_records_window_and_awake():
    ov = coros.parse_sleep_overview(oc.OVERVIEW_2025 + oc.OVERVIEW_LEGACY.split("========================\n", 1)[1])
    sleep = {r.day: r for r in coros.sleep_dailies(ov)}
    assert sleep[date(2025, 10, 26)].value == 423 - 22  # « Main Sleep » is the window there, awake inside
    assert sleep[date(2026, 7, 17)].value == 259 and "tz" not in sleep[date(2026, 7, 17)].details


def test_sleep_hr_only_from_a_main_sleep_summary():
    daily = coros.parse_daily_sleep(oc.DAILY)
    ov = coros.parse_sleep_overview(oc.OVERVIEW_2026 + oc.OVERVIEW_2025
                                    + oc.OVERVIEW_LEGACY.split("========================\n", 1)[1])
    naps = coros.parse_naps(oc.OVERVIEW_2026 + oc.OVERVIEW_2025)
    hr = {r.day: r for r in coros.hr_night_dailies(daily, ov, naps)}
    assert hr[D].value == 37 and hr[D].details == {"min": 31, "max": 56, "method": "coros_sleep_summary",
                                                   "nap_day": True}
    assert hr[date(2026, 10, 6)].value == 35 and hr[date(2026, 10, 6)].details["nap_day"] is False
    assert hr[date(2025, 10, 31)].details["nap_day"] is True  # summary total 5h38 = the window: nap out
    assert date(2026, 9, 25) not in hr  # the summary of a lone nap, not of a night
    assert date(2026, 7, 17) not in hr  # malformed: total 4h19 against a 4h35 window, and no HR line


def test_nap_guards():
    legacy = coros.parse_naps(oc.OVERVIEW_LEGACY)
    assert legacy[date(2026, 7, 16)]["legacy"] is True
    assert coros.nap_dailies(legacy) == []  # dated 1982: not a nap of that day
    autumn = coros.parse_naps(oc.OVERVIEW_2025)
    [n29, n31] = coros.nap_dailies(autumn, coros.parse_sleep_overview(oc.OVERVIEW_2025))
    assert (n29.day, n29.value, n29.details) == (date(2025, 10, 29), 40, {
        "period": 40, "windows": [["2025-10-29T12:33", "2025-10-29T13:13"]], "legacy": True})
    assert n31.details["windows"] == [["2025-10-31T05:58", "2025-10-31T07:30"]]
    d = date(2026, 10, 7)
    main = (datetime(2026, 10, 6, 23, 35), datetime(2026, 10, 7, 5, 38))
    w = lambda a, b: (datetime(2026, 10, *a), datetime(2026, 10, *b))  # noqa: E731
    # over the night: kept since 2026-10-09, the night's end, folded into it when read (health.fold_naps); dropped
    # here its minutes were lost (owner's report 2026-10-09)
    assert nap_guard(d, [w((7, 5, 0), (7, 6, 0))], main) == [w((7, 5, 0), (7, 6, 0))]
    assert nap_guard(d, [w((7, 12, 0), (7, 18, 30))]) == []  # longer than 6 h
    assert nap_guard(d, [w((6, 13, 0), (6, 13, 40))]) == [w((6, 13, 0), (6, 13, 40))]  # the afternoon before
    assert nap_guard(d, [w((6, 10, 0), (6, 11, 0))]) == []  # ends before 12:00 the day before
    # one window of two goes: the minutes shrink in proportion
    row = nap_daily(d, 90, 100, [w((7, 13, 0), (7, 13, 50)), w((6, 10, 0), (6, 10, 50))], main)
    assert row.value == 45 and row.details == {"period": 50, "windows": [["2026-10-07T13:00", "2026-10-07T13:50"]]}
    assert nap_daily(d, 20, None, []).details == {"period": None, "windows": []}  # Garmin's minutes alone
    assert nap_daily(d, 0, None, []) is None


# ── readers of the rows written before ──────────────────────────────────────

def test_old_rows_are_still_read():
    d = date(2026, 10, 7)
    assert nap_windows(d, {"windows": [["06:42", "09:07"], ["23:30", "00:10"], ["bad"]]}) == [
        (datetime(2026, 10, 7, 6, 42), datetime(2026, 10, 7, 9, 7)),
        (datetime(2026, 10, 6, 23, 30), datetime(2026, 10, 7, 0, 10))]
    assert nap_windows(d, {"windows": [["2026-10-07T06:42", "2026-10-07T09:07"]]}) == [
        (datetime(2026, 10, 7, 6, 42), datetime(2026, 10, 7, 9, 7))]
    assert main_window(d, {"bedtime": "23:35", "wake": "05:38"}) == (datetime(2026, 10, 6, 23, 35),
                                                                     datetime(2026, 10, 7, 5, 38))
    assert main_window(d, {"bedtime": "00:31", "wake": "09:31"}) == (datetime(2026, 10, 7, 0, 31),
                                                                     datetime(2026, 10, 7, 9, 31))
    assert main_window(d, {"main_start": "2026-10-06T23:35", "main_end": "2026-10-07T05:38"})[1].hour == 5
    assert main_window(d, {"bedtime": "22:00", "wake": "06:00", "from_in_bed": True}) is None
    assert feel_of(3, {"legs_heavy": True}) == {"value": 3, "why": ["legs"], "alcohol": False, "answered": True}
    assert feel_of(3, {"why": ["sick", "nope"], "alcohol": True}) == {"value": 3, "why": ["sick"], "alcohol": True,
                                                                      "answered": True}
    assert feel_of(2, {"alcohol": True, "answered": False})["answered"] is False  # only « alcool hier » was tapped
    assert feel_of(None, {}) is None


def test_sample_only_sources_split_episodes_on_an_hour_awake():
    """The hour rule merged a morning nap into the night; episodes keep it out (H: a gap ≥ 60 min)."""
    t = lambda h, m=0, day=7: datetime(2026, 10, day, h, m)  # noqa: E731
    row = lambda a, b, kind="core": SimpleNamespace(start_at=a, end_at=b, value=0, source="Phone", kind=kind)  # noqa: E731
    night = [row(t(23, 35, 6), t(2, 0)), row(t(2, 0), t(5, 38), "rem")]
    nap = [row(t(6, 42), t(9, 7))]
    out = aggregate_days("sleep", night + nap, [D])[D]
    assert out["value"] == 363 and out["details"]["wake"] == "05:38"
    assert out["details"]["main_end"] == "2026-10-07T05:38"
    close = [row(t(6, 20), t(7, 0))]  # back asleep after 42 min: the same episode
    assert aggregate_days("sleep", night + close, [D])[D]["details"]["wake"] == "07:00"
    assert main_episode([(t(14, 0), t(15, 0))], D) is None  # an afternoon episode is never the night
    # a watch's samples are its timeline only: never aggregated into the night
    watch = [SimpleNamespace(start_at=r.start_at, end_at=r.end_at, value=0, source="Garmin", kind=r.kind)
             for r in night]
    assert aggregate_days("sleep", watch, [D]) == {}
