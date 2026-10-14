import logging
import os
import secrets
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import StravaAPIError, StravaRateLimitError, StravaTokenError
from app.models.user import User
from app.services import strava_state

logger = logging.getLogger(__name__)

STRAVA_BASE_URL = "https://www.strava.com"
STRAVA_API_URL = f"{STRAVA_BASE_URL}/api/v3"
STRAVA_OAUTH_URL = f"{STRAVA_BASE_URL}/oauth"
QUOTA_SHARE = 0.8  # of a Strava rate-limit window, the background syncs' share
SHARED_SUB_KEEP_S = 12 * 3600  # the shared app's subscription id, as Strava gave it, is remembered this long
SHARED_SUB_RETRY_S = 5 * 60  # a failed lookup is not tried again before this
SHARED_SUB_RECHECK_S = 10 * 60  # an event of another subscription asks Strava again at most this often


def backing_off(client_id: str | None) -> bool:
    """The background syncs of this app wait for its quota window to turn (strava_state)."""
    until = strava_state.backoff_until(client_id)
    return until is not None and until > time.time()


# ── the shared app's webhook subscription ───────────────────────────────────

def configured_shared_subscription() -> int | None:
    """STRAVA_SUBSCRIPTION_ID when set: the shared app's subscription, Strava not asked (optional)."""
    raw = str(getattr(settings, "STRAVA_SUBSCRIPTION_ID", None) or os.getenv("STRAVA_SUBSCRIPTION_ID", "")).strip()
    return int(raw) if raw.isdigit() else None


def shared_subscription_check(subscription_id: int) -> bool | None:
    """Whether an event's subscription is the shared app's, without asking Strava (the webhook answers within 2 s):
    True, False, or None when Strava is to be asked — not known yet, or another id while the last lookup is over
    SHARED_SUB_RECHECK_S old (the subscription may have been made again)."""
    configured = configured_shared_subscription()
    if configured is not None:
        return subscription_id == configured
    known, cached = strava_state.shared_subscription()
    if known and cached == subscription_id:
        return True
    if known and strava_state.read("shared_sub:recheck") is not None:
        return False
    return None


async def lookup_shared_subscription() -> tuple[bool, int | None]:
    """The shared app's push subscription (one per app), from Strava's GET /push_subscriptions with the app's keys:
    remembered SHARED_SUB_KEEP_S, a failure SHARED_SUB_RETRY_S (Strava not asked meanwhile). Returns (known, id),
    id None when the app has none. An app-level request: no athlete's token, no athlete's quota."""
    if not settings.strava_shared_ready:
        return False, None
    if strava_state.read("shared_sub:failed") is not None:
        return strava_state.shared_subscription()
    strava_state.mark("shared_sub:recheck", SHARED_SUB_RECHECK_S)
    try:
        async with httpx.AsyncClient(timeout=10) as client:  # the keys in the query: httpx logs no URL (WARNING)
            response = await client.get(f"{STRAVA_API_URL}/push_subscriptions", params={
                "client_id": settings.STRAVA_CLIENT_ID, "client_secret": settings.STRAVA_CLIENT_SECRET})
        if response.status_code != 200:
            raise StravaAPIError(f"push_subscriptions: {response.status_code}", status_code=response.status_code)
        sub_id = next((s["id"] for s in response.json() if isinstance(s, dict) and isinstance(s.get("id"), int)),
                      None)
    except Exception:
        logger.warning("Strava: the shared app's webhook subscription could not be read", exc_info=True)
        strava_state.mark("shared_sub:failed", SHARED_SUB_RETRY_S)
        return strava_state.shared_subscription()
    strava_state.remember_shared_subscription(sub_id, SHARED_SUB_KEEP_S)
    logger.info("Strava: the shared app's webhook subscription is %s", sub_id)
    return True, sub_id


async def shared_subscription_matches(subscription_id: int) -> bool | None:
    """shared_subscription_check, asking Strava when it cannot tell (an event's task: never the webhook's answer).
    None: Strava could not say."""
    check = shared_subscription_check(subscription_id)
    if check is not None:
        return check
    known, cached = await lookup_shared_subscription()
    return cached == subscription_id if known else None


async def refresh_shared_subscription() -> None:
    """The hourly round keeps the shared app's subscription id at hand (looked up again once forgotten)."""
    if (settings.strava_shared_ready and configured_shared_subscription() is None
            and not strava_state.shared_subscription()[0]):
        await lookup_shared_subscription()


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
        """Use only the app to which this account explicitly authorized its tokens."""
        if user.has_own_strava_app:
            return cls(
                db,
                client_id=user.strava_client_id,
                client_secret=user.strava_client_secret,
            )
        if user.has_strava_app:
            return cls(db)
        raise StravaTokenError()

    def get_authorize_url(self, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": f"{settings.BASE_URL}/auth/strava/callback",
            "response_type": "code",
            "scope": "read,activity:read_all,activity:write",
            "approval_prompt": "auto",
            "state": state,
        }
        from urllib.parse import urlencode
        query = urlencode(params)
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
        if not user.strava_refresh_token or user.strava_token_expires_at is None:
            raise StravaTokenError()  # disconnected: nothing to refresh
        if user.strava_token_expires_at > int(time.time()) + 300:
            return user
        # one refresh at a time per athlete: Strava rotates the refresh token,
        # so a worker that refreshed first (its pair maybe not committed yet)
        # must be waited for and its pair used, not refreshed again (the
        # athlete's row is locked until that worker commits)
        await self.db.flush()
        # FOR NO KEY UPDATE: serializes refreshers without blocking the
        # athlete's other writes (rows pointing at users take KEY SHARE)
        await self.db.refresh(user, attribute_names=[
            "strava_access_token", "strava_refresh_token", "strava_token_expires_at"],
            with_for_update={"key_share": True})
        if not user.strava_refresh_token or user.strava_token_expires_at is None:
            raise StravaTokenError()  # disconnected meanwhile
        if user.strava_token_expires_at > int(time.time()) + 300:
            return user
        sent = user.strava_refresh_token

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
            # 401: the app's keys are refused (new keys needed, through the
            # wizard); 400: the athlete revoked PaceForge on Strava (the keys
            # are fine: the link is gone, one click reconnects it). Either way
            # the syncs stop asking until then.
            if response.status_code in (400, 401):
                # another worker may have refreshed first: Strava then refuses
                # the refresh token we sent, but the stored pair is fine
                await self.db.refresh(user, attribute_names=[
                    "strava_access_token", "strava_refresh_token", "strava_token_expires_at"])
                if not user.strava_refresh_token:
                    raise StravaTokenError()  # disconnected meanwhile: nothing to flag
                if user.strava_refresh_token != sent:
                    return user
                logger.error("Strava refused the refresh for user %d (%s)", user.id, response.status_code)
                if response.status_code == 401:
                    user.strava_credentials_valid = False
                else:
                    _forget_link(user)
                await self.db.flush()
                raise StravaTokenError()

            if response.status_code != 200:
                logger.error("Strava token refresh failed: %s", response.text)
                raise StravaTokenError()

            data = response.json()
            user.strava_credentials_valid = True  # a refresh that works heals a stale flag
            user.strava_access_token = data["access_token"]
            user.strava_refresh_token = data["refresh_token"]
            user.strava_token_expires_at = data["expires_at"]
            # committed at once (which also ends the row lock): Strava has already invalidated the refresh token
            # sent, so the new pair must outlive whatever the caller does next — a rollback after a failed sync,
            # an early return without a commit; lost, the next refresh would be refused and the link forgotten
            await self.db.commit()
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

        self._note_quota(response)

        if response.status_code == 429:
            raise StravaRateLimitError()

        if response.status_code >= 400:
            raise StravaAPIError(
                f"Strava API error: {response.status_code} {response.text}",
                status_code=response.status_code,
            )

        return response

    def _note_quota(self, response: httpx.Response) -> None:
        """The app's quota is shared by its athletes: past QUOTA_SHARE of a Strava window, or on a 429, the
        background syncs (round, first import's details, races' D+) wait until that window turns — the next quarter
        hour, or midnight UTC for the day's — leaving the rest to the webhooks and the athletes' own clicks."""
        windows = []
        for prefix in ("X-RateLimit", "X-ReadRateLimit"):  # all requests, and the reads (Strava counts both)
            try:
                used = [int(v) for v in response.headers[f"{prefix}-Usage"].split(",")]
                limit = [int(v) for v in response.headers[f"{prefix}-Limit"].split(",")]
            except (KeyError, ValueError):
                continue
            windows += [(u, n, i == 1) for i, (u, n) in enumerate(zip(used, limit)) if n > 0]
        full = [daily for used, limit, daily in windows if used >= QUOTA_SHARE * limit]
        if response.status_code == 429 and not full:
            full = [False]  # no usable header: the quarter hour
        if not full:
            return
        now = time.time()
        step = 86400 if any(full) else 900
        until = (now // step + 1) * step
        strava_state.back_off(self.client_id, until)
        logger.warning("Strava quota of app %s (%s): background syncs wait %d min", self.client_id,
                       response.headers.get("X-RateLimit-Usage") or response.status_code, (until - now) // 60)

    def backing_off(self) -> bool:
        """This app's background syncs wait for its quota window to turn."""
        return backing_off(self.client_id)

    async def get_athlete(self, user: User) -> dict:
        response = await self._make_request(user, "GET", "/athlete")
        return response.json()

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

    async def delete_webhook_subscription(self, subscription_id: int) -> bool:
        """Delete a Strava webhook subscription. True when it is gone (deleted
        now, or already unknown to Strava)."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.delete(
                    f"{STRAVA_API_URL}/push_subscriptions/{subscription_id}",
                    params={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                )
        except Exception:
            logger.exception("Failed to delete webhook subscription %d", subscription_id)
            return False
        if response.status_code in (200, 204, 404):
            logger.info("Webhook subscription %d deleted", subscription_id)
            return True
        logger.warning("Webhook subscription %d not deleted: %s", subscription_id, response.status_code)
        return False

    async def deauthorize(self, user: User) -> None:
        try:
            user = await self.refresh_token_if_needed(user)
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(  # token in the body, never in a logged URL
                    f"{STRAVA_OAUTH_URL}/deauthorize",
                    data={"access_token": user.strava_access_token},
                )
            if response.status_code == 200:
                logger.info("Deauthorized Strava for user %d", user.id)
            else:
                logger.warning("Strava deauthorize answered %s for user %d", response.status_code, user.id)
        except Exception:
            logger.exception("Failed to deauthorize Strava for user %d", user.id)


def link_intact(user: User) -> bool:
    """After a StravaTokenError: whether the link survived it. Strava refused nothing (its token endpoint failed:
    a 5xx, an odd answer), so the work is to be tried again. A refusal forgets the link (400) or flags the app's
    keys (401): nothing to try until the athlete reconnects."""
    return bool(user.strava_refresh_token and user.strava_credentials_valid)


def _forget_link(user: User) -> None:
    """The tokens go (the athlete and the app keys stay, so reconnecting is
    one click); reconnecting fetches what was recorded meanwhile."""
    user.strava_access_token = None
    user.strava_refresh_token = None
    user.strava_token_expires_at = None
    user.initial_sync_done = False
    user.last_activity_poll_at = None


def strava_status(user: User) -> dict:
    """The link state, including shared app availability without exposing keys."""
    from app.services.health import _ago

    if not user.has_strava_linked:
        # stored keys Strava refused: say so and offer the wizard
        return {"connected": False, "bad_keys": user.has_own_strava_app and not user.strava_credentials_valid,
                "own_app": user.has_own_strava_app, "shared_ready": settings.strava_shared_ready}
    # a first import that failed (Strava's quota, an error), tried again by the round at `sync_retry_at`: as the
    # watches' last_error, read by /connections/strava (« La synchronisation est à vérifier… ») and the app's list
    failed = None
    if not user.initial_sync_done:
        failed = strava_state.initial_failure(user.id)
    return {
        "connected": True,
        "needs_reauth": not (user.strava_credentials_valid and user.has_strava_app),
        "own_app": user.has_own_strava_app, "shared_ready": settings.strava_shared_ready,
        "last_sync_at": user.last_activity_poll_at,
        "last_sync_ago": _ago(user.last_activity_poll_at),
        "last_error": FIRST_IMPORT_ERRORS.get(failed.get("error"), FIRST_IMPORT_ERRORS["failed"]) if failed else None,
        "sync_retry_at": datetime.fromtimestamp(failed.get("next_at", 0), timezone.utc) if failed else None,
    }


FIRST_IMPORT_ERRORS = {  # strava_status' last_error (no template prints it yet: the interface pass decides)
    "rate_limited": "Strava a limité la première synchro ; nouvel essai automatique.",
    "failed": "La première synchro a échoué ; nouvel essai automatique.",
}


async def disconnect(db: AsyncSession, user: User) -> None:
    """Revoke PaceForge on Strava (best effort), stop the athlete's own app
    pushing to us, and forget the tokens. Activities already received stay;
    the athlete and their app keys are kept, so reconnecting is one click."""
    if user.has_strava_app:
        strava = StravaService.for_user(db, user)
        await strava.deauthorize(user)
    if (user.has_own_strava_app and user.strava_webhook_subscription_id
            and not (settings.strava_shared_ready and user.strava_client_id == settings.STRAVA_CLIENT_ID)):
        if await strava.delete_webhook_subscription(user.strava_webhook_subscription_id):
            user.strava_webhook_subscription_id = None  # else kept, to retry on the next disconnect
    _forget_link(user)  # the import skips what is already there
    await db.flush()
    logger.info("Strava disconnected for user %d", user.id)
