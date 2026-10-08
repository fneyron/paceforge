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
from app.services import nutrition as nu
from app.services import race_prep as rp
from app.services import sante
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
    # 2026-10-08 (« Fais comme WHOOP »): a window caps the score, no Charge sub-score any more
    ("app/services/sante_training.py", "EFFORT_RULES", {"ultra": ((3, 35), (10, 65)),  # v4.3: 35 (H)
                                                       "very_long": ((2, 45), (5, 65)),
                                                       "long": ((3, 65),)}),
    # v4.2 (research_efforts.md §b): « fenêtres indicatives », no official body gives days (Kellmann 2018)
    ("app/services/sante_training.py", "LONG_RACE_LAST", 5),
    # v4.4 (research_data.md R3): a Longue run like a race, whoever measured it (session RPE: Foster 2001)
    ("app/services/sante_training.py", "INTENSE_RPE", 8),
    ("app/services/sante_training.py", "ULTRA_LONG_MIN", 1440),
    ("app/services/sante_training.py", "ULTRA_LONG_LAST", 13),
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
    # v4.2 (research_recovery.md §3.2): the weights, Charge only in a window, the FC de nuit anchors (Alavi 2022;
    # Bosquet 2008), the joint VFC/FC cap (Buchheit 2014, Table 2)
    ("app/services/sante_score.py", "WEIGHTS", {"hrv": 25, "hr": 25, "sleep": 30}),  # 2026-10-08: no Charge
    ("app/services/sante_score.py", "HRV_FULL_Z", -0.5),
    ("app/services/sante_score.py", "HRV_ZERO_Z", -2.5),
    ("app/services/sante_score.py", "HR_FULL_BPM", 2),
    ("app/services/sante_score.py", "HR_MID_BPM", 5),
    ("app/services/sante_score.py", "HR_MID_SUB", 40),
    ("app/services/sante_score.py", "HR_ZERO_BPM", 8),
    # 2026-10-09: the 24 h against the day's need (4 h, 6 h, 7 h for 8 h), the debt in the need
    ("app/services/sante_score.py", "SLEEP_SHARES", ((0.5, 0), (0.75, 60), (0.875, 100))),
    ("app/services/sante_score.py", "CAP_JOINT", 69),
    ("app/services/sante_score.py", "JOINT_HR_BPM", 3),
    ("app/services/sante_score.py", "BANDS", {"ok": (70, 100), "warn": (40, 69), "danger": (0, 39)}),
    # the bands' SDs shrunk towards a prior (Kellmann 2018's Bayesian individualisation; Mishica 2022)
    ("app/services/nights.py", "PRIOR_NIGHTS", 7),
    ("app/services/nights.py", "HRV_SD_PRIOR", 0.10),
    ("app/services/nights.py", "HR_SD_PRIOR", 0.04),
    # §8 (after the visual judges): the state is the band of the score, the caps decide what it can reach
    ("app/services/sante_score.py", "CAP_ILL", 39),
    ("app/services/sante_score.py", "CAP_RED", 69),
    ("app/services/sante_score.py", "RED_SUB", 40),
    ("app/services/sante_score.py", "CAP_SHORT", 65),
    ("app/services/sante_score.py", "CAP_NO_HEART", 80),
    # 2026-10-09 (owner: « utilise la respiration aussi si tu l'as »): the breathing rate's line and its cap
    ("app/services/sante_score.py", "CAP_RESP", 69),
    ("app/services/nights.py", "RESP_UP_MIN", 1.0),
    ("app/services/nights.py", "RESP_UP_SD", 2),
    ("app/services/nights.py", "RESP_SD_PRIOR", 0.5),
    # « sortie intense le soir »: only a vigorous session close to sleep (Stutz 2019; Myllymäki 2012)
    ("app/services/nights.py", "VIGOROUS_HRR", 0.8),
    ("app/services/nights.py", "VIGOROUS_MIN", 20),
    ("app/services/sante_sleep.py", "USUAL_NIGHTS", 5),
    # 2026-10-09 (owner: « Ne demande pas, ce doit être auto comme WHOOP »): the sleep need, its base, its additions
    ("app/services/sante_sleep.py", "NEED_DEFAULT", 480),
    ("app/services/sante_sleep.py", "NEED_EFFORT", 30),
    ("app/services/sante_sleep.py", "NEED_DEBT_DAYS", 7),
    ("app/services/sante_sleep.py", "NEED_DEBT_MAX", 60),
    ("app/services/sante_sleep.py", "NEED_STEP", 10),
    ("app/services/sante.py", "SUFFICIENT_SHARE", 7 / 8),
    # 2026-10-08 (« Fais comme WHOOP »): the Entraînement dial's usual week and its « comme d'habitude »
    ("app/services/sante.py", "USUAL_WEEKS", 11),
    ("app/services/sante.py", "USUAL_MIN_WEEKS", 4),
    ("app/services/sante.py", "USUAL_SPREAD", 0.2),
    # 2026-10-09 (owner, on the mockup: « Oui vas-y »): the water to carry to the next ravito, inside ISSN 2019's
    # 450–750 ml/h, rounded up to half a litre, and the hot race that raises it and says its sodium
    ("app/services/nutrition.py", "WATER_ML_H", 500),
    ("app/services/nutrition.py", "WATER_HOT_ML_H", 750),
    ("app/services/nutrition.py", "WATER_STEP_ML", 500),
    ("app/services/nutrition.py", "WATER_MIN_ML", 500),
    ("app/services/nutrition.py", "HOT_RACE_C", 25),
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
           "app/services/sante_today.py": td, "app/services/nutrition.py": nu}


@pytest.mark.parametrize("path,name,value", HEURISTICS)
def test_each_heuristic_constant_is_marked_h(path, name, value):
    assert getattr(MODULES[path], name) == value  # (H)
    line = next(ln for ln in (ROOT / path).read_text().splitlines()
                if re.match(rf"^[A-Z_, ]*\b{name}\b[A-Z_, ]*=", ln))
    assert "(H)" in line, line


def test_the_folds_print_no_h_and_no_citation():
    """v4.3: the two folds are a few plain bullets (« Comment je calcule ta récupération » 8 at most since v4.4
    explains VFC, FC de nuit and the percentages, « Comment je lis tes nuits » 5 at most) with no « (H) » and no
    citation (their
    sources are on /sante/sources); the (H) marks stay on the constants' lines (above) and in these tests."""
    assert len(sc.METHOD) <= 8 and len(sl.METHOD) <= 5
    for text in (sc.flat(sc.METHOD), sc.flat(sl.METHOD)):
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
