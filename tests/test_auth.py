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
    assert response.status_code == 303
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
    assert response.status_code == 303


async def test_register_and_login_log_the_account_id_never_the_email(client, db_session, monkeypatch, caplog):
    import logging

    from sqlalchemy import select

    from app.models.user import User

    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *args: True)
    caplog.set_level(logging.INFO)
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        await c.post("/auth/register", data={"email": "private@x.fr", "password": "password-123"})
        user = (await db_session.execute(select(User).where(User.email == "private@x.fr"))).scalar_one()
        user.email_verified = True
        await db_session.flush()
        assert (await c.post("/auth/login", data={"email": "private@x.fr", "password": "password-123"})).status_code == 302
    assert f"User {user.id} registered" in caplog.text and f"User {user.id} logged in" in caplog.text
    assert "private@x.fr" not in caplog.text
