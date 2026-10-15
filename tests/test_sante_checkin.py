"""Dated self-report: explicit answers, isolated accounts, no invented physiology."""
from datetime import date, timedelta
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.models.health import HealthMetric
from app.models.user import User
from app.services import sante, sante_checkin as checkin
from app.services.health import feel_of
from tests import test_coros
from tests.test_nights import night_rows

as_user, no_commit = test_coros.as_user, test_coros.no_commit

D = date(2026, 10, 15)


async def row(db, uid, day=D):
    return (await db.execute(select(HealthMetric).where(
        HealthMetric.user_id == uid, HealthMetric.date == day,
        HealthMetric.metric == "feel", HealthMetric.source == "PaceForge"))).scalar_one_or_none()


def test_no_inferred_answers_from_missing_legacy_or_unknown_payloads():
    for details in (None, [], {"why": ["fatigue"], "answered": True},
                    {checkin.KEY: {"v": 2, "pain": "present"}},
                    {checkin.KEY: {"v": 1, "pain": "maybe"}}):
        result = checkin.presentation(D, details)
        assert result["answers"] == {} and not result["saved"] and not result["attention"]


async def test_checkin_keeps_legacy_answers_and_clears_only_its_own_data(db_session, test_user):
    legacy = {"answered": True, "why": ["sick"], "alcohol": True, "legs_heavy": True}
    db_session.add(HealthMetric(user_id=test_user.id, date=D, metric="feel", source="PaceForge",
                                value=3, details=legacy.copy()))
    await db_session.flush()
    before = feel_of(3, legacy)
    result = await checkin.save(db_session, test_user.id, D, {"pain": "present", "effort": "not_done"})
    assert result["attention"] and result["signals"] == ["Douleur signalée"]
    saved = await row(db_session, test_user.id)
    assert saved.value == 3 and feel_of(saved.value, saved.details) == before
    assert {k: v for k, v in saved.details.items() if k != checkin.KEY} == legacy
    await checkin.save(db_session, test_user.id, D, None)
    assert saved.details == legacy and saved.value == 3


async def test_answers_do_not_cross_date_account_or_source(db_session, test_user):
    other = User(strava_athlete_id=76543210, firstname="Other")
    db_session.add(other)
    await db_session.flush()
    await checkin.save(db_session, other.id, D, {"pain": "present"})
    await checkin.save(db_session, test_user.id, D - timedelta(days=1), {"fatigue": "high"})
    db_session.add(HealthMetric(user_id=test_user.id, date=D, metric="feel", source="Other provider", value=3,
                                details={checkin.KEY: {"v": 1, "pain": "present"}}))
    await db_session.flush()
    assert not (await checkin.read(db_session, test_user.id, D))["saved"]
    await checkin.save(db_session, test_user.id, D, {"effort": "not_done"})
    assert (await checkin.read(db_session, test_user.id, D))["answers"] == {"effort": "not_done"}
    saved = await row(db_session, test_user.id)
    assert feel_of(saved.value, saved.details) == {"value": 2, "answered": False, "why": [], "alcohol": False}


async def test_report_does_not_change_measured_index(db_session, test_user):
    for metric, days in night_rows(range(60), today=D).items():
        for day, (value, details, source) in days.items():
            db_session.add(HealthMetric(user_id=test_user.id, date=day, metric=metric, source=source,
                                        value=value, details=details))
    await db_session.flush()
    before = await sante.health_page(db_session, test_user.id, today=D)
    await checkin.save(db_session, test_user.id, D, {"fatigue": "high", "pain": "present", "effort": "harder"})
    after = await sante.health_page(db_session, test_user.id, today=D)
    assert after["synthesis"]["signal"] == before["synthesis"]["signal"]
    assert after["synthesis"]["signal"]["value"] is not None


async def test_save_clear_and_stale_day_guard(as_user, db_session, test_user, monkeypatch):
    monkeypatch.setattr(sante, "athlete_today", AsyncMock(return_value=D))
    headers = {"HX-Request": "true"}
    response = await as_user.post("/sante/ressenti", data={"day": D.isoformat(), "pain": "present"}, headers=headers)
    assert response.status_code == 200 and 'value="present" checked' in response.text
    assert "Douleur signalée" in response.text
    response = await as_user.post("/sante/ressenti", data={"day": (D - timedelta(days=1)).isoformat(),
                                                           "pain": "none"}, headers=headers)
    assert response.status_code == 200 and "Le jour a changé" in response.text
    assert (await checkin.read(db_session, test_user.id, D))["answers"] == {"pain": "present"}
    assert await row(db_session, test_user.id, D - timedelta(days=1)) is None
    response = await as_user.post("/sante/ressenti", data={"day": D.isoformat(), "action": "clear"}, headers=headers)
    assert response.status_code == 200 and " checked" not in response.text
    assert not (await checkin.read(db_session, test_user.id, D))["saved"]


async def test_empty_and_invalid_answers_do_not_create_a_report(as_user, db_session, test_user, monkeypatch):
    monkeypatch.setattr(sante, "athlete_today", AsyncMock(return_value=D))
    response = await as_user.post("/sante/ressenti", data={"day": D.isoformat()}, headers={"HX-Request": "true"})
    assert response.status_code == 200 and "Choisis au moins" in response.text
    response = await as_user.post("/sante/ressenti", data={"day": D.isoformat(), "pain": "maybe"})
    assert response.status_code == 422
    assert await row(db_session, test_user.id) is None


async def test_page_uses_calendar_day_for_report_when_night_is_pending(db_session, test_user, monkeypatch):
    from app.routers import sante as router

    monkeypatch.setattr(router, "link_state", AsyncMock(return_value=({"connected": False}, {"connected": False})))
    monkeypatch.setattr(router, "health_page", AsyncMock(return_value={"today": D - timedelta(days=1),
                         "calendar_day": D, "cycle_pending": True}))
    await checkin.save(db_session, test_user.id, D, {"fatigue": "high"})
    page = await router._health_context(db_session, test_user)
    assert page["checkin"]["day"] == D.isoformat()
    assert page["checkin"]["answers"] == {"fatigue": "high"}
