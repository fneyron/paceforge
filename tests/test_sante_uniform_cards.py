"""The shared readout preserves real measurements and phase totals."""
import json
from datetime import date, timedelta

import pytest

from app.services import sante, sante_sleep as sl, viz
from tests.test_sante_score import D, _nights, _rich, _session
from tests.test_viz import _env


def test_sleep_phase_geometry_preserves_total_and_a_missing_night():
    nights = _nights(_rich(days=5))
    nights[D].stages = {"awake": 10, "light": 240, "deep": 100, "rem": 80}
    del nights[D - timedelta(days=1)]
    c = sl.bars(nights, D, "14")
    bar = c["bars"][-1]
    assert sum(p["h"] for p in bar["phases"]) == pytest.approx(bar["h"], abs=.03)
    assert [p["key"] for p in bar["phases"]] == ["light", "deep", "rem"]
    assert c["bars"][-2]["miss"] and "phases" not in c["bars"][-2]
    assert c["targets"] is None and c["ref"] is None
    assert json.loads(c["data"])["selectionDates"][-1] == "7 → 8 oct."


def test_night_selection_dates_across_month_and_year():
    assert viz.selection_date(date(2026, 10, 1), night=True) == "30 sept. → 1 oct."
    assert viz.selection_date(date(2027, 1, 1), night=True) == "31 déc. → 1 janv."


def test_page_shows_charge_and_recent_activities_without_disclosure():
    sessions = [_session(D, 40, sid=2, name="Sortie récente"),
                _session(D - timedelta(days=7), 40, sid=1, name="Sortie ancienne")]
    p = sante._compute(_nights(_rich(), sessions), sessions, D, D, None, {}, True, 190, {})
    html = _env().get_template("partials/sante_page.html").render(page=p)
    assert [a["id"] for a in p["recent_activities"]] == [2]
    assert '<section id="charge-detail"' in html and 'href="/activity/2"' in html
    assert "Sortie ancienne" not in html
    assert 'class="pf-help-tip" role="tooltip"' in html
    assert "<small>30 jours</small>" not in html and "Repère de forme" not in html
    assert "repère 8h00" not in html and "Tendance 7 j ·" not in html
    assert "Mes horaires habituels" not in html
