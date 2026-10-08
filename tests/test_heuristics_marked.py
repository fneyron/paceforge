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
from app.services import sante_score as sc
from app.services import sante_sleep as sl
from app.services import sante_today as td
from app.services import sante_training as st

ROOT = Path(__file__).resolve().parent.parent

# (module file, constant, value) — every one a PaceForge heuristic (H)
HEURISTICS = [
    ("app/services/sante_training.py", "MIN_FIT_RUNS", 6),
    ("app/services/sante_training.py", "EASY_FIT_DAYS", 365),
    ("app/services/sante_training.py", "MIN_HISTORY_DAYS", 42),
    ("app/services/sante_training.py", "CTL_DAYS", 42),
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
    ("app/services/race_prep.py", "HOT_FACTOR", 1.06),
    ("app/services/nights.py", "MIN_PROVISIONAL_NIGHTS", 7),
    # Santé v4 (SANTE_V4_SPEC.md): the effort classes, their windows and Charge, the score, the rings, the habits
    ("app/services/sante_training.py", "EFFORT_ULTRA", 600),
    ("app/services/sante_training.py", "EFFORT_VERY_LONG", 360),
    ("app/services/sante_training.py", "EFFORT_LONG", 180),
    ("app/services/sante_training.py", "EFFORT_DPLUS", 1500),
    ("app/services/sante_training.py", "STOPPED_WATCH", 2),
    ("app/services/sante_training.py", "EFFORT_RULES", {"ultra": (((3, 35), (10, 65)), 20),  # v4.3: 35 (H)
                                                       "very_long": (((2, 45), (5, 65)), 30),
                                                       "long": (((3, 65),), 50)}),
    # v4.2 (research_efforts.md §b): « fenêtres indicatives », no official body gives days (Kellmann 2018)
    ("app/services/sante_training.py", "LONG_RACE_LAST", 5),
    ("app/services/sante_training.py", "ULTRA_LONG_MIN", 1440),
    ("app/services/sante_training.py", "ULTRA_LONG_LAST", 13),
    ("app/services/sante_training.py", "NIGHT_SPAN", (time(1), time(5))),
    ("app/services/sante_training.py", "LOWER", {"ultra": "very_long", "very_long": "long", "long": None}),
    ("app/services/sante_training.py", "NIGHT_TAGS", {"long": ("long", 1), "very_long": ("big", 3),
                                                     "ultra": ("ultra", 4)}),
    ("app/services/sante_training.py", "ULTRA_TAIL_MIN", 1200),
    ("app/services/sante_training.py", "ULTRA_TAIL", (5, 7)),
    ("app/services/sante_training.py", "AFTER_ULTRA_DAYS", 21),
    # v4.2 sleep (research_sleep.md): the stages to 10 min, the late-nap cut-off (Mograss 2022; Walsh 2021)
    ("app/services/sante_sleep.py", "STAGE_STEP", 10),
    ("app/services/nights.py", "LATE_NAP_H", 7),
    ("app/services/nights.py", "NAP_LATEST", time(16)),
    ("app/services/nights.py", "USUAL_BED_NIGHTS", 5),
    ("app/services/nights.py", "USUAL_BED_DAYS", 28),
    # v4.2 (research_recovery.md §3.2): the weights, Charge only in a window, the FC de nuit anchors (Alavi 2022;
    # Bosquet 2008), the joint VFC/FC cap (Buchheit 2014, Table 2)
    ("app/services/sante_score.py", "WEIGHTS", {"hrv": 25, "hr": 25, "sleep": 30, "load": 20}),
    ("app/services/sante_score.py", "HRV_FULL_Z", -0.5),
    ("app/services/sante_score.py", "HRV_ZERO_Z", -2.5),
    ("app/services/sante_score.py", "HR_FULL_BPM", 2),
    ("app/services/sante_score.py", "HR_MID_BPM", 5),
    ("app/services/sante_score.py", "HR_MID_SUB", 40),
    ("app/services/sante_score.py", "HR_ZERO_BPM", 8),
    ("app/services/sante_score.py", "SLEEP_POINTS", ((240, 0), (360, 60), (420, 100))),
    ("app/services/sante_score.py", "SLEEP_DEBT", 2 / 3),
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
    # « sortie intense le soir »: only a vigorous session close to sleep (Stutz 2019; Myllymäki 2012)
    ("app/services/nights.py", "VIGOROUS_HRR", 0.8),
    ("app/services/nights.py", "VIGOROUS_MIN", 20),
    ("app/services/sante.py", "SLEEP_FULL", 480),
    ("app/services/sante.py", "USUAL_WEEKS", 12),
    ("app/services/sante_sleep.py", "USUAL_NIGHTS", 5),
]
MODULES = {"app/services/sante.py": sante, "app/services/sante_training.py": st, "app/services/nights.py": nt,
           "app/services/sante_sleep.py": sl, "app/services/race_prep.py": rp, "app/services/sante_score.py": sc,
           "app/services/sante_today.py": td}


@pytest.mark.parametrize("path,name,value", HEURISTICS)
def test_each_heuristic_constant_is_marked_h(path, name, value):
    assert getattr(MODULES[path], name) == value  # (H)
    line = next(ln for ln in (ROOT / path).read_text().splitlines()
                if re.match(rf"^[A-Z_, ]*\b{name}\b[A-Z_, ]*=", ln))
    assert "(H)" in line, line


def test_the_folds_print_no_h_and_no_citation():
    """v4.3: the two folds are a few plain bullets (« Comment je calcule ta récupération » 6 at most, « Comment je
    lis tes nuits » 5 at most) with no « (H) » and no citation (their sources are on /sante/sources); the (H)
    marks stay on the constants' lines (above) and in these tests."""
    assert len(sc.METHOD) <= 6 and len(sl.METHOD) <= 5
    for text in (sc.flat(sc.METHOD), sc.flat(sl.METHOD)):
        assert "(H)" not in text and not re.search(r"[A-Z][a-z]+ (19|20)\d\d", text), text
    assert "quelques points d'écart ne veulent rien dire" in sc.flat(sc.METHOD)
    assert not hasattr(st, "CLIMB_RATIO") and not hasattr(st, "back_to_back") and not hasattr(td, "HR_UP_BPM")


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
