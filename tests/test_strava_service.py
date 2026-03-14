import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StravaAPIError, StravaRateLimitError, StravaTokenError
from app.models.user import User
from app.services.strava import StravaService


@pytest.fixture
def strava_service(db_session: AsyncSession):
    return StravaService(db_session)


@pytest.mark.asyncio
async def test_get_authorize_url(strava_service: StravaService):
    url = strava_service.get_authorize_url()
    assert "strava.com/oauth/authorize" in url
    assert "activity:read_all" in url
    assert "activity:write" in url


@pytest.mark.asyncio
async def test_refresh_token_not_needed(
    strava_service: StravaService, test_user: User
):
    test_user.strava_token_expires_at = int(time.time()) + 7200
    result = await strava_service.refresh_token_if_needed(test_user)
    assert result is test_user


@pytest.mark.asyncio
async def test_refresh_token_when_expired(
    strava_service: StravaService, test_user: User
):
    test_user.strava_token_expires_at = int(time.time()) - 100

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "new_access",
        "refresh_token": "new_refresh",
        "expires_at": int(time.time()) + 7200,
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await strava_service.refresh_token_if_needed(test_user)

    assert result.strava_access_token == "new_access"
    assert result.strava_refresh_token == "new_refresh"


@pytest.mark.asyncio
async def test_refresh_token_failure(
    strava_service: StravaService, test_user: User
):
    test_user.strava_token_expires_at = int(time.time()) - 100

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        with pytest.raises(StravaTokenError):
            await strava_service.refresh_token_if_needed(test_user)


@pytest.mark.asyncio
async def test_exchange_token_success(strava_service: StravaService):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "token",
        "refresh_token": "refresh",
        "expires_at": 9999999999,
        "athlete": {"id": 123, "firstname": "Test"},
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await strava_service.exchange_token("test_code")

    assert result["access_token"] == "token"
    assert result["athlete"]["id"] == 123


@pytest.mark.asyncio
async def test_exchange_token_failure(strava_service: StravaService):
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        with pytest.raises(StravaAPIError):
            await strava_service.exchange_token("bad_code")
