"""Santé's Tendances (owner, 2026-10-16: « les tendances sur poids fc pas »): 90 days as dots, the trailing mean as a
line, its change over 4 weeks; FC sur 24 h, stress and the phone's 24-h breathing left the page."""
from datetime import date, datetime, timedelta, timezone

from app.models.health import HealthMetric
from app.models.mobile import MobileDaily
from app.services import sante
from app.services import sante_trends as tr
from tests.test_sante_score import D, _nights, _rich
from tests.test_viz import _env

TODAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 15, tzinfo=timezone.utc)


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


async def test_the_page_draws_three_months_of_weight_body_fat_and_steps(db_session, test_user):
    await _weigh_ins_and_steps(db_session, test_user)
    p = await sante.health_page(db_session, test_user.id, today=TODAY, now=NOW)
    trends = {t["key"]: t for t in p["trends"]}
    assert list(trends) == ["weight", "body_fat", "steps"]
    weight = trends["weight"]
    assert (weight["title"], weight["value"], weight["chart"]["n"]) == ("Poids", "70,7 kg", 90)
    assert weight["chart"]["legend"] == [("is-dot", "pesée"), ("is-mean", "moyenne 14 jours")]
    assert weight["delta"] == "−0,4 kg en 4 sem."
    assert len(weight["chart"]["dots"]) == 13
    assert weight["chart"]["mean"].count("M") == 1  # a weekly weigh-in: one line, never a dash a week
    assert trends["body_fat"]["delta"] == "0,0 % en 4 sem."
    steps = trends["steps"]
    assert steps["chart"]["legend"] == [("is-dot", "jour"), ("is-mean", "moyenne 7 jours")]
    assert steps["delta"] == "0 pas en 4 sem."  # today's 1 200 steps, a day in progress, never lower the mean
    html = _env().get_template("partials/sante_page.html").render(page=p)
    assert 'id="tendances"' in html and "Au quotidien" not in html
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


def test_the_night_hr_trend_is_the_fc_card_s_line_over_ninety_nights():
    p = sante._compute(_nights(_rich()), [], D, D, None, {}, True, 190, {})
    hr = p["hr_trend"]
    assert (hr["key"], hr["title"], hr["chart"]["n"]) == ("hr_night", "FC de nuit", 90)
    assert hr["chart"]["summary"] == "FC de nuit, 90 nuits : 60 enregistrées"
    assert hr["chart"]["legend"] == [("is-dot", "nuit"), ("is-mean", "moyenne 7 jours")]
    assert hr["value"] == p["fc"]["measurement"]["value"]
