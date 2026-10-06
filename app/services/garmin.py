"""Garmin: what the athlete's watch knows about recovery and fitness, and the
sessions it recorded.

Garmin publishes no MCP server or public athlete API, so PaceForge reads
Garmin Connect the way its mobile app does, with no AI involved:
- Login: the athlete types their Garmin email and password once, in Réglages.
  The python-garminconnect library (mobile SSO, then the "DI" OAuth exchange)
  does it in a thread; the password only lives in that thread's memory and is
  never stored. When Garmin asks for a code (MFA) the login waits for it.
  A login runs in the web worker that started it, while the code may arrive on
  another one: the state and the code go through Redis (`_store`).
- Tokens: the DI access token (a JWT, ~1 day) and its refresh token are
  Fernet-encrypted. A refresh may ROTATE the refresh token, so the new pair is
  committed before anything else, one refresh at a time per athlete (row lock
  + per-process lock), like COROS.
- Data: plain JSON from connectapi.garmin.com (httpx, async). Nights (sleep
  stages with their real times, overnight HRV), resting HR and VO2 max become
  HealthSample rows (source "Garmin") through health.store_samples; the values
  Garmin gives per day (training readiness as recovery, acute/chronic load,
  stress, steps, body battery, HRV normal range, race predictions) go straight
  to HealthMetric (health.store_daily). Sessions go to Activity (a session
  Strava already has only gets its Garmin id).
  60 days of health and 180 days of sessions the first time, then the last
  7 days, at most every 6 hours (Celery beat, app.tasks.garmin_sync), right
  after connecting and on demand.

Times are the athlete's local wall clock (naive), as Garmin's *Local fields are.
"""
import asyncio
import base64
import json
import logging
import secrets
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models.activity import Activity
from app.models.garmin import GarminConnection
from app.models.health import HealthMetric, HealthSample
from app.services.activity_dedupe import _DUP_DISTANCE_TOL, _DUP_WINDOW_S, _same_family
from app.services.coros import _drop_stale_intervals, sleep_samples
from app.services.health import (
    _RANGES,
    DAILY_LABELS,
    DAILY_METRICS,
    METRIC_LABELS,
    METRICS,
    Daily,
    Sample,
    _ago,
    _today,
    store_daily,
    store_samples,
)

logger = logging.getLogger(__name__)

SOURCE = "Garmin"

BACKFILL_DAYS = 60
RECENT_DAYS = 7
ACTIVITY_BACKFILL_DAYS = 180
HRV_CHUNK_DAYS = 28  # hrv-service range: 28 days at most
ACTIVITY_PAGE = 100
SYNC_EVERY = timedelta(hours=6)
CLAIM_TTL = timedelta(minutes=15)  # a crashed sync frees its claim after this
REFRESH_MARGIN = timedelta(minutes=15)  # DI access tokens last about a day
CALL_DELAY_S = 0.3
MAX_CALLS = 160  # a 60-day backfill takes ~135

LOGIN_TTL = 15 * 60  # how long a login (and its MFA wait) is remembered
MFA_WAIT_S = 10 * 60
MFA_TRIES = 3

# A Garmin account lives on garmin.com, or garmin.cn for China.
REGIONS = {
    "monde": ("Monde", "garmin.com"),
    "chine": ("Chine", "garmin.cn"),
}
DEFAULT_REGION = "monde"

# the headers Garmin's Android app sends (the API tier checks them)
_APP_HEADERS = {
    "User-Agent": "GCM-Android-5.23",
    "X-Garmin-User-Agent": ("com.garmin.android.apps.connectmobile/5.23; ; Google/sdk_gphone64_arm64/google; "
                            "Android/33; Dalvik/2.1.0"),
    "X-Garmin-Paired-App-Version": "10861",
    "X-Garmin-Client-Platform": "Android",
    "X-App-Ver": "10861",
    "X-Lang": "en",
    "X-GCExperience": "GC5",
    "Accept-Language": "en-US,en;q=0.9",
}

# tests swap in an httpx.MockTransport here, and a fake login client below
_transport: httpx.AsyncBaseTransport | None = None


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport, timeout=30, follow_redirects=False)


class GarminError(Exception):
    """A sync or connect failure; the message is plain French, shown as is."""


class GarminAuthError(GarminError):
    """The link is gone (refresh rejected): the athlete must reconnect."""


class GarminRateLimited(GarminError):
    pass


def region_domain(region: str | None) -> str:
    return REGIONS.get(region or "", REGIONS[DEFAULT_REGION])[1]


# ── tokens ──────────────────────────────────────────────────────────────────

def _jwt_claims(token: str | None) -> dict:
    """The payload of a JWT, unverified (only Garmin can check its signature;
    PaceForge reads its expiry and client id)."""
    try:
        part = (token or "").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _expiry(token: str, expires_in=None) -> datetime | None:
    exp = _jwt_claims(token).get("exp")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        try:
            return datetime.fromtimestamp(exp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    try:
        seconds = int(expires_in or 0)
    except (TypeError, ValueError):
        seconds = 0
    return datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None


def _apply_tokens(conn: GarminConnection, access: str, refresh: str | None,
                  client_id: str | None = None, expires_in=None) -> None:
    conn.access_token_encrypted = encrypt_secret(access)
    if refresh:
        conn.refresh_token_encrypted = encrypt_secret(refresh)
    conn.di_client_id = str(_jwt_claims(access).get("client_id") or client_id or conn.di_client_id)
    conn.expires_at = _expiry(access, expires_in)


async def connection_for(db: AsyncSession, user_id: int) -> GarminConnection | None:
    return (await db.execute(
        select(GarminConnection).where(GarminConnection.user_id == user_id))).scalar_one_or_none()


async def save_connection(db: AsyncSession, user_id: int, domain: str, tokens: dict) -> GarminConnection:
    """Store the link the login gave (replacing an old one)."""
    conn = await connection_for(db, user_id)
    if conn is None:
        conn = GarminConnection(user_id=user_id)
        db.add(conn)
    conn.domain = domain
    conn.di_client_id = tokens["di_client_id"]
    _apply_tokens(conn, tokens["di_token"], tokens.get("di_refresh_token"), tokens["di_client_id"])
    if not tokens.get("di_refresh_token"):
        conn.refresh_token_encrypted = None
    conn.display_name = None  # read again on the first sync
    conn.connected_at = datetime.now(timezone.utc)
    conn.last_sync_at = None  # (re)connected: backfill again, the upserts are idempotent
    conn.sync_claimed_at = None
    conn.last_error = None
    conn.needs_reauth = False
    await db.flush()
    logger.info("Garmin connected for user %d (%s)", user_id, domain)
    return conn


_locks: dict[int, tuple[asyncio.AbstractEventLoop, dict[int, asyncio.Lock]]] = {}


def _lock_for(user_id: int) -> asyncio.Lock:
    """One lock per athlete and event loop (Celery runs each task in a new loop)."""
    loop = asyncio.get_running_loop()
    for key in [k for k, (lp, _) in _locks.items() if lp.is_closed()]:
        del _locks[key]
    entry = _locks.get(id(loop))
    if entry is None or entry[0] is not loop:
        entry = _locks[id(loop)] = (loop, defaultdict(asyncio.Lock))
    return entry[1][user_id]


def _as_utc(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _fresh(conn: GarminConnection) -> bool:
    exp = _as_utc(conn.expires_at)
    return exp is None or exp - datetime.now(timezone.utc) > REFRESH_MARGIN


async def _mark_reauth(db: AsyncSession, conn: GarminConnection, message: str) -> GarminAuthError:
    conn.needs_reauth = True
    conn.last_error = message
    await db.commit()
    logger.warning("Garmin link of user %d needs reconnecting: %s", conn.user_id, message)
    return GarminAuthError(message)


async def access_token(db: AsyncSession, conn: GarminConnection, client: httpx.AsyncClient,
                       rejected: str | None = None) -> str:
    """A usable access token, refreshed when it expires within 15 minutes or
    when Garmin just `rejected` it. Commits: a rotated refresh token must never
    be lost to a later rollback."""
    if rejected is None and _fresh(conn):
        return decrypt_secret(conn.access_token_encrypted)
    async with _lock_for(conn.user_id):
        # another worker may have rotated the pair meanwhile: re-read it, locked
        await db.refresh(conn, with_for_update=True)
        current = decrypt_secret(conn.access_token_encrypted)
        if conn.needs_reauth:
            await db.commit()
            raise GarminAuthError(conn.last_error or "Reconnecte Garmin.")
        if (rejected is None and _fresh(conn)) or (rejected is not None and current != rejected):
            await db.commit()  # releases the row lock
            return current
        if not conn.refresh_token_encrypted:
            raise await _mark_reauth(db, conn, "La connexion à Garmin a expiré : reconnecte-toi.")
        r = await client.post(
            f"https://diauth.{conn.domain}/di-oauth2-service/oauth/token",
            headers={**_APP_HEADERS, "Accept": "application/json", "Cache-Control": "no-cache",
                     "Authorization": "Basic " + base64.b64encode(f"{conn.di_client_id}:".encode()).decode()},
            data={"grant_type": "refresh_token", "client_id": conn.di_client_id,
                  "refresh_token": decrypt_secret(conn.refresh_token_encrypted)})
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or not isinstance(body, dict) or not body.get("access_token"):
            if r.status_code in (400, 401, 403):
                raise await _mark_reauth(db, conn, "La connexion à Garmin a expiré ou a été retirée : reconnecte-toi.")
            await db.commit()
            raise GarminError("Garmin ne répond pas pour l'instant.")
        _apply_tokens(conn, body["access_token"], body.get("refresh_token"), expires_in=body.get("expires_in"))
        await db.commit()
        logger.info("Garmin tokens refreshed for user %d", conn.user_id)
        return body["access_token"]


# ── login (python-garminconnect, in a thread) ───────────────────────────────

def _new_login_client(domain: str):
    from garminconnect.client import Client

    return Client(domain=domain)


def login_error_message(e: Exception) -> str:
    from garminconnect.exceptions import (
        GarminConnectAuthenticationError,
        GarminConnectTooManyRequestsError,
    )

    if isinstance(e, GarminConnectAuthenticationError):
        return "Email ou mot de passe Garmin incorrect."
    if isinstance(e, GarminConnectTooManyRequestsError):
        return "Garmin bloque les connexions pour l'instant. Réessaie dans une heure."
    return "Garmin ne répond pas pour l'instant. Réessaie dans quelques minutes."


def _tokens_of(client) -> dict:
    if not getattr(client, "di_token", None):
        # the web-cookie fallback gives no token PaceForge can keep
        raise GarminError("Garmin n'a pas donné d'accès durable à ce compte. Réessaie plus tard.")
    return {"di_token": client.di_token, "di_refresh_token": client.di_refresh_token,
            "di_client_id": client.di_client_id or _jwt_claims(client.di_token).get("client_id") or ""}


class _RedisStore:
    """Login state and MFA codes, shared by every web worker."""

    def __init__(self):
        import redis.asyncio as aioredis

        self.r = aioredis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

    async def set_state(self, ticket: str, state: dict) -> None:
        await self.r.set(f"garmin:login:{ticket}", json.dumps(state), ex=LOGIN_TTL)

    async def get_state(self, ticket: str) -> dict | None:
        raw = await self.r.get(f"garmin:login:{ticket}")
        return json.loads(raw) if raw else None

    async def push_code(self, ticket: str, code: str) -> None:
        key = f"garmin:code:{ticket}"
        await self.r.rpush(key, code)
        await self.r.expire(key, LOGIN_TTL)

    async def pop_code(self, ticket: str) -> str | None:
        return await self.r.lpop(f"garmin:code:{ticket}")


class MemoryStore:
    """The same, in this process only (tests)."""

    def __init__(self):
        self.states: dict[str, dict] = {}
        self.codes: dict[str, list[str]] = defaultdict(list)

    async def set_state(self, ticket, state):
        self.states[ticket] = state

    async def get_state(self, ticket):
        return self.states.get(ticket)

    async def push_code(self, ticket, code):
        self.codes[ticket].append(code)

    async def pop_code(self, ticket):
        return self.codes[ticket].pop(0) if self.codes.get(ticket) else None


_store = None
_CODE_POLL_S = 1.0


def store():
    global _store
    if _store is None:
        _store = _RedisStore()
    return _store


async def _wait_code(ticket: str) -> str | None:
    deadline = asyncio.get_running_loop().time() + MFA_WAIT_S
    while asyncio.get_running_loop().time() < deadline:
        code = await store().pop_code(ticket)
        if code:
            return code
        await asyncio.sleep(_CODE_POLL_S)
    return None


async def _set(ticket: str, user_id: int, state: str, message: str | None = None) -> None:
    await store().set_state(ticket, {"user_id": user_id, "state": state, "message": message})


async def _run_login(ticket: str, user_id: int, email: str, password: str, domain: str) -> None:
    """The whole login: credentials, the MFA code if Garmin asks for one, then
    the link stored and the first sync started. Every outcome lands in the
    ticket's state, which the page polls."""
    client = _new_login_client(domain)
    try:
        state, _ = await asyncio.to_thread(client.login, email, password, None, True)
    except Exception as e:
        logger.info("Garmin login failed for user %d: %s", user_id, type(e).__name__)
        await _set(ticket, user_id, "error", login_error_message(e))
        return
    if state == "needs_mfa":
        await _set(ticket, user_id, "mfa")
        for attempt in range(MFA_TRIES):
            code = await _wait_code(ticket)
            if code is None:
                await _set(ticket, user_id, "error", "Le code n'est pas arrivé à temps. Recommence.")
                return
            try:
                await asyncio.to_thread(client.resume_login, None, code)
                break
            except Exception as e:
                logger.info("Garmin MFA failed for user %d: %s", user_id, type(e).__name__)
                if attempt + 1 == MFA_TRIES:
                    await _set(ticket, user_id, "error", "Code refusé trop de fois. Recommence.")
                    return
                await _set(ticket, user_id, "mfa", "Code refusé : vérifie-le et réessaie.")
    try:
        tokens = _tokens_of(client)
        from app.database import async_session_factory

        async with async_session_factory() as db:
            await save_connection(db, user_id, domain, tokens)
            await db.commit()
    except GarminError as e:
        await _set(ticket, user_id, "error", str(e))
        return
    except Exception:
        logger.exception("Garmin link could not be stored for user %d", user_id)
        await _set(ticket, user_id, "error", "La connexion à Garmin a échoué. Réessaie.")
        return
    await _set(ticket, user_id, "done")
    schedule_sync(user_id)


_pending: set[asyncio.Task] = set()


def _keep(task: asyncio.Task) -> None:
    _pending.add(task)
    task.add_done_callback(_done)


def _done(task: asyncio.Task) -> None:
    _pending.discard(task)
    if not task.cancelled() and task.exception():
        logger.warning("Garmin background task failed: %r", task.exception())


async def start_login(user_id: int, email: str, password: str, region: str | None) -> str:
    """Start the login in the background; returns the ticket the page polls."""
    ticket = secrets.token_urlsafe(18)
    await _set(ticket, user_id, "running")
    _keep(asyncio.get_running_loop().create_task(
        _run_login(ticket, user_id, email.strip(), password, region_domain(region))))
    return ticket


async def login_state(ticket: str | None, user_id: int) -> dict | None:
    """{state: running|mfa|done|error, message}, None when unknown or not this athlete's."""
    if not ticket:
        return None
    state = await store().get_state(ticket)
    if not state or state.get("user_id") != user_id:
        return None
    return state


async def submit_code(ticket: str, user_id: int, code: str) -> bool:
    state = await login_state(ticket, user_id)
    if not state or state["state"] != "mfa":
        return False
    await store().push_code(ticket, code.strip())
    await _set(ticket, user_id, "running", "Vérification du code…")
    return True


# ── API client ──────────────────────────────────────────────────────────────

class GarminApi:
    """GET calls on connectapi with the athlete's token, refreshed when needed."""

    def __init__(self, db: AsyncSession, conn: GarminConnection, client: httpx.AsyncClient):
        self.db, self.conn, self.client = db, conn, client
        self.base = f"https://connectapi.{conn.domain}"
        self.token: str | None = None

    async def get(self, path: str, params: dict | None = None):
        if self.token is None:
            self.token = await access_token(self.db, self.conn, self.client)
        for attempt in range(2):
            r = await self.client.get(self.base + path, params=params, headers={
                **_APP_HEADERS, "Authorization": f"Bearer {self.token}", "Accept": "application/json"})
            if r.status_code != 401:
                break
            if attempt:
                raise await _mark_reauth(self.db, self.conn, "Garmin refuse l'accès : reconnecte-toi.")
            self.token = await access_token(self.db, self.conn, self.client, rejected=self.token)
        if r.status_code == 429:
            raise GarminRateLimited("Garmin limite les demandes pour l'instant : la synchro reprendra plus tard.")
        if r.status_code in (204, 404):
            return None
        if r.status_code >= 400:
            raise GarminError(f"Garmin ne répond pas pour l'instant (erreur {r.status_code}).")
        try:
            return r.json()
        except ValueError as e:
            raise GarminError("Garmin a renvoyé une réponse illisible.") from e


class _Fetcher:
    """Calls with a small pause between them and a cap per sync. A failed call
    only loses that piece; a rate limit or a lost link stops the sync."""

    def __init__(self, api: GarminApi):
        self.api, self.calls, self.failed = api, 0, 0

    async def __call__(self, path: str, params: dict | None = None):
        if self.calls >= MAX_CALLS:
            logger.info("Garmin call cap reached, %s skipped", path)
            return None
        if self.calls:
            await asyncio.sleep(CALL_DELAY_S)
        self.calls += 1
        try:
            return await self.api.get(path, params)
        except (GarminAuthError, GarminRateLimited):
            raise
        except (GarminError, httpx.HTTPError) as e:
            self.failed += 1
            logger.info("Garmin %s failed: %r", path, e)
            return None


# ── parsers (pure, on Garmin's JSON; unknown shapes give nothing) ───────────

def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _f(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _local_ms(ms) -> datetime | None:
    """Garmin's *TimestampLocal: the local wall clock written as epoch ms."""
    v = _f(ms)
    if v is None:
        return None
    try:
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _gmt(s) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


# sleepLevels activityLevel → stage
_LEVELS = {0: "deep", 1: "core", 2: "rem", 3: "awake"}


def parse_sleep(data) -> dict | None:
    """dailySleepData of one night (named by its wake-up day): the main sleep
    window, minutes per stage, the stage intervals when Garmin gives them, and
    the overnight HRV and resting HR it carries."""
    if not isinstance(data, dict):
        return None
    dto = data.get("dailySleepDTO") or {}
    day = _d(dto.get("calendarDate"))
    start, end = _local_ms(dto.get("sleepStartTimestampLocal")), _local_ms(dto.get("sleepEndTimestampLocal"))
    if not day or not start or not end or not timedelta(0) < end - start <= timedelta(hours=16):
        return None
    stages = {}
    for kind, key in (("deep", "deepSleepSeconds"), ("core", "lightSleepSeconds"),
                      ("rem", "remSleepSeconds"), ("awake", "awakeSleepSeconds")):
        v = _f(dto.get(key))
        if v is not None:
            stages[kind] = round(v / 60)
    out = {"day": day, "start": start, "end": end, "stages": stages, "intervals": [],
           "hrv": _f(data.get("avgOvernightHrv")), "rhr": _f(data.get("restingHeartRate"))}
    gmt_start = _f(dto.get("sleepStartTimestampGMT"))
    if gmt_start is not None:
        offset = timedelta(milliseconds=_f(dto.get("sleepStartTimestampLocal")) - gmt_start)
        for lv in data.get("sleepLevels") or []:
            if not isinstance(lv, dict):
                continue
            a, b = _gmt(lv.get("startGMT")), _gmt(lv.get("endGMT"))
            kind = _LEVELS.get(int(lv["activityLevel"])) if _f(lv.get("activityLevel")) is not None else None
            if a and b and kind and b > a:
                out["intervals"].append((kind, a + offset, b + offset))
    return out


def parse_hrv(data) -> tuple[dict[date, float], dict[date, dict]]:
    """hrv-service range: each night's average (ms) and its balanced range."""
    values, ranges = {}, {}
    rows = data.get("hrvSummaries") if isinstance(data, dict) else None
    for s in rows if isinstance(rows, list) else []:
        day = _d(s.get("calendarDate")) if isinstance(s, dict) else None
        if not day:
            continue
        if _f(s.get("lastNightAvg")) is not None:
            values[day] = _f(s["lastNightAvg"])
        base = s.get("baseline") or {}
        lo, hi = _f(base.get("balancedLow")), _f(base.get("balancedUpper"))
        if lo is not None and hi is not None:
            ranges[day] = {"lo": lo, "hi": hi, "base": _f(base.get("markerValue"))}
    return values, ranges


def parse_summary(data) -> dict | None:
    """usersummary of one day: steps, kcal, intensity minutes, stress, resting
    HR and body battery."""
    if not isinstance(data, dict) or not _d(data.get("calendarDate")):
        return None
    mod, vig = _f(data.get("moderateIntensityMinutes")), _f(data.get("vigorousIntensityMinutes"))
    return {
        "day": _d(data["calendarDate"]),
        "steps": _f(data.get("totalSteps")),
        "kcal": _f(data.get("totalKilocalories")),
        "exercise": round((mod or 0) + (vig or 0)) if mod is not None or vig is not None else None,
        "stress": _f(data.get("averageStressLevel")),
        "rhr": _f(data.get("restingHeartRate")),
        "bb_high": _f(data.get("bodyBatteryHighestValue")),
        "bb_low": _f(data.get("bodyBatteryLowestValue")),
        "bb_wake": _f(data.get("bodyBatteryAtWakeTime")),
    }


def parse_vo2max(data) -> dict[date, float]:
    """maxmet range: the running VO2 max of each day it was (re)estimated."""
    out = {}
    for row in data if isinstance(data, list) else []:
        g = (row or {}).get("generic") or {}
        day, v = _d(g.get("calendarDate")), _f(g.get("vo2MaxPreciseValue")) or _f(g.get("vo2MaxValue"))
        if day and v is not None:
            out[day] = v
    return out


# Garmin's readiness level → the wording health/sante understand
_READINESS = {"PRIME": "Ready for high intensity", "HIGH": "High intensity possible",
              "MODERATE": "Moderate", "LOW": "Light training", "POOR": "Rest"}


def parse_readiness(data) -> dict | None:
    """trainingreadiness of today: the latest score (0–100), its level and the
    recovery time left (h)."""
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    rows = [r for r in rows if isinstance(r, dict) and _f(r.get("score")) is not None]
    if not rows:
        return None
    r = max(rows, key=lambda x: str(x.get("timestampLocal") or x.get("timestamp") or ""))
    level = str(r.get("level") or "").upper()
    rec = _f(r.get("recoveryTime"))
    return {"day": _d(r.get("calendarDate")), "pct": _f(r["score"]),
            "level": _READINESS.get(level, level.title() or None),
            "full_h": round(rec / 60, 1) if rec is not None else None}


def parse_training_status(data) -> dict | None:
    """trainingstatus/aggregated: acute (7 days) and chronic (28 days) load of
    the primary device, their ratio, and Garmin's verdict when it is "very high"."""
    latest = (((data or {}).get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}) \
        if isinstance(data, dict) else {}
    entries = [e for e in latest.values() if isinstance(e, dict) and e.get("acuteTrainingLoadDTO")]
    if not entries:
        return None
    e = next((x for x in entries if x.get("primaryTrainingDevice")), entries[0])
    acute = e["acuteTrainingLoadDTO"]
    short, long_ = _f(acute.get("dailyTrainingLoadAcute")), _f(acute.get("dailyTrainingLoadChronic"))
    day = _d(e.get("calendarDate"))
    if short is None or long_ is None or not day:
        return None
    ratio = _f(acute.get("dailyAcuteChronicWorkloadRatio"))
    if ratio is None and long_ > 0:
        ratio = round(short / long_, 2)
    status = str(acute.get("acwrStatus") or "").upper()
    return {"day": day, "short": short, "long": long_, "ratio": ratio,
            "comment": "Excessive" if status == "VERY_HIGH" else None}


def parse_predictions(data) -> dict:
    """racepredictions/latest: {5k, 10k, half, marathon} in seconds."""
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, field in (("5k", "time5K"), ("10k", "time10K"), ("half", "timeHalfMarathon"),
                       ("marathon", "timeMarathon")):
        v = _f(data.get(field))
        if v:
            out[key] = round(v)
    return out


# Garmin activity type → the sport types the app knows (Strava's)
SPORT_TYPES = {
    "running": "Run", "street_running": "Run", "track_running": "Run", "treadmill_running": "Run",
    "indoor_running": "Run", "ultra_run": "Run", "obstacle_run": "Run", "virtual_run": "VirtualRun",
    "trail_running": "TrailRun",
    "cycling": "Ride", "road_biking": "Ride", "virtual_ride": "VirtualRide", "indoor_cycling": "VirtualRide",
    "mountain_biking": "MountainBikeRide", "gravel_cycling": "GravelRide", "e_bike_fitness": "EBikeRide",
    "e_bike_mountain": "EBikeRide",
    "hiking": "Hike", "walking": "Walk", "casual_walking": "Walk", "speed_walking": "Walk",
    "mountaineering": "Hike",
    "lap_swimming": "Swim", "open_water_swimming": "Swim",
    "strength_training": "WeightTraining", "yoga": "Yoga",
    "resort_skiing_snowboarding_ws": "AlpineSki", "backcountry_skiing": "BackcountrySki",
    "cross_country_skiing_ws": "NordicSki", "skate_skiing_ws": "NordicSki",
}


def activity_fields(a: dict) -> dict | None:
    """The Activity columns of one Garmin session (activitylist), in Strava's
    units (m, s, m/s, run cadence per leg)."""
    try:
        gid = int(a["activityId"])
        start = datetime.strptime(str(a["startTimeGMT"])[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    type_key = ((a.get("activityType") or {}).get("typeKey") or "").lower()
    elapsed = _f(a.get("elapsedDuration")) or _f(a.get("duration")) or 0
    moving = _f(a.get("movingDuration")) or _f(a.get("duration")) or elapsed
    spm = _f(a.get("averageRunningCadenceInStepsPerMinute"))
    return {
        "garmin_activity_id": gid,
        "sport_type": SPORT_TYPES.get(type_key, "Workout"),
        "name": (a.get("activityName") or "Séance Garmin")[:255],
        "start_date": start,
        "distance": _f(a.get("distance")) or 0.0,
        "moving_time": round(moving),
        "elapsed_time": round(elapsed),
        "total_elevation_gain": _f(a.get("elevationGain")) or 0.0,
        "average_speed": _f(a.get("averageSpeed")),
        "max_speed": _f(a.get("maxSpeed")),
        "average_heartrate": _f(a.get("averageHR")),
        "max_heartrate": _f(a.get("maxHR")),
        "average_cadence": spm / 2 if spm else _f(a.get("averageBikingCadenceInRevPerMinute")),
        "average_watts": _f(a.get("avgPower")),
        "max_watts": _f(a.get("maxPower")),
        "weighted_average_watts": _f(a.get("normPower")),
        "calories": _f(a.get("calories")),
        "raw_data": {**a, "source": "garmin"},
    }


# ── samples and daily values ────────────────────────────────────────────────

HRV_AT = time(5, 0)  # inside the 22:00 → 10:00 night window of health._hrv_day
RHR_AT = time(23, 59)  # the last of the day
VO2_AT = time(12, 0)


def _point(metric: str, day: date, at: time, value: float | None) -> Sample | None:
    if value is None:
        return None
    lo, hi = _RANGES[metric]
    if not lo <= value <= hi:
        logger.info("Garmin %s %s out of range (%s): skipped", metric, day, value)
        return None
    t = datetime.combine(day, at)
    return Sample(metric, "", t, t, round(value, 3), SOURCE)


def night_samples(night: dict) -> list[Sample]:
    """The night's stage intervals as Garmin timed them (clipped to the main
    sleep window), else stage blocks built from its minutes."""
    out = []
    for kind, a, b in sorted(night["intervals"], key=lambda x: x[1]):
        a, b = max(a, night["start"]), min(b, night["end"])
        if b > a:
            out.append(Sample("sleep", kind, a, b, round((b - a).total_seconds() / 60, 2), SOURCE))
    if out:
        return out
    stages = night["stages"]
    if sum(stages.get(k, 0) for k in ("deep", "core", "rem")) <= 0:
        return []
    return sleep_samples({"start": night["start"], "end": night["end"]}, stages, source=SOURCE)


def build_samples(data: dict, today: date) -> list[Sample]:
    out: list[Sample] = []
    hrv = dict(data["hrv"])
    for night in data["nights"].values():
        out += night_samples(night)
        if night["hrv"] is not None:
            hrv.setdefault(night["day"], night["hrv"])
    out += [s for d, v in hrv.items() if (s := _point("hrv", d, HRV_AT, v))]
    rhr = {d: v["rhr"] for d, v in data["summaries"].items() if v.get("rhr")}
    for night in data["nights"].values():
        if night["rhr"]:
            rhr.setdefault(night["day"], night["rhr"])
    out += [s for d, v in rhr.items() if (s := _point("rhr", d, RHR_AT, v))]
    out += [s for d, v in data["vo2max"].items() if d <= today and (s := _point("vo2max", d, VO2_AT, v))]
    return out


def _ok(value, lo: float, hi: float) -> bool:
    return isinstance(value, (int, float)) and lo <= value <= hi


def build_daily(data: dict, today: date) -> list[Daily]:
    """The per-day values, implausible ones left out."""
    out: list[Daily] = []
    load = data.get("load")
    if load and _ok(load["short"], 0, 5000) and _ok(load["long"], 0, 5000):
        ratio = load["ratio"] if _ok(load.get("ratio"), 0, 20) else None
        out.append(Daily("load", load["day"], load["short"],
                         {"long": load["long"], "ratio": ratio, "comment": load["comment"]}))
    rec = data.get("readiness")
    if rec and _ok(rec["pct"], 0, 100):
        full = rec["full_h"] if _ok(rec.get("full_h"), 0, 500) else None
        out.append(Daily("recovery", rec["day"] or today, rec["pct"], {"level": rec["level"], "full_h": full}))
    for d, v in data["summaries"].items():
        if _ok(v.get("stress"), 1, 100):
            out.append(Daily("stress", d, v["stress"]))
        if _ok(v.get("steps"), 1, 200_000):  # 0 steps: the watch wasn't worn that day
            out.append(Daily("steps", d, v["steps"], {"kcal": v.get("kcal"), "exercise": v.get("exercise")}))
        bb = v.get("bb_wake") if _ok(v.get("bb_wake"), 0, 100) else v.get("bb_high")
        if _ok(bb, 1, 100):
            out.append(Daily("body_battery", d, bb, {"high": v.get("bb_high"), "low": v.get("bb_low")}))
    for d, v in data["hrv_range"].items():
        if _ok(v["lo"], 5, 300) and _ok(v["hi"], v["lo"], 300):
            base = v["base"] if _ok(v.get("base"), 5, 300) else round((v["lo"] + v["hi"]) / 2, 1)
            out.append(Daily("hrv_norm", d, base, {"lo": v["lo"], "hi": v["hi"]}))
    fit: dict = {}
    vo2 = data["vo2max"].get(max(data["vo2max"], default=None)) if data["vo2max"] else None
    if _ok(vo2, 10, 95):
        fit["vo2max"] = vo2
    preds = {k: s for k, s in (data.get("predictions") or {}).items() if _ok(s, 600, 12 * 3600)}
    if preds:
        fit["pred"] = preds
    if fit:
        out.append(Daily("fitness", today, fit.get("vo2max") or 0, fit))
    return out


# ── sync ────────────────────────────────────────────────────────────────────

def _ranges(lo: date, hi: date, size: int) -> list[tuple[date, date]]:
    """[lo, hi] cut in pieces of `size` days, latest first."""
    out, end = [], hi
    while end >= lo:
        start = max(lo, end - timedelta(days=size - 1))
        out.append((start, end))
        end = start - timedelta(days=1)
    return out


async def _display_name(db: AsyncSession, conn: GarminConnection, call: _Fetcher) -> str | None:
    if not conn.display_name:
        profile = await call("/userprofile-service/socialProfile")
        name = profile.get("displayName") if isinstance(profile, dict) else None
        if name:
            conn.display_name = str(name)[:255]
            await db.commit()
    return conn.display_name


async def _fetch(db: AsyncSession, conn: GarminConnection, call: _Fetcher, days: int, activity_days: int,
                 today: date) -> dict:
    lo = today - timedelta(days=days - 1)
    data: dict = {"nights": {}, "summaries": {}, "hrv": {}, "hrv_range": {}, "vo2max": {},
                  "load": None, "readiness": None, "predictions": {}, "activities": []}
    name = await _display_name(db, conn, call)
    # what changes every day first (today's readiness and load), then the history
    data["readiness"] = parse_readiness(await call(f"/metrics-service/metrics/trainingreadiness/{today}"))
    data["load"] = parse_training_status(await call(f"/metrics-service/metrics/trainingstatus/aggregated/{today}"))
    for a, b in _ranges(lo, today, HRV_CHUNK_DAYS):
        values, ranges = parse_hrv(await call(f"/hrv-service/hrv/daily/{a}/{b}"))
        data["hrv"].update(values)
        data["hrv_range"].update(ranges)
    data["vo2max"] = parse_vo2max(await call(f"/metrics-service/metrics/maxmet/daily/{lo}/{today}"))
    page = 0
    since = today - timedelta(days=activity_days - 1)
    while page < 10:
        rows = await call("/activitylist-service/activities/search/activities",
                          {"startDate": since.isoformat(), "start": page * ACTIVITY_PAGE, "limit": ACTIVITY_PAGE})
        if not isinstance(rows, list) or not rows:
            break
        data["activities"] += rows
        if len(rows) < ACTIVITY_PAGE:
            break
        page += 1
    if name:
        q = quote(name, safe="")
        data["predictions"] = parse_predictions(await call(f"/metrics-service/metrics/racepredictions/latest/{q}"))
        for i in range(days):
            d = today - timedelta(days=i)
            night = parse_sleep(await call(f"/wellness-service/wellness/dailySleepData/{q}",
                                           {"date": d.isoformat(), "nonSleepBufferMinutes": 60}))
            if night:
                data["nights"][night["day"]] = night
            summary = parse_summary(await call(f"/usersummary-service/usersummary/daily/{q}",
                                               {"calendarDate": d.isoformat()}))
            if summary:
                data["summaries"][summary["day"]] = summary
    return data


async def import_activities(db: AsyncSession, user_id: int, rows: list[dict]) -> dict:
    """Garmin sessions into Activity: the one already imported is updated; a
    session Strava already brought (same start within 3 min, same sport family,
    close distance) only gets its Garmin id; else a new row."""
    inserted = linked = updated = 0
    for raw in rows:
        f = activity_fields(raw) if isinstance(raw, dict) else None
        if not f:
            continue
        act = (await db.execute(select(Activity).where(
            Activity.garmin_activity_id == f["garmin_activity_id"]))).scalar_one_or_none()
        if act is not None and act.user_id != user_id:
            continue
        if act is None:
            window = timedelta(seconds=_DUP_WINDOW_S)
            candidates = (await db.execute(select(Activity).where(
                Activity.user_id == user_id, Activity.garmin_activity_id.is_(None),
                Activity.start_date >= f["start_date"] - window,
                Activity.start_date <= f["start_date"] + window))).scalars().all()
            for c in candidates:
                big = max(c.distance or 0, f["distance"])
                if _same_family(c.sport_type, f["sport_type"]) and (
                        big <= 0 or abs((c.distance or 0) - f["distance"]) / big <= _DUP_DISTANCE_TOL):
                    c.garmin_activity_id = f["garmin_activity_id"]
                    linked += 1
                    break
            else:
                db.add(Activity(user_id=user_id, **f))
                inserted += 1
        elif act.strava_activity_id is None:  # Garmin's own row: keep it current
            for k, v in f.items():
                setattr(act, k, v)
            updated += 1
    await db.flush()
    return {"inserted": inserted, "linked": linked, "updated": updated}


async def sync_connection(db: AsyncSession, conn: GarminConnection) -> dict:
    """Fetch and store; returns store_samples' counts plus the sessions. Raises
    GarminAuthError when the athlete must reconnect, GarminError when Garmin
    can't be read."""
    # the history comes once: on the first sync, or while no daily value arrived yet
    have = (await db.execute(
        select(func.count(HealthMetric.id)).where(
            HealthMetric.user_id == conn.user_id, HealthMetric.source == SOURCE,
            HealthMetric.metric.in_(("steps", "stress", "load")))
    )).scalar()
    first = conn.last_sync_at is None
    days = BACKFILL_DAYS if first or not have else RECENT_DAYS
    activity_days = ACTIVITY_BACKFILL_DAYS if first else RECENT_DAYS
    today = datetime.now(timezone.utc).date()
    async with http_client() as client:
        call = _Fetcher(GarminApi(db, conn, client))
        data = await _fetch(db, conn, call, days, activity_days, today)
    if call.calls and call.failed == call.calls:
        raise GarminError("Garmin n'a renvoyé aucune donnée lisible.")

    # the athlete's today (a day ahead of the server's in Asia mornings)
    latest = max([*data["nights"], *data["summaries"]], default=None)
    today = _today(latest)
    samples = build_samples(data, today)
    await _drop_stale_intervals(db, conn.user_id, data["nights"], samples, source=SOURCE)
    result = await store_samples(db, conn.user_id, samples) if samples else {
        "received": 0, "inserted": 0, "updated": 0, "unchanged": 0, "by_metric": {}, "days": {}}
    daily = await store_daily(db, conn.user_id, build_daily(data, today), SOURCE)
    acts = await import_activities(db, conn.user_id, data["activities"])
    result["inserted"] += daily["inserted"] + acts["inserted"]
    result["updated"] += daily["updated"] + acts["updated"] + acts["linked"]
    result["by_metric"] = {**result["by_metric"], **daily["by_metric"]}
    result["activities"] = acts
    logger.info("Garmin sync user %d (%d days, %d calls, %d failed): %s, sessions %s",
                conn.user_id, days, call.calls, call.failed, result["by_metric"], acts)
    return result


async def claim(db: AsyncSession, conn: GarminConnection) -> bool:
    """Take the sync for this worker; False when another one holds it."""
    now = datetime.now(timezone.utc)
    res = await db.execute(
        update(GarminConnection)
        .where(GarminConnection.id == conn.id,
               or_(GarminConnection.sync_claimed_at.is_(None),
                   GarminConnection.sync_claimed_at < now - CLAIM_TTL))
        .values(sync_claimed_at=now)
        .execution_options(synchronize_session=False))
    await db.commit()
    if res.rowcount != 1:
        return False
    conn.sync_claimed_at = now  # so that releasing it later is seen as a change
    return True


async def run_sync(db: AsyncSession, conn: GarminConnection) -> dict | None:
    """Claim, sync, record how it went. None when a sync is already running."""
    if conn.needs_reauth or not await claim(db, conn):
        return None
    try:
        result = await sync_connection(db, conn)
        conn.last_sync_at = datetime.now(timezone.utc)
        conn.last_error = None
        outcome = {"ok": True, "result": result}
    except GarminAuthError as e:
        outcome = {"ok": False, "error": str(e)}
    except (GarminError, httpx.HTTPError) as e:
        logger.warning("Garmin sync failed for user %d: %r", conn.user_id, e)
        conn.last_error = str(e) if isinstance(e, GarminError) else "Garmin ne répond pas pour l'instant."
        outcome = {"ok": False, "error": conn.last_error}
    conn.sync_claimed_at = None
    await db.commit()
    return outcome


async def _sync_in_background(user_id: int) -> None:
    from app.database import async_session_factory

    async with async_session_factory() as db:
        conn = await connection_for(db, user_id)
        if conn:
            await run_sync(db, conn)


def schedule_sync(user_id: int) -> None:
    """Start a sync in this process without waiting for it (after connecting)."""
    if not settings.GARMIN_SYNC:
        return
    try:
        _keep(asyncio.get_running_loop().create_task(_sync_in_background(user_id)))
    except RuntimeError:
        return


async def disconnect(db: AsyncSession, conn: GarminConnection) -> None:
    """Forget the tokens (Garmin has no revocation for them: the athlete can
    also sign out of the devices in Garmin Connect). Data already received stays."""
    await db.delete(conn)
    await db.flush()
    logger.info("Garmin disconnected for user %d", conn.user_id)


# ── settings status ─────────────────────────────────────────────────────────

async def garmin_status(db: AsyncSession, user_id: int) -> dict:
    conn = await connection_for(db, user_id)
    if conn is None:
        return {"connected": False}
    rows = await db.execute(
        select(HealthSample.metric, func.count(func.distinct(func.date(HealthSample.end_at))))
        .where(HealthSample.user_id == user_id, HealthSample.source == SOURCE)
        .group_by(HealthSample.metric))
    days = dict(rows.all())
    rows = await db.execute(
        select(HealthMetric.metric, func.count(HealthMetric.id))
        .where(HealthMetric.user_id == user_id, HealthMetric.source == SOURCE,
               HealthMetric.metric.in_(DAILY_METRICS))
        .group_by(HealthMetric.metric))
    daily = dict(rows.all())
    sessions = (await db.execute(select(func.count(Activity.id)).where(
        Activity.user_id == user_id, Activity.garmin_activity_id.is_not(None)))).scalar() or 0
    claimed = _as_utc(conn.sync_claimed_at)
    return {
        "connected": True,
        "needs_reauth": conn.needs_reauth,
        "last_sync_at": conn.last_sync_at,
        "last_sync_ago": _ago(conn.last_sync_at),
        "syncing": bool(claimed and datetime.now(timezone.utc) - claimed < CLAIM_TTL),
        "last_error": conn.last_error,
        "sessions": sessions,
        "per_metric": [(METRIC_LABELS[m], days[m]) for m in METRICS if days.get(m)]
                      + [(DAILY_LABELS[m], daily[m]) for m in DAILY_LABELS if daily.get(m)],
    }
