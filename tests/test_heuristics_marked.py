"""SANTE_DECISIONS: « thresholds not from a study are marked (H) in code
comments and tests ». Each heuristic constant below is (H) on the line that
sets it, and its value is pinned here (H) so a change is a decision. v4.3
(owner: « trop d'explication … ne mets pas les citations »): the (H) stays in
the code comments and the tests, never in the text the page prints."""
import re
from datetime import time
from pathlib import Path

import pytest

from app.services import nights as nt
from app.services import race_prep as rp
from app.services import sante
from app.services import sante_load
from app.services import sante_score as sc
from app.services import sante_sleep as sl
from app.services import sante_today as td
from app.services import sante_training as st

ROOT = Path(__file__).resolve().parent.parent

# (module file, constant, value) — every one a PaceForge heuristic (H)
HEURISTICS = [
    ("app/services/sante_training.py", "MIN_FIT_RUNS", 6),
    ("app/services/sante_training.py", "EASY_FIT_DAYS", 365),
    ("app/services/sante_training.py", "FLAG_REF_DAYS", 14),
    ("app/services/sante_training.py", "LINE_RUNS", 6),
    ("app/services/sante_training.py", "EASY_KM", 5),
    ("app/services/sante_training.py", "EASY_DPLUS_PER_KM", 12),
    ("app/services/sante_training.py", "EASY_HR_SHARE", 0.82),
    ("app/services/sante_training.py", "HOT_C", 25),
    ("app/services/nights.py", "BAND_DAYS", 60),
    ("app/services/nights.py", "MIN_BAND_NIGHTS", 14),
    ("app/services/nights.py", "RACE_WINDOW", 7),
    ("app/services/nights.py", "TZ_CHANGE_MIN", 60),
    # v4.4 (research_data.md R2): where the athlete sleeps (Latshang 2013), carried over rest days up there
    ("app/services/nights.py", "ALTITUDE_M", 1600),
    ("app/services/nights.py", "ALTITUDE_CARRY", 3),
    ("app/services/race_prep.py", "HOT_FACTOR", 1.06),
    ("app/services/nights.py", "MIN_PROVISIONAL_NIGHTS", 7),
    # Santé v4 (SANTE_V4_SPEC.md): the effort classes, their windows and Charge, the score, the habits
    ("app/services/sante_training.py", "EFFORT_ULTRA", 600),
    ("app/services/sante_training.py", "EFFORT_VERY_LONG", 360),
    ("app/services/sante_training.py", "EFFORT_LONG", 180),
    ("app/services/sante_training.py", "EFFORT_DPLUS", 1500),
    ("app/services/sante_training.py", "STOPPED_WATCH", 2),
    # owner, 2026-10-14: scientific basis — a window is a reason of the state, no cap (EFFORT_RULES' 35/45/65 caps
    # had no basis): orange through D+1 / D+3 / D+5, red on D+0 and D+1 after an Ultra
    ("app/services/sante_training.py", "EFFORT_WINDOWS", {"ultra": 5, "very_long": 3, "long": 1}),
    ("app/services/sante_training.py", "ULTRA_RED_LAST", 1),
    # v4.2 (research_efforts.md §b): « fenêtres indicatives », no official body gives days (Kellmann 2018);
    # owner, 2026-10-14: scientific basis — a Longue run like a race through D+2 (Petersen 2007), was D+5
    ("app/services/sante_training.py", "LONG_RACE_LAST", 2),
    # v4.4 (research_data.md R3): a Longue run like a race, whoever measured it (session RPE: Foster 2001)
    ("app/services/sante_training.py", "INTENSE_RPE", 8),
    ("app/services/sante_training.py", "ULTRA_LONG_MIN", 1440),
    ("app/services/sante_training.py", "ULTRA_LONG_LAST", 7),  # owner, 2026-10-14: scientific basis (was D+13)
    ("app/services/sante_training.py", "NIGHT_SPAN", (time(1), time(5))),
    ("app/services/sante_training.py", "LOWER", {"ultra": "very_long", "very_long": "long", "long": None}),
    ("app/services/sante_training.py", "NIGHT_TAGS", {"long": ("long", 1), "very_long": ("big", 3),
                                                     "ultra": ("ultra", 4)}),
    ("app/services/sante_training.py", "AFTER_ULTRA_DAYS", 21),
    # v4.2 sleep (research_sleep.md): the stages to 10 min, the late-nap cut-off (Mograss 2022; Walsh 2021)
    ("app/services/sante_sleep.py", "STAGE_STEP", 10),
    ("app/services/nights.py", "LATE_NAP_H", 7),
    ("app/services/nights.py", "NAP_LATEST", time(16)),
    ("app/services/nights.py", "USUAL_BED_NIGHTS", 5),
    ("app/services/nights.py", "USUAL_BED_DAYS", 28),
    # owner, 2026-10-14: scientific basis — the weighted index (WEIGHTS, its anchors, SLEEP_SHARES, its caps) is
    # replaced by a rule-based state (test_the_replaced_heuristics_are_gone); its « indice nocturne »: 100 from the
    # smallest worthwhile change, 0 at 2.5 SD, held to the state's range
    ("app/services/sante_score.py", "Z_FLOOR", 2.5),
    ("app/services/sante_score.py", "Z_SPAN", 2.0),
    ("app/services/sante_score.py", "HOLD", {"ok": (70, 100), "warn": (0, 69), "danger": (0, 39)}),
    # the baseline series: the device with the most nights in 28 days among those worn in the last 7; ties by the
    # larger baseline, the day before's series, then this order
    ("app/services/nights.py", "SERIES_DAYS", 28),
    ("app/services/nights.py", "SERIES_ACTIVE_DAYS", 7),
    ("app/services/nights.py", "DEVICE_ORDER", ("COROS", "Garmin")),
    # the baseline stops a week before the day it reads (a design choice), the trend's threshold assumes a lag-1
    # correlation of 0.3 between nights (the second statistics review, 2026-10-14)
    ("app/services/nights.py", "BASELINE_LAG", 7),
    ("app/services/nights.py", "TREND_RHO", 0.3),
    # the night-HR illness alert's line, and the line its episode (« FC de nuit haute ») ends on (the review of
    # 2026-10-14: the median + 3 bpm again, not the trend band's top)
    ("app/services/nights.py", "ALERT_SD", 2),
    ("app/services/nights.py", "ALERT_MIN_BPM", 5),
    ("app/services/nights.py", "ALERT_END_BPM", 3),
    ("app/services/nights.py", "ILL_MAX", 14),
    # the bands' SDs shrunk towards a prior (Kellmann 2018's Bayesian individualisation; Mishica 2022)
    ("app/services/nights.py", "PRIOR_NIGHTS", 7),
    ("app/services/nights.py", "HRV_SD_PRIOR", 0.10),
    ("app/services/nights.py", "HR_SD_PRIOR", 0.04),
    # 2026-10-09 (owner: « utilise la respiration aussi si tu l'as »): the breathing rate's line (a reason of the
    # state since 2026-10-14, no cap)
    ("app/services/nights.py", "RESP_UP_MIN", 1.0),
    ("app/services/nights.py", "RESP_UP_SD", 2),
    ("app/services/nights.py", "RESP_SD_PRIOR", 0.5),
    # « sortie intense le soir »: only a vigorous session close to sleep (Stutz 2019; Myllymäki 2012)
    ("app/services/nights.py", "VIGOROUS_HRR", 0.8),
    ("app/services/nights.py", "VIGOROUS_MIN", 20),
    ("app/services/sante_sleep.py", "USUAL_NIGHTS", 5),
    # 2026-10-09 (owner: « Ne demande pas, ce doit être auto comme WHOOP »): the sleep need, 8 h for everyone since
    # 2026-10-14 (scientific basis: no addition, no learnt base); the sleep owed over 7 nights, said apart
    ("app/services/sante_sleep.py", "NEED_DEFAULT", 480),
    ("app/services/sante_sleep.py", "DEFICIT_DAYS", 7),
    ("app/services/sante_sleep.py", "DEFICIT_MIN_NIGHTS", 4),
    ("app/services/sante_sleep.py", "DEFICIT_Z", 1.28),  # the sleep owed over its own noise only
    ("app/services/sante_sleep.py", "TST_SD_PRIOR", 45.0),
    # 2026-10-08 (« Fais comme WHOOP »): the Entraînement dial's usual week (one load pipeline since 2026-10-14:
    # sante_load), « comme d'habitude » from the lightest to the heaviest of its active weeks (the statistics
    # review, 2026-10-14; USUAL_SPREAD's ± 20 %, then the 25th–75th percentiles, are gone)
    ("app/services/sante_load.py", "USUAL_WEEKS", 11),
    ("app/services/sante_load.py", "USUAL_MIN_WEEKS", 4),
    ("app/services/sante_load.py", "ACUTE_TAU", 7),
    ("app/services/sante_load.py", "CHRONIC_TAU", 42),
    ("app/services/sante_load.py", "INIT_DAYS", 28),
    ("app/services/sante_load.py", "READY_DAYS", 28),
    ("app/services/sante_load.py", "READY_WEEKS", 4),
    # 2026-10-09 (« Oui vas-y », research_ind_train.md): the dial from heart rate (Banister 1991's weighting), its
    # glitch guard, its fallback for minutes without a readable HR, and the card's « Intensité »
    ("app/services/sante_training.py", "LOAD_A", 0.64),
    ("app/services/sante_training.py", "LOAD_B", 1.92),
    ("app/services/sante_training.py", "SEG_COVER", 0.8),
    ("app/services/sante_training.py", "HR_FLOOR", 40),
    ("app/services/sante_training.py", "HR_OVER_MAX", 10),
    ("app/services/sante_training.py", "RUN_MIN_HRR", 0.3),
    ("app/services/sante_training.py", "BY_TIME", {"WeightTraining", "Crossfit", "Workout", "Yoga", "Pilates"}),
    ("app/services/sante_training.py", "RATE_DAYS", 365),
    ("app/services/sante_training.py", "RATE_MIN", 5),
    ("app/services/sante_training.py", "RATE_DEFAULT", 1.2),
    ("app/services/sante.py", "INTENSITY_SPREAD", 0.1),
    ("app/services/sante.py", "INTENSITY_READ", 0.5),
]
MODULES = {"app/services/sante.py": sante, "app/services/sante_training.py": st, "app/services/nights.py": nt,
           "app/services/sante_sleep.py": sl, "app/services/race_prep.py": rp, "app/services/sante_score.py": sc,
           "app/services/sante_today.py": td, "app/services/sante_load.py": sante_load}
# owner, 2026-10-14: scientific basis — heuristics replaced by the rule-based state, the 8-h need and the one load
# pipeline: each one's removal is a decision too
GONE = [("app/services/sante_training.py", "EFFORT_RULES")] + [
    ("app/services/sante_score.py", name) for name in (
        "WEIGHTS", "HRV_FULL_Z", "HRV_ZERO_Z", "HR_FULL_BPM", "HR_MID_BPM", "HR_MID_SUB", "HR_ZERO_BPM",
        "SLEEP_SHARES", "CAP_JOINT", "JOINT_HR_BPM", "BANDS", "CAP_ILL", "CAP_RED", "RED_SUB", "CAP_SHORT",
        "CAP_NO_HEART", "CAP_RESP")] + [
    ("app/services/sante_sleep.py", name) for name in ("NEED_EFFORT", "NEED_DEBT_DAYS", "NEED_DEBT_MAX",
                                                       "NEED_STEP")] + [
    ("app/services/sante.py", name) for name in ("SUFFICIENT_SHARE", "USUAL_WEEKS", "USUAL_MIN_WEEKS",
                                                 "USUAL_SPREAD")]


@pytest.mark.parametrize("path,name,value", HEURISTICS)
def test_each_heuristic_constant_is_marked_h(path, name, value):
    assert getattr(MODULES[path], name) == value  # (H)
    line = next(ln for ln in (ROOT / path).read_text().splitlines()
                if re.match(rf"^[A-Z_, ]*\b{name}\b[A-Z_, ]*=", ln))
    assert "(H)" in line, line


@pytest.mark.parametrize("path,name", GONE)
def test_the_replaced_heuristics_are_gone(path, name):
    assert not hasattr(MODULES[path], name)
    assert not any(re.match(rf"^[A-Z_, ]*\b{name}\b[A-Z_, ]*=", ln)
                   for ln in (ROOT / path).read_text().splitlines()), name


def test_the_folds_print_no_h_and_no_citation():
    """v4.3: the method fold is a few plain bullets (one « Comment je calcule » since the audit, 2026-10-09, 5 at
    most, for the three dials, the stages and the margin) with no « (H) » and no citation (its sources are on
    /sante/sources); the (H) marks stay on the constants' lines (above) and in these tests."""
    assert len(sc.METHOD) <= 5 and not hasattr(sl, "METHOD")
    text = sc.flat(sc.METHOD)
    assert "(H)" not in text and not re.search(r"[A-Z][a-z]+ (19|20)\d\d", text), text
    assert "quelques points d'écart ne veulent rien dire" in sc.flat(sc.METHOD)
    assert not hasattr(st, "CLIMB_RATIO") and not hasattr(st, "back_to_back") and not hasattr(td, "HR_UP_BPM")
    # every measured night counts (owner, 2026-10-08): no ≥ 20-h tail out of the band any more; no Charge récente
    assert not hasattr(st, "ULTRA_TAIL") and not hasattr(st, "ULTRA_TAIL_MIN") and not hasattr(sc, "load_sub")


def test_the_late_session_gap_is_marked_h():
    from datetime import timedelta
    assert nt.LATE_SESSION_GAP == timedelta(hours=2)  # (H)
    line = next(ln for ln in (ROOT / "app/services/nights.py").read_text().splitlines()
                if ln.startswith("LATE_SESSION_GAP ="))
    assert "(H)" in line and "Stutz 2019" in line
    assert not hasattr(nt, "EVENING") and not hasattr(sc, "CAP_LOW_HRV") and not hasattr(sc, "place")


def test_the_heuristics_are_marked_h_where_they_are_explained():
    assert "(H)" in st.easy_runs.__doc__ and "(H)" in st.easy_model.__doc__
    assert "(H" in sl.habits.__doc__ and "(H)" in st.effort_of.__doc__ and "(H)" in st.effort_window.__doc__
    assert "(H)" in sante.__doc__ and "(H)" in td.__doc__ and "(H)" in sc.__doc__
    assert "(H)" in st.hr_readable.__doc__ and "(H)" in st.session_load.__doc__ and "(H)" in st.loads.__doc__
    assert "(H)" in st.load_family.__doc__ and "(H)" in sante.training.__doc__
