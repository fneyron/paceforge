"""Running zones require an athlete-confirmed maximum, never an imported peak."""
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
    assert "À renseigner" in empty and 'class="pf-hr-zone"' not in empty


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
    assert "À renseigner" in html and 'class="pf-hr-zone"' not in html
    assert "195 bpm" not in html and "210 bpm" not in html
    test_user.max_heart_rate = 180
    await db_session.flush()
    html = (await as_user.get("/sante")).text
    assert "FC maximale renseignée · 180 bpm" in html
    assert "195 bpm" not in html and "210 bpm" not in html
    assert html.index('id="entrainement"') < html.index('id="activites-recentes"') < html.index('id="zones-fc"')
