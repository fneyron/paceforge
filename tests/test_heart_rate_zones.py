"""Confirmed maximum wins; automatic zones need detailed support on independent days."""
from dataclasses import replace
from datetime import timedelta

import pytest

from app.models.activity import Activity
from app.models.user import User
from app.services.heart_rate_zones import observed_reference, running_zones
from tests import test_coros
from tests.test_sante_score import D, _session
from tests.test_viz import render

as_user, no_commit = test_coros.as_user, test_coros.no_commit


@pytest.mark.parametrize("value", [None, 0, 99, 231, 180.5, float("nan"), float("inf")])
def test_missing_or_invalid_maximum_never_becomes_default_zones(value):
    zones = running_zones(value)
    assert not zones["ready"] and not zones["rows"] and zones["reference"] is None
    assert zones["message"]


def test_confirmed_maximum_defines_all_five_zones_without_overlap():
    zones = running_zones(183)
    assert zones["reference"] == 183
    assert [(z["low"], z["high"]) for z in zones["rows"]] == [
        (92, 109), (110, 128), (129, 146), (147, 164), (165, 183)]


def test_card_explains_the_personal_reference_and_offers_a_setting():
    html = render('{% include "partials/heart_rate_zones.html" %}', p={"hr_zones": running_zones(180)})
    assert html.count('class="pf-hr-zone"') == 5
    assert 'class="pf-health-panel pf-hr-zones"' in html
    assert "FC maximale renseignée · 180 bpm" in html and 'href="/settings#max-heart-rate"' in html
    empty = render('{% include "partials/heart_rate_zones.html" %}', p={"hr_zones": running_zones(None)})
    assert "Données insuffisantes" in empty and 'class="pf-hr-zone"' not in empty


def supported_run(days=1, peak=179, **kw):
    return replace(_session(D - timedelta(days=days), 45, sid=days), hr_peak=peak,
                   segs=((5, 140), (4, peak - 5), (4, peak - 6)), **kw)


def test_sustained_passages_corroborate_peaks_without_turning_summary_spikes_into_a_maximum():
    runs = [supported_run(1, 179), supported_run(3, 178)]
    runs += [replace(supported_run(day, 195), segs=((5, 140), (4, 171), (4, 174))) for day in (4, 5, 6)]
    zones = running_zones(None, runs, D)
    assert zones["estimated"] and zones["reference"] == 179
    assert [r["id"] for r in zones["evidence"]] == [1, 3]
    assert running_zones(183, runs, D)["reference"] == 183
    assert not running_zones(183, runs, D)["estimated"]
    html = render('{% include "partials/heart_rate_zones.html" %}', p={"hr_zones": zones})
    assert "Estimation" in html and "Repère observé · 179 bpm" in html
    assert "2 sorties concordantes" in html and "ta FC maximale réelle peut être plus haute" in html
    assert 'href="/activity/1"' in html and 'href="/activity/3"' in html
    assert "FC maximale renseignée" not in html


@pytest.mark.parametrize("change", [
    {"segs": ()}, {"segs": ((10, 175),)},  # no detail or a single high mean
    {"segs": ((.5, 175), (.5, 175), (12, 140))},  # too little corroborating duration
    {"segs": ((2, 175), (2, 175))},  # too little overall coverage
    {"segs": ((10, 175),) * 6},  # duplicated/overlapping coverage
    {"segs": ((5, 140), (4, float("nan")), (4, float("inf")))},
    {"segs": ((5, 140), (4, 200), (4, 200))},  # averages above the recorded peak
    {"hr": None}, {"hr": 200}, {"hr": float("nan")},
    {"hr_peak": float("nan")}, {"hr_peak": float("inf")}, {"hr_peak": 231},
    {"minutes": 9}, {"sport": "Ride"},
])
def test_unsupported_or_incoherent_second_outing_does_not_complete_a_reference(change):
    runs = [supported_run(1), replace(supported_run(2), **change)]
    assert observed_reference(runs, D) is None


def test_independent_days_recent_completed_and_same_sport_are_required():
    run = supported_run(1)
    assert observed_reference([run, replace(run, id=10)], D) is None
    assert observed_reference([supported_run(91), supported_run(92)], D) is None
    assert observed_reference([supported_run(366), run], D) is None
    assert observed_reference([supported_run(-1), run], D) is None
    assert observed_reference([replace(supported_run(0), elapsed=1500), run], D) is None
    assert observed_reference([supported_run(2, 190), run], D) is None
    assert observed_reference([supported_run(2, sport="TrailRun"), run], D)["reference"] == 179


def test_single_supported_spike_is_not_selected_over_two_concordant_days():
    result = observed_reference([supported_run(1, 195), supported_run(2, 179), supported_run(3, 178)], D)
    assert result["reference"] == 179 and len(result["evidence"]) == 2


async def test_repeated_peaks_cannot_replace_the_athletes_confirmed_maximum(
        as_user, db_session, test_user, monkeypatch):
    from app.services import sante

    async def today(*args, **kwargs):
        return D

    monkeypatch.setattr(sante, "athlete_today", today)
    other = User(firstname="Other", max_heart_rate=210)
    db_session.add(other)
    await db_session.flush()
    for i in range(3):
        s = replace(_session(D - timedelta(days=i + 1), 45, sid=i + 1), hr_peak=195)
        db_session.add(Activity(user_id=test_user.id, sport_type="Run", name="Course", start_date=s.start,
                               distance=7500, moving_time=2700, elapsed_time=2700,
                               average_heartrate=140, max_heartrate=195, raw_data={}))
    await db_session.flush()
    html = (await as_user.get("/sante")).text
    assert "Données insuffisantes" in html and 'class="pf-hr-zone"' not in html
    assert "195 bpm" not in html and "210 bpm" not in html
    test_user.max_heart_rate = 180
    await db_session.flush()
    html = (await as_user.get("/sante")).text
    assert "FC maximale renseignée · 180 bpm" in html
    assert "195 bpm" not in html and "210 bpm" not in html
    assert html.index('id="entrainement"') < html.index('id="activites-recentes"') < html.index('id="zones-fc"')


async def test_dashboard_uses_detailed_activities_automatically_without_writing_a_confirmed_maximum(
        as_user, db_session, test_user, monkeypatch):
    from app.services import sante
    from app.services import sante_training as st

    async def today(*args, **kwargs):
        return D

    monkeypatch.setattr(sante, "athlete_today", today)
    for days, peak in ((1, 179), (3, 178), (4, 195), (5, 195)):
        s = supported_run(days, peak)
        detail = (140, 174, 173) if peak < 190 else (140, 171, 174)
        db_session.add(Activity(user_id=test_user.id, sport_type="Run", name="Course", start_date=s.start,
                               distance=7500, moving_time=2700, elapsed_time=2700,
                               average_heartrate=140, max_heartrate=peak, raw_data={},
                               laps=[{"moving_time": t * 60, "average_heartrate": hr}
                                     for t, hr in zip((5, 4, 4), detail, strict=True)]))
    await db_session.flush()
    st._CACHE.clear()
    html = (await as_user.get("/sante")).text
    assert "Repère observé · 179 bpm" in html and "2 sorties concordantes" in html
    assert 'class="pf-hr-zone"' in html and "195 bpm" not in html
    assert test_user.max_heart_rate is None
