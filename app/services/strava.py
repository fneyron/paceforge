import logging
import time

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import StravaAPIError, StravaRateLimitError, StravaTokenError
from app.models.user import User

logger = logging.getLogger(__name__)

STRAVA_BASE_URL = "https://www.strava.com"
STRAVA_API_URL = f"{STRAVA_BASE_URL}/api/v3"
STRAVA_OAUTH_URL = f"{STRAVA_BASE_URL}/oauth"


class StravaService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def get_authorize_url(self) -> str:
        params = {
            "client_id": settings.STRAVA_CLIENT_ID,
            "redirect_uri": f"{settings.BASE_URL}/auth/strava/callback",
            "response_type": "code",
            "scope": "read,activity:read_all,activity:write",
            "approval_prompt": "auto",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{STRAVA_OAUTH_URL}/authorize?{query}"

    async def exchange_token(self, code: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{STRAVA_OAUTH_URL}/token",
                data={
                    "client_id": settings.STRAVA_CLIENT_ID,
                    "client_secret": settings.STRAVA_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            if response.status_code != 200:
                logger.error("Strava token exchange failed: %s", response.text)
                raise StravaAPIError(
                    f"Token exchange failed: {response.status_code}",
                    status_code=response.status_code,
                )
            return response.json()

    async def refresh_token_if_needed(self, user: User) -> User:
        if user.strava_token_expires_at > int(time.time()) + 300:
            return user

        logger.info("Refreshing Strava token for user %d", user.id)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{STRAVA_OAUTH_URL}/token",
                data={
                    "client_id": settings.STRAVA_CLIENT_ID,
                    "client_secret": settings.STRAVA_CLIENT_SECRET,
                    "refresh_token": user.strava_refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if response.status_code != 200:
                logger.error("Strava token refresh failed: %s", response.text)
                raise StravaTokenError()

            data = response.json()
            user.strava_access_token = data["access_token"]
            user.strava_refresh_token = data["refresh_token"]
            user.strava_token_expires_at = data["expires_at"]
            await self.db.flush()
            logger.info("Token refreshed for user %d", user.id)
            return user

    async def _make_request(
        self, user: User, method: str, path: str, **kwargs
    ) -> httpx.Response:
        user = await self.refresh_token_if_needed(user)

        async with httpx.AsyncClient(
            base_url=STRAVA_API_URL,
            timeout=30,
        ) as client:
            response = await client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {user.strava_access_token}"},
                **kwargs,
            )

        # Check rate limits
        rate_limit_usage = response.headers.get("X-RateLimit-Usage", "")
        if rate_limit_usage:
            short, daily = rate_limit_usage.split(",")
            if int(short) > 90 or int(daily) > 950:
                logger.warning(
                    "Strava rate limit warning: %s/100 (15min), %s/1000 (daily)",
                    short,
                    daily,
                )

        if response.status_code == 429:
            raise StravaRateLimitError()

        if response.status_code >= 400:
            raise StravaAPIError(
                f"Strava API error: {response.status_code} {response.text}",
                status_code=response.status_code,
            )

        return response

    async def get_activity(self, user: User, strava_activity_id: int) -> dict:
        response = await self._make_request(
            user, "GET", f"/activities/{strava_activity_id}"
        )
        return response.json()

    async def get_recent_activities(
        self, user: User, per_page: int = 30, page: int = 1
    ) -> list[dict]:
        response = await self._make_request(
            user,
            "GET",
            "/athlete/activities",
            params={"per_page": per_page, "page": page},
        )
        return response.json()

    async def post_comment(
        self, user: User, strava_activity_id: int, text: str
    ) -> dict:
        response = await self._make_request(
            user,
            "POST",
            f"/activities/{strava_activity_id}/comments",
            data={"text": text},
        )
        return response.json()

    async def deauthorize(self, user: User) -> None:
        try:
            user = await self.refresh_token_if_needed(user)
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(
                    f"{STRAVA_OAUTH_URL}/deauthorize",
                    params={"access_token": user.strava_access_token},
                )
            logger.info("Deauthorized Strava for user %d", user.id)
        except Exception:
            logger.exception("Failed to deauthorize Strava for user %d", user.id)
