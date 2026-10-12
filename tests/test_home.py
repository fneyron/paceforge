"""The daily dashboard must summarize real, dated, account-scoped data."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.models.activity import Activity
from app.models.health import HealthMetric
from app.models.mobile import MobileDaily
from app.models.user import User
from app.routers import sante as routes
from app.services import sante, sante_training
from app.services.home import overview
from tests.test_settings_links import as_user, no_commit  # noqa: F401

D = date(2026, 10, 12)


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(sante_training, "athlete_today", AsyncMock(return_value=D))


def activity(user, at, name, **fields):
    return Activity(user_id=user.id, name=name, sport_type="Run", start_date=at,
                    distance=10000, moving_time=3600, elapsed_time=3600,
                    raw_data={"utc_offset": 7200}, **fields)


async def test_home_requires_session(client):
    response = await client.get("/accueil")
    assert response.status_code == 303 and response.headers["location"] == "/auth/login"


async def test_empty_home_never_fabricates_metrics(as_user, test_user):
    test_user.strava_access_token = None
    response = await as_user.get("/accueil")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert "Connecter un service" in response.text
    assert "Pas encore de score" in response.text and "Aucune nuit récente" in response.text
    assert "Aucune activité reçue sur ces 7 jours" in response.text
    assert 'class="pf-home-measure"' not in response.text


async def test_home_measures_keep_date_source_and_watch_priority(as_user, db_session, test_user):
    db_session.add_all([
        HealthMetric(user_id=test_user.id, date=D, metric="steps", value=5000, source="COROS"),
        MobileDaily(user_id=test_user.id, platform="ios", date=D, metric="steps", value=5200, sources=["Montre"]),
        MobileDaily(user_id=test_user.id, platform="ios", date=D-timedelta(days=31), metric="weight",
                    value=72.4, sources=["Balance"]),
    ])
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=D)
    home = await overview(db_session, test_user, page, {}, {})
    steps, weight = home["daily"]
    assert steps["source"] == "COROS" and "5" in steps["value"]
    assert weight["latest"] == "11/09/2026" and weight["stale"]
    assert weight["source"] == "Apple Santé · Balance"
    assert page["score"]["value"] is None  # day data alone cannot produce a recovery score
    html = (await as_user.get("/accueil")).text
    assert "Donnée ancienne" in html and "11/09/2026" not in html and "Journée en cours" in html
    assert "Apple Santé · Balance" not in html and "Sources et mises à jour" not in html


async def test_home_counts_one_session_and_isolates_accounts(db_session, test_user):
    other = User(firstname="Other")
    db_session.add(other)
    await db_session.flush()
    at = datetime(2026, 10, 11, 22, 30, tzinfo=timezone.utc)
    db_session.add_all([
        activity(test_user, at, "Sortie complète", coros_activity_id=91, strava_activity_id=92, average_heartrate=140),
        activity(test_user, at+timedelta(seconds=20), "Copie", garmin_activity_id=93),
        activity(other, at, "Privée", strava_activity_id=94),
    ])
    await db_session.flush()
    home = await overview(db_session, test_user, {"calendar_day": D}, {}, {})
    assert home["week"]["count"] == 1 and home["week"]["duration"] == "1h00"
    assert [a["name"] for a in home["activities"]] == ["Sortie complète"]
    assert home["activities"][0]["source"] == "COROS · Strava"
    assert home["activities"][0]["date"] == "12/10/2026 · 00:30"


async def test_home_preserves_pending_cycle_date(as_user, db_session, test_user, monkeypatch):
    yesterday = D-timedelta(days=1)
    db_session.add(HealthMetric(user_id=test_user.id, date=yesterday, metric="sleep", value=480, source="COROS",
        details={"main_start": "2026-10-10T23:00", "main_end": "2026-10-11T07:00", "timeline": True}))
    await db_session.flush()
    monkeypatch.setattr(sante, "local_clock", AsyncMock(return_value=datetime(2026, 10, 12, 6)))
    html = (await as_user.get("/accueil")).text
    assert html.count("En attente de la prochaine nuit") == 1
    assert "Dernier bilan" in html and "Relevé du" not in html


async def test_health_failure_keeps_activities_visible(as_user, db_session, test_user, monkeypatch):
    db_session.add(activity(test_user, datetime(2026, 10, 11, 8, tzinfo=timezone.utc), "Ma séance"))
    await db_session.flush()
    monkeypatch.setattr(routes, "health_page", AsyncMock(side_effect=RuntimeError("unavailable")))
    html = (await as_user.get("/accueil")).text
    assert "Ma séance" in html and "Bilan de santé indisponible" in html
    assert "Aucune mesure quotidienne reçue" not in html


async def test_activity_only_sync_refreshes_home(as_user, db_session, test_user):
    version = await routes.data_version(db_session, test_user.id, home=True)
    db_session.add(activity(test_user, datetime(2026, 10, 11, 8, tzinfo=timezone.utc), "Nouvelle séance"))
    await db_session.flush()
    response = await as_user.get("/sante/sync-status", params={"home": "true", "v": version})
    assert response.headers["HX-Refresh"] == "true"


async def test_sync_without_new_measurement_refreshes_source_date(as_user, db_session, test_user):
    from tests.test_coros import _link

    connection = await _link(db_session, test_user, last_sync_at=datetime(2026, 10, 11, tzinfo=timezone.utc))
    version = await routes.data_version(db_session, test_user.id, home=True)
    connection.last_sync_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
    await db_session.flush()
    response = await as_user.get("/sante/sync-status", params={"home": "true", "v": version})
    assert response.headers["HX-Refresh"] == "true"
