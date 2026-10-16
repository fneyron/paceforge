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


# ── attempts per address: logins, sign-ups, reset emails; quotas per account or address ──
# Counted per client address and email, and per address alone (one address
# trying many emails), in Redis so every web worker shares them. When Redis
# does not answer, each worker counts alone for a minute at a time and the
# limits still hold, with this leniency: N web workers (4 in the Dockerfile)
# give at most N times the allowance, never an unlimited one. Emails and
# addresses are only stored hashed. Each attempt is counted first, atomically,
# before any other work: parallel attempts cannot slip past the limit, and a
# refused one costs neither a database connection nor a password check. A
# login that works is then taken back (logins count failures only).

LIMITS = {  # action: (per address and email, per address, window in seconds)
    "login": (10, 50, 15 * 60),  # failed logins (each counted when it starts, taken back if it works)
    "register": (5, 20, 60 * 60),
    "forgot": (5, 20, 60 * 60),  # reset emails asked for
}
TOO_MANY = "Trop de tentatives. Réessaie plus tard."  # windows of 15 minutes to an hour

# Quotas on the costly or sensitive requests (over_quota): one window each, counted per signed-in account
# (user=) and/or per client address (ip=), each on its own key.
QUOTAS = {  # action: (window in seconds, {scope: allowed in the window})
    # an address is often shared (a carrier's NAT, a club's Wi-Fi): its allowance is wide, the account's is the one
    "exchange": (60 * 60, {"ip": 100}),  # phone pairing code exchanges (each a guess at a 32-byte code)
    "weather": (60 * 60, {"user": 60, "ip": 600}),  # forecasts (an Open-Meteo call each, when not cached)
    "gpx": (60 * 60, {"user": 20}),  # GPX imports (parsed on the web worker)
    "export": (60 * 60, {"user": 60}),  # plan exports: GPX, TCX, CSV, the phone's offline copy
    # Strava's events all come from its few egress addresses: a ceiling on the flood only, never on its traffic
    "webhook": (10 * 60, {"ip": 3000}),
    "resend": (60, {"user": 1}),  # verification emails: one a minute per account
}
RETRY_AFTER = "3600"


def client_ip(request) -> str:
    """The client's address for the limits. Deployed behind exactly one proxy (Traefik), which appends the address
    it saw as the last X-Forwarded-For hop: that hop is used. uvicorn's --proxy-headers with
    --forwarded-allow-ips='*' takes the first hop instead, which the client writes itself. Another proxy in front
    (a CDN) would make its own address the last hop: then trust its header instead."""
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if hops:
        return hops[-1]
    return request.client.host if request.client else ""


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


# one more on each key, its window starting with its first (EXPIRE when the count is 1): one atomic step, so no
# key is ever left without its window
_INCR = ("local out = {} for i, key in ipairs(KEYS) do local n = redis.call('INCR', key) "
         "if n == 1 then redis.call('EXPIRE', key, ARGV[1]) end out[i] = n end return out")
# one less, never below zero, never a key without its window (a plain DECR would make one)
_DECR = "local v = tonumber(redis.call('GET', KEYS[1]) or '0') if v > 0 then redis.call('DECR', KEYS[1]) end return 0"


class _RedisCounter:
    def __init__(self):
        import redis.asyncio as aioredis

        self.r = aioredis.Redis.from_url(settings.REDIS_URL, socket_timeout=0.5, socket_connect_timeout=0.5)

    async def incr(self, keys: list[str], window: int) -> list[int]:
        return [int(n) for n in await self.r.eval(_INCR, len(keys), *keys, window)]

    async def delete(self, key: str) -> None:
        await self.r.delete(key)

    async def decr(self, key: str) -> None:
        await self.r.eval(_DECR, 1, key)


RETRY_REDIS_AFTER_S = 60


class FallbackCounter:
    """Redis, shared by the workers; this process alone while Redis does not answer (a minute at a time), so a
    limit always holds: never open, at worst N times as lenient as there are web workers."""

    def __init__(self):
        self.shared = None
        self.memory = MemoryCounter()
        self.down_until = 0.0

    def _redis(self):
        if self.shared is None:
            self.shared = _RedisCounter()
        return self.shared

    async def _down(self) -> None:
        """Redis did not answer: left alone for a minute, its client closed (a new one is opened after)."""
        client, self.shared, self.down_until = self.shared, None, time.monotonic() + RETRY_REDIS_AFTER_S
        if client is not None:
            try:
                await client.r.aclose()
            except Exception:
                pass

    async def _call(self, name: str, *args):
        if time.monotonic() >= self.down_until:
            try:
                return await getattr(self._redis(), name)(*args)
            except Exception:
                await self._down()
                logger.warning("Rate limit counters unavailable: this worker counts alone for a minute")
        return await getattr(self.memory, name)(*args)

    async def incr(self, keys: list[str], window: int) -> list[int]:
        return await self._call("incr", keys, window)

    async def delete(self, key: str) -> None:
        await self._call("delete", key)

    async def decr(self, key: str) -> None:
        await self._call("decr", key)


_counter = None


def counter():
    global _counter
    if _counter is None:
        _counter = FallbackCounter()
    return _counter


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _keys(action: str, ip: str, email: str) -> list[str]:
    return [f"rl:{action}:{_digest(f'{ip}|{email}')}", f"rl:{action}:ip:{_digest(ip)}"]


async def count_attempt(action: str, ip: str, email: str) -> bool:
    """Count one attempt, before any other work; True when it is over the limit (it must then be refused)."""
    per_email, per_address, window = LIMITS[action]
    counts = await counter().incr(_keys(action, ip, email), window)
    return counts[0] > per_email or counts[1] > per_address


async def forget_attempts(action: str, ip: str, email: str) -> None:
    """A login that worked: this address and email start afresh, and the address is not charged for it."""
    key, address = _keys(action, ip, email)
    await counter().delete(key)
    await counter().decr(address)


async def over_quota(action: str, **subjects) -> bool:
    """Count one `action` for each subject given (user=the account's id, ip=the client's address) and tell whether
    any of them is over its quota for the window: the request must then be refused (429)."""
    window, allowed = QUOTAS[action]
    scopes = [scope for scope in allowed if scope in subjects]
    keys = [f"rl:{action}:{scope}:{_digest(str(subjects[scope]))}" for scope in scopes]
    counts = await counter().incr(keys, window)
    return any(count > allowed[scope] for scope, count in zip(scopes, counts, strict=True))


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """How an email's token (verification, password reset) is stored: its SHA-256, so a read of the table gives
    no working link; the email carries the token itself and a lookup hashes what it receives."""
    return hashlib.sha256(token.encode()).hexdigest()


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
