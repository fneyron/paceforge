"""SANTE_DECISIONS: « thresholds not from a study are marked (H) in code
comments and tests ». Each heuristic constant below is (H) on the line that
sets it, and its value is pinned here (H) so a change is a decision."""
import re
from pathlib import Path

import pytest

from app.services import nights as nt
from app.services import race_prep as rp
from app.services import sante
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
]
MODULES = {"app/services/sante.py": sante, "app/services/sante_training.py": st, "app/services/nights.py": nt,
           "app/services/sante_sleep.py": sl, "app/services/race_prep.py": rp}


@pytest.mark.parametrize("path,name,value", HEURISTICS)
def test_each_heuristic_constant_is_marked_h(path, name, value):
    assert getattr(MODULES[path], name) == value  # (H)
    line = next(ln for ln in (ROOT / path).read_text().splitlines()
                if re.match(rf"^[A-Z_, ]*\b{name}\b[A-Z_, ]*=", ln))
    assert "(H)" in line, line


def test_the_easy_run_filters_are_marked_h_where_they_are_explained():
    assert "(H)" in st.easy_runs.__doc__ and "(H)" in st.easy_model.__doc__
    assert "(H" in sante._usual.__doc__
