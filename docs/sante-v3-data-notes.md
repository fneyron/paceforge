# Santé v3 — data notes (checked 2026-10-07)

How I checked: read-only COROS MCP calls on the owner's account (`querySleepOverview`, `querySleepHrv`,
`queryDailyHealthData`), then offline analysis. The exact answers are kept as fixtures in
`tests/owner_coros.py`, and every figure below is asserted in `tests/test_sante_data.py`.

## 1. HRV readings: what `timezone=` means

**Rule:** `timezone` counts quarter hours east of UTC, so local time = UTC timestamp + timezone × 15 min.
**Status: verified.**

| Night (wake day) | Place | tz | Readings in the main window | Window |
|---|---|---|---|---|
| 2026-10-07 | Korea | 36 (UTC+9) | 32 (3 from the 6th's group, 29 from the 7th's) | 23:35 → 05:38 |
| 2025-11-05 | France | 4 (UTC+1) | 50 (read as UTC: 43) | 22:41 → 07:30 |
| 2025-10-31 | France | 4 | 26 | 23:02 → 04:40 |
| 2025-10-26 | Greece, the night the clocks changed | 12 (UTC+3) all night | 20 | 22:32 → 05:35 |
| 2025-10-28 | Greece | 8 (UTC+2) | 25 | 00:56 → 07:04 |

Why I trust this reading:
- Each local-day group holds only readings of that local day.
- On every night, the first reading comes a few minutes after sleep onset and the last a few minutes before waking.

**The series is grouped by calendar day, not by night.**
- A night spans two groups, D−1 and D.
- A group also holds daytime readings and the start of the next night.
- So the sync reads both days and clips the readings to the main window. `coros.hrv_dailies` writes a night only when both of its days were read.

**The autumn clock change (26 Oct 2025, clocks back at 01:00 UTC).**
- Every reading of that night keeps timezone 12.
- COROS's main window is in the same frame: the last reading falls at 05:29:50 at UTC+3, and the window ends at 05:35. The wall clock showed about 04:35.
- So clipping is consistent. The times printed for that one night are in the frame before the change.
- The next night carries timezone 8.

**Not observed:** a spring clock change. No measured night falls near 30 Mar 2025 or 29 Mar 2026.

## 2. Does COROS « HRV Avg » include the nap?

**No.** It is the arithmetic mean of the readings inside the main window.

| Night | COROS « HRV Avg » | Mean, main window (n) | Mean, main window + nap (n) |
|---|---|---|---|
| 2026-10-07 (nap 06:42–09:07) | 97 | 97.7 (32) | 99.9 (46) |
| 2025-10-31 (nap 05:58–07:30) | 98 | 98.2 (26) | 102.3 (34) |
| 2025-11-05 (no nap) | 83 | 83.2 (50) | — |
| 2025-10-28 | 91 | 91.4 (25) | — |
| 2025-10-26 (clock change) | 82 | 77.0 (20): not reproduced | — |

- The earlier « 14 of 44 points fall inside the nap » is true of the readings, not of the average.
- PaceForge does not read COROS's average either way.
- Its own value is exp(mean ln RMSSD) of the main-window readings, with at least 12 readings (H). On 7 Oct that gives 95.1 ms from 32 readings.
- Nights with a nap keep their HRV, because the value is clipped to the main window.

## 3. Does COROS « Sleep HR Avg » include the nap?

**Not verified, but consistent with "main sleep only".**

- **No COROS tool gives a heart-rate curve.**
  - `queryHealthCheckTimeSeries` covers manual checks only.
  - `queryStressTimeSeries` has `stressHr=0` on every point.
- **The line sits in the « Sleep Summary » block of `queryDailyHealthData`, and that block describes the main sleep.**
  - On nap days its Total is the main period, without the nap.
  - 2026-10-07: 6h03, with stages 49 + 205 + 96 + 13 = 363 min; the 2h25 nap is left out.
  - 2025-10-31: 5h38, the main window; the 1h32 nap is left out.
- **On the nap-only day (2026-09-25) the same block describes the nap:** Total 1h29, HR 34.

What the code does:
- It keeps `hr_night` only when a main window exists and the summary's Total equals the main period within 15 min (H). This drops 2026-09-25 and the malformed 2026-07-17 record.
- It marks `nap_day` on days with a nap.
- While `nights.COROS_SLEEP_HR_NAP_VERIFIED` is False, those nights stay out of the HR band, the 7-night mean and the illness alert.

Garmin:
- Nightly HR is the mean of the `sleepHeartRate` readings inside the main window, so naps are left out by construction.
- The key names come from python-garminconnect, not from a real account.

## 4. Format facts the parsers rely on

- **Legacy naps.** Some are dated 1982, with « Light 100% » and « (includes legacy reported durations) », e.g. 2026-07-16. The nap guards drop them: a window must end between 12:00 the day before and 23:59, last at most 6 h, and stay off the main window.
- **Older records** (before mid-2026) have no « (asleep) » line. Their « Main Sleep » is the window's length, so minutes asleep = window − awake.
- **2026-07-17 is malformed.** Its Total (4h19) is shorter than the window (4h35), and it has no Sleep HR line.
- **Cross-check of PaceForge's 24-h total.** « Daily Sleep (incl. naps) » for 7 Oct is 8h10, and PaceForge's own total is 350 + 140 = 490 min. It is stored as `sleep.details.daily` and never used.

## 5. Rows as written from now on (source "COROS" / "Garmin", one per wake-up day)

| Metric | Value | details |
|---|---|---|
| `sleep` | min asleep, main episode | `main_start`, `main_end` (local ISO), `period`, `tz` (min east of UTC), `timeline`, `bedtime`, `wake`, COROS `daily` |
| `nap` | min asleep in naps ending that day | `period`, `windows` [[ISO, ISO]], `legacy` |
| `hrv` | exp(mean ln RMSSD), main window | `n`, `tz`, `method: "ln_mean_main"` |
| `hr_night` | nightly HR | `min`, `max`, `method` (`coros_sleep_summary` or `points`), `nap_day` |
| `resp_night` | breaths/min (Garmin) | `method` (`points` or `garmin_summary`) |

- Rows written before keep their old shape, and the readers (`health.main_window`, `health.nap_windows`, `health.feel_of`) still accept them.
- An `hrv` row without the method is the watch's own average, and it is never read as PaceForge's.
- A watch whose nights exist only in the old format reads its 60 days again once (`health.nights_upgraded`).
- Brand rows are no longer written or read, and stored ones are kept: load, recovery, stress, fitness, hrv_norm, body_battery, sleep_score, vo2max and the daytime `rhr`.

## 6. Not verified, or not done

- COROS Sleep HR on nap days (section 3).
- Garmin raw-reading key names.
- A spring clock change.
- Every (H) threshold is a choice.
- The 400-day COROS history backfill is not built: the first sync still reads 60 days.
