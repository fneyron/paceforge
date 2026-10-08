"""The owner's real COROS answers (read-only MCP calls, 2026-10-07), the
fixtures of the Santé v3 data tests. Times are as COROS prints them; the HRV
readings carry a UTC timestamp and the timezone in quarter hours."""

# querySleepOverview 2026-09-24 → 2026-10-07 (the Transjeju 100M started 02/10 21:00, in Korea, UTC+9)
OVERVIEW_2026 = """Sleep Overview
========================
Note: each record below is dated by its wake-up day.

2026-09-24
Sleep Score: 0
Sleep detail for this day is not available yet.

2026-09-25
Sleep Score: -1
Daily Sleep: 1h 22min (incl. naps)
Sleep metrics scope: daily
Naps Total (asleep): 1h 22min
Naps Period (incl. awake): 1h 29min
Nap Window: 2026-09-25 01:23 - 2026-09-25 02:52

2026-09-26
Sleep Score: 0
Sleep detail for this day is not available yet.

2026-09-29
Sleep Score: 89
Daily Sleep: 8h 46min (incl. naps)
Main Sleep (asleep): 8h 46min
Main Sleep Period (incl. awake): 9h 0min
Sleep metrics scope: daily
Deep Sleep Ratio: 11%
Light Sleep Ratio: 63%
REM Ratio: 23%
Awake Ratio: 3%
Awake Time: 14 min
Awake Count (>5 min): 0
Main Sleep Window: 2026-09-29 00:31 - 2026-09-29 09:31
Naps Total: 0 min

2026-09-30
Sleep Score: 91
Daily Sleep: 9h 48min (incl. naps)
Main Sleep (asleep): 9h 48min
Main Sleep Period (incl. awake): 10h 2min
Sleep metrics scope: daily
Deep Sleep Ratio: 13%
Light Sleep Ratio: 57%
REM Ratio: 28%
Awake Ratio: 2%
Awake Time: 14 min
Awake Count (>5 min): 0
Main Sleep Window: 2026-09-29 23:52 - 2026-09-30 09:54
Naps Total: 0 min

2026-10-01
Sleep Score: 85
Daily Sleep: 9h 1min (incl. naps)
Main Sleep (asleep): 9h 1min
Main Sleep Period (incl. awake): 9h 22min
Sleep metrics scope: daily
Deep Sleep Ratio: 9%
Light Sleep Ratio: 57%
REM Ratio: 30%
Awake Ratio: 4%
Awake Time: 21 min
Awake Count (>5 min): 0
Main Sleep Window: 2026-09-30 23:55 - 2026-10-01 09:17
Naps Total: 0 min

2026-10-02
Sleep Score: 0
Sleep detail for this day is not available yet.

2026-10-03
Sleep Score: 0
Sleep detail for this day is not available yet.

2026-10-06
Sleep Score: 66
Daily Sleep: 5h 33min (incl. naps)
Main Sleep (asleep): 5h 33min
Main Sleep Period (incl. awake): 5h 44min
Sleep metrics scope: daily
Deep Sleep Ratio: 11%
Light Sleep Ratio: 64%
REM Ratio: 22%
Awake Ratio: 3%
Awake Time: 11 min
Awake Count (>5 min): 0
Main Sleep Window: 2026-10-06 01:01 - 2026-10-06 06:45
Naps Total: 0 min

2026-10-07
Sleep Score: 89
Daily Sleep: 8h 10min (incl. naps)
Main Sleep (asleep): 5h 50min
Main Sleep Period (incl. awake): 6h 3min
Sleep metrics scope: daily
Deep Sleep Ratio: 13%
Light Sleep Ratio: 57%
REM Ratio: 26%
Awake Ratio: 4%
Awake Time: 13 min
Awake Count (>5 min): 0
Main Sleep Window: 2026-10-06 23:35 - 2026-10-07 05:38
Naps Total (asleep): 2h 20min
Naps Period (incl. awake): 2h 25min
Nap Window: 2026-10-07 06:42 - 2026-10-07 09:07
"""

# querySleepOverview: a legacy 1982 nap and the malformed 2026-07-17 record
OVERVIEW_LEGACY = """Sleep Overview
========================
Note: each record below is dated by its wake-up day.

2026-07-16
Sleep Score: -1
Daily Sleep: 1h 33min (incl. naps)
Deep Sleep Ratio: 0%
Light Sleep Ratio: 100%
REM Ratio: 0%
Awake Ratio: 0%
Awake Time: 0 min
Awake Count (>5 min): 0
Naps Total: 1h 33min (includes legacy reported durations)
Nap Window: 1982-07-17 04:41 - 1982-07-17 06:14

2026-07-17
Sleep Score: 44
Daily Sleep: 4h 19min (incl. naps)
Main Sleep: 4h 35min
Deep Sleep Ratio: 6%
Light Sleep Ratio: 56%
REM Ratio: 32%
Awake Ratio: 6%
Awake Time: 16 min
Awake Count (>5 min): 0
Main Sleep Window: 2026-07-17 01:29 - 2026-07-17 06:04
Naps Total: 0 min
"""

# querySleepOverview, autumn 2025 (older record format: « Main Sleep » is the window)
OVERVIEW_2025 = """Sleep Overview
========================
Note: each record below is dated by its wake-up day.

2025-10-25
Sleep Score: 48
Main Sleep: 3h 30min
Deep Sleep Ratio: 23%
Light Sleep Ratio: 59%
REM Ratio: 13%
Awake Ratio: 5%
Awake Time: 10 min
Main Sleep Window: 2025-10-24 23:17 - 2025-10-25 02:47
Naps Total: 0 min

2025-10-26
Sleep Score: 74
Main Sleep: 7h 3min
Deep Sleep Ratio: 14%
Light Sleep Ratio: 59%
REM Ratio: 22%
Awake Ratio: 5%
Awake Time: 22 min
Main Sleep Window: 2025-10-25 22:32 - 2025-10-26 05:35
Naps Total: 0 min

2025-10-28
Sleep Score: 68
Main Sleep: 6h 8min
Deep Sleep Ratio: 12%
Light Sleep Ratio: 53%
REM Ratio: 33%
Awake Ratio: 2%
Awake Time: 8 min
Main Sleep Window: 2025-10-28 00:56 - 2025-10-28 07:04
Naps Total: 0 min

2025-10-29
Sleep Score: 71
Main Sleep: 7h 40min
Deep Sleep Ratio: 13%
Light Sleep Ratio: 60%
REM Ratio: 22%
Awake Ratio: 5%
Awake Time: 25 min
Awake Count (>5 min): 1
Main Sleep Window: 2025-10-28 22:33 - 2025-10-29 06:13
Naps Total: 40 min (includes legacy reported durations)
Nap Window: 2025-10-29 12:33 - 2025-10-29 13:13

2025-10-31
Sleep Score: 74
Main Sleep: 5h 38min
Deep Sleep Ratio: 13%
Light Sleep Ratio: 60%
REM Ratio: 24%
Awake Ratio: 3%
Awake Time: 11 min
Main Sleep Window: 2025-10-30 23:02 - 2025-10-31 04:40
Naps Total: 1h 32min (includes legacy reported durations)
Nap Window: 2025-10-31 05:58 - 2025-10-31 07:30

2025-11-05
Sleep Score: 73
Main Sleep: 8h 49min
Deep Sleep Ratio: 13%
Light Sleep Ratio: 59%
REM Ratio: 25%
Awake Ratio: 3%
Awake Time: 18 min
Main Sleep Window: 2025-11-04 22:41 - 2025-11-05 07:30
Naps Total: 0 min
"""

# queryDailyHealthData (the Sleep Summary blocks that matter)
DAILY = """Daily Health Data — Last 345 days | Resting HR: 32 bpm | HRV Baseline: 42 ms
Note: sleep entries are dated by their wake-up day.

--- 20251031 ---
Steps: 6,900 | Calories: 486 kcal | Exercise: 19 min
Stress: Avg 27
Sleep Summary:
  Total: 5h 38min | Deep: 43 min | Light: 3h 22min | REM: 1h 22min | Awake: 11 min
  Sleep HR: Avg 39 bpm | Min 34 bpm | Max 53 bpm

--- 20260717 ---
Steps: 61,085 | Calories: 4,458 kcal | Exercise: 10h 52min
Stress: Avg 55
Sleep Summary: (Score: 44)
  Total: 4h 19min | Awake: 16 min
  Deep: 16 min | Light: 2h 35min | REM: 1h 28min

--- 20260925 ---
Steps: 12,103 | Calories: 363 kcal | Exercise: 3 min
Stress: Avg 28
Sleep Summary:
  Total: 1h 29min | Deep: 10 min | Light: 59 min | REM: 13 min | Awake: 7 min
  Sleep HR: Avg 34 bpm | Min 31 bpm | Max 44 bpm

--- 20261006 ---
Steps: 16,395 | Calories: 556 kcal | Exercise: 4 min
Stress: Avg 27
Sleep Summary:
  Total: 5h 44min | Deep: 39 min | Light: 3h 38min | REM: 1h 16min | Awake: 11 min
  Sleep HR: Avg 35 bpm | Min 31 bpm | Max 56 bpm

--- 20261007 ---
Steps: 712 | Calories: 63 kcal | Exercise: 0 min
Stress: Avg 13
Sleep Summary:
  Total: 6h 3min | Deep: 49 min | Light: 3h 25min | REM: 1h 36min | Awake: 13 min
  Sleep HR: Avg 37 bpm | Min 31 bpm | Max 56 bpm
"""

# querySleepHrv 2026-10-06 → 2026-10-07 (Korea, timezone=36): the night 23:35–05:38, the nap 06:42–09:07
HRV_2026_10_07 = """Sleep HRV — 2026-10-06 to 2026-10-07
========================
Note: dates are wake-up days (each value comes from the night that ended that morning).

HRV Assessment — Last 2 days
========================

2026-10-07:
  HRV Avg: 97 ms — Above normal
  Normal Range: 70 - 84 ms
  Baseline: 77 ms
2026-10-06:
  HRV Avg: 84 ms — Normal
  Normal Range: 70 - 84 ms
  Baseline: 77 ms

Sleep HRV Time Series — Last 2 days
========================

2026-10-06:
  timestamp=1791216290, timezone=36, hrv=70 ms, status=4, confidence=94444
  timestamp=1791217490, timezone=36, hrv=71 ms, status=4, confidence=94444
  timestamp=1791218990, timezone=36, hrv=68 ms, status=4, confidence=100000
  timestamp=1791219590, timezone=36, hrv=61 ms, status=4, confidence=100000
  timestamp=1791220190, timezone=36, hrv=67 ms, status=4, confidence=100000
  timestamp=1791220790, timezone=36, hrv=102 ms, status=4, confidence=88888
  timestamp=1791221390, timezone=36, hrv=55 ms, status=4, confidence=100000
  timestamp=1791221990, timezone=36, hrv=69 ms, status=4, confidence=100000
  timestamp=1791222890, timezone=36, hrv=111 ms, status=4, confidence=100000
  timestamp=1791223490, timezone=36, hrv=102 ms, status=4, confidence=84858
  timestamp=1791224690, timezone=36, hrv=82 ms, status=4, confidence=100000
  timestamp=1791226790, timezone=36, hrv=80 ms, status=4, confidence=88888
  timestamp=1791227390, timezone=36, hrv=83 ms, status=4, confidence=100000
  timestamp=1791227990, timezone=36, hrv=80 ms, status=4, confidence=100000
  timestamp=1791230090, timezone=36, hrv=74 ms, status=4, confidence=100000
  timestamp=1791230690, timezone=36, hrv=96 ms, status=4, confidence=87500
  timestamp=1791231290, timezone=36, hrv=102 ms, status=4, confidence=94117
  timestamp=1791231890, timezone=36, hrv=149 ms, status=4, confidence=100000
  timestamp=1791232490, timezone=36, hrv=94 ms, status=4, confidence=100000
  timestamp=1791233990, timezone=36, hrv=69 ms, status=4, confidence=94117
  timestamp=1791234590, timezone=36, hrv=85 ms, status=4, confidence=88888
  timestamp=1791235190, timezone=36, hrv=89 ms, status=4, confidence=88235
  timestamp=1791235790, timezone=36, hrv=70 ms, status=4, confidence=90909
  timestamp=1791236390, timezone=36, hrv=114 ms, status=4, confidence=100000
  timestamp=1791236990, timezone=36, hrv=46 ms, status=4, confidence=100000
  timestamp=1791250490, timezone=36, hrv=120 ms, status=4, confidence=100000
  timestamp=1791253490, timezone=36, hrv=90 ms, status=4, confidence=95000
  timestamp=1791259190, timezone=36, hrv=75 ms, status=4, confidence=90909
  timestamp=1791260090, timezone=36, hrv=63 ms, status=4, confidence=100000
  timestamp=1791260690, timezone=36, hrv=71 ms, status=4, confidence=95238
  timestamp=1791262490, timezone=36, hrv=70 ms, status=4, confidence=100000
  timestamp=1791263090, timezone=36, hrv=63 ms, status=4, confidence=100000
  timestamp=1791264590, timezone=36, hrv=111 ms, status=4, confidence=94117
  timestamp=1791268790, timezone=36, hrv=87 ms, status=4, confidence=100000
  timestamp=1791273890, timezone=36, hrv=106 ms, status=4, confidence=95000
  timestamp=1791274190, timezone=36, hrv=89 ms, status=4, confidence=86363
  timestamp=1791274490, timezone=36, hrv=77 ms, status=4, confidence=100000
  timestamp=1791275090, timezone=36, hrv=85 ms, status=4, confidence=100000
  timestamp=1791275390, timezone=36, hrv=80 ms, status=4, confidence=94736
  timestamp=1791293690, timezone=36, hrv=85 ms, status=4, confidence=90476
  timestamp=1791297590, timezone=36, hrv=94 ms, status=4, confidence=85000
  timestamp=1791297890, timezone=36, hrv=59 ms, status=4, confidence=100000
  timestamp=1791298490, timezone=36, hrv=93 ms, status=4, confidence=95000
2026-10-07:
  timestamp=1791299990, timezone=36, hrv=111 ms, status=4, confidence=91470
  timestamp=1791300590, timezone=36, hrv=99 ms, status=4, confidence=100000
  timestamp=1791301190, timezone=36, hrv=73 ms, status=4, confidence=100000
  timestamp=1791301790, timezone=36, hrv=75 ms, status=4, confidence=100000
  timestamp=1791302390, timezone=36, hrv=61 ms, status=4, confidence=94736
  timestamp=1791302990, timezone=36, hrv=80 ms, status=4, confidence=100000
  timestamp=1791304490, timezone=36, hrv=71 ms, status=4, confidence=90476
  timestamp=1791305090, timezone=36, hrv=78 ms, status=4, confidence=95000
  timestamp=1791305690, timezone=36, hrv=126 ms, status=4, confidence=88235
  timestamp=1791306290, timezone=36, hrv=106 ms, status=4, confidence=100000
  timestamp=1791306890, timezone=36, hrv=149 ms, status=4, confidence=100000
  timestamp=1791307790, timezone=36, hrv=119 ms, status=4, confidence=100000
  timestamp=1791308390, timezone=36, hrv=92 ms, status=4, confidence=100000
  timestamp=1791308990, timezone=36, hrv=94 ms, status=4, confidence=89473
  timestamp=1791309590, timezone=36, hrv=70 ms, status=4, confidence=90000
  timestamp=1791310190, timezone=36, hrv=102 ms, status=4, confidence=86666
  timestamp=1791310790, timezone=36, hrv=94 ms, status=4, confidence=100000
  timestamp=1791311690, timezone=36, hrv=97 ms, status=4, confidence=100000
  timestamp=1791312290, timezone=36, hrv=142 ms, status=4, confidence=100000
  timestamp=1791312890, timezone=36, hrv=105 ms, status=4, confidence=100000
  timestamp=1791313490, timezone=36, hrv=110 ms, status=4, confidence=100000
  timestamp=1791314090, timezone=36, hrv=97 ms, status=4, confidence=86842
  timestamp=1791314690, timezone=36, hrv=130 ms, status=4, confidence=93750
  timestamp=1791315590, timezone=36, hrv=133 ms, status=4, confidence=93750
  timestamp=1791316190, timezone=36, hrv=88 ms, status=4, confidence=100000
  timestamp=1791316790, timezone=36, hrv=104 ms, status=4, confidence=96551
  timestamp=1791317390, timezone=36, hrv=64 ms, status=4, confidence=100000
  timestamp=1791317990, timezone=36, hrv=101 ms, status=4, confidence=93333
  timestamp=1791318590, timezone=36, hrv=110 ms, status=4, confidence=100000
  timestamp=1791320090, timezone=36, hrv=63 ms, status=4, confidence=100000
  timestamp=1791323090, timezone=36, hrv=125 ms, status=4, confidence=88235
  timestamp=1791323390, timezone=36, hrv=113 ms, status=4, confidence=88235
  timestamp=1791323690, timezone=36, hrv=117 ms, status=4, confidence=87500
  timestamp=1791324290, timezone=36, hrv=117 ms, status=4, confidence=86666
  timestamp=1791324590, timezone=36, hrv=114 ms, status=4, confidence=88888
  timestamp=1791325190, timezone=36, hrv=63 ms, status=4, confidence=100000
  timestamp=1791325790, timezone=36, hrv=171 ms, status=4, confidence=93750
  timestamp=1791326690, timezone=36, hrv=75 ms, status=4, confidence=100000
  timestamp=1791327890, timezone=36, hrv=135 ms, status=4, confidence=85069
  timestamp=1791328490, timezone=36, hrv=97 ms, status=4, confidence=91176
  timestamp=1791329390, timezone=36, hrv=97 ms, status=4, confidence=100000
  timestamp=1791329990, timezone=36, hrv=65 ms, status=4, confidence=100000
  timestamp=1791330590, timezone=36, hrv=101 ms, status=4, confidence=94444
  timestamp=1791331190, timezone=36, hrv=78 ms, status=4, confidence=100000
"""

# querySleepHrv 2025-10-30 → 2025-10-31 (France, timezone=4): night 23:02–04:40, nap 05:58–07:30
HRV_2025_10_31 = """Sleep HRV — 2025-10-30 to 2025-10-31
========================

HRV Assessment — Last 2 days
========================

2025-10-31:
  HRV Avg: 98 ms — Normal
  Normal Range: 83 - 103 ms
  Baseline: 93 ms

Sleep HRV Time Series — Last 2 days
========================

2025-10-30:
  timestamp=1761832490, timezone=4, hrv=52 ms, status=4, confidence=92000
  timestamp=1761836090, timezone=4, hrv=82 ms, status=4, confidence=88888
  timestamp=1761861890, timezone=4, hrv=38 ms, status=4, confidence=100000
  timestamp=1761862790, timezone=4, hrv=120 ms, status=4, confidence=94444
  timestamp=1761863090, timezone=4, hrv=108 ms, status=4, confidence=100000
  timestamp=1761863390, timezone=4, hrv=115 ms, status=4, confidence=100000
  timestamp=1761863990, timezone=4, hrv=113 ms, status=4, confidence=100000
  timestamp=1761864890, timezone=4, hrv=111 ms, status=4, confidence=95000
2025-10-31:
  timestamp=1761865490, timezone=4, hrv=94 ms, status=4, confidence=95000
  timestamp=1761866090, timezone=4, hrv=124 ms, status=4, confidence=95238
  timestamp=1761867290, timezone=4, hrv=82 ms, status=4, confidence=100000
  timestamp=1761867890, timezone=4, hrv=82 ms, status=4, confidence=100000
  timestamp=1761868790, timezone=4, hrv=94 ms, status=4, confidence=95000
  timestamp=1761869990, timezone=4, hrv=81 ms, status=4, confidence=95000
  timestamp=1761871190, timezone=4, hrv=95 ms, status=4, confidence=90000
  timestamp=1761873290, timezone=4, hrv=46 ms, status=4, confidence=100000
  timestamp=1761874490, timezone=4, hrv=82 ms, status=4, confidence=88888
  timestamp=1761875390, timezone=4, hrv=99 ms, status=4, confidence=84895
  timestamp=1761875990, timezone=4, hrv=146 ms, status=4, confidence=94117
  timestamp=1761876590, timezone=4, hrv=104 ms, status=4, confidence=91666
  timestamp=1761877190, timezone=4, hrv=81 ms, status=4, confidence=100000
  timestamp=1761877790, timezone=4, hrv=99 ms, status=4, confidence=82857
  timestamp=1761878390, timezone=4, hrv=89 ms, status=4, confidence=89473
  timestamp=1761879290, timezone=4, hrv=140 ms, status=4, confidence=94117
  timestamp=1761879890, timezone=4, hrv=95 ms, status=4, confidence=100000
  timestamp=1761880490, timezone=4, hrv=107 ms, status=4, confidence=100000
  timestamp=1761881090, timezone=4, hrv=125 ms, status=4, confidence=100000
  timestamp=1761881690, timezone=4, hrv=82 ms, status=4, confidence=100000
  timestamp=1761887090, timezone=4, hrv=130 ms, status=4, confidence=83070
  timestamp=1761887390, timezone=4, hrv=136 ms, status=4, confidence=100000
  timestamp=1761887990, timezone=4, hrv=129 ms, status=4, confidence=88070
  timestamp=1761888290, timezone=4, hrv=129 ms, status=4, confidence=100000
  timestamp=1761889490, timezone=4, hrv=105 ms, status=4, confidence=100000
  timestamp=1761890390, timezone=4, hrv=87 ms, status=4, confidence=85714
  timestamp=1761890990, timezone=4, hrv=125 ms, status=4, confidence=94444
  timestamp=1761891590, timezone=4, hrv=85 ms, status=4, confidence=100000
  timestamp=1761905090, timezone=4, hrv=80 ms, status=4, confidence=85000
"""

# querySleepHrv 2025-11-04 → 2025-11-05 (France, timezone=4): night 22:41–07:30, COROS « HRV Avg » 83
HRV_2025_11_05 = """Sleep HRV — 2025-11-04 to 2025-11-05
========================

HRV Assessment — Last 2 days
========================

2025-11-05:
  HRV Avg: 83 ms — Normal
  Normal Range: 82 - 102 ms
  Baseline: 92 ms

Sleep HRV Time Series — Last 2 days
========================

2025-11-04:
  timestamp=1762263890, timezone=4, hrv=72 ms, status=4, confidence=86178
  timestamp=1762265390, timezone=4, hrv=105 ms, status=4, confidence=89055
  timestamp=1762266590, timezone=4, hrv=70 ms, status=4, confidence=100000
  timestamp=1762266890, timezone=4, hrv=80 ms, status=4, confidence=100000
  timestamp=1762292690, timezone=4, hrv=70 ms, status=4, confidence=100000
  timestamp=1762293290, timezone=4, hrv=50 ms, status=4, confidence=100000
  timestamp=1762293590, timezone=4, hrv=58 ms, status=4, confidence=100000
  timestamp=1762293890, timezone=4, hrv=50 ms, status=4, confidence=100000
  timestamp=1762294490, timezone=4, hrv=74 ms, status=4, confidence=100000
  timestamp=1762295090, timezone=4, hrv=48 ms, status=4, confidence=100000
  timestamp=1762295690, timezone=4, hrv=55 ms, status=4, confidence=100000
  timestamp=1762296290, timezone=4, hrv=73 ms, status=4, confidence=100000
  timestamp=1762296890, timezone=4, hrv=62 ms, status=4, confidence=100000
2025-11-05:
  timestamp=1762297490, timezone=4, hrv=103 ms, status=4, confidence=86956
  timestamp=1762298390, timezone=4, hrv=66 ms, status=4, confidence=100000
  timestamp=1762298990, timezone=4, hrv=111 ms, status=4, confidence=92000
  timestamp=1762299590, timezone=4, hrv=48 ms, status=4, confidence=100000
  timestamp=1762300190, timezone=4, hrv=62 ms, status=4, confidence=100000
  timestamp=1762300790, timezone=4, hrv=101 ms, status=4, confidence=100000
  timestamp=1762301390, timezone=4, hrv=120 ms, status=4, confidence=100000
  timestamp=1762301990, timezone=4, hrv=82 ms, status=4, confidence=100000
  timestamp=1762302890, timezone=4, hrv=139 ms, status=4, confidence=100000
  timestamp=1762303490, timezone=4, hrv=96 ms, status=4, confidence=100000
  timestamp=1762304090, timezone=4, hrv=71 ms, status=4, confidence=100000
  timestamp=1762304690, timezone=4, hrv=107 ms, status=4, confidence=100000
  timestamp=1762305290, timezone=4, hrv=85 ms, status=4, confidence=100000
  timestamp=1762305890, timezone=4, hrv=84 ms, status=4, confidence=96551
  timestamp=1762306790, timezone=4, hrv=79 ms, status=4, confidence=100000
  timestamp=1762307390, timezone=4, hrv=78 ms, status=4, confidence=100000
  timestamp=1762307990, timezone=4, hrv=96 ms, status=4, confidence=94736
  timestamp=1762308590, timezone=4, hrv=85 ms, status=4, confidence=85172
  timestamp=1762309190, timezone=4, hrv=77 ms, status=4, confidence=100000
  timestamp=1762309790, timezone=4, hrv=95 ms, status=4, confidence=86363
  timestamp=1762310390, timezone=4, hrv=75 ms, status=4, confidence=100000
  timestamp=1762311290, timezone=4, hrv=49 ms, status=4, confidence=100000
  timestamp=1762311890, timezone=4, hrv=44 ms, status=4, confidence=100000
  timestamp=1762312490, timezone=4, hrv=112 ms, status=4, confidence=83475
  timestamp=1762313090, timezone=4, hrv=55 ms, status=4, confidence=100000
  timestamp=1762313690, timezone=4, hrv=95 ms, status=4, confidence=100000
  timestamp=1762314590, timezone=4, hrv=77 ms, status=4, confidence=100000
  timestamp=1762315190, timezone=4, hrv=80 ms, status=4, confidence=100000
  timestamp=1762316390, timezone=4, hrv=102 ms, status=4, confidence=100000
  timestamp=1762316990, timezone=4, hrv=93 ms, status=4, confidence=86363
  timestamp=1762317890, timezone=4, hrv=48 ms, status=4, confidence=100000
  timestamp=1762318490, timezone=4, hrv=95 ms, status=4, confidence=88871
  timestamp=1762319090, timezone=4, hrv=117 ms, status=4, confidence=100000
  timestamp=1762319690, timezone=4, hrv=124 ms, status=4, confidence=91666
  timestamp=1762320290, timezone=4, hrv=96 ms, status=4, confidence=94736
  timestamp=1762321190, timezone=4, hrv=125 ms, status=4, confidence=94736
  timestamp=1762321790, timezone=4, hrv=85 ms, status=4, confidence=90476
  timestamp=1762322390, timezone=4, hrv=84 ms, status=4, confidence=100000
  timestamp=1762322990, timezone=4, hrv=124 ms, status=4, confidence=95000
  timestamp=1762323590, timezone=4, hrv=72 ms, status=4, confidence=100000
  timestamp=1762324190, timezone=4, hrv=82 ms, status=4, confidence=100000
  timestamp=1762330190, timezone=4, hrv=51 ms, status=4, confidence=100000
  timestamp=1762335290, timezone=4, hrv=45 ms, status=4, confidence=90100
  timestamp=1762335590, timezone=4, hrv=21 ms, status=4, confidence=96551
  timestamp=1762375790, timezone=4, hrv=60 ms, status=4, confidence=96000
  timestamp=1762381190, timezone=4, hrv=100 ms, status=4, confidence=100000
  timestamp=1762381490, timezone=4, hrv=54 ms, status=4, confidence=100000
  timestamp=1762382090, timezone=4, hrv=99 ms, status=4, confidence=95454
  timestamp=1762382390, timezone=4, hrv=57 ms, status=4, confidence=100000
  timestamp=1762382690, timezone=4, hrv=77 ms, status=4, confidence=100000
  timestamp=1762383290, timezone=4, hrv=106 ms, status=4, confidence=100000
"""

# querySleepHrv 2025-10-25 → 2025-10-28 (three answers joined, the 25th and 28th trimmed to the readings
# around the nights): the night of the autumn clock change (Greece, timezone=12 all night although the
# clocks went back at 01:00 UTC), then 2025-10-28 on the new offset (timezone=8)
HRV_2025_DST = """Sleep HRV — 2025-10-25 to 2025-10-28
========================

HRV Assessment
========================

2025-10-26:
  HRV Avg: 82 ms — Below normal
  Normal Range: 84 - 108 ms
  Baseline: 96 ms
2025-10-28:
  HRV Avg: 91 ms — Normal
  Normal Range: 84 - 106 ms
  Baseline: 95 ms

Sleep HRV Time Series
========================

2025-10-25:
  timestamp=1761373190, timezone=12, hrv=114 ms, status=4, confidence=86363
  timestamp=1761405590, timezone=12, hrv=87 ms, status=4, confidence=100000
  timestamp=1761417590, timezone=12, hrv=84 ms, status=4, confidence=100000
  timestamp=1761417890, timezone=12, hrv=66 ms, status=4, confidence=100000
  timestamp=1761418790, timezone=12, hrv=86 ms, status=4, confidence=91304
  timestamp=1761420590, timezone=12, hrv=107 ms, status=4, confidence=90909
  timestamp=1761421790, timezone=12, hrv=73 ms, status=4, confidence=100000
  timestamp=1761422090, timezone=12, hrv=51 ms, status=4, confidence=100000
  timestamp=1761425090, timezone=12, hrv=70 ms, status=4, confidence=100000
  timestamp=1761425990, timezone=12, hrv=99 ms, status=4, confidence=85714
2025-10-26:
  timestamp=1761426590, timezone=12, hrv=81 ms, status=4, confidence=95000
  timestamp=1761427190, timezone=12, hrv=102 ms, status=4, confidence=85000
  timestamp=1761429290, timezone=12, hrv=89 ms, status=4, confidence=89473
  timestamp=1761429890, timezone=12, hrv=90 ms, status=4, confidence=86363
  timestamp=1761431690, timezone=12, hrv=67 ms, status=4, confidence=100000
  timestamp=1761433790, timezone=12, hrv=117 ms, status=4, confidence=94736
  timestamp=1761437690, timezone=12, hrv=105 ms, status=4, confidence=82500
  timestamp=1761438590, timezone=12, hrv=62 ms, status=4, confidence=95000
  timestamp=1761439790, timezone=12, hrv=70 ms, status=4, confidence=95238
  timestamp=1761441890, timezone=12, hrv=65 ms, status=4, confidence=100000
  timestamp=1761442490, timezone=12, hrv=24 ms, status=4, confidence=100000
  timestamp=1761443090, timezone=12, hrv=64 ms, status=4, confidence=100000
  timestamp=1761443690, timezone=12, hrv=79 ms, status=4, confidence=86956
  timestamp=1761444290, timezone=12, hrv=58 ms, status=4, confidence=100000
  timestamp=1761444890, timezone=12, hrv=41 ms, status=4, confidence=100000
  timestamp=1761445790, timezone=12, hrv=132 ms, status=4, confidence=90476
2025-10-28:
  timestamp=1761606290, timezone=8, hrv=109 ms, status=4, confidence=86363
  timestamp=1761606590, timezone=8, hrv=63 ms, status=4, confidence=100000
  timestamp=1761607190, timezone=8, hrv=56 ms, status=4, confidence=95833
  timestamp=1761607490, timezone=8, hrv=97 ms, status=4, confidence=100000
  timestamp=1761608690, timezone=8, hrv=92 ms, status=4, confidence=85304
  timestamp=1761609290, timezone=8, hrv=74 ms, status=4, confidence=91304
  timestamp=1761610190, timezone=8, hrv=98 ms, status=4, confidence=100000
  timestamp=1761610790, timezone=8, hrv=88 ms, status=4, confidence=100000
  timestamp=1761611390, timezone=8, hrv=118 ms, status=4, confidence=85714
  timestamp=1761611990, timezone=8, hrv=84 ms, status=4, confidence=87500
  timestamp=1761612590, timezone=8, hrv=73 ms, status=4, confidence=100000
  timestamp=1761613190, timezone=8, hrv=111 ms, status=4, confidence=100000
  timestamp=1761614090, timezone=8, hrv=73 ms, status=4, confidence=100000
  timestamp=1761614690, timezone=8, hrv=110 ms, status=4, confidence=87698
  timestamp=1761615890, timezone=8, hrv=69 ms, status=4, confidence=96000
  timestamp=1761616490, timezone=8, hrv=101 ms, status=4, confidence=100000
  timestamp=1761618590, timezone=8, hrv=93 ms, status=4, confidence=82817
  timestamp=1761619190, timezone=8, hrv=87 ms, status=4, confidence=90476
  timestamp=1761620090, timezone=8, hrv=101 ms, status=4, confidence=85714
  timestamp=1761620690, timezone=8, hrv=131 ms, status=4, confidence=85000
  timestamp=1761621890, timezone=8, hrv=136 ms, status=4, confidence=89473
  timestamp=1761625190, timezone=8, hrv=76 ms, status=4, confidence=95238
  timestamp=1761626090, timezone=8, hrv=38 ms, status=4, confidence=90322
  timestamp=1761626690, timezone=8, hrv=96 ms, status=4, confidence=90476
  timestamp=1761627290, timezone=8, hrv=111 ms, status=4, confidence=90476
  timestamp=1761634490, timezone=8, hrv=75 ms, status=4, confidence=87774
  timestamp=1761635390, timezone=8, hrv=97 ms, status=4, confidence=95454
  timestamp=1761669590, timezone=8, hrv=96 ms, status=4, confidence=95238
  timestamp=1761670490, timezone=8, hrv=106 ms, status=4, confidence=100000
"""

# ── 2026-10-08 (Santé v4's owner fixture), read from COROS that morning (08:00 in Korea) ─────────────────────────

# querySleepOverview 2026-10-08: the night 22:42 → 07:30, 8h36 asleep, no nap
OVERVIEW_2026_10_08 = """Sleep Overview
========================
Note: each record below is dated by its wake-up day.

2026-10-08
Sleep Score: 90
Daily Sleep: 8h 36min (incl. naps)
Main Sleep (asleep): 8h 36min
Main Sleep Period (incl. awake): 8h 48min
Sleep metrics scope: daily
Deep Sleep Ratio: 13%
Light Sleep Ratio: 62%
REM Ratio: 23%
Awake Ratio: 2%
Awake Time: 12 min
Awake Count (>5 min): 0
Main Sleep Window: 2026-10-07 22:42 - 2026-10-08 07:30
Naps Total: 0 min
"""

# queryDailyHealthData: the 2026-10-08 Sleep Summary (« Sleep HR » 35 bpm)
DAILY_2026_10_08 = """Daily Health Data — Last 2 days | Resting HR: 32 bpm | HRV Baseline: 42 ms
Note: sleep entries are dated by their wake-up day.

--- 20261008 ---
Steps: 70 | Calories: 14 kcal | Exercise: 0 min
Stress: Avg 7
Sleep Summary:
  Total: 8h 48min | Deep: 1h 11min | Light: 5h 26min | REM: 1h 59min | Awake: 12 min
  Sleep HR: Avg 35 bpm | Min 31 bpm | Max 59 bpm
"""

# querySleepHrv 2026-10-07 → 2026-10-08 (Korea, timezone=36): both local days of the 07 → 08 night
HRV_2026_10_08 = """Sleep HRV — 2026-10-07 to 2026-10-08
========================
Note: dates are wake-up days (each value comes from the night that ended that morning).

HRV Assessment — Last 2 days
========================

2026-10-08:
  HRV Avg: 103 ms — Above normal
  Normal Range: 70 - 86 ms
  Baseline: 78 ms
2026-10-07:
  HRV Avg: 97 ms — Above normal
  Normal Range: 70 - 84 ms
  Baseline: 77 ms

Sleep HRV Time Series — Last 2 days
========================

2026-10-07:
""" + "".join(f"  timestamp={ts}, timezone=36, hrv={v} ms, status=4, confidence=100000\n" for ts, v in (
    (1791299990, 111), (1791300590, 99), (1791301190, 73), (1791301790, 75), (1791302390, 61), (1791302990, 80),
    (1791304490, 71), (1791305090, 78), (1791305690, 126), (1791306290, 106), (1791306890, 149), (1791307790, 119),
    (1791308390, 92), (1791308990, 94), (1791309590, 70), (1791310190, 102), (1791310790, 94), (1791311690, 97),
    (1791312290, 142), (1791312890, 105), (1791313490, 110), (1791314090, 97), (1791314690, 130), (1791315590, 133),
    (1791316190, 88), (1791316790, 104), (1791317390, 64), (1791317990, 101), (1791318590, 110), (1791320090, 63),
    (1791323090, 125), (1791323390, 113), (1791323690, 117), (1791324290, 117), (1791324590, 114), (1791325190, 63),
    (1791325790, 171), (1791326690, 75), (1791327890, 135), (1791328490, 97), (1791329390, 97), (1791329990, 65),
    (1791330590, 101), (1791331190, 78), (1791370190, 108), (1791377390, 93), (1791380690, 110), (1791380990, 112),
    (1791381590, 69), (1791381890, 102), (1791382190, 125), (1791383090, 111), (1791383690, 82), (1791384290, 81),
    (1791384890, 84))) + "2026-10-08:\n" + "".join(
    f"  timestamp={ts}, timezone=36, hrv={v} ms, status=4, confidence=100000\n" for ts, v in (
        (1791385490, 102), (1791386090, 105), (1791387590, 107), (1791388190, 94), (1791388790, 97), (1791389390, 157),
        (1791390290, 76), (1791390890, 117), (1791391490, 83), (1791392090, 97), (1791392690, 111), (1791393290, 162),
        (1791393890, 162), (1791394790, 93), (1791395390, 142), (1791395990, 103), (1791398690, 84), (1791400790, 90),
        (1791401390, 70), (1791402590, 100), (1791403190, 75), (1791403790, 86), (1791405290, 94), (1791405890, 126),
        (1791406490, 90), (1791407990, 103), (1791408590, 74), (1791411890, 102)))

# querySportRecords: the Transjeju 100M as COROS recorded it (trail run, 02/10 21:00 in Korea, 16:53:27)
TRANSJEJU_START, TRANSJEJU_END = 1790942428, 1791003235

# querySleepHrv 2026-09-28 (Korea): answered, no reading (the night ending 29/09 started at 00:31 on 29/09)
HRV_2026_09_28 = """Sleep HRV — 2026-09-28
========================
Note: dates are wake-up days (each value comes from the night that ended that morning).

HRV Assessment — Last 1 days
========================

2026-09-28:
  No data

Sleep HRV Time Series — Last 1 days
========================

2026-09-28: no official sleep HRV value for this day; raw HRV points omitted."""

# querySleepHrv 2026-09-29 → 2026-10-01 (Korea, timezone=36): the three nights before the Transjeju, read on 2026-10-08
HRV_2026_10_01 = """Sleep HRV — 2026-09-29 to 2026-10-01
========================
Note: dates are wake-up days (each value comes from the night that ended that morning).

HRV Assessment — Last 3 days
========================

2026-10-01:
  HRV Avg: 80 ms — Normal
  Normal Range: 70 - 84 ms
  Baseline: 77 ms
2026-09-30:
  HRV Avg: 83 ms — Normal
  Normal Range: 70 - 84 ms
  Baseline: 77 ms
2026-09-29:
  HRV Avg: 90 ms — Above normal
  Normal Range: 70 - 84 ms
  Baseline: 77 ms

Sleep HRV Time Series — Last 3 days
========================

2026-09-29:
  timestamp=1790610290, timezone=36, hrv=77 ms, status=4, confidence=100000
  timestamp=1790610590, timezone=36, hrv=101 ms, status=4, confidence=100000
  timestamp=1790610890, timezone=36, hrv=92 ms, status=4, confidence=90000
  timestamp=1790611190, timezone=36, hrv=44 ms, status=4, confidence=100000
  timestamp=1790612090, timezone=36, hrv=84 ms, status=4, confidence=86842
  timestamp=1790612690, timezone=36, hrv=117 ms, status=4, confidence=94117
  timestamp=1790613890, timezone=36, hrv=58 ms, status=4, confidence=100000
  timestamp=1790614490, timezone=36, hrv=69 ms, status=4, confidence=100000
  timestamp=1790615390, timezone=36, hrv=87 ms, status=4, confidence=100000
  timestamp=1790615990, timezone=36, hrv=60 ms, status=4, confidence=100000
  timestamp=1790617190, timezone=36, hrv=91 ms, status=4, confidence=94444
  timestamp=1790617790, timezone=36, hrv=94 ms, status=4, confidence=95000
  timestamp=1790620490, timezone=36, hrv=122 ms, status=4, confidence=89473
  timestamp=1790621090, timezone=36, hrv=108 ms, status=4, confidence=100000
  timestamp=1790622590, timezone=36, hrv=56 ms, status=4, confidence=100000
  timestamp=1790623790, timezone=36, hrv=62 ms, status=4, confidence=100000
  timestamp=1790625290, timezone=36, hrv=78 ms, status=4, confidence=94444
  timestamp=1790625890, timezone=36, hrv=68 ms, status=4, confidence=100000
  timestamp=1790626490, timezone=36, hrv=79 ms, status=4, confidence=100000
  timestamp=1790627090, timezone=36, hrv=59 ms, status=4, confidence=86363
  timestamp=1790628590, timezone=36, hrv=100 ms, status=4, confidence=93750
  timestamp=1790629190, timezone=36, hrv=101 ms, status=4, confidence=100000
  timestamp=1790629790, timezone=36, hrv=100 ms, status=4, confidence=100000
  timestamp=1790630390, timezone=36, hrv=95 ms, status=4, confidence=89473
  timestamp=1790631290, timezone=36, hrv=115 ms, status=4, confidence=100000
  timestamp=1790631890, timezone=36, hrv=139 ms, status=4, confidence=91176
  timestamp=1790634590, timezone=36, hrv=60 ms, status=4, confidence=88461
  timestamp=1790635190, timezone=36, hrv=59 ms, status=4, confidence=100000
  timestamp=1790636690, timezone=36, hrv=159 ms, status=4, confidence=83055
  timestamp=1790638490, timezone=36, hrv=102 ms, status=4, confidence=86585
  timestamp=1790639390, timezone=36, hrv=70 ms, status=4, confidence=94736
  timestamp=1790639990, timezone=36, hrv=130 ms, status=4, confidence=94444
  timestamp=1790640590, timezone=36, hrv=118 ms, status=4, confidence=100000
  timestamp=1790641190, timezone=36, hrv=126 ms, status=4, confidence=85637
  timestamp=1790641790, timezone=36, hrv=93 ms, status=4, confidence=88235
  timestamp=1790649590, timezone=36, hrv=98 ms, status=4, confidence=91954
  timestamp=1790649890, timezone=36, hrv=74 ms, status=4, confidence=100000
  timestamp=1790658290, timezone=36, hrv=73 ms, status=4, confidence=91470
  timestamp=1790693690, timezone=36, hrv=53 ms, status=4, confidence=100000
  timestamp=1790693990, timezone=36, hrv=88 ms, status=4, confidence=85714
2026-09-30:
  timestamp=1790694590, timezone=36, hrv=98 ms, status=4, confidence=95000
  timestamp=1790694890, timezone=36, hrv=43 ms, status=4, confidence=100000
  timestamp=1790695790, timezone=36, hrv=56 ms, status=4, confidence=100000
  timestamp=1790697290, timezone=36, hrv=99 ms, status=4, confidence=88888
  timestamp=1790698790, timezone=36, hrv=69 ms, status=4, confidence=95238
  timestamp=1790699390, timezone=36, hrv=76 ms, status=4, confidence=100000
  timestamp=1790699990, timezone=36, hrv=62 ms, status=4, confidence=100000
  timestamp=1790700590, timezone=36, hrv=59 ms, status=4, confidence=100000
  timestamp=1790701190, timezone=36, hrv=82 ms, status=4, confidence=89473
  timestamp=1790701790, timezone=36, hrv=66 ms, status=4, confidence=95238
  timestamp=1790702390, timezone=36, hrv=81 ms, status=4, confidence=100000
  timestamp=1790703890, timezone=36, hrv=108 ms, status=4, confidence=87058
  timestamp=1790705990, timezone=36, hrv=159 ms, status=4, confidence=100000
  timestamp=1790706590, timezone=36, hrv=70 ms, status=4, confidence=100000
  timestamp=1790707190, timezone=36, hrv=74 ms, status=4, confidence=100000
  timestamp=1790707790, timezone=36, hrv=74 ms, status=4, confidence=90476
  timestamp=1790709290, timezone=36, hrv=114 ms, status=4, confidence=85000
  timestamp=1790709890, timezone=36, hrv=73 ms, status=4, confidence=94736
  timestamp=1790711690, timezone=36, hrv=109 ms, status=4, confidence=100000
  timestamp=1790712290, timezone=36, hrv=82 ms, status=4, confidence=94444
  timestamp=1790713190, timezone=36, hrv=89 ms, status=4, confidence=100000
  timestamp=1790713790, timezone=36, hrv=77 ms, status=4, confidence=100000
  timestamp=1790714390, timezone=36, hrv=60 ms, status=4, confidence=100000
  timestamp=1790715590, timezone=36, hrv=97 ms, status=4, confidence=89473
  timestamp=1790716190, timezone=36, hrv=67 ms, status=4, confidence=95238
  timestamp=1790717090, timezone=36, hrv=119 ms, status=4, confidence=93333
  timestamp=1790717690, timezone=36, hrv=67 ms, status=4, confidence=100000
  timestamp=1790720390, timezone=36, hrv=110 ms, status=4, confidence=100000
  timestamp=1790723090, timezone=36, hrv=94 ms, status=4, confidence=88095
  timestamp=1790723690, timezone=36, hrv=69 ms, status=4, confidence=100000
  timestamp=1790724290, timezone=36, hrv=82 ms, status=4, confidence=94117
  timestamp=1790724890, timezone=36, hrv=93 ms, status=4, confidence=100000
  timestamp=1790726090, timezone=36, hrv=103 ms, status=4, confidence=100000
  timestamp=1790727590, timezone=36, hrv=76 ms, status=4, confidence=100000
  timestamp=1790728790, timezone=36, hrv=94 ms, status=4, confidence=87931
  timestamp=1790729990, timezone=36, hrv=88 ms, status=4, confidence=90909
2026-10-01:
  timestamp=1790780690, timezone=36, hrv=74 ms, status=4, confidence=100000
  timestamp=1790781590, timezone=36, hrv=64 ms, status=4, confidence=100000
  timestamp=1790781890, timezone=36, hrv=87 ms, status=4, confidence=100000
  timestamp=1790782490, timezone=36, hrv=35 ms, status=4, confidence=100000
  timestamp=1790783390, timezone=36, hrv=95 ms, status=4, confidence=85000
  timestamp=1790783990, timezone=36, hrv=104 ms, status=4, confidence=90476
  timestamp=1790784590, timezone=36, hrv=87 ms, status=4, confidence=100000
  timestamp=1790785190, timezone=36, hrv=41 ms, status=4, confidence=100000
  timestamp=1790785790, timezone=36, hrv=96 ms, status=4, confidence=90000
  timestamp=1790787290, timezone=36, hrv=79 ms, status=4, confidence=95000
  timestamp=1790787890, timezone=36, hrv=55 ms, status=4, confidence=100000
  timestamp=1790789090, timezone=36, hrv=90 ms, status=4, confidence=100000
  timestamp=1790789990, timezone=36, hrv=116 ms, status=4, confidence=100000
  timestamp=1790790590, timezone=36, hrv=84 ms, status=4, confidence=90476
  timestamp=1790791190, timezone=36, hrv=95 ms, status=4, confidence=100000
  timestamp=1790791790, timezone=36, hrv=51 ms, status=4, confidence=100000
  timestamp=1790795090, timezone=36, hrv=86 ms, status=4, confidence=100000
  timestamp=1790797790, timezone=36, hrv=62 ms, status=4, confidence=96428
  timestamp=1790799890, timezone=36, hrv=122 ms, status=4, confidence=100000
  timestamp=1790801390, timezone=36, hrv=143 ms, status=4, confidence=100000
  timestamp=1790804090, timezone=36, hrv=45 ms, status=4, confidence=100000
  timestamp=1790804690, timezone=36, hrv=68 ms, status=4, confidence=100000
  timestamp=1790806490, timezone=36, hrv=80 ms, status=4, confidence=94736
  timestamp=1790808590, timezone=36, hrv=77 ms, status=4, confidence=94736
  timestamp=1790809190, timezone=36, hrv=82 ms, status=4, confidence=95454
  timestamp=1790810690, timezone=36, hrv=63 ms, status=4, confidence=95454
  timestamp=1790811890, timezone=36, hrv=72 ms, status=4, confidence=100000
  timestamp=1790812490, timezone=36, hrv=72 ms, status=4, confidence=100000
  timestamp=1790813090, timezone=36, hrv=113 ms, status=4, confidence=100000
  timestamp=1790822390, timezone=36, hrv=70 ms, status=4, confidence=100000"""

# queryDailyHealthData read on 2026-10-08: the three nights before the Transjeju (« Sleep HR » of the main sleep)
DAILY_2026_10_01 = """Daily Health Data — Last 12 days | Resting HR: 32 bpm | HRV Baseline: 42 ms
Note: sleep entries are dated by their wake-up day.

--- 20260929 ---
Steps: 18,055 | Calories: 786 kcal | Exercise: 39 min
Stress: Avg 23
Sleep Summary:
  Total: 9h 0min | Deep: 1h 0min | Light: 5h 41min | REM: 2h 5min | Awake: 14 min
  Sleep HR: Avg 35 bpm | Min 30 bpm | Max 53 bpm

--- 20260930 ---
Steps: 15,411 | Calories: 965 kcal | Exercise: 36 min
Stress: Avg 24
Sleep Summary:
  Total: 10h 2min | Deep: 1h 16min | Light: 5h 43min | REM: 2h 49min | Awake: 14 min
  Sleep HR: Avg 35 bpm | Min 30 bpm | Max 52 bpm

--- 20261001 ---
Steps: 16,599 | Calories: 730 kcal | Exercise: 21 min
Stress: Avg 23
Sleep Summary:
  Total: 9h 22min | Deep: 53 min | Light: 5h 19min | REM: 2h 49min | Awake: 21 min
  Sleep HR: Avg 36 bpm | Min 30 bpm | Max 51 bpm
"""
