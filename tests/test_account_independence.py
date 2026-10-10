"""A PaceForge account works before linking Strava and after disconnecting it."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.crypto import encrypt_secret
from app.models.activity import Activity
from app.models.coros import CorosConnection
from app.models.garmin import GarminConnection
from app.models.health import HealthMetric
from app.models.route import Route
from app.models.user import User
from app.schemas.user import UserResponse
from app.services.strava import StravaService
from tests.test_settings_links import _logged_in
from tests.test_simulator_routes import _create_route


@pytest.fixture(autouse=True)
def no_commit(db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)


async def test_register_plan_export_and_login_without_any_provider(client, db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.routers.auth.send_verification_email", lambda *args: sent.append(args) or True
    )
    sync = Mock(side_effect=AssertionError("No Strava sync for an independent account"))
    monkeypatch.setattr("app.tasks.initial_sync.initial_sync.delay", sync)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        credentials = {"email": "independent@example.com", "password": "account-password"}
        response = await c.post("/auth/register", data=credentials)
        assert response.headers["location"] == "/auth/check-email"
        response = await c.get("/auth/verify-email", params={"token": sent[0][1]})
        assert response.headers["location"] == "/sante"
        user = (
            await db_session.execute(select(User).where(User.email == credentials["email"]))
        ).scalar_one()
        assert not user.has_strava_linked and user.strava_athlete_id is None
        assert UserResponse.model_validate(user).strava_athlete_id is None
        for path in ("/sante", "/activities", "/simulator", "/settings"):
            assert (await c.get(path)).status_code == 200, path
        assert 'hx-post="/api/sync"' not in (await c.get("/activities")).text

        gpx = b"""<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
          <trk><name>Course autonome</name><trkseg>
            <trkpt lat="45" lon="6"><ele>500</ele></trkpt>
            <trkpt lat="45.01" lon="6.01"><ele>550</ele></trkpt>
            <trkpt lat="45.02" lon="6.02"><ele>510</ele></trkpt>
          </trkseg></trk></gpx>"""
        response = await c.post(
            "/partials/simulator/gpx-upload", files={"gpx_file": ("course.gpx", gpx)}
        )
        assert response.status_code == 204
        page_url = response.headers["HX-Redirect"]
        route_id = int(page_url.rsplit("/", 1)[1])
        route = await db_session.get(Route, route_id)
        assert route.user_id == user.id
        response = await c.post(
            "/api/simulator/routes",
            data={
                "route_id": route_id,
                "name": "Course autonome",
                "course_json": json.dumps(route.course_json),
                "target_time_s": 3600,
                "start_hour": 8,
                "sport_type": "trail",
            },
        )
        assert response.status_code == 200
        await c.get("/auth/logout")
        response = await c.post("/auth/login", data=credentials)
        assert response.headers["location"] == "/sante"
        assert (await c.get(page_url)).status_code == 200
        response = await c.get(f"/api/simulator/routes/{route_id}/pace-export?format=csv")
        assert response.status_code == 200 and "09:00" in response.text
        sync.assert_not_called()


@pytest.mark.parametrize("provider", ["COROS", "Garmin"])
async def test_watch_data_and_account_survive_strava_disconnect(
    client, db_session, monkeypatch, provider
):
    monkeypatch.setattr(StravaService, "deauthorize", AsyncMock())
    async with _logged_in(client, db_session) as (c, user):
        if provider == "COROS":
            watch = CorosConnection(
                user_id=user.id,
                issuer="https://example.invalid",
                client_id="watch-client",
                resource_url="https://example.invalid/mcp",
                token_endpoint="https://example.invalid/token",
                access_token_encrypted=encrypt_secret("watch-token"),
            )
        else:
            watch = GarminConnection(
                user_id=user.id,
                di_client_id="watch-client",
                access_token_encrypted=encrypt_secret("watch-token"),
            )
        today = datetime.now(UTC).date()
        metric = HealthMetric(
            user_id=user.id,
            date=today,
            metric="sleep",
            value=480,
            source=provider,
            details={
                "main_start": f"{today - timedelta(days=1)}T23:00",
                "main_end": f"{today}T07:00",
            },
        )
        activity = Activity(
            user_id=user.id,
            name="Sortie de ma montre",
            sport_type="Run",
            start_date=datetime.now(UTC),
            distance=10000,
            moving_time=3600,
            elapsed_time=3600,
            total_elevation_gain=100,
            raw_data={},
            **{f"{provider.lower()}_activity_id": 771},
        )
        db_session.add_all([watch, metric, activity])
        await db_session.flush()
        route_id = await _create_route(c)

        # A watch's data is visible before this account has ever linked Strava.
        assert user.strava_athlete_id is None and activity.strava_activity_id is None
        health = await c.get("/sante")
        assert health.status_code == 200 and 'data-viz-key="sommeil-14"' in health.text
        assert "8h00" in health.text
        assert "Sortie de ma montre" in (await c.get("/activities")).text

        # Linking and then disconnecting only removes the optional Strava access.
        user.strava_athlete_id = 456
        user.strava_access_token = "strava-token"
        user.strava_refresh_token = "refresh-token"
        user.strava_token_expires_at = 9999999999
        await db_session.flush()
        response = await c.post("/settings/strava/disconnect")
        assert response.status_code == 303 and not user.has_strava_linked
        await c.get("/auth/logout")
        response = await c.post(
            "/auth/login", data={"email": user.email, "password": "pw-12345678"}
        )
        assert response.headers["location"] == "/sante"
        assert (await c.get(f"/simulator/routes/{route_id}")).status_code == 200
        assert "Sortie de ma montre" in (await c.get("/activities")).text
        assert 'data-viz-key="sommeil-14"' in (await c.get("/sante")).text
        assert await db_session.get(type(watch), watch.id) is watch
        assert await db_session.get(HealthMetric, metric.id) is metric
        assert await db_session.get(Activity, activity.id) is activity
