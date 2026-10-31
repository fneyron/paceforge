import base64
import hashlib
import hmac
import logging
import re
import secrets
import time

import bcrypt
from itsdangerous import BadData, BadSignature, TimestampSigner, URLSafeTimedSerializer
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
# Failed logins per account whatever the address: a guessing campaign spread over many addresses is held to this
# (1 200 a day at most). Counted only for attempts the limits above let through, and higher than one address can
# reach alone (4 windows of 10 an hour), so no single address can lock anyone out; a password reset clears it.
# A few addresses together can reach it, though: so a browser that has already signed in to the account (its
# KNOWN_COOKIE names it) is never held by this cap, only by the address limits, and the account's owner is told
# by email the first time the cap refuses a login in a window (routers.auth.login).
ACCOUNT_LIMITS = {"login": (50, 60 * 60)}  # action: (per account, window in seconds)
TOO_MANY = "Trop de tentatives. Réessaie plus tard."  # windows of 15 minutes to an hour
ADDRESS, ACCOUNT = "address", "account"  # the limit an attempt is over (count_attempt)

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
    "data_export": (60 * 60, {"user": 3}),  # the account's data downloaded (Réglages): every row read again
    # the email telling an account's owner its login cap was reached: once per window of that cap, per account
    "login_alert": (ACCOUNT_LIMITS["login"][1], {"account": 1}),
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


def _account_key(action: str, email: str) -> str:
    return f"rl:{action}:account:{_digest(email)}"


async def count_attempt(action: str, ip: str, email: str) -> str | None:
    """Count one attempt, before any other work; the limit it is over, when it is (it must then be refused, unless
    ACCOUNT and the browser is known to the account): ADDRESS (this address and email, or this address), ACCOUNT
    (the account's own cap, whatever the address); None within them."""
    per_email, per_address, window = LIMITS[action]
    counts = await counter().incr(_keys(action, ip, email), window)
    if counts[0] > per_email or counts[1] > per_address:
        return ADDRESS
    if action in ACCOUNT_LIMITS:
        per_account, account_window = ACCOUNT_LIMITS[action]
        if (await counter().incr([_account_key(action, email)], account_window))[0] > per_account:
            return ACCOUNT
    return None


async def forget_attempts(action: str, ip: str, email: str) -> None:
    """A login that worked: this address and email start afresh, and neither the address nor the account is
    charged for it."""
    key, address = _keys(action, ip, email)
    await counter().delete(key)
    await counter().decr(address)
    if action in ACCOUNT_LIMITS:
        await counter().decr(_account_key(action, email))


async def forget_account_attempts(action: str, email: str) -> None:
    """The account's own count starts afresh (a password reset: whoever holds the mailbox signs in again)."""
    if action in ACCOUNT_LIMITS:
        await counter().delete(_account_key(action, email))


async def over_quota(action: str, **subjects) -> bool:
    """Count one `action` for each subject given (user=the account's id, ip=the client's address) and tell whether
    any of them is over its quota for the window: the request must then be refused (429)."""
    window, allowed = QUOTAS[action]
    scopes = [scope for scope in allowed if scope in subjects]
    keys = [f"rl:{action}:{scope}:{_digest(str(subjects[scope]))}" for scope in scopes]
    counts = await counter().incr(keys, window)
    return any(count > allowed[scope] for scope, count in zip(scopes, counts, strict=True))


# ── browsers known to an account (the login cap spares them) ──
# A signed, HttpOnly cookie, set when a login (or a password reset) succeeds: the accounts this browser signed in
# to, each as a keyed digest of its email (never the email) with the account's session version then. A password
# reset bumps that version: a cookie from before it no longer counts. Only the auth routes ever receive it.
KNOWN_COOKIE = "pf_known"
KNOWN_MAX_AGE = 365 * 24 * 60 * 60
KNOWN_ACCOUNTS = 5  # the accounts one browser is remembered for, the most recent first


def _known_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="known-browser")


def _account_tag(email: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"known|{email}".encode(), hashlib.sha256).hexdigest()[:32]


def _known_accounts(cookie: str | None) -> list[tuple[str, int]]:
    if not cookie:
        return []
    try:
        entries = _known_serializer().loads(cookie, max_age=KNOWN_MAX_AGE)
    except BadData:  # forged, older than a year, or not ours
        return []
    if not isinstance(entries, list):
        return []
    return [(e[0], e[1]) for e in entries[:KNOWN_ACCOUNTS]
            if isinstance(e, list) and len(e) == 2 and isinstance(e[0], str) and isinstance(e[1], int)]


def known_version(cookie: str | None, email: str) -> int | None:
    """The session version this browser's cookie holds for the account of `email`; None when it does not name it."""
    tag = _account_tag(email)
    return next((version for t, version in _known_accounts(cookie) if t == tag), None)


def known_cookie(cookie: str | None, email: str, session_version: int) -> str:
    """The cookie's new value once this browser has signed in to the account of `email`."""
    tag = _account_tag(email)
    entries = [[tag, session_version]] + [[t, v] for t, v in _known_accounts(cookie) if t != tag]
    return _known_serializer().dumps(entries[:KNOWN_ACCOUNTS])


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
