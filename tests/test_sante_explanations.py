"""Contextual wording must follow the existing signal, its date and its measurement method."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.health import HealthMetric
from app.models.mobile import MobileDaily
from app.services import nights as nt, sante, sante_explain, sante_load
from app.services.sante_daily import daily_card, daily_context
from tests.test_sante_score import D, _nights, _rich, night_rows
from tests.test_viz import _env


def page(rows):
    return sante._compute(_nights(rows), [], D, D, None, {}, True, 190, {})


def test_lower_weekly_hrv_explains_possible_strain_without_changing_the_score():
    rows = _rich(hrv_last=40)
    p = page(rows)
    interpretation = p["vfc"]["interpretation"]
    assert interpretation["key"] == "low_hrv"
    assert interpretation["text"] == "Cette baisse peut accompagner fatigue ou stress."
    assert "n’indique pas la cause" in interpretation["detail"]
    assert "ressenti" in interpretation["detail"]
    assert p["score"] == sante._assess(_nights(rows), [], [], D)["score"]
    assert p["vfc"]["status"]["key"] == "below"


def test_a_low_latest_point_does_not_become_a_low_weekly_trend():
    rows = _rich()
    _, details, source = rows["hrv"][D]
    rows["hrv"][D] = (60, details, source)
    card = page(rows)["vfc"]
    assert card["status"]["key"] == "in"
    assert card["interpretation"]["key"] == "latest_low"
    assert "tendance sur 7 jours habituelle" in card["interpretation"]["text"]
    assert "Une nuit isolée ne suffit pas" in card["interpretation"]["detail"]


def test_normal_hrv_does_not_always_add_a_paragraph():
    rows = _rich(hrv_last=70)
    card = page(rows)["vfc"]
    assert card["interpretation"] == {"key": "usual", "text": "", "detail": sante_explain.HRV_CONTEXT}


def test_provisional_and_missing_reference_are_not_called_established_baselines():
    provisional = page(night_rows(range(9)))["vfc"]
    assert provisional["reference"]["provisional"]
    assert provisional["interpretation"]["key"] == "provisional"
    assert "provisoire" in provisional["interpretation"]["text"]
    building = page(night_rows(range(4)))["vfc"]
    assert building["interpretation"]["key"] == "building"
    assert "nombre seul" in building["interpretation"]["detail"]
    low = page(night_rows(range(15), hrv=lambda k: 35 if k < 3 else 75))["vfc"]
    assert low["interpretation"]["key"] == "low_hrv"
    assert low["reference"]["provisional"]
    assert "Ton repère est encore provisoire" in low["interpretation"]["text"]


def test_missing_night_keeps_an_old_low_measurement_dated_not_a_current_warning():
    rows = _rich(hrv_last=40)
    rows["hrv"].pop(D)
    card = page(rows)["vfc"]
    assert card["interpretation"]["key"] == "missing"
    assert not card["measurement"]["is_current"]
    assert card["measurement"]["date"] == (D - timedelta(days=1)).isoformat()


def test_sdnn_stays_descriptive_even_if_a_low_trend_can_be_drawn():
    source = "Apple Santé:" + "a" * 64 + " (SDNN sommeil)"
    nights = {D - timedelta(days=i): nt.Night(D - timedelta(days=i), hrv=25 if i < 7 else 50 + i % 3,
                                            hrv_source=source) for i in range(60)}
    p = sante._compute(nights, [], D, D, None, {}, True, 190, {})
    assert p["vfc"]["interpretation"]["key"] == "sdnn"
    assert p["score"]["value"] is None
    explanation = next(i for i in p["explanations"]["items"] if i["key"] == "vfc")
    assert "n’entre pas dans l’indice" in explanation["text"]
    assert "a" * 64 not in str(p["explanations"])


def test_new_sdnn_point_is_never_explained_as_the_established_rmssd_trend():
    nights = _nights(_rich())
    nights[D].hrv_source = "Apple Santé:" + "b" * 64 + " (SDNN sommeil)"
    nights[D].hrv = 30
    p = sante._compute(nights, [], D, D, None, {}, True, 190, {})
    assert p["vfc"]["interpretation"]["key"] == "different_series"
    assert p["vfc"]["interpretation"]["text"] == "Ce relevé n’est pas comparable à la tendance affichée."
    item = next(i for i in p["explanations"]["items"] if i["key"] == "vfc")
    # the cards print no sentence (owner, 2026-10-16: « pas du texte »): the warning is in the shared explanation
    assert item["text"] == "Ce relevé n’est pas comparable à la tendance affichée."
    details = " ".join(item["details"])
    assert "Tendance : RMSSD" in details
    assert "Le dernier relevé est une SDNN" in details
    assert "b" * 64 not in str(p["explanations"])


@pytest.mark.parametrize("age", [0, 40])
def test_respiration_provenance_never_exposes_device_identifiers_even_without_chart(age):
    source = "Apple Santé:" + "c" * 64
    rows = [HealthMetric(date=D - timedelta(days=age + i), metric="resp_night", value=14 + i / 10,
                         source=source) for i in range(5)]
    card = daily_card("resp_night", rows, D)
    explanation = sante_explain.build({"respiration": card})
    assert card["source"] == "Apple Santé"
    assert "c" * 64 not in str(explanation)
    assert bool(card["chart"]) == (age == 0)


def test_explanations_group_interpretation_methods_reference_and_quality():
    p = page(_rich(hrv_last=40, hr_last=49))
    explanation = p["explanations"]
    assert explanation["title"] == "Comprendre mes indicateurs"
    items = {i["key"]: i for i in explanation["items"]}
    assert "RMSSD nocturne" in " ".join(items["vfc"]["details"])
    assert "Référence personnelle" in " ".join(items["vfc"]["details"])
    assert "distincte de la FC de repos" in " ".join(items["fc"]["details"])
    assert items["vfc"]["source"] == "Garmin"
    assert items["vfc"]["date"] == p["vfc"]["measurement"]["date_label"]
    assert "FC de nuit : Utilisée" in " ".join(items["recovery"]["details"])
    assert "pas une mesure de ton besoin personnel" in " ".join(items["sleep"]["details"])
    assert p["synthesis"]["sleep_gap"]["label"] in items["sleep"]["details"]
    assert "100 % est un repère, pas un objectif" in " ".join(items["training"]["details"])
    assert f"après {sante_load.USUAL_LABEL_WEEKS} semaines actives" in " ".join(items["training"]["details"])
    assert p["vfc"]["interpretation"]["text"] == items["vfc"]["text"]  # said once, in the shared explanation


@pytest.mark.asyncio
async def test_actual_nightly_respiration_moves_unchanged_and_phone_day_respiration_stays_separate(db_session, test_user):
    last_night = D - timedelta(days=2)
    for i in range(5):
        db_session.add(HealthMetric(user_id=test_user.id, date=last_night - timedelta(days=i), metric="resp_night",
                                    value=14.1 + i / 10, source="Garmin", details={"method": "garmin_summary"}))
    db_session.add(MobileDaily(user_id=test_user.id, date=D, metric="resp_day", value=17.5, platform="ios",
                               sources=["Apple Watch"]))
    await db_session.flush()
    now = datetime.combine(D, datetime.min.time(), timezone.utc) + timedelta(hours=15)
    original = await daily_context(db_session, test_user.id, D, now=now)
    expected = next(c for c in original if c["key"] == "resp_night")
    p = await sante.health_page(db_session, test_user.id, today=D, now=now)
    assert p["respiration"] == expected
    assert p["respiration"]["latest"] == last_night.strftime("%d/%m/%Y")
    assert p["respiration"]["source"] == "Garmin (résumé)"
    assert "daily" not in p and not any(t["key"] == "resp_day" for t in p["trends"])
    item = next(i for i in p["explanations"]["items"] if i["key"] == "resp_night")
    assert item["date"] == expected["latest"] and item["source"] == expected["source"]
    assert item["chart_key"] == expected["chart"]["key"]
    html = _env().get_template("partials/sante_page.html").render(page=p)
    assert html.count('id="respiration"') == 1
    assert html.index('id="recuperation"') < html.index('id="respiration"')
    assert "Respiration sur 24 h" not in html and 'id="au-quotidien"' not in html  # the phone's 24 h left the page


@pytest.mark.asyncio
async def test_phone_daily_breathing_never_invents_a_nocturnal_card(db_session, test_user):
    db_session.add(MobileDaily(user_id=test_user.id, date=D, metric="resp_day", value=15.7, platform="ios",
                               sources=["Apple Watch"]))
    await db_session.flush()
    p = await sante.health_page(db_session, test_user.id, today=D)
    assert p["respiration"] is None
    assert not any(i["key"] == "resp_night" for i in p["explanations"]["items"])
    assert p["score"]["value"] is None
    html = _env().get_template("partials/sante_page.html").render(page=p)
    assert 'id="respiration"' not in html and "Respiration sur 24 h" not in html
