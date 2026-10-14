import base64
import hashlib
import logging
import re
import secrets
import time

import bcrypt
from itsdangerous import BadSignature, TimestampSigner
from starlette.concurrency import run_in_threadpool

from app.config import settings

logger = logging.getLogger(__name__)

PASSWORD_PREFIX = "bcrypt-sha256$"


def valid_email(email: str) -> bool:
    if len(email) > 254 or email.count("@") != 1:
        return False
    local, domain = email.rsplit("@", 1)
    return bool(
        0 < len(local) <= 64 and not local.startswith(".") and not local.endswith(".")
        and ".." not in local and re.fullmatch(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
        and "." in domain and all(
            re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
            for label in domain.split(".")
        )
    )


def _password_bytes(password: str) -> bytes:
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def hash_password(password: str) -> str:
    return PASSWORD_PREFIX + bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if len(password) > 1024:
        return False
    try:
        if password_hash.startswith(PASSWORD_PREFIX):
            stored = password_hash[len(PASSWORD_PREFIX):].encode()
            return bcrypt.checkpw(_password_bytes(password), stored)
        # Legacy bcrypt used at most 72 bytes; upgrade after successful login.
        return bcrypt.checkpw(password.encode()[:72], password_hash.encode())
    except (ValueError, TypeError):
        return False


# bcrypt takes ~0.3 s of CPU: never on the event loop (a few wrong logins a second would freeze every request)
async def hash_password_async(password: str) -> str:
    return await run_in_threadpool(hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await run_in_threadpool(verify_password, password, password_hash)


# The hash of a random value nobody kept, with the cost of a real one: an unknown email (or an account
# without a password) is checked against it, so the answer takes as long and tells nothing.
DUMMY_HASH = PASSWORD_PREFIX + "$2b$12$edK.6ioJQ40/cwK4VO7A7eRiNpHRI8.fMNhHacQFbhNT4wJymKTie"


# ── attempts per address: logins, sign-ups, reset emails ────────────────────
# Counted per client address and email, and per address alone (one address
# trying many emails), in Redis so every web worker shares them; when Redis
# does not answer, nobody is held back (fail open). Emails and addresses are
# only stored hashed. Each attempt is counted first, atomically, before any
# other work: parallel attempts cannot slip past the limit, and a refused one
# costs neither a database connection nor a password check. A login that works
# is then taken back (logins count failures only).

LIMITS = {  # action: (per address and email, per address, window in seconds)
    "login": (10, 50, 15 * 60),  # failed logins (each counted when it starts, taken back if it works)
    "register": (5, 20, 60 * 60),
    "forgot": (5, 20, 60 * 60),  # reset emails asked for
}
TOO_MANY = "Trop de tentatives. Réessaie dans quelques minutes."


class MemoryCounter:
    """The same counters in this process only (tests)."""

    def __init__(self):
        self.hits: dict[str, tuple[int, float]] = {}

    def _live(self, key: str) -> int:
        count, until = self.hits.get(key, (0, 0.0))
        return count if until > time.monotonic() else 0

    async def incr(self, keys: list[str], window: int) -> list[int]:
        out = []
        for k in keys:
            count = self._live(k)
            until = self.hits[k][1] if count else time.monotonic() + window
            self.hits[k] = (count + 1, until)
            out.append(count + 1)
        return out

    async def delete(self, key: str) -> None:
        self.hits.pop(key, None)

    async def decr(self, key: str) -> None:
        if count := self._live(key):
            self.hits[key] = (count - 1, self.hits[key][1])


# one less, never below zero, never a key without its window (a plain DECR would make one)
_DECR = "local v = tonumber(redis.call('GET', KEYS[1]) or '0') if v > 0 then redis.call('DECR', KEYS[1]) end return 0"


class _RedisCounter:
    def __init__(self):
        import redis.asyncio as aioredis

        self.r = aioredis.Redis.from_url(settings.REDIS_URL, socket_timeout=0.5, socket_connect_timeout=0.5)

    async def incr(self, keys: list[str], window: int) -> list[int]:
        async with self.r.pipeline(transaction=False) as pipe:
            for k in keys:
                pipe.set(k, 0, ex=window, nx=True)  # the window starts with the first attempt
                pipe.incr(k)
            return (await pipe.execute())[1::2]

    async def delete(self, key: str) -> None:
        await self.r.delete(key)

    async def decr(self, key: str) -> None:
        await self.r.eval(_DECR, 1, key)


_counter = None


def counter():
    global _counter
    if _counter is None:
        _counter = _RedisCounter()
    return _counter


def _keys(action: str, ip: str, email: str) -> list[str]:
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:32]
    return [f"rl:{action}:{digest(f'{ip}|{email}')}", f"rl:{action}:ip:{digest(ip)}"]


async def count_attempt(action: str, ip: str, email: str) -> bool:
    """Count one attempt, before any other work; True when it is over the limit (it must then be refused)."""
    per_email, per_address, window = LIMITS[action]
    try:
        counts = await counter().incr(_keys(action, ip, email), window)
    except Exception:
        logger.warning("Rate limit counters unavailable: %s not limited", action)
        return False
    return counts[0] > per_email or counts[1] > per_address


async def forget_attempts(action: str, ip: str, email: str) -> None:
    """A login that worked: this address and email start afresh, and the address is not charged for it."""
    key, address = _keys(action, ip, email)
    try:
        await counter().delete(key)
        await counter().decr(address)
    except Exception:
        logger.warning("Rate limit counters unavailable: %s not reset", action)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def verification_token() -> str:
    signer = TimestampSigner(settings.SECRET_KEY, salt="email-verification")
    return signer.sign(generate_token()).decode()


def valid_verification_token(token: str, max_age: int = 86400) -> bool:
    try:
        signer = TimestampSigner(settings.SECRET_KEY, salt="email-verification")
        signer.unsign(token, max_age=max_age)
        return True
    except BadSignature:
        return False
