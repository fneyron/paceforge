"""Naps next to the night, and each nap once (owner's report, 2026-10-09).

- COROS's 23:03 → 00:09 « nap » of the 23:57 → 04:58 night of 9 Oct is the start of that night
  (health.fold_naps, coros.fold_overview when stored, nights.build_nights when read): one night from
  23:03, its minutes outside the main window added once, never over COROS's « Daily Sleep ».
- An afternoon nap counts in one morning's 24 h only (nights.day_naps): the bar, the ring, the score
  and the sleep owed read one figure.
- A COROS sync deletes the nightly rows it no longer yields for the days it read (health.store_daily
  `covered`, coros.covered_days), never another watch's, never another day's.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.models.health import HealthMetric
from app.services import coros
from app.services import nights as nt
from app.services import sante_sleep as sl
from app.services.health import Daily, fold_naps, store_daily

D8, D9 = date(2026, 10, 8), date(2026, 10, 9)

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


def overview(day: date, main: str, naps: list[str], asleep="7h 30min", period="7h 40min", nap="40 min",
             daily="8h 10min") -> str:
    """A querySleepOverview block in COROS's wording."""
    lines = [f"{day}", "Sleep Score: 80", f"Daily Sleep: {daily} (incl. naps)", f"Main Sleep (asleep): {asleep}",
             f"Main Sleep Period (incl. awake): {period}", f"Main Sleep Window: {main}"]
    if naps:
        lines += [f"Naps Total (asleep): {nap}", f"Nap Window{'s' if len(naps) > 1 else ''}: {', '.join(naps)}"]
    else:
        lines.append("Naps Total: 0 min")
    return "\n".join(lines) + "\n\n"


def build(text: str, daily_text: str = "") -> dict:
    data = {"overview": coros.parse_sleep_overview(text), "naps": coros.parse_naps(text),
            "daily": coros.parse_daily_sleep(daily_text)}
    out: dict = {}
    for r in coros.build_daily(data, D9):
        out.setdefault(r.metric, {})[r.day] = r
    return out


# ── the night of 9 Oct ──────────────────────────────────────────────────────

def test_the_9_october_nap_is_the_start_of_its_night():
    rows = build(OCT9, DAILY_OCT9)
    sleep = rows["sleep"][D9]
    # 4h50 + the 47 of its 57 min outside 23:57 → 04:58 (the 12 min inside counted once): 5h37, under COROS's 5h47
    assert sleep.value == 337 and sleep.value <= sleep.details["daily"] == 347
    det = sleep.details
    assert (det["main_start"], det["main_end"], det["bedtime"], det["period"]) == (
        "2026-10-08T23:03", "2026-10-09T04:58", "23:03", 355)
    assert det["folded"] == [["2026-10-08T23:03", "2026-10-09T00:09"]] and det["naps_folded"]
    assert "nap" not in rows  # no « sieste » left over the night
    # COROS's « Sleep Summary » that day is the 23:03 segment's (1h06, light sleep only, 44 bpm): checked against
    # COROS's own main period (5h01), still not the night's stages nor its nightly HR
    assert "stages" not in det and "hr_night" not in rows
    nights = nt.build_nights({"sleep": {D9: (sleep.value, det, "COROS")}}, D9)
    h = sl.hero(nights, D9)
    assert (h["label"], h["times"], h["nap"], h["total"]) == ("Cette nuit", "23:05 → 05:00", None, "5h37")
    assert nt.day_tst24(nights, D9) == nt.slept_before_wake(nights, D9) == 337


def test_production_rows_with_the_stale_nap_read_as_one_night():
    """Before the next sync deletes it, the 23:03 nap row an earlier sync stored sits next to main's 23:57 night:
    read, it is folded too (build_nights), never « + sieste 57 min » over the night."""
    rows = {"sleep": {D9: (290, {"main_start": "2026-10-08T23:57", "main_end": "2026-10-09T04:58",
                                 "daily": 347}, "COROS")},
            "nap": {D9: (57, {"period": 66, "windows": [["2026-10-08T23:03", "2026-10-09T00:09"]]}, "COROS")}}
    nights = nt.build_nights(rows, D9)
    n = nights[D9]
    assert (n.start, n.end, n.asleep, n.naps) == (dt(D8, 23, 3), dt(D9, 4, 58), 337, [])
    h = sl.hero(nights, D9)
    assert (h["times"], h["nap"], h["total"]) == ("23:05 → 05:00", None, "5h37")
    # the night already folded (the fixed sync's row) and the old nap row: nothing counted twice
    rows["sleep"][D9] = (337, {"main_start": "2026-10-08T23:03", "main_end": "2026-10-09T04:58", "daily": 347},
                         "COROS")
    assert nt.build_nights(rows, D9)[D9].asleep == 337


# ── the rule (H) ────────────────────────────────────────────────────────────

def test_a_nap_ending_within_30_min_before_the_night_is_its_start():
    night = (dt(D8, 23, 0), dt(D9, 7, 0))
    # dozing 22:00 → 22:40, in bed at 23:00: one night from 22:00, its 35 min added (the 20 min between: awake)
    assert fold_naps(*night, 450, [(dt(D8, 22), dt(D8, 22, 40), 35.0)])[:3] == (dt(D8, 22), night[1], 485)
    # two in a row: 22:10 → 22:40 joins, then 21:00 → 21:45 is 25 min before the new bedtime
    start, _, asleep, joined, left = fold_naps(*night, 450, [(dt(D8, 21), dt(D8, 21, 45), 40.0),
                                                              (dt(D8, 22, 10), dt(D8, 22, 40), 25.0)])
    assert (start, asleep, len(joined), left) == (dt(D8, 21), 515, 2, [])
    # 45 min before the night: an evening nap, kept as a nap
    start, _, asleep, joined, left = fold_naps(*night, 450, [(dt(D8, 21, 30), dt(D8, 22, 15), 40.0)])
    assert (start, asleep, joined, len(left)) == (night[0], 450, [], 1)
    # COROS's « Daily Sleep » (which holds the naps listed under the night's day, `own`) caps the night, less the
    # day's naps that stay, never under its own minutes; a nap listed under the day before is not in it
    nap, later = (dt(D8, 22), dt(D8, 22, 40), 35.0), (dt(D9, 14), dt(D9, 15), 20.0)
    assert fold_naps(*night, 450, [nap], daily=470, own=[nap])[2] == 470
    assert fold_naps(*night, 450, [nap, later], daily=490, own=[nap, later])[2] == 470
    assert fold_naps(*night, 450, [nap], daily=400, own=[nap])[2] == 450
    assert fold_naps(*night, 450, [nap], daily=450)[2] == 485


def test_coros_folds_an_adjacent_nap_listed_under_either_day():
    """COROS lists a nap under a wake-up day: the night's own, or the day before (one ending before midnight)."""
    main = "2026-10-08 23:00 - 2026-10-09 07:00"
    rows = build(overview(D9, main, ["2026-10-08 22:00 - 2026-10-08 22:40"], nap="35 min", daily="8h 5min"))
    assert rows["sleep"][D9].value == 485 and rows["sleep"][D9].details["main_start"] == "2026-10-08T22:00"
    assert "nap" not in rows
    text = (overview(D8, "2026-10-07 23:00 - 2026-10-08 07:00", ["2026-10-08 14:00 - 2026-10-08 15:00",
                                                                   "2026-10-08 22:00 - 2026-10-08 22:40"],
                     nap="1h 35min", daily="9h 5min")
            + overview(D9, main, [], daily="7h 30min"))
    rows = build(text)
    # the 22:00 window is 9 Oct's night start; the 14:00 one stays 8 Oct's nap, its share of the 95 min (60/100)
    assert rows["sleep"][D9].details["main_start"] == "2026-10-08T22:00" and rows["sleep"][D9].value == 450 + 38
    assert rows["nap"][D8].value == 57 and rows["nap"][D8].details["windows"] == [
        ["2026-10-08T14:00", "2026-10-08T15:00"]]
    assert rows["sleep"][D8].details["main_start"] == "2026-10-07T23:00"  # 8 Oct's own night untouched


def test_after_the_wake_a_nap_stays_rendormi_unless_it_overlaps():
    """Main's « rendormi » (a nap ≤ 3 h after the wake: a nap of that night, its wake left out of the wake
    median) is kept for a nap starting after the wake, even 20 min after; one overlapping the wake has no wake
    in between: the night runs to its end."""
    night = (dt(D8, 23, 0), dt(D9, 7, 0))
    assert fold_naps(*night, 450, [(dt(D9, 7, 20), dt(D9, 8, 30), 60.0)])[3] == []
    rows = {"sleep": {D9: (450, {"main_start": "2026-10-08T23:00", "main_end": "2026-10-09T07:00"}, "COROS")},
            "nap": {D9: (60, {"windows": [["2026-10-09T07:20", "2026-10-09T08:30"]]}, "COROS")}}
    nights = nt.build_nights(rows, D9)
    n = nights[D9]
    assert n.end == dt(D9, 7) and n.resettled and len(n.naps) == 1
    assert nt.day_tst24(nights, D9) == nt.slept_before_wake(nights, D9) == 510  # with its own night, once
    # 06:50 → 08:30 overlaps the wake: one night to 08:30, the 90 of its 100 min after 07:00 added
    assert fold_naps(*night, 450, [(dt(D9, 6, 50), dt(D9, 8, 30), 100.0)])[:3] == (night[0], dt(D9, 8, 30), 540)


# ── each nap once ───────────────────────────────────────────────────────────

def test_an_afternoon_nap_is_a_nap_counted_once_in_the_next_morning():
    """8 Oct 14:00 → 15:00 after an 07:00 wake: a nap (never folded), in 9 Oct's 24 h only — its bar, its ring and
    score figure, its readout, the card and the sleep owed agree; 8 Oct's 24 h is its night alone."""
    rows = {"sleep": {D8: (450, {"main_start": "2026-10-07T23:00", "main_end": "2026-10-08T07:00"}, "COROS"),
                      D9: (450, {"main_start": "2026-10-08T23:00", "main_end": "2026-10-09T07:00"}, "COROS")},
            "nap": {D8: (60, {"windows": [["2026-10-08T14:00", "2026-10-08T15:00"]]}, "COROS")}}
    nights = nt.build_nights(rows, D9)
    assert nights[D8].naps == [(dt(D8, 14), dt(D8, 15), 60)] and nights[D8].start == dt(D8 - timedelta(days=1), 23)
    assert nt.day_tst24(nights, D8) == nt.slept_before_wake(nights, D8) == 450 and nt.bar_nap_min(nights, D8) == 0
    assert nt.day_tst24(nights, D9) == nt.slept_before_wake(nights, D9) == 510 and nt.bar_nap_min(nights, D9) == 60
    assert nt.day_tst24(nights, D8) + nt.day_tst24(nights, D9) == 450 + 60 + 450  # slept once, counted once
    c = sl.bars(nights, D9, "14")
    assert [b.get("top") is not None for b in c["bars"][-2:]] == [False, True]  # the nap on 9 Oct's bar only
    h = sl.hero(nights, D9)
    assert (h["nap"], h["total"]) == ("+ sieste 1h00", "8h30")
    table = {r["iso"]: (r["tst"], r["nap"]) for r in sl.rows(nights, D9)}
    assert table == {"2026-10-09": ("8h30", "1h00"), "2026-10-08": ("7h30", "—")}
    assert [nt.night_value(nights, d, "tst24") for d in (D8, D9)] == [450, 510]  # what the bands read


# ── stale rows ──────────────────────────────────────────────────────────────

async def _row(db, user, day, metric, value, source, details=None):
    db.add(HealthMetric(user_id=user.id, date=day, metric=metric, value=value, source=source,
                        details=details, n_samples=1))


async def test_a_resync_deletes_only_its_own_stale_rows(db_session, test_user):
    stale = {"period": 66, "windows": [["2026-10-08T23:03", "2026-10-09T00:09"]]}
    await _row(db_session, test_user, D9, "nap", 57, "COROS", stale)  # stored before COROS had the night
    await _row(db_session, test_user, D9, "hr_day", 52, "COROS")  # another metric, same day
    await _row(db_session, test_user, D9 - timedelta(days=2), "nap", 40, "Garmin")  # another watch, a day read
    await _row(db_session, test_user, D9 - timedelta(days=10), "nap", 30, "COROS")  # a day this sync did not read
    await db_session.flush()
    night = Daily("sleep", D9, 337, {"main_start": "2026-10-08T23:03", "main_end": "2026-10-09T04:58"})
    days = {D9 - timedelta(days=k) for k in range(7)}
    out = await store_daily(db_session, test_user.id, [night], "COROS", {"sleep": days, "nap": days})
    assert (out["inserted"], out["deleted"]) == (1, 1)
    left = {(m.date, m.metric, m.source) for m in (await db_session.execute(
        select(HealthMetric).where(HealthMetric.user_id == test_user.id))).scalars()}
    assert left == {(D9, "sleep", "COROS"), (D9, "hr_day", "COROS"), (D9 - timedelta(days=2), "nap", "Garmin"),
                    (D9 - timedelta(days=10), "nap", "COROS")}
    # without `covered` (Garmin's sync, a test) nothing is ever deleted
    assert (await store_daily(db_session, test_user.id, [], "COROS"))["deleted"] == 0


def test_covered_days_are_the_days_coros_described():
    text = (overview(D9, "2026-10-08 23:00 - 2026-10-09 07:00", [])
            + f"{D8}\nSleep Score: 0\nSleep detail for this day is not available yet.\n\n"
            + f"{D8 - timedelta(days=1)}\nSleep Score: -1\nDaily Sleep: 1h 0min (incl. naps)\n"
              "Naps Total (asleep): 1h 0min\nNap Window: 2026-10-07 01:00 - 2026-10-07 02:05\n")
    seen = coros.sleep_days(text)
    assert seen == {D9, D8 - timedelta(days=1)}  # « not available yet »: nothing known, nothing deleted
    data = {"sleep_days": seen, "daily_read": (D8, D9), "daily_ok": True, "hrv_days": {D8, D9}}
    cov = coros.covered_days(data)
    assert cov["sleep"] == cov["nap"] == seen and cov["hr_night"] == {D9} and cov["hrv"] == {D9}
    assert "hr_night" not in coros.covered_days({**data, "daily_ok": False})  # its answer not read whole
    assert coros.covered_days({})["nap"] == set()
