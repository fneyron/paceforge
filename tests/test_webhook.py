from unittest.mock import patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_webhook_validation_success(client: AsyncClient):
    response = await client.get(
        "/webhook/strava",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "test_challenge_123",
            "hub.verify_token": "paceforge-verify",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["hub.challenge"] == "test_challenge_123"


@pytest.mark.asyncio
async def test_webhook_validation_invalid_token(client: AsyncClient):
    response = await client.get(
        "/webhook/strava",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "test_challenge",
            "hub.verify_token": "wrong_token",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_event_create_activity(client: AsyncClient):
    with patch("app.routers.webhook.process_new_activity") as mock_task:
        mock_task.delay = lambda **kwargs: None

        response = await client.post(
            "/webhook/strava",
            json={
                "object_type": "activity",
                "object_id": 12345,
                "aspect_type": "create",
                "owner_id": 67890,
                "subscription_id": 1,
                "event_time": 1234567890,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_event_ignores_non_create(client: AsyncClient):
    response = await client.post(
        "/webhook/strava",
        json={
            "object_type": "activity",
            "object_id": 12345,
            "aspect_type": "update",
            "owner_id": 67890,
            "subscription_id": 1,
            "event_time": 1234567890,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_event_invalid_payload(client: AsyncClient):
    response = await client.post(
        "/webhook/strava",
        json={"invalid": "data"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
