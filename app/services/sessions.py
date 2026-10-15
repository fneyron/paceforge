"""Browser sessions that end.

Each session carries an id and its birth time (dependencies.sign_in). Logout revokes the id, kept in Redis for the
session's remaining lifetime so every web worker refuses it, and in this process whatever Redis says, so the worker
that signed out refuses it at once. No session outlives SESSION_LIFETIME_DAYS, whatever its cookie says
(SessionMiddleware slides the cookie's 30 days at every answer). When Redis does not answer, a logout handled by
another worker is not seen here for a minute at a time: a known, narrow gap, logged.
"""
import logging
import secrets
import time

from app.config import settings

logger = logging.getLogger(__name__)

PREFIX = "paceforge:session:revoked:"
RETRY_REDIS_AFTER_S = 60


def new_id() -> str:
    return secrets.token_urlsafe(16)


def lifetime_s() -> int:
    return settings.SESSION_LIFETIME_DAYS * 86400


def alive(iat) -> bool:
    """Whether a session born at `iat` (epoch seconds) is still within its absolute lifetime."""
    return isinstance(iat, int | float) and 0 < iat <= time.time() < iat + lifetime_s()


def remaining_s(iat) -> int:
    """How long a revocation must be kept: until the session would have died anyway."""
    if not isinstance(iat, int | float):
        return lifetime_s()
    return max(1, int(iat + lifetime_s() - time.time()))


class MemoryRevocations:
    """The ids signed out, in this process (tests; the fallback)."""

    def __init__(self):
        self.until: dict[str, float] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        if len(self.until) > 10000:
            self.until = {sid: at for sid, at in self.until.items() if at > now}

    async def revoke(self, sid: str, ttl: int) -> None:
        self._prune()
        self.until[sid] = time.monotonic() + ttl

    async def is_revoked(self, sid: str) -> bool:
        at = self.until.get(sid)
        if at is None:
            return False
        if at <= time.monotonic():
            self.until.pop(sid, None)
            return False
        return True


class _RedisRevocations:
    def __init__(self):
        import redis.asyncio as aioredis

        self.r = aioredis.Redis.from_url(settings.REDIS_URL, socket_timeout=0.5, socket_connect_timeout=0.5)

    async def revoke(self, sid: str, ttl: int) -> None:
        await self.r.set(PREFIX + sid, "1", ex=max(1, ttl))

    async def is_revoked(self, sid: str) -> bool:
        return bool(await self.r.exists(PREFIX + sid))


class Revocations:
    """Redis, shared by the workers, and this process: a revocation is written to both and read from either."""

    def __init__(self):
        self.memory = MemoryRevocations()
        self.redis = None
        self.down_until = 0.0

    def _shared(self):
        if self.redis is None:
            self.redis = _RedisRevocations()
        return self.redis

    def _down(self) -> None:
        self.redis, self.down_until = None, time.monotonic() + RETRY_REDIS_AFTER_S

    async def revoke(self, sid: str, ttl: int) -> None:
        await self.memory.revoke(sid, ttl)
        try:
            await self._shared().revoke(sid, ttl)
        except Exception:
            self._down()
            logger.warning("Session revocations unavailable: a logout holds on this worker only for now")

    async def is_revoked(self, sid: str) -> bool:
        if await self.memory.is_revoked(sid):
            return True
        if time.monotonic() < self.down_until:
            return False
        try:
            return await self._shared().is_revoked(sid)
        except Exception:
            self._down()
            logger.warning("Session revocations unavailable: logouts from other workers not seen for a minute")
            return False


_store = None


def store():
    global _store
    if _store is None:
        _store = Revocations()
    return _store


async def revoke(sid: str, iat) -> None:
    await store().revoke(sid, remaining_s(iat))


async def is_revoked(sid: str) -> bool:
    return await store().is_revoked(sid)
