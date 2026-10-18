"""Santé's Tendances (owner, 2026-10-16: « les tendances sur poids fc pas »): 90 days as dots, the trailing mean as a
line, its change over 4 weeks; FC sur 24 h, stress and the phone's 24-h breathing left the page."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.health import HealthMetric
from app.models.mobile import MobileDaily
from app.services import sante
from app.services import sante_trends as tr
from tests.test_sante_score import D, _nights, _rich
from tests.test_viz import _env

TODAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize(("offsets", "expected"), [
    ([0], 14), ([0, 2, 4, 7], 14), ([0, 11], 14), ([0, 12], 30),
    ([0, 27], 30), ([0, 28], 90), ([0, 84], 90), ([80, 84], 90),
])
def test_sparse_trends_zoom_without_hiding_points_or_their_age(offsets, expected):
    days = tr.days_to(TODAY)
    values = [70.0 if (TODAY - d).days in offsets else None for d in days]
    means, counts = tr.trailing(values, 14, 2)
    c = tr.chart("weight", "zoom", "Poids", days, values, means, counts,
                 unit="kg", digits=1, min_span=2)
    payload = json.loads(c["data"])
    assert c["n"] == expected and len(c["dots"]) == len(offsets)
    assert payload["d"][-1] == TODAY.isoformat()
    assert payload["d"][0] == (TODAY - timedelta(days=expected - 1)).isoformat()
    assert payload["sel"] == len(payload["d"])
    assert c["read"][:2] == ["70,0\u00a0kg", "en moyenne"]
    assert f"{expected} jours" in c["period_label"]


def test_zoom_preserves_the_mean_and_aligned_readouts():
    days = tr.days_to(TODAY)
    values = [None] * 86 + [70, None, 72, 74]
    means, counts = tr.trailing(values, 14, 2)
    c = tr.chart("weight", "zoom", "Poids", days, values, means, counts,
                 unit="kg", digits=1, min_span=2, sources=["Balance"] * 90,
                 notes=[""] * 89 + ["mesure récente"], mean_sources=[None] * 90)
    payload = json.loads(c["data"])
    assert c["n"] == 14 and len(payload["r"]) == 14
    assert "72,0" in payload["r"][-1][3]  # the original 14-day mean
    assert "3 pesées" in payload["r"][-1][3] and "mesure récente" in payload["r"][-1][3]
    assert means[-1] == 72 and len(means) == 90  # drawing never mutates the calculation inputs


def test_the_trailing_mean_needs_its_values_and_never_reads_a_day_in_progress():
    means, counts = tr.trailing([1, None, 3, None, 5], window=3, need=2)
    assert means == [None, None, 2, None, 4] and counts == [1, 1, 2, 1, 2]
    means, counts = tr.trailing([8000, 8000, 8000, 8000, 1200], window=7, need=4, skip=4)
    assert means[-1] == 8000 and counts[-1] == 4


def test_the_change_reads_the_mean_four_weeks_before_or_nothing():
    means = [None] * 60 + [71.15] + [None] * 27 + [70.75]
    assert tr.delta(means, 1, "kg") == "\u22120,4\u202fkg en 4 sem."
    assert tr.delta([None] * 61 + [None] * 27 + [70.75], 1, "kg") is None
    assert tr.delta([70.0] * 29, 1, "kg") == "0,0 kg en 4 sem."  # never « −0,0 »
    assert tr.delta([44.0] + [None] * 27 + [48.0], 0, "bpm") == "+4 bpm en 4 sem."


async def _weigh_ins_and_steps(db, user):
    for k in range(13):  # a weigh-in a week, 100 g lighter each one
        at = datetime.combine(TODAY - timedelta(days=7 * k), datetime.min.time(), timezone.utc) + timedelta(hours=6)
        db.add(MobileDaily(user_id=user.id, platform="ios", date=at.date(), metric="weight", value=70.7 + k / 10,
                           sources=["Balance"], measured_at=at))
        db.add(HealthMetric(user_id=user.id, date=at.date(), metric="body_fat", value=18.0, source="Garmin",
                            details={}))
    for k in range(90):
        db.add(MobileDaily(user_id=user.id, platform="ios", date=TODAY - timedelta(days=k), metric="steps",
                           value=1200 if k == 0 else 8000, sources=["iPhone"]))
    for k in range(5):  # the cards that left the page
        db.add(HealthMetric(user_id=user.id, date=TODAY - timedelta(days=k), metric="hr_day", value=62,
                            source="Garmin", details={}))
        db.add(HealthMetric(user_id=user.id, date=TODAY - timedelta(days=k), metric="stress", value=30,
                            source="Garmin", details={}))
        db.add(MobileDaily(user_id=user.id, platform="ios", date=TODAY - timedelta(days=k), metric="resp_day",
                           value=15.5, sources=["Apple Watch"]))
    await db.flush()


async def test_the_page_keeps_weight_and_body_fat_without_steps(db_session, test_user):
    await _weigh_ins_and_steps(db_session, test_user)
    p = await sante.health_page(db_session, test_user.id, today=TODAY, now=NOW)
    trends = {t["key"]: t for t in p["trends"]}
    assert list(trends) == ["weight", "body_fat"]
    weight = trends["weight"]
    assert (weight["title"], weight["value"], weight["chart"]["n"]) == ("Poids", "70,7 kg", 90)
    assert weight["chart"]["legend"] == [("is-dot", "pesée"), ("is-mean", "moyenne glissante")]
    assert weight["delta"] == "−0,4 kg en 4 sem."
    assert len(weight["chart"]["dots"]) == 13
    assert weight["chart"]["mean"].count("M") == 1  # a weekly weigh-in: one line, never a dash a week
    assert trends["body_fat"]["delta"] == "0,0 % en 4 sem."
    html = _env().get_template("partials/sante_page.html").render(page=p)
    assert 'id="tendances"' in html and "Au quotidien" not in html
    assert 'id="tendance-steps"' not in html
    for gone in ("FC sur 24 h", "Stress", "Respiration sur 24 h", "pf-measurement-insight"):
        assert gone not in html, gone
    # the explanations are a fold at the bottom, after the charts
    assert html.index('id="tendances"') < html.index('id="comprendre-indicateurs"')
    items = {i["key"]: i for i in p["explanations"]["items"]}
    assert items["weight"]["chart_key"] == "trend-weight" and "14 jours" in " ".join(items["weight"]["details"])


async def test_a_typed_weight_stays_a_value_without_a_chart(db_session, test_user):
    test_user.weight_kg, test_user.weight_set_at = 71.0, None
    await db_session.flush()
    p = await sante.health_page(db_session, test_user.id, today=TODAY, now=NOW)
    weight = next(t for t in p["trends"] if t["key"] == "weight")
    assert (weight["title"], weight["value"], weight["chart"]) == ("Poids du profil", "71,0 kg", None)
    html = _env().get_template("partials/sante_trends.html").render(page=p)
    assert "71,0 kg" in html and "Valeur du profil" in html


def test_night_hr_has_one_curve_without_a_duplicate_trend():
    p = sante._compute(_nights(_rich()), [], D, D, None, {}, True, 190, {})
    assert p["fc"] and "hr_trend" not in p
