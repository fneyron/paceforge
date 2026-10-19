"""One dated personal reference for the chart, trend and index, from its first usable day."""
import math
from datetime import timedelta

import pytest

from app.services import nights as nt
from app.services import sante
from app.services import sante_score as sc
from tests.test_sante_score import D, _history, _nights, night_rows


@pytest.mark.parametrize("count, expected, remaining", [(6, 0, 9), (14, 6, 1), (15, 7, 0), (22, 14, 0)])
def test_reference_initialisation_never_uses_the_assessed_week(count, expected, remaining):
    nights = _nights(night_rows(range(count), hrv=lambda k: 90 if k >= 8 else 60))
    with nt.memo():
        nt.freeze(nights)
        day = sante._assess(nights, [], [], D)
        for metric in ("hrv", "hr"):
            band = nt.normal(nights, metric, D)
            trend = nt.trend(nights, metric, D)
            card = sante._night_card(nights, metric, D, day)
            assert nt.drawn_band(nights, metric, D) == band == trend["baseline"]
            assert day["stats"][metric]["normal"] == band
            assert nt.reference_count(nights, metric, D) == card["reference"]["n"] == expected
            assert nt.reference_remaining(nights, metric, D) == card["reference"]["remaining"] == remaining
            assert card["reference"]["ready"] == (count >= 15)
            assert card["reference"]["provisional"] == (15 <= count < 22)
            if band:
                assert band["until"] == D - timedelta(days=7)
                assert band["source"] == trend["source"]
                part = next(p for p in day["score"]["parts"] if p["key"] == metric)
                assert part["centre"] == band["center"]
        assert (day["score"]["value"] is None) == (count < 15)


@pytest.mark.parametrize("metric", ["hr", "hrv"])
def test_fourteenth_reference_night_changes_coverage_without_changing_the_reference_policy(metric):
    # The first 14 reference observations are identical; only their count and dispersion estimate evolve.
    # At the 14th, the centre must not jump to a differently selected recent or historical population.
    rows = night_rows(range(23), hr=lambda k: 45 if k >= 8 else 50,
                      hrv=lambda k: 90 if k >= 8 else 60)
    nights = _nights(rows)
    yesterday = D - timedelta(days=1)
    before = nt.drawn_band(nights, metric, yesterday - timedelta(days=1))
    after = nt.drawn_band(nights, metric, yesterday)
    assert (before["n"], after["n"]) == (13, 14)
    assert before["provisional"] and not after["provisional"]
    assert before["center"] == pytest.approx(after["center"])
    prior = nt.HRV_SD_PRIOR if metric == "hrv" else nt.HR_SD_PRIOR * 45
    for reference in (before, after):
        assert reference["sd"] == pytest.approx(math.sqrt(7 * prior**2 / (reference["n"] + 7)))
    assert nt.trend(nights, metric, yesterday - timedelta(days=1))["baseline"] == before
    assert nt.trend(nights, metric, yesterday)["baseline"] == after


def test_new_nights_cannot_rewrite_a_dated_reference_or_its_chart():
    rows = night_rows(range(32), hr=lambda k: 44 + k % 3, hrv=lambda k: 65 + k % 11)
    original = _nights(rows)
    future_rows = night_rows(range(-10, 0), hr=90, hrv=20)
    combined = {m: {**rows[m], **future_rows[m]} for m in rows}
    future = _nights(combined)
    for day in (D - timedelta(days=13), D):
        before = sante._assess(original, [], [], day)
        after = sante._assess(future, [], [], day)
        assert before["score"] == after["score"]
        for metric in ("hr", "hrv"):
            assert nt.normal(original, metric, day) == nt.normal(future, metric, day)
            assert nt.reference_remaining(original, metric, day) == nt.reference_remaining(future, metric, day)
            assert sante._night_card(original, metric, day, before) == sante._night_card(future, metric, day, after)


def test_each_history_bar_uses_the_reference_that_day_could_have_used():
    rows = night_rows(range(28), hr=lambda k: 41 if k < 7 else 36,
                      hrv=lambda k: 65 if k < 7 else 88)
    history = _history(rows)
    for day, _, score in history:
        cut = {metric: {d: row for d, row in by_day.items() if d <= day} for metric, by_day in rows.items()}
        nights = _nights(cut, today=day)
        assessed = sante._assess(nights, [], [], day)
        assert score == assessed["score"]
        assert sc.quality(assessed)["provisional"] == (day < D - timedelta(days=6))


def test_explicit_source_progress_cannot_borrow_another_methods_reference():
    nights = _nights(night_rows(range(60)))
    for k in range(3):
        n = nights[D - timedelta(days=k)]
        n.hrv_source = "Garmin (sommeil)"
    assert nt.reference_count(nights, "hrv", D, "Garmin (sommeil)") == 0
    assert nt.reference_count(nights, "hrv", D, "Garmin") == 52


def test_late_effort_context_never_invents_a_cardiac_reference():
    from tests.test_nights import session

    run = session(D, 21, 40, hr=180)
    assert not nt.vigorous(run, rest=45, peak=None)
    assert nt.vigorous(run, rest=45, peak=186)
