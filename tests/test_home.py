"""Legacy home navigation and quiet refresh of the main health dashboard."""
from datetime import datetime, timezone

from app.models.activity import Activity
from app.routers import sante as routes
from tests.test_settings_links import as_user, no_commit  # noqa: F401


def activity(user, at, name):
    return Activity(user_id=user.id, name=name, sport_type="Run", start_date=at,
                    distance=10000, moving_time=3600, elapsed_time=3600,
                    raw_data={"utc_offset": 7200})


async def test_legacy_home_requires_session(client):
    response = await client.get("/accueil")
    assert response.status_code == 303 and response.headers["location"] == "/auth/login"


async def test_legacy_home_opens_health(as_user):
    response = await as_user.get("/accueil")
    assert response.status_code == 302 and response.headers["location"] == "/sante"
    page = await as_user.get(response.headers["location"])
    assert page.status_code == 200
    assert 'pf-home' not in page.text


async def test_activity_only_sync_refreshes_health(as_user, db_session, test_user):
    version = await routes.data_version(db_session, test_user.id, home=False)
    db_session.add(activity(test_user, datetime(2026, 10, 11, 8, tzinfo=timezone.utc), "Nouvelle séance"))
    await db_session.flush()
    response = await as_user.get("/sante/sync-status", params={"v": version})
    assert response.headers["HX-Refresh"] == "true"


async def test_sync_without_new_measurement_refreshes_source_date(as_user, db_session, test_user):
    from tests.test_coros import _link

    connection = await _link(db_session, test_user, last_sync_at=datetime(2026, 10, 11, tzinfo=timezone.utc))
    version = await routes.data_version(db_session, test_user.id, home=True)
    connection.last_sync_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
    await db_session.flush()
    response = await as_user.get("/sante/sync-status", params={"v": version, "home": "true"})
    assert response.headers["HX-Refresh"] == "true"
