from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_landing_page(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    assert "PaceForge" in response.text
    assert "Strava" in response.text


@pytest.mark.asyncio
async def test_strava_login_redirects(client: AsyncClient):
    response = await client.get("/auth/strava", follow_redirects=False)
    assert response.status_code == 302
    assert "strava.com/oauth/authorize" in response.headers["location"]


@pytest.mark.asyncio
async def test_logout(client: AsyncClient):
    response = await client.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"


@pytest.mark.asyncio
async def test_dashboard_requires_auth(client: AsyncClient):
    response = await client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 307
