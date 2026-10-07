"""SANTE_DECISIONS: « thresholds not from a study are marked (H) in code
comments and tests ». Each heuristic constant below is (H) on the line that
sets it, and its value is pinned here (H) so a change is a decision."""
import re
from pathlib import Path

import pytest

from app.services import nights as nt
from app.services import race_prep as rp
from app.services import sante
from app.services import sante_score as sc
from app.services import sante_sleep as sl
from app.services import sante_training as st

ROOT = Path(__file__).resolve().parent.parent

# (module file, constant, value) — every one a PaceForge heuristic (H)
HEURISTICS = [
    ("app/services/sante.py", "USUAL_NIGHTS", 5),
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
    ("app/services/nights.py", "REPRISE_PRE_DAYS", 14),
    ("app/services/nights.py", "REPRISE_EASY_BPM", 3),
    ("app/services/sante_sleep.py", "YEAR_OLD_NIGHTS", 30),
    ("app/services/sante_sleep.py", "YEAR_WEEK_NIGHTS", 3),
    ("app/services/race_prep.py", "HOT_FACTOR", 1.06),
    ("app/services/nights.py", "MIN_PROVISIONAL_NIGHTS", 7),
    # « Forme du jour » (SCORE_SPEC.md): every weight, threshold and cap
    ("app/services/sante_score.py", "WEIGHTS", {"hrv": 30, "hr": 25, "sleep": 20, "load": 15, "feel": 10}),
    ("app/services/sante_score.py", "HRV_FULL_Z", -0.5),
    ("app/services/sante_score.py", "HRV_ZERO_Z", -2.5),
    ("app/services/sante_score.py", "HR_FULL_BPM", 2),
    ("app/services/sante_score.py", "HR_ZERO_BPM", 10),
    ("app/services/sante_score.py", "SLEEP_POINTS", ((240, 0), (360, 60), (420, 100))),
    ("app/services/sante_score.py", "SLEEP_DEBT", 2 / 3),
    ("app/services/sante_score.py", "LOAD_BIG", 50),
    ("app/services/sante_score.py", "LOAD_HUGE", 30),
    ("app/services/sante_score.py", "HUGE_MIN", 360),
    ("app/services/sante_score.py", "MIN_LOAD_SESSIONS", 6),
    ("app/services/sante_score.py", "FEEL_BASE", {1: 100, 2: 85, 3: 50}),
    ("app/services/sante_score.py", "FEEL_WHY", 10),
    ("app/services/sante_score.py", "FEEL_SICK", 10),
    ("app/services/sante_score.py", "FEEL_ALCOHOL", 15),
    ("app/services/sante_score.py", "CAP_REPRISE", 60),
    ("app/services/sante_score.py", "CAP_POST_EARLY", 40),
    ("app/services/sante_score.py", "CAP_POST", 65),
    ("app/services/sante_score.py", "CAP_LOW_HRV", 60),
    ("app/services/sante_score.py", "CAP_SHORT", 65),
    ("app/services/sante_score.py", "POST_EARLY_DAYS", 3),
]
MODULES = {"app/services/sante.py": sante, "app/services/sante_training.py": st, "app/services/nights.py": nt,
           "app/services/sante_sleep.py": sl, "app/services/race_prep.py": rp, "app/services/sante_score.py": sc}


@pytest.mark.parametrize("path,name,value", HEURISTICS)
def test_each_heuristic_constant_is_marked_h(path, name, value):
    assert getattr(MODULES[path], name) == value  # (H)
    line = next(ln for ln in (ROOT / path).read_text().splitlines()
                if re.match(rf"^[A-Z_, ]*\b{name}\b[A-Z_, ]*=", ln))
    assert "(H)" in line, line


def test_the_score_method_marks_its_heuristics():
    """The fold says the weights, thresholds, caps and the provisional normal are (H)."""
    text = " ".join(sc.METHOD)
    assert text.count("(H)") >= 6 and "Les poids (H)" in text and "Plafonds (H)" in text and "14 nuits (H)" in text
    assert "Une différence de quelques points ne veut rien dire" in text


def test_the_easy_run_filters_are_marked_h_where_they_are_explained():
    assert "(H)" in st.easy_runs.__doc__ and "(H)" in st.easy_model.__doc__
    assert "(H" in sante._usual.__doc__
