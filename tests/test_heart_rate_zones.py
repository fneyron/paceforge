"""Zones must not turn missing data, an isolated peak or another athlete's HR into personalised limits."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.models.activity import Activity
from app.models.user import User
from app.services.heart_rate_zones import running_zones
from tests import test_coros
from tests.test_sante_score import D, _session
from tests.test_viz import render

as_user, no_commit = test_coros.as_user, test_coros.no_commit


def runs(*peaks):
    return [replace(_session(D - timedelta(days=i + 1), 45, sid=i + 1), hr_peak=peak)
            for i, peak in enumerate(peaks)]


def test_zones_use_corroborated_running_peaks_not_an_isolated_spike():
    sessions = runs(200, 198, 197, 225)
    zones = running_zones(sessions, D)
    assert zones["reference"] == 200
    assert zones["isolated_peak"] == 225
    assert zones["support_days"] == 3
    assert [(z["low"], z["high"]) for z in zones["rows"]] == [
        (100, 119), (120, 139), (140, 159), (160, 179), (180, 200)]
    assert running_zones(list(reversed(sessions)), D) == zones


def test_fractional_boundaries_cover_each_integer_bpm_once():
    zones = running_zones(runs(183, 181, 180), D)
    assert [(z["low"], z["high"]) for z in zones["rows"]] == [
        (92, 109), (110, 128), (129, 146), (147, 164), (165, 183)]


@pytest.mark.parametrize("sessions", [
    [], runs(None, None, None), runs(190, 189), runs(170, 180, 190),
    runs(190) * 3,  # three imports on the same day are not corroboration
    [replace(s, sport="Ride") for s in runs(210, 209, 208)],
    [replace(s, day=D + timedelta(days=1)) for s in runs(190, 189, 188)],
    [replace(s, day=s.day - timedelta(days=365)) for s in runs(190, 189, 188)],
    runs(float("nan"), float("inf"), 250),
    [replace(s, hr=200) for s in runs(190, 189, 188)],
    [replace(s, minutes=2) for s in runs(190, 189, 188)],
])
def test_insufficient_or_invalid_data_never_become_default_zones(sessions):
    zones = running_zones(sessions, D)
    assert not zones["ready"] and not zones["rows"] and zones["reference"] is None
    assert zones["message"]


def test_old_corroborated_peaks_are_not_presented_as_current():
    sessions = [replace(s, day=s.day - timedelta(days=100)) for s in runs(190, 189, 188)]
    zones = running_zones(sessions, D)
    assert not zones["ready"] and "plus de 3 mois" in zones["message"]


def test_card_exposes_the_reference_and_limits_without_a_fake_threshold():
    html = render('{% include "partials/heart_rate_zones.html" %}',
                  p={"hr_zones": running_zones(runs(200, 199, 198, 225), D)})
    assert html.count('class="pf-hr-zone"') == 5
    assert "Course à pied · Estimation" in html and "Base observée · 200 bpm" in html
    assert "225 bpm" in html and "Ton maximum réel peut être plus haut" in html
    empty = render('{% include "partials/heart_rate_zones.html" %}', p={"hr_zones": running_zones([], D)})
    assert "À compléter" in empty and 'class="pf-hr-zone"' not in empty


async def test_sante_loads_only_the_athletes_activities_and_renders_zones(as_user, db_session, test_user, monkeypatch):
    from app.services import sante

    async def today(*args, **kwargs):
        return D

    monkeypatch.setattr(sante, "athlete_today", today)
    other = User(firstname="Other")
    db_session.add(other)
    await db_session.flush()
    for uid, sessions in ((test_user.id, runs(183, 182, 181)), (other.id, runs(210, 209, 208))):
        for s in sessions:
            db_session.add(Activity(user_id=uid, sport_type="Run", name="Course", start_date=s.start,
                                    distance=7500, moving_time=2700, elapsed_time=2700,
                                    average_heartrate=140, max_heartrate=s.hr_peak, raw_data={}))
    await db_session.flush()
    response = await as_user.get("/sante")
    assert response.status_code == 200
    html = response.text
    assert 'id="zones-fc"' in html and "Base observée · 183 bpm" in html
    assert "Base observée · 210 bpm" not in html
    assert html.index('id="entrainement"') < html.index('id="zones-fc"')
