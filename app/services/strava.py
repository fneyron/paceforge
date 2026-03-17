import logging
import secrets
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
    def __init__(
        self,
        db: AsyncSession,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.db = db
        self.client_id = client_id or settings.STRAVA_CLIENT_ID
        self.client_secret = client_secret or settings.STRAVA_CLIENT_SECRET

    @classmethod
    def for_user(cls, db: AsyncSession, user: User) -> "StravaService":
        """Create a StravaService using the user's own Strava app credentials."""
        if user.has_own_strava_app:
            return cls(
                db,
                client_id=user.strava_client_id,
                client_secret=user.strava_client_secret,
            )
        # Fallback to global credentials (dev/testing only)
        return cls(db)

    def get_authorize_url(self) -> str:
        params = {
            "client_id": self.client_id,
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
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
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
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": user.strava_refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if response.status_code == 401:
                logger.error("Strava credentials invalid for user %d", user.id)
                user.strava_credentials_valid = False
                await self.db.flush()
                raise StravaTokenError()

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

    async def get_all_activities_since(
        self, user: User, after_epoch: int, per_page: int = 100
    ) -> list[dict]:
        """Fetch all activities since a given epoch timestamp, paginating automatically."""
        all_activities = []
        page = 1
        while True:
            response = await self._make_request(
                user,
                "GET",
                "/athlete/activities",
                params={"after": after_epoch, "per_page": per_page, "page": page},
            )
            batch = response.json()
            if not batch:
                break
            all_activities.extend(batch)
            logger.info(
                "Fetched page %d (%d activities) for user %d",
                page, len(batch), user.id,
            )
            if len(batch) < per_page:
                break
            page += 1
        return all_activities

    async def get_activity_streams(
        self, user: User, strava_activity_id: int,
        stream_types: list[str] | None = None,
    ) -> dict[str, list]:
        """Fetch second-by-second streams for an activity."""
        if stream_types is None:
            stream_types = [
                "time", "heartrate", "cadence", "watts",
                "velocity_smooth", "altitude", "distance",
            ]
        keys = ",".join(stream_types)
        response = await self._make_request(
            user, "GET",
            f"/activities/{strava_activity_id}/streams",
            params={"keys": keys, "key_type": "time"},
        )
        raw = response.json()
        return {
            stream["type"]: stream["data"]
            for stream in raw
            if "type" in stream and "data" in stream
        }

    async def update_activity_description(
        self, user: User, strava_activity_id: int, description: str
    ) -> dict:
        response = await self._make_request(
            user,
            "PUT",
            f"/activities/{strava_activity_id}",
            json={"description": description},
        )
        return response.json()

    async def create_webhook_subscription(self, user: User) -> int | None:
        """Create a Strava webhook subscription for the user's app.

        Returns the subscription_id or None on failure.
        """
        verify_token = f"paceforge-{secrets.token_hex(16)}"

        # Store verify token in Redis with 60s TTL for the verification callback
        try:
            import redis as redis_lib
            r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            r.setex(f"strava_webhook_verify:{verify_token}", 60, "pending")
            r.close()
        except Exception:
            logger.exception("Failed to store webhook verify token in Redis")
            return None

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{STRAVA_API_URL}/push_subscriptions",
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "callback_url": f"{settings.BASE_URL}/webhook/strava",
                        "verify_token": verify_token,
                    },
                )

            if response.status_code in (200, 201):
                data = response.json()
                subscription_id = data.get("id")
                logger.info(
                    "Webhook subscription %d created for user %d",
                    subscription_id, user.id,
                )
                return subscription_id

            logger.warning(
                "Webhook subscription failed for user %d: %s %s",
                user.id, response.status_code, response.text,
            )
            return None
        except Exception:
            logger.exception("Failed to create webhook subscription for user %d", user.id)
            return None

    async def delete_webhook_subscription(self, subscription_id: int) -> None:
        """Delete a Strava webhook subscription."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.delete(
                    f"{STRAVA_API_URL}/push_subscriptions/{subscription_id}",
                    params={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                )
            logger.info("Webhook subscription %d deleted", subscription_id)
        except Exception:
            logger.exception("Failed to delete webhook subscription %d", subscription_id)

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
