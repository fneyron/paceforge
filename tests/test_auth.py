from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_landing_page(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    assert "PaceForge" in response.text


@pytest.mark.asyncio
async def test_register_page(client: AsyncClient):
    response = await client.get("/auth/register")
    assert response.status_code == 200
    assert "Créer" in response.text or "compte" in response.text


@pytest.mark.asyncio
async def test_login_page(client: AsyncClient):
    response = await client.get("/auth/login")
    assert response.status_code == 200
    assert "Connexion" in response.text


@pytest.mark.asyncio
async def test_strava_link_requires_login(client: AsyncClient):
    """Without session, /auth/strava redirects to login."""
    response = await client.get("/auth/strava", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_setup_page_loads(client: AsyncClient):
    response = await client.get("/setup")
    assert response.status_code == 200
    assert "Client ID" in response.text


@pytest.mark.asyncio
async def test_logout(client: AsyncClient):
    response = await client.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"


@pytest.mark.asyncio
async def test_dashboard_requires_auth(client: AsyncClient):
    response = await client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 307
