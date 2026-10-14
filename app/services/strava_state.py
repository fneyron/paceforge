"""What the Strava syncs remember between runs, in Redis (no column): the app's quota backoff, the athletes the
webhook reached lately, the rounds' claims, a first import's failures and the « Synchroniser » cooldown.

Redis is also Celery's broker: when it is down nothing is remembered and every helper answers so (no backoff, not
reached, free to claim), which leaves Strava's own limits and the rounds as they were.
"""
from __future__ import annotations

import json
import logging
import time

from app.config import settings

logger = logging.getLogger(__name__)

PREFIX = "paceforge:strava:"
WEBHOOK_FRESH_S = 24 * 3600  # an athlete the webhook reached within this is not polled hourly
POLLED_EVERY_S = 24 * 3600  # every linked athlete is polled at least this often all the same (an event may be lost)
INITIAL_RUN_S = 3 * 3600  # a first import running (a lost worker frees it after this)
INITIAL_RETRY_S, INITIAL_RETRY_MAX_S = 3600, 24 * 3600  # a failed first import waits 1 h, 2 h, 4 h… 24 h at most
_KEEP_FAILURE_S = 7 * 24 * 3600

_client = None


def _redis():
    global _client
    if _client is None:
        import redis

        _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True,
                                       socket_timeout=1, socket_connect_timeout=1)
    return _client


# ── primitives ──────────────────────────────────────────────────────────────

def claim(name: str, ttl: float) -> bool:
    """Holds `name` for `ttl` seconds: True when it was free (or when Redis cannot tell)."""
    try:
        return bool(_redis().set(PREFIX + name, "1", nx=True, ex=max(1, int(ttl))))
    except Exception:
        logger.warning("Strava state unavailable: %s not claimed", name)
        return True


def release(name: str) -> None:
    try:
        _redis().delete(PREFIX + name)
    except Exception:
        logger.warning("Strava state unavailable: %s not released", name)


def mark(name: str, ttl: float, value: str = "1") -> None:
    try:
        _redis().set(PREFIX + name, value, ex=max(1, int(ttl)))
    except Exception:
        logger.warning("Strava state unavailable: %s not marked", name)


def keep(name: str, value: str) -> None:
    """Sets `name` for good (no expiry)."""
    try:
        _redis().set(PREFIX + name, value)
    except Exception:
        logger.warning("Strava state unavailable: %s not kept", name)


def read(name: str) -> str | None:
    try:
        return _redis().get(PREFIX + name)
    except Exception:
        return None


# ── the app's quota (shared by every athlete of that app) ───────────────────

def back_off(client_id: str | None, until: float) -> None:
    mark(f"backoff:{client_id}", until - time.time(), str(until))


def backoff_until(client_id: str | None) -> float | None:
    """When the background syncs of this app may call Strava again (None: now)."""
    raw = read(f"backoff:{client_id}")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


# ── the shared app's webhook subscription ───────────────────────────────────

def shared_subscription() -> tuple[bool, int | None]:
    """What Strava last said the shared app's subscription is: (known, its id; None when the app has none)."""
    raw = read("shared_sub")
    if raw is None:
        return False, None
    return True, int(raw) if raw.isdigit() else None


def remember_shared_subscription(sub_id: int | None, fresh_for: float) -> None:
    """Kept for good (a later lookup that fails leaves it); read again from Strava after `fresh_for` s."""
    keep("shared_sub", str(sub_id) if sub_id else "none")
    mark("shared_sub:fresh", fresh_for)


def shared_subscription_fresh() -> bool:
    return read("shared_sub:fresh") is not None


# ── the webhook ─────────────────────────────────────────────────────────────

def webhook_reached(user_id: int) -> None:
    mark(f"webhook:{user_id}", WEBHOOK_FRESH_S)


def webhook_recent(user_id: int) -> bool:
    return read(f"webhook:{user_id}") is not None


def webhook_forget(user_id: int) -> None:
    """The next round polls this athlete again (an event the webhook could not bring)."""
    release(f"webhook:{user_id}")
    release(f"polled:{user_id}")


def app_webhook_reached(client_id: str | None) -> None:
    """An event of this app was accepted: its webhook works (for all of its athletes)."""
    mark(f"webhook_app:{client_id}", WEBHOOK_FRESH_S)


def app_webhook_alive(client_id: str | None) -> bool:
    return read(f"webhook_app:{client_id}") is not None


def polled(user_id: int) -> None:
    """The athlete's newest activities were just listed (a poll, « Synchroniser », the first import)."""
    mark(f"polled:{user_id}", POLLED_EVERY_S)


def polled_recently(user_id: int) -> bool:
    return read(f"polled:{user_id}") is not None


# ── the first import ────────────────────────────────────────────────────────

def initial_lock(user_id: int) -> str:
    return f"initial:{user_id}:running"


def initial_failure(user_id: int) -> dict | None:
    """The last failure of a first import still to do: {failures, next_at (epoch s), error}."""
    raw = read(f"initial:{user_id}:failed")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def initial_failed(user_id: int, error: str) -> dict:
    n = (initial_failure(user_id) or {}).get("failures", 0) + 1
    record = {"failures": n, "error": error,
              "next_at": time.time() + min(INITIAL_RETRY_MAX_S, INITIAL_RETRY_S * 2 ** (n - 1))}
    mark(f"initial:{user_id}:failed", _KEEP_FAILURE_S, json.dumps(record))
    return record


def initial_clear(user_id: int) -> None:
    release(f"initial:{user_id}:failed")


def initial_due(user_id: int, now: float | None = None) -> bool:
    """Not running, and its wait after the last failure over (never failed: due)."""
    if read(initial_lock(user_id)) is not None:
        return False
    failed = initial_failure(user_id)
    return failed is None or (now if now is not None else time.time()) >= failed.get("next_at", 0)
