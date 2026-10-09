"""Naps next to the night, and each nap once (owner's report, 2026-10-09; reviewers' findings the same day).

- COROS's 23:03 → 00:09 « nap » of the 23:57 → 04:58 night of 9 Oct is the start of that night
  (health.fold_naps): stored as COROS gives it (health.nap_guard keeps it), folded into the night when read
  (nights.build_nights): one night from 23:03, its minutes outside the main window added once, never over
  COROS's « Daily Sleep » (plus a nap folded from the day before). The nightly HRV is read inside that whole
  window (coros.fold_overview), only when the day before was read too.
- Each nap is counted by one morning only (nights.day_naps): an afternoon nap is the next morning's; what no
  next morning counts stays its own day's; today's afternoon nap is named on the card, counted tomorrow. The
  bar, the ring, the score, the table and the sleep owed read one figure.
- A COROS sync deletes the nightly rows it no longer yields for the days its parsers read (health.store_daily
  `covered`, coros.covered_days), a few at most, never another watch's, never another day's.
"""
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.models.health import HealthMetric
from app.services import coros
from app.services import nights as nt
from app.services import race_prep as rp
from app.services import sante_sleep as sl
from app.services.health import STALE_MAX, Daily, fold_naps, store_daily
from tests.test_coros import as_user, hrv_text, no_commit  # noqa: F401  (fixtures)

D7, D8, D9 = date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9)

# the owner's real answers for 9 Oct (querySleepOverview, queryDailyHealthData, read 2026-10-09)
OCT9 = """Sleep Overview
========================
Note: each record below is dated by its wake-up day.

2026-10-09
Sleep Score: 72
Daily Sleep: 5h 47min (incl. naps)
Main Sleep (asleep): 4h 50min
Main Sleep Period (incl. awake): 5h 1min
Sleep metrics scope: daily
Deep Sleep Ratio: 18%
Light Sleep Ratio: 55%
REM Ratio: 23%
Awake Ratio: 4%
Awake Time: 11 min
Awake Count (>5 min): 0
Main Sleep Window: 2026-10-08 23:57 - 2026-10-09 04:58
Naps Total (asleep): 57 min
Naps Period (incl. awake): 1h 6min
Nap Window: 2026-10-08 23:03 - 2026-10-09 00:09"""

DAILY_OCT9 = """Daily Health Data — Last 14 days | Resting HR: 32 bpm | HRV Baseline: 42 ms
Note: sleep entries are dated by their wake-up day.

--- 20261009 ---
Steps: 6,215 | Calories: 330 kcal | Exercise: 4 min
Stress: Avg 24
Sleep Summary:
  Total: 1h 6min | Deep: 0 min | Light: 57 min | REM: 0 min | Awake: 9 min
  Sleep HR: Avg 44 bpm | Min 32 bpm | Max 64 bpm"""


def dt(day: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m)


def overview(day: date, main: str | None, naps: list[str], asleep="7h 30min", period="7h 40min", nap="40 min",
             daily="8h 10min") -> str:
    """A querySleepOverview block in COROS's wording (`main` None: a nap-only day)."""
    lines = [f"{day}", "Sleep Score: 80", f"Daily Sleep: {daily} (incl. naps)"]
    if main:
        lines += [f"Main Sleep (asleep): {asleep}", f"Main Sleep Period (incl. awake): {period}",
                  f"Main Sleep Window: {main}"]
    if naps:
        lines += [f"Naps Total (asleep): {nap}", f"Nap Window{'s' if len(naps) > 1 else ''}: {', '.join(naps)}"]
    else:
        lines.append("Naps Total: 0 min")
    return "\n".join(lines) + "\n\n"


def build(text: str, daily_text: str = "", **extra) -> dict:
    data = {"overview": coros.parse_sleep_overview(text), "naps": coros.parse_naps(text),
            "daily": coros.parse_daily_sleep(daily_text), **extra}
    out: dict = {}
    for r in coros.build_daily(data, D9):
        out.setdefault(r.metric, {})[r.day] = r
    return out


def stored(rows: dict) -> dict:
    """build()'s values as the rows build_nights reads."""
    return {m: {d: (r.value, r.details, "COROS") for d, r in by.items()} for m, by in rows.items()}


def night(start: datetime, end: datetime, asleep: int, daily: int | None = None) -> tuple:
    det = {"main_start": start.isoformat(timespec="minutes"), "main_end": end.isoformat(timespec="minutes")}
    if daily is not None:
        det["daily"] = daily
    return asleep, det, "COROS"


def nap(*windows: tuple[datetime, datetime], minutes: int) -> tuple:
    return minutes, {"windows": [[a.isoformat(timespec="minutes"), b.isoformat(timespec="minutes")]
                                 for a, b in windows]}, "COROS"


def counted_once(nights: dict) -> None:
    """Every stored nap minute is in exactly one place: a morning's 24 h, a nap-only day's own bar, or the
    card's « comptée dans ta prochaine nuit » (the rounding of a split nap aside)."""
    stored_min = sum(n.nap_min for n in nights.values())
    in_24h = sum(t - n.asleep for d, n in nights.items() if (t := nt.day_tst24(nights, d)) is not None)
    alone = sum(nt.bar_nap_min(nights, d) for d, n in nights.items() if n.asleep is None)
    pending = sum(m for d in nights for _, _, m in nt.pending_naps(nights, d))
    assert abs(in_24h + alone + pending - stored_min) <= 1, (in_24h, alone, pending, stored_min)


# ── the night of 9 Oct ──────────────────────────────────────────────────────

def test_the_9_october_nap_is_the_start_of_its_night():
    rows = build(OCT9, DAILY_OCT9)
    sleep, kept = rows["sleep"][D9], rows["nap"][D9]
    # stored as COROS gives them: its main sleep, and its « nap » kept (main's guard dropped it: 57 min lost)
    assert (sleep.value, sleep.details["main_start"], sleep.details["main_end"], sleep.details["daily"]) == (
        290, "2026-10-08T23:57", "2026-10-09T04:58", 347)
    assert sleep.details["naps_folded"] and "folded" not in sleep.details
    assert (kept.value, kept.details["windows"]) == (57, [["2026-10-08T23:03", "2026-10-09T00:09"]])
    # COROS's « Sleep Summary » that day is the 23:03 segment's (1h06, light sleep only, 44 bpm): checked against
    # COROS's own main period (5h01), still not the night's stages nor its nightly HR
    assert "stages" not in sleep.details and "hr_night" not in rows
    nights = nt.build_nights(stored(rows), D9)
    n = nights[D9]
    # read: one night from 23:03, 4h50 + the 47 of its 57 min outside 23:57 → 04:58 (the 12 min inside counted
    # once): 5h37, under COROS's 5h47
    assert (n.start, n.end, n.asleep, n.naps) == (dt(D8, 23, 3), dt(D9, 4, 58), 337, [])
    h = sl.hero(nights, D9)
    assert (h["label"], h["times"], h["nap"], h["total"], h["later"]) == (
        "Cette nuit", "23:05 → 05:00", None, "5h37", None)
    assert nt.day_tst24(nights, D9) == nt.slept_before_wake(nights, D9) == 337 and nt.bar_nap_min(nights, D9) == 0


def test_the_9_october_hrv_is_read_over_the_whole_night_once_both_days_were_read():
    windows = {D9: (dt(D8, 23, 3), dt(D9, 4, 58))}
    points = coros.parse_hrv_points(hrv_text(windows, tz=8, every_min=10))
    inside = sum(1 for ts, tz, _ in points if dt(D8, 23, 3) <= coros.local_time(ts, tz) <= dt(D9, 4, 58))
    rows = build(OCT9, DAILY_OCT9, hrv_points=points, hrv_days={D8, D9}, overview_days={D8, D9})
    assert rows["hrv"][D9].details["n"] == inside == 36  # 23:07 → 04:57: the first hour's readings in
    # the day before not read (a 7-day window starting on 9 Oct): no HRV computed, the stored one stays
    assert "hrv" not in build(OCT9, DAILY_OCT9, hrv_points=points, hrv_days={D8, D9}, overview_days={D9})


def test_production_rows_with_the_stale_nap_read_as_one_night():
    """The rows a sync of main stored on 9 Oct (its night, the 23:03 nap stored before COROS had the night): read,
    the nap is folded too (build_nights), never « + sieste 57 min » over the night."""
    rows = {"sleep": {D9: night(dt(D8, 23, 57), dt(D9, 4, 58), 290, daily=347)},
            "nap": {D9: nap((dt(D8, 23, 3), dt(D9, 0, 9)), minutes=57)}}
    nights = nt.build_nights(rows, D9)
    n = nights[D9]
    assert (n.start, n.end, n.asleep, n.naps) == (dt(D8, 23, 3), dt(D9, 4, 58), 337, [])
    h = sl.hero(nights, D9)
    assert (h["times"], h["nap"], h["total"]) == ("23:05 → 05:00", None, "5h37")
    # a night whose window already holds the nap: it adds nothing (never counted twice)
    rows["sleep"][D9] = night(dt(D8, 23, 3), dt(D9, 4, 58), 337, daily=347)
    assert nt.build_nights(rows, D9)[D9].asleep == 337


# ── the rule (H) ────────────────────────────────────────────────────────────

def test_a_nap_ending_within_30_min_before_the_night_is_its_start():
    window = (dt(D8, 23, 0), dt(D9, 7, 0))
    # dozing 22:00 → 22:40, in bed at 23:00: one night from 22:00, its 35 min added (the 20 min between: awake)
    assert fold_naps(*window, 450, [(dt(D8, 22), dt(D8, 22, 40), 35.0)])[:3] == (dt(D8, 22), window[1], 485)
    # two in a row: 22:10 → 22:40 joins, then 21:00 → 21:45 is 25 min before the new bedtime
    start, _, asleep, joined, left = fold_naps(*window, 450, [(dt(D8, 21), dt(D8, 21, 45), 40.0),
                                                               (dt(D8, 22, 10), dt(D8, 22, 40), 25.0)])
    assert (start, asleep, len(joined), left) == (dt(D8, 21), 515, 2, [])
    # 45 min before the night: an evening nap, kept as a nap
    start, _, asleep, joined, left = fold_naps(*window, 450, [(dt(D8, 21, 30), dt(D8, 22, 15), 40.0)])
    assert (start, asleep, joined, len(left)) == (window[0], 450, [], 1)
    # COROS's « Daily Sleep » (which holds the naps listed under the night's day, `own`) caps the night, less the
    # day's naps that stay, never under its own minutes; a nap listed under the day before is not in it
    early, later = (dt(D8, 22), dt(D8, 22, 40), 35.0), (dt(D9, 14), dt(D9, 15), 20.0)
    assert fold_naps(*window, 450, [early], daily=470, own=[early])[2] == 470
    assert fold_naps(*window, 450, [early, later], daily=490, own=[early, later])[2] == 470
    assert fold_naps(*window, 450, [early], daily=400, own=[early])[2] == 450
    assert fold_naps(*window, 450, [early], daily=450)[2] == 485


def test_a_night_over_its_own_daily_sleep_took_the_nap_off_the_day_before():
    """Reviewers' finding 4: a nap ending before midnight is listed by COROS under the day before, in that day's
    « Daily Sleep ». Folded, the night goes over its own day's « Daily Sleep » (7h20 + 20 min) and that day's
    24 h loses it: the two days together never go over COROS's two figures."""
    text = (overview(D8, "2026-10-07 23:00 - 2026-10-08 07:00", ["2026-10-08 22:40 - 2026-10-08 23:05"],
                     asleep="7h 50min", nap="20 min", daily="8h 10min")
            + overview(D9, "2026-10-08 23:30 - 2026-10-09 07:00", [], asleep="7h 20min", daily="7h 20min"))
    nights = nt.build_nights(stored(build(text)), D9)
    assert (nights[D9].start, nights[D9].asleep) == (dt(D8, 22, 40), 460)  # over COROS's 440 for 9 Oct
    assert nights[D8].naps == [] and nt.day_tst24(nights, D8) == 470
    assert nt.day_tst24(nights, D8) + nt.day_tst24(nights, D9) == 930 == 490 + 440
    counted_once(nights)


def test_coros_stores_an_adjacent_nap_as_it_is_and_it_is_folded_when_read():
    """COROS lists a nap under a wake-up day: the night's own, or the day before (one ending before midnight)."""
    main = "2026-10-08 23:00 - 2026-10-09 07:00"
    rows = build(overview(D9, main, ["2026-10-08 22:00 - 2026-10-08 22:40"], nap="35 min", daily="8h 5min"))
    assert rows["sleep"][D9].value == 450 and rows["sleep"][D9].details["main_start"] == "2026-10-08T23:00"
    assert rows["nap"][D9].value == 35  # kept, never dropped
    n = nt.build_nights(stored(rows), D9)[D9]
    assert (n.start, n.asleep, n.naps) == (dt(D8, 22), 485, [])
    text = (overview(D8, "2026-10-07 23:00 - 2026-10-08 07:00", ["2026-10-08 14:00 - 2026-10-08 15:00",
                                                                   "2026-10-08 22:00 - 2026-10-08 22:40"],
                     nap="1h 35min", daily="9h 5min")
            + overview(D9, main, [], daily="7h 30min"))
    rows = build(text)
    assert rows["nap"][D8].value == 95 and len(rows["nap"][D8].details["windows"]) == 2
    nights = nt.build_nights(stored(rows), D9)
    # the 22:00 window (its 38 of the 95 min, by length) is 9 Oct's night start; the 14:00 one (57) stays a nap,
    # the next morning's: 9 Oct's 24 h holds both, 8 Oct's its night alone
    assert (nights[D9].start, nights[D9].asleep) == (dt(D8, 22), 450 + 38)
    assert nights[D8].naps == [(dt(D8, 14), dt(D8, 15), 57)] and nights[D8].start == dt(D7, 23)
    assert (nt.day_tst24(nights, D8), nt.day_tst24(nights, D9)) == (450, 488 + 57)
    counted_once(nights)


def test_after_the_wake_a_nap_stays_rendormi_unless_it_overlaps():
    """Main's « rendormi » (a nap ≤ 3 h after the wake: a nap of that night, its wake left out of the wake
    median) is kept for a nap starting after the wake, even 20 min after; one overlapping the wake has no wake
    in between: the night runs to its end (COROS's, stored as it is: the guard keeps it)."""
    window = (dt(D8, 23, 0), dt(D9, 7, 0))
    assert fold_naps(*window, 450, [(dt(D9, 7, 20), dt(D9, 8, 30), 60.0)])[3] == []
    rows = {"sleep": {D9: night(*window, 450)}, "nap": {D9: nap((dt(D9, 7, 20), dt(D9, 8, 30)), minutes=60)}}
    nights = nt.build_nights(rows, D9)
    n = nights[D9]
    assert n.end == dt(D9, 7) and n.resettled and len(n.naps) == 1
    assert nt.day_tst24(nights, D9) == nt.slept_before_wake(nights, D9) == 510  # with its own night, once
    # 06:50 → 08:30 overlaps the wake: one night to 08:30, the 90 of its 100 min after 07:00 added
    assert fold_naps(*window, 450, [(dt(D9, 6, 50), dt(D9, 8, 30), 100.0)])[:3] == (window[0], dt(D9, 8, 30), 540)
    rows = build(overview(D9, "2026-10-08 23:00 - 2026-10-09 07:00", ["2026-10-09 06:30 - 2026-10-09 08:00"],
                          asleep="7h 40min", nap="1h 20min", daily="9h 0min"))
    assert rows["nap"][D9].value == 80  # main's guard dropped it: 80 min lost
    n = nt.build_nights(stored(rows), D9)[D9]
    assert (n.end, n.asleep, n.naps, n.resettled) == (dt(D9, 8), 460 + 53, [], False)


# ── each nap once ───────────────────────────────────────────────────────────

def test_an_afternoon_nap_is_a_nap_counted_once_in_the_next_morning():
    """8 Oct 14:00 → 15:00 after an 07:00 wake: a nap (never folded), in 9 Oct's 24 h only — its bar, its ring and
    score figure, its readout, the card and the sleep owed agree; 8 Oct's 24 h is its night alone."""
    rows = {"sleep": {D8: night(dt(D7, 23), dt(D8, 7), 450), D9: night(dt(D8, 23), dt(D9, 7), 450)},
            "nap": {D8: nap((dt(D8, 14), dt(D8, 15)), minutes=60)}}
    nights = nt.build_nights(rows, D9)
    assert nights[D8].naps == [(dt(D8, 14), dt(D8, 15), 60)] and nights[D8].start == dt(D7, 23)
    assert nt.day_tst24(nights, D8) == nt.slept_before_wake(nights, D8) == 450 and nt.bar_nap_min(nights, D8) == 0
    assert nt.day_tst24(nights, D9) == nt.slept_before_wake(nights, D9) == 510 and nt.bar_nap_min(nights, D9) == 60
    counted_once(nights)
    c = sl.bars(nights, D9, "14")
    assert [b.get("top") is not None for b in c["bars"][-2:]] == [False, True]  # the nap on 9 Oct's bar only
    h = sl.hero(nights, D9)
    assert (h["nap"], h["total"], h["later"]) == ("+ sieste 1h00", "8h30", None)
    table = {r["iso"]: (r["tst"], r["nap"]) for r in sl.rows(nights, D9)}
    assert table == {"2026-10-09": ("8h30", "1h00"), "2026-10-08": ("7h30", "—")}
    assert [nt.night_value(nights, d, "tst24") for d in (D8, D9)] == [450, 510]  # what the bands read


def test_a_nap_before_its_own_wake_is_never_the_next_mornings_too():
    """Reviewers' E7: after a night out, 8 Oct's nap 03:00 → 05:30 then its main sleep 07:00 → 13:00; 9 Oct's
    night 21:00 → 04:30. The nap is 8 Oct's (it ended before that wake), never again 9 Oct's (it also ended in
    the 24 h before 04:30)."""
    rows = {"sleep": {D8: night(dt(D8, 7), dt(D8, 13), 350), D9: night(dt(D8, 21), dt(D9, 4, 30), 440)},
            "nap": {D8: nap((dt(D8, 3), dt(D8, 5, 30)), minutes=140)}}
    nights = nt.build_nights(rows, D9)
    assert (nt.day_tst24(nights, D8), nt.day_tst24(nights, D9)) == (490, 440)
    assert (nt.bar_nap_min(nights, D8), nt.bar_nap_min(nights, D9)) == (140, 0)
    counted_once(nights)


def test_a_nap_no_next_morning_counts_stays_its_own_days():
    """Reviewers' E8, E12 and a next night not recorded: never lost (main counted every nap on its own day)."""
    # E8: wake 06:00, nap 10:00 → 11:00, next night 01:30 → 11:30: the nap ended 24 h 30 before that wake
    rows = {"sleep": {D8: night(dt(D7, 23), dt(D8, 6), 410), D9: night(dt(D9, 1, 30), dt(D9, 11, 30), 590)},
            "nap": {D8: nap((dt(D8, 10), dt(D8, 11)), minutes=55)}}
    nights = nt.build_nights(rows, D9)
    assert (nt.day_tst24(nights, D8), nt.day_tst24(nights, D9)) == (465, 590)  # main's 465 on 8 Oct
    assert nt.bar_nap_min(nights, D8) == 55 and sl.rows(nights, D9)[1]["nap"] == "55 min"
    counted_once(nights)
    # E12: wake 06:30, nap 10:00 → 12:00 (110 min), next wake 11:00: its hour after 11:00 is 9 Oct's, the hour
    # before 8 Oct's
    rows = {"sleep": {D8: night(dt(D7, 23), dt(D8, 6, 30), 440), D9: night(dt(D9, 1), dt(D9, 11), 580)},
            "nap": {D8: nap((dt(D8, 10), dt(D8, 12)), minutes=110)}}
    nights = nt.build_nights(rows, D9)
    assert (nt.bar_nap_min(nights, D8), nt.bar_nap_min(nights, D9)) == (55, 55)
    counted_once(nights)
    # the next morning has no night (COROS « not available yet »), the one after has: its own day's, on its bar
    d1, d2, d3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
    rows = {"sleep": {d1: night(dt(d1, 0), dt(d1, 7, 30), 450), d3: night(dt(d3, 0), dt(d3, 7, 30), 450)},
            "nap": {d1: nap((dt(d1, 14), dt(d1, 15)), minutes=60)}}
    nights = nt.build_nights(rows, d3)
    assert [nt.day_tst24(nights, d) for d in (d1, d2, d3)] == [510, None, 450]
    assert [nt.bar_nap_min(nights, d) for d in (d1, d2, d3)] == [60, 0, 0]
    assert sl.bars(nights, d3, "14")["bars"][-3].get("top") is not None
    counted_once(nights)


def test_todays_afternoon_nap_is_named_on_the_card_and_counted_tomorrow():
    """Reviewers' findings 5 and 8: a nap taken after this morning's wake is in tomorrow morning's 24 h (each nap
    once): out of today's total, dial and bar, but named on the card; tomorrow's night counts it."""
    rows = {"sleep": {D8: night(dt(D7, 23), dt(D8, 7), 450)}, "nap": {D8: nap((dt(D8, 14), dt(D8, 15)), minutes=60)}}
    nights = nt.build_nights(rows, D8)
    assert nt.day_tst24(nights, D8) == 450 and nt.pending_naps(nights, D8) == [(dt(D8, 14), dt(D8, 15), 60)]
    h = sl.hero(nights, D8)
    assert (h["nap"], h["total"], h["later"]) == (
        None, "7h30", "sieste 14:00 → 15:00 · comptée dans ta prochaine nuit")
    counted_once(nights)
    # the next morning's night arrives: counted there, no longer named
    rows["sleep"][D9] = night(dt(D8, 23), dt(D9, 7), 450)
    nights = nt.build_nights(rows, D9)
    assert (nt.day_tst24(nights, D8), nt.day_tst24(nights, D9), nt.pending_naps(nights, D8)) == (450, 510, [])
    assert sl.hero(nights, D9)["later"] is None
    # yesterday's afternoon nap before this morning's night synced: named, counted by no morning yet
    rows["sleep"].pop(D9)
    assert sl.hero(nt.build_nights(rows, D9), D9)["later"] == "sieste 14:00 → 15:00 · comptée dans ta prochaine nuit"


def test_a_nap_only_days_nap_is_on_one_bar():
    """Reviewers' E9: a nap-only 8 Oct (14:00 → 15:00) before 9 Oct's night: in 9 Oct's 24 h and on its bar; 8 Oct
    draws nothing, its readout says where the nap went."""
    rows = {"sleep": {D9: night(dt(D8, 23, 30), dt(D9, 7), 440)},
            "nap": {D8: nap((dt(D8, 14), dt(D8, 15)), minutes=55)}}
    nights = nt.build_nights(rows, D9)
    assert (nt.bar_nap_min(nights, D8), nt.bar_nap_min(nights, D9), nt.day_tst24(nights, D9)) == (0, 55, 495)
    c = sl.bars(nights, D9, "14")
    assert c["bars"][-2].get("miss") and c["bars"][-1].get("top") is not None
    assert sl._readout(nights, D8)[0][:2] == ["55 min", "sieste comptée le lendemain"]
    counted_once(nights)
    # the race page's bars alike: the nap on 9 Oct's column, 8 Oct's readout says where it went
    c = rp.night_bars(nights, D9 + timedelta(days=3), D9)
    i = json.loads(c["data"].replace("<\\/", "</"))["d"].index(D8.isoformat())
    assert c["cols"][i].get("miss") and c["cols"][i + 1].get("nap")
    assert json.loads(c["data"].replace("<\\/", "</"))["r"][i][1] == "Sieste 55 min · comptée le lendemain"
    # no next night: drawn on its own bar, « sieste seule », as before
    nights = nt.build_nights({"nap": rows["nap"]}, D9)
    assert nt.bar_nap_min(nights, D8) == 55 and sl._readout(nights, D8)[0][:2] == ["55 min", "sieste seule"]


async def test_the_sante_page_names_todays_afternoon_nap(as_user, db_session, test_user):  # noqa: F811
    today = datetime.now(timezone.utc).date()
    y = today - timedelta(days=1)
    db_session.add(HealthMetric(user_id=test_user.id, date=today, metric="sleep", value=450, source="COROS",
                                n_samples=1, details={"main_start": f"{y}T23:00", "main_end": f"{today}T07:00"}))
    db_session.add(HealthMetric(user_id=test_user.id, date=today, metric="nap", value=60, source="COROS",
                                n_samples=1, details={"windows": [[f"{today}T14:00", f"{today}T15:00"]]}))
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    card = page.split('id="sommeil"')[1].split("</section>")[0]
    assert "7h30" in card and "+ sieste" not in card
    assert '<p class="pf-viz-cap">sieste 14:00 → 15:00 · comptée dans ta prochaine nuit</p>' in card


# ── stale rows ──────────────────────────────────────────────────────────────

async def _row(db, user, day, metric, value, source, details=None):
    db.add(HealthMetric(user_id=user.id, date=day, metric=metric, value=value, source=source,
                        details=details, n_samples=1))


async def _left(db, user) -> set:
    return {(m.date, m.metric, m.source) for m in (await db.execute(
        select(HealthMetric).where(HealthMetric.user_id == user.id))).scalars()}


async def test_a_resync_deletes_only_its_own_stale_rows(db_session, test_user):
    stale = {"period": 66, "windows": [["2026-10-08T23:03", "2026-10-09T00:09"]]}
    await _row(db_session, test_user, D9, "nap", 57, "COROS", stale)  # COROS no longer lists it
    await _row(db_session, test_user, D9, "hr_day", 52, "COROS")  # another metric, same day
    await _row(db_session, test_user, D9 - timedelta(days=2), "nap", 40, "Garmin")  # another watch, a day read
    await _row(db_session, test_user, D9 - timedelta(days=10), "nap", 30, "COROS")  # a day this sync did not read
    await db_session.flush()
    rows = [Daily("sleep", D9, 290, {"main_start": "2026-10-08T23:57", "main_end": "2026-10-09T04:58"})]
    days = {D9 - timedelta(days=k) for k in range(7)}
    out = await store_daily(db_session, test_user.id, rows, "COROS", {"sleep": days, "nap": days})
    assert (out["inserted"], out["deleted"], out["stale_kept"]) == (1, 1, 0)
    assert await _left(db_session, test_user) == {
        (D9, "sleep", "COROS"), (D9, "hr_day", "COROS"), (D9 - timedelta(days=2), "nap", "Garmin"),
        (D9 - timedelta(days=10), "nap", "COROS")}
    # without `covered` (Garmin's sync, a test) nothing is ever deleted
    assert (await store_daily(db_session, test_user.id, [], "COROS"))["deleted"] == 0


async def test_a_sync_that_would_erase_a_metric_deletes_nothing_of_it(db_session, test_user):
    """The safety net (H): more than STALE_MAX stale rows of a metric, or every row it has on the days read (2 or
    more), is an answer read wrong: kept and logged, never deleted (main only stopped updating them)."""
    days = {D9 - timedelta(days=k) for k in range(10)}
    for k in range(STALE_MAX + 1):
        await _row(db_session, test_user, D9 - timedelta(days=k), "hrv", 80, "COROS")
    for k in range(2):
        await _row(db_session, test_user, D9 - timedelta(days=k), "hr_night", 40, "COROS")
    for day, value in ((D9, 30), (D8, 20), (D7, 25)):
        await _row(db_session, test_user, day, "nap", value, "COROS")
    await db_session.flush()
    keep = [Daily("nap", D7, 25, None)]  # one nap still given: 2 stale of 3, not all of them
    out = await store_daily(db_session, test_user.id, keep, "COROS", {"hrv": days, "hr_night": days, "nap": days})
    assert (out["deleted"], out["stale_kept"]) == (2, STALE_MAX + 1 + 2)
    left = await _left(db_session, test_user)
    assert sum(1 for _, m, _ in left if m == "hrv") == STALE_MAX + 1
    assert sum(1 for _, m, _ in left if m == "hr_night") == 2
    assert {d for d, m, _ in left if m == "nap"} == {D7}


def test_covered_days_are_the_days_the_parsers_read():
    """Reviewers' findings 1 and 7: a day counts as read for a value only when its parser read it."""
    d6 = D7 - timedelta(days=1)
    text = (overview(D9, "2026-10-08 23:00 - 2026-10-09 07:00", [])
            + f"{D8}\nSleep Score: 0\nSleep detail for this day is not available yet.\n\n"
            + overview(D7, None, ["2026-10-07 01:00 - 2026-10-07 02:05"], nap="1h 0min", daily="1h 0min")
            + overview(d6, "2026/10/05 23:00 - 2026/10/06 07:00", []))  # worded anew: the parser skips it
    ov, naps = coros.parse_sleep_overview(text), coros.parse_naps(text)
    read = coros.overview_read(text, ov, naps)
    # « not available yet » and a window not understood: nothing read, nothing deleted; the nap-only day: its
    # sleep and its naps read; 9 Oct's « Naps Total: 0 min »: its naps read
    assert read == {"sleep": {D9, D7}, "nap": {D9, D7}}
    windows = {D9: (dt(D8, 23), dt(D9, 7))}
    data = {"overview": ov, "naps": naps, "read": read, "daily": coros.parse_daily_sleep(DAILY_OCT9),
            "hrv_points": coros.parse_hrv_points(hrv_text(windows)), "hrv_days": {D8, D9}, "overview_days": {D8, D9}}
    cov = coros.covered_days(data)
    assert cov["sleep"] == cov["nap"] == {D9, D7}
    assert cov["hr_night"] == {D9}  # its summary read (and rejected: a stale nightly HR goes)
    assert cov["hrv"] == {D9}
    # a summary worded anew, an HRV answer without its series or without a reading in the night: nothing
    reworded = DAILY_OCT9.replace("Sleep Summary", "Sleep summary")
    assert coros.covered_days({**data, "daily": coros.parse_daily_sleep(reworded)})["hr_night"] == set()
    for hrv in (hrv_text(windows).replace("Sleep HRV Time Series", "Sleep HRV Readings"),
                "Sleep HRV\nNo readings for this period.\n", hrv_text({D7: (dt(D7, 1), dt(D7, 2))})):
        assert coros.covered_days({**data, "hrv_points": coros.parse_hrv_points(hrv)})["hrv"] == set()
    # the day before the night not read (the window starts on it): its HRV neither computed nor deleted
    assert coros.covered_days({**data, "overview_days": {D9}})["hrv"] == set()
    assert coros.covered_days({}) == {"sleep": set(), "nap": set(), "hr_night": set(), "hrv": set()}
