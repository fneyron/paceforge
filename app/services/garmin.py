"""Garmin: what the athlete's watch knows about recovery and fitness, and the
sessions it recorded.

Garmin publishes no MCP server or public athlete API, so PaceForge reads
Garmin Connect the way its mobile app does, with no AI involved:
- Login: the athlete types their Garmin email and password once, in Réglages
  (Garmin's own sign-in page never sends them back to PaceForge: it loops on
  itself for a site that isn't Garmin's). The python-garminconnect library
  (mobile SSO, then the "DI" OAuth exchange) does it in a thread; the password
  only lives in that thread's memory and is never stored. When Garmin asks for a code (MFA) the login waits for it.
  A login runs in the web worker that started it, while the code may arrive on
  another one: the state and the code go through Redis (`_store`).
- Tokens: the DI access token (a JWT, ~1 day) and its refresh token are
  Fernet-encrypted. A refresh may ROTATE the refresh token, so the new pair is
  committed before anything else, one refresh at a time per athlete (row lock
  + per-process lock), like COROS.
- Data: plain JSON from connectapi.garmin.com (httpx, async). Each night
  becomes PaceForge's own values (HealthMetric via health.store_daily, source
  "Garmin"): the main sleep (window, minutes asleep), the day's naps (local
  windows, guarded), nightly HR, HRV and respiration computed from the raw
  readings inside the main window, the main night's stage minutes (the DTO's,
  else its sleepLevels summed), plus steps as a « watch worn » marker. The
  real stage intervals (sleepLevels) are kept as HealthSample rows, the
  hypnogram's timeline. No brand value is read (Training Readiness, Body
  Battery, stress, sleep score, HRV status and averages, load, VO2 max, race
  predictions, the daytime resting HR). Sessions go to Activity (a session
  Strava already has only gets its Garmin id). The key names of the raw
  readings (sleepHeartRate, hrvData, wellnessEpochRespirationDataDTOList) come
  from python-garminconnect, not from a real account.
  60 days of health and 180 days of sessions the first time, then the last
  7 days, at most every 2 hours (Celery beat, app.tasks.garmin_sync), right
  after connecting and on demand (Réglages); the 60 days once more when the
  stored nights predate a format change.

Times are the athlete's local wall clock (naive), as Garmin's *Local fields are.
"""
import asyncio
import base64
import json
import logging
import math
import secrets
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models.activity import Activity
from app.models.garmin import GarminConnection
from app.models.health import HealthMetric
from app.services import activity_env
from app.services.activity_sources import find_twin, merge_twins
from app.services.health import (
    HRV_METHOD,
    STAGES_READ,
    Daily,
    Sample,
    _ago,
    _today,
    drop_stale_intervals,
    iso_min,
    nap_daily,
    nights_upgraded,
    store_daily,
    store_samples,
)
from app.services.night_measurements import FILTER_READ, select_readings

logger = logging.getLogger(__name__)

SOURCE = "Garmin"

BACKFILL_DAYS = 60
RECENT_DAYS = 7
ACTIVITY_BACKFILL_DAYS = 180
HRV_CHUNK_DAYS = 28  # hrv-service range: 28 days at most
ACTIVITY_PAGE = 100
SYNC_EVERY = timedelta(hours=2)  # last night shows up the same morning
CLAIM_TTL = timedelta(minutes=15)  # a crashed sync frees its claim after this
# read after « Dernière synchro échouée : »; also how the next sync knows the history is owed
PARTIAL = "Garmin a cessé de répondre en cours de route, le reste de ton historique arrive à la prochaine synchro."
REFRESH_MARGIN = timedelta(minutes=15)  # DI access tokens last about a day
CALL_DELAY_S = 0.3
MAX_CALLS = 160  # a 60-day backfill takes ~135

LOGIN_TTL = 15 * 60  # how long a login (and its MFA wait) is remembered
MFA_WAIT_S = 10 * 60
MFA_TRIES = 3

# garmin.com serves every account outside China (garmin.cn is not offered)
DOMAIN = "garmin.com"

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


async def start_login(user_id: int, email: str, password: str) -> str:
    """Start the login in the background; returns the ticket the page polls."""
    ticket = secrets.token_urlsafe(18)
    await _set(ticket, user_id, "running")
    _keep(asyncio.get_running_loop().create_task(
        _run_login(ticket, user_id, email.strip(), password, DOMAIN)))
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
    (an error, a timeout, a 5xx) only loses that piece; a rate limit or a lost
    link stops the sync."""

    def __init__(self, api: GarminApi):
        self.api, self.calls, self.failed, self.down, self.stopped = api, 0, 0, 0, False

    async def __call__(self, path: str, params: dict | None = None):
        if self.stopped:
            return None
        if self.calls >= MAX_CALLS:
            logger.info("Garmin call cap reached, %s skipped", path)
            return None
        if self.calls:
            await asyncio.sleep(CALL_DELAY_S)
        self.calls += 1
        try:
            out = await self.api.get(path, params)
        except (GarminAuthError, GarminRateLimited):
            raise
        except httpx.TransportError as e:
            # one slow call is skipped; after 3 in a row Garmin is down: fail at once when nothing
            # came back, else stop calling and keep what arrived
            self.failed += 1
            self.down += 1
            logger.info("Garmin %s failed: %r", path, e)
            if self.down >= 3:
                if self.failed == self.calls:
                    raise GarminError("Garmin ne répond pas pour l'instant.") from e
                self.stopped = True
            return None
        except (GarminError, httpx.HTTPError) as e:
            self.failed += 1
            self.down = 0  # an answer, not an outage
            logger.info("Garmin %s failed: %r", path, e)
            return None
        self.down = 0
        return out


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


def _ms_or_iso(v) -> datetime | None:
    """A GMT time Garmin writes as epoch ms or as an ISO string."""
    if _f(v) is not None:
        return _local_ms(v)
    return _gmt(v) if isinstance(v, str) else None


def _readings(rows, value_keys, time_keys, offset: timedelta) -> list[tuple[datetime, float]]:
    """[(local time, value)] of a list of raw readings (unknown shapes skipped)."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        v = next((_f(r.get(k)) for k in value_keys if _f(r.get(k)) is not None), None)
        t = next((_ms_or_iso(r.get(k)) for k in time_keys if r.get(k) is not None), None)
        if v is not None and t is not None:
            out.append((t + offset, v))
    return sorted(out)


def parse_sleep(data) -> dict | None:
    """dailySleepData of one night (named by its wake-up day): the main sleep
    window, minutes asleep, the stage intervals when Garmin gives them, its
    timezone, and the raw readings PaceForge averages itself (heart rate, HRV,
    respiration). Sleep scores, HRV status and averages are not read."""
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
    asleep = _f(dto.get("sleepTimeSeconds"))
    asleep = round(asleep / 60) if asleep else sum(stages.get(k, 0) for k in ("deep", "core", "rem")) or None
    out = {"day": day, "start": start, "end": end, "asleep": asleep, "intervals": [], "tz": None,
           "hr": [], "hrv": [], "resp": [], "resp_avg": _f(dto.get("averageRespirationValue")), "stages": stages}
    gmt_start = _f(dto.get("sleepStartTimestampGMT"))
    if gmt_start is not None:
        offset = timedelta(milliseconds=_f(dto.get("sleepStartTimestampLocal")) - gmt_start)
        out["tz"] = round(offset.total_seconds() / 60)
        for lv in data.get("sleepLevels") or []:
            if not isinstance(lv, dict):
                continue
            a, b = _gmt(lv.get("startGMT")), _gmt(lv.get("endGMT"))
            kind = _LEVELS.get(_f(lv.get("activityLevel")))
            if a and b and kind and b > a:
                out["intervals"].append((kind, a + offset, b + offset))
        out["hr"] = _readings(data.get("sleepHeartRate"), ("value",), ("startGMT",), offset)
        out["hrv"] = _readings(data.get("hrvData"), ("value",), ("startGMT",), offset)
        out["resp"] = _readings(data.get("wellnessEpochRespirationDataDTOList"), ("respirationValue", "value"),
                                ("startTimeGMT", "startGMT"), offset)
    return out


def parse_naps(data) -> tuple[date, dict] | None:
    """dailySleepData's naps (dailyNapDTOS, else the DTO's napTimeSeconds):
    (day, {asleep, period (min), windows [(start, end)] local}), None without one."""
    if not isinstance(data, dict):
        return None
    dto = data.get("dailySleepDTO") or {}
    day = _d(dto.get("calendarDate"))
    local, gmt = _f(dto.get("sleepStartTimestampLocal")), _f(dto.get("sleepStartTimestampGMT"))
    offset = timedelta(milliseconds=local - gmt) if local is not None and gmt is not None else None
    asleep, windows = 0.0, []
    for n in data.get("dailyNapDTOS") or []:
        if not isinstance(n, dict):
            continue
        day = day or _d(n.get("calendarDate"))
        secs = _f(n.get("napTimeSec"))
        a, b = _gmt(n.get("napStartTimestampGMT")), _gmt(n.get("napEndTimestampGMT"))
        if secs is None and a and b:
            secs = (b - a).total_seconds()
        if not secs or secs <= 0:
            continue
        asleep += secs
        if a and b and b > a and offset is not None:
            windows.append((a + offset, b + offset))
    if not asleep:
        asleep = _f(dto.get("napTimeSeconds")) or 0
    if not day or asleep < 60:
        return None
    period = sum((b - a).total_seconds() for a, b in windows) / 60 if windows else None
    return day, {"asleep": round(asleep / 60), "period": round(period) if period else None, "windows": windows}


ALTITUDE_PLAUSIBLE = (-500, 9000)  # m


def parse_summary(data) -> dict | None:
    """usersummary of one day: steps, kcal and intensity minutes (the « watch
    worn » marker; stress, body battery and resting HR are not read), and the
    day's average altitude where the watch was worn
    (`averageMonitoringEnvironmentAltitude`, m, barometric models; key from
    python-garminconnect and ha_garmin, no live account seen: without it, the
    night's altitude comes from the activities, Santé v4.4)."""
    if not isinstance(data, dict) or not _d(data.get("calendarDate")):
        return None
    mod, vig = _f(data.get("moderateIntensityMinutes")), _f(data.get("vigorousIntensityMinutes"))
    alt = _f(data.get("averageMonitoringEnvironmentAltitude"))
    return {
        "day": _d(data["calendarDate"]),
        "steps": _f(data.get("totalSteps")),
        "kcal": _f(data.get("totalKilocalories")),
        "exercise": round((mod or 0) + (vig or 0)) if mod is not None or vig is not None else None,
        **({"alt": alt} if alt is not None and ALTITUDE_PLAUSIBLE[0] <= alt <= ALTITUDE_PLAUSIBLE[1] else {}),
    }


def _filled(summary: dict) -> bool:
    """A day the watch reported, not the dated skeleton Garmin answers for a
    day with nothing uploaded yet."""
    return _ok(summary.get("steps"), 1, 200_000) or _ok(summary.get("kcal"), 1, 20_000)


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


INDOOR_TYPES = {"treadmill_running", "indoor_running", "virtual_run", "indoor_cycling", "virtual_ride",
                "indoor_rowing"}


def activity_fields(a: dict) -> dict | None:
    """The Activity columns of one Garmin session (activitylist), in Strava's
    units (m, s, m/s, run cadence per leg); an indoor one is marked
    `trainer`, as Strava and COROS mark theirs (never « chaud », never a
    place to sleep)."""
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
        "name": (a.get("activityName") or "Activité Garmin")[:255],
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
        "raw_data": {**a, "source": "garmin", **({"trainer": True} if type_key in INDOOR_TYPES else {})},
    }


# ── samples and daily values ────────────────────────────────────────────────

MIN_READINGS = 12  # (H) distinct readings retained after sleep filtering


def _asleep_readings(readings, night, lo, hi) -> tuple[list[float], dict]:
    return select_readings([(t, v) for t, v in readings if lo <= v <= hi],
                           night["start"], night["end"], night["intervals"])


def night_samples(night: dict) -> list[Sample]:
    """The night's stage intervals as Garmin timed them, clipped to the main
    sleep window: the hypnogram's timeline. Without them, nothing (minutes
    laid out as blocks would be an invented timeline)."""
    out = []
    for kind, a, b in sorted(night["intervals"], key=lambda x: x[1]):
        a, b = max(a, night["start"]), min(b, night["end"])
        if b > a:
            out.append(Sample("sleep", kind, a, b, round((b - a).total_seconds() / 60, 2), SOURCE))
    return out


def build_samples(data: dict, today: date) -> list[Sample]:
    out: list[Sample] = []
    for night in data["nights"].values():
        out += night_samples(night)
    return out


def _ok(value, lo: float, hi: float) -> bool:
    return isinstance(value, (int, float)) and lo <= value <= hi


def night_stages(night: dict) -> dict | None:
    """The main night's stage minutes {deep, light, rem, awake}: the DTO's
    (deep/light/rem/awake seconds), else summed from its real sleepLevels
    intervals inside the main window; None without any sleep stage."""
    st = night.get("stages") or {}
    parts = {"deep": st.get("deep"), "light": st.get("core"), "rem": st.get("rem"), "awake": st.get("awake") or 0}
    if all(parts[k] is not None for k in ("deep", "light", "rem")) and parts["deep"] + parts["light"] + parts["rem"]:
        return parts
    summed = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0}
    for x in night_samples(night):
        summed["light" if x.kind == "core" else x.kind] += x.value
    out = {k: round(v) for k, v in summed.items()}
    return out if out["deep"] + out["light"] + out["rem"] else None


def night_dailies(night: dict, timeline: bool) -> list[Daily]:
    """PaceForge's values of one night: main sleep (with its stage minutes,
    night_stages), HRV (exp of the mean ln RMSSD), heart rate and respiration
    (means), from asleep stages when timed (wake and unknown gaps excluded).
    Without timed stages, a labelled main-window estimate. 12 readings after
    filtering are required (H), never rescued with awake readings."""
    d, out = night["day"], []
    if _ok(night.get("asleep"), 1, 16 * 60):
        det = {"main_start": iso_min(night["start"]), "main_end": iso_min(night["end"]),
               "period": round((night["end"] - night["start"]).total_seconds() / 60),
               "bedtime": night["start"].strftime("%H:%M"), "wake": night["end"].strftime("%H:%M"),
               "timeline": timeline}
        if night.get("tz") is not None:
            det["tz"] = night["tz"]
        stages = night_stages(night)
        if stages:
            det["stages"] = stages
        det[STAGES_READ] = True
        det[FILTER_READ] = True
        out.append(Daily("sleep", d, night["asleep"], det))
    hrv, hrv_scope = _asleep_readings(night["hrv"], night, 5, 300)
    if len(hrv) >= MIN_READINGS:
        out.append(Daily("hrv", d, round(math.exp(statistics.fmean(math.log(v) for v in hrv)), 1),
                         {"n": len(hrv), "tz": night.get("tz"), "method": HRV_METHOD, **hrv_scope}))
    hr, hr_scope = _asleep_readings(night["hr"], night, 25, 200)
    if len(hr) >= MIN_READINGS and _ok(statistics.fmean(hr), 25, 120):
        out.append(Daily("hr_night", d, round(statistics.fmean(hr), 1),
                         {"min": min(hr), "max": max(hr), "n": len(hr), "method": "points", "nap_day": False,
                          **hr_scope}))
    resp, resp_scope = _asleep_readings(night["resp"], night, 4, 40)
    if len(resp) >= MIN_READINGS:
        out.append(Daily("resp_night", d, round(statistics.fmean(resp), 1),
                         {"n": len(resp), "method": "points", **resp_scope}))
    elif _ok(night.get("resp_avg"), 4, 40):
        out.append(Daily("resp_night", d, night["resp_avg"], {"method": "garmin_summary"}))
    return out


def build_daily(data: dict, today: date) -> list[Daily]:
    """The per-day values, implausible ones left out."""
    out: list[Daily] = []
    for d, v in data["summaries"].items():
        if _ok(v.get("steps"), 1, 200_000):  # 0 steps: the watch wasn't worn that day
            det = {"kcal": v.get("kcal"), "exercise": v.get("exercise")}
            if v.get("alt") is not None:  # the night after it reads it (nights: « en altitude »)
                det["alt"] = v["alt"]
            out.append(Daily("steps", d, v["steps"], det))
    for night in (data.get("nights") or {}).values():
        out += night_dailies(night, bool(night_samples(night)))
    nights = data.get("nights") or {}
    for d, v in (data.get("naps") or {}).items():
        n = nights.get(d)
        row = nap_daily(d, v.get("asleep"), v.get("period"), v.get("windows"), (n["start"], n["end"]) if n else None)
        if row:
            out.append(row)
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
    data: dict = {"nights": {}, "naps": {}, "summaries": {}, "activities": []}
    name = await _display_name(db, conn, call)
    q = quote(name, safe="") if name else None
    # the nights first (last night is what the morning's decision reads), the sessions last
    if q:
        # Garmin dates days on the athlete's clock, which can be a day ahead of
        # the server's (UTC): ask for that day too (a future day is just empty)
        for i in range(-1, days - 1):  # same number of days, one later
            d = today - timedelta(days=i)
            raw = await call(f"/wellness-service/wellness/dailySleepData/{q}",
                             {"date": d.isoformat(), "nonSleepBufferMinutes": 60})
            night = parse_sleep(raw)
            if night:
                data["nights"][night["day"]] = night
            nap = parse_naps(raw)
            if nap:
                data["naps"][nap[0]] = nap[1]
            summary = parse_summary(await call(f"/usersummary-service/usersummary/daily/{q}",
                                               {"calendarDate": d.isoformat()}))
            if summary:
                data["summaries"][summary["day"]] = summary
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
    return data


async def import_activities(db: AsyncSession, user_id: int, rows: list[dict]) -> dict:
    """Garmin sessions into Activity, one row per outing (activity_sources): the
    one already imported is updated; a session another service already brought
    only gets its Garmin id; else a new row. Rows Strava saved apart are merged."""
    inserted = linked = updated = 0
    starts = []
    for raw in rows:
        f = activity_fields(raw) if isinstance(raw, dict) else None
        if not f:
            continue
        starts.append(f["start_date"])
        act = (await db.execute(select(Activity).where(
            Activity.garmin_activity_id == f["garmin_activity_id"]))).scalar_one_or_none()
        if act is not None and act.user_id != user_id:
            continue
        if act is None:
            twin = await find_twin(db, user_id, f["sport_type"], f["start_date"], f["distance"], f["moving_time"],
                                   Activity.garmin_activity_id.is_(None))
            if twin is not None:
                twin.garmin_activity_id = f["garmin_activity_id"]
                linked += 1
            else:
                db.add(Activity(user_id=user_id, **f))
                inserted += 1
        elif act.strava_activity_id is None and act.coros_activity_id is None:  # Garmin's own row: keep it current
            env = (act.raw_data or {}).get(activity_env.ENV_KEY)
            for k, v in f.items():
                setattr(act, k, v)
            if env is not None:  # what was looked up for it stays
                act.raw_data = {**f["raw_data"], activity_env.ENV_KEY: env}
            updated += 1
    await db.flush()
    merged = await merge_twins(db, user_id, min(starts)) if starts else 0
    return {"inserted": inserted, "linked": linked, "updated": updated, "merged": merged}


async def drop_rejected_values(db: AsyncSession, user_id: int, nights: dict, rows: list[Daily]) -> int:
    """Remove a former mean only when newly received readings cannot support it.

    Missing/failed provider responses never erase history. A parsed raw series
    rejected by the sleep filter or minimum count must not leave yesterday's
    calculation behind, even if many nights fail the stricter filter at once.
    Other providers, users and days are untouched.
    """
    produced = {(r.metric, r.day) for r in rows}
    deleted = 0
    for metric, key in (("hrv", "hrv"), ("hr_night", "hr"), ("resp_night", "resp")):
        rejected = [d for d, night in nights.items() if night[key] and (metric, d) not in produced]
        if rejected:
            result = await db.execute(delete(HealthMetric).where(
                HealthMetric.user_id == user_id, HealthMetric.source == SOURCE,
                HealthMetric.metric == metric, HealthMetric.date.in_(rejected)))
            deleted += result.rowcount or 0
    return deleted


async def sync_connection(db: AsyncSession, conn: GarminConnection) -> dict:
    """Fetch and store; returns store_samples' counts plus the sessions. Raises
    GarminAuthError when the athlete must reconnect, GarminError when Garmin
    can't be read."""
    # the history comes once: on the first sync, or while no daily value arrived yet,
    # and once more when the nights are still in their pre-2026-10 format, or were
    # written before their stage minutes (2026-10-08) or awake filtering (2026-10-10)
    have = (await db.execute(
        select(func.count(HealthMetric.id)).where(
            HealthMetric.user_id == conn.user_id, HealthMetric.source == SOURCE,
            HealthMetric.metric.in_(("steps", "sleep")))
    )).scalar()
    first = conn.last_sync_at is None or conn.last_error == PARTIAL
    upgraded = (await nights_upgraded(db, conn.user_id, SOURCE, days=BACKFILL_DAYS)
                and await nights_upgraded(db, conn.user_id, SOURCE, days=BACKFILL_DAYS, key=STAGES_READ)
                and await nights_upgraded(db, conn.user_id, SOURCE, days=BACKFILL_DAYS, key=FILTER_READ))
    days = BACKFILL_DAYS if first or not have or not upgraded else RECENT_DAYS
    activity_days = ACTIVITY_BACKFILL_DAYS if first else RECENT_DAYS
    today = datetime.now(timezone.utc).date()
    async with http_client() as client:
        call = _Fetcher(GarminApi(db, conn, client))
        data = await _fetch(db, conn, call, days, activity_days, today)
    if call.calls and call.failed == call.calls:
        raise GarminError("Garmin n'a renvoyé aucune donnée lisible.")

    # the athlete's today (a day ahead of the server's in Asia mornings); a day
    # Garmin only answers with its dated empty summary doesn't count
    latest = max([*data["nights"], *(d for d, v in data["summaries"].items() if _filled(v))], default=None)
    today = _today(latest)
    samples = build_samples(data, today)
    await drop_stale_intervals(db, conn.user_id, SOURCE, {d: (n["start"], n["end"]) for d, n in data["nights"].items()},
                               samples)
    result = await store_samples(db, conn.user_id, samples) if samples else {
        "received": 0, "inserted": 0, "updated": 0, "unchanged": 0, "by_metric": {}, "days": {}}
    daily_rows = build_daily(data, today)
    result["deleted"] = await drop_rejected_values(db, conn.user_id, data["nights"], daily_rows)
    daily = await store_daily(db, conn.user_id, daily_rows, SOURCE)
    acts = await import_activities(db, conn.user_id, data["activities"])
    result["inserted"] += daily["inserted"] + acts["inserted"]
    result["updated"] += daily["updated"] + acts["updated"] + acts["linked"]
    result["by_metric"] = {**result["by_metric"], **daily["by_metric"]}
    result["activities"] = acts
    # each new outdoor session's altitude and weather (Open-Meteo), the backlog a few at a time; never fails a sync
    result["env"] = await activity_env.enrich(db, conn.user_id)
    # Garmin stopped answering halfway through the history: keep it, but ask for it all again next time
    result["backfill_partial"] = call.stopped and (days == BACKFILL_DAYS or activity_days == ACTIVITY_BACKFILL_DAYS)
    logger.info("Garmin sync user %d (%d days, %d calls, %d failed%s): %s, sessions %s",
                conn.user_id, days, call.calls, call.failed, ", stopped" if call.stopped else "",
                result["by_metric"], acts)
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
    # as if read back: releasing it is then a change, even when nothing was written in between
    # (a plain assignment over a loaded None, set back to None, writes nothing)
    set_committed_value(conn, "sync_claimed_at", now)
    return True


async def run_sync(db: AsyncSession, conn: GarminConnection) -> dict | None:
    """Claim, sync, record how it went. None when a sync is already running."""
    if conn.needs_reauth or not await claim(db, conn):
        return None
    try:
        result = await sync_connection(db, conn)
        conn.last_sync_at = datetime.now(timezone.utc)
        # a history cut short is not done: said so, and the next sync asks for all of it again
        conn.last_error = PARTIAL if result.get("backfill_partial") else None
        outcome = {"ok": True, "result": result}
    except GarminAuthError as e:
        outcome = {"ok": False, "error": str(e)}
    except (GarminError, httpx.HTTPError) as e:
        logger.warning("Garmin sync failed for user %d: %r", conn.user_id, e)
        conn.last_error = str(e) if isinstance(e, GarminError) else "Garmin ne répond pas pour l'instant."
        outcome = {"ok": False, "error": conn.last_error}
    except asyncio.CancelledError:  # the worker stops (a deploy): never leave the link claimed
        await _release(db, conn, conn.user_id)
        raise
    except Exception:  # a bug must not leave the link « en cours » for the claim's 15 min
        logger.exception("Garmin sync crashed for user %d", conn.user_id)
        await db.rollback()
        conn.last_error = "La synchro a échoué : réessaie plus tard."
        outcome = {"ok": False, "error": conn.last_error}
    conn.sync_claimed_at = None
    await db.commit()
    return outcome


async def _release(db: AsyncSession, conn, user_id: int) -> None:
    """Best effort, shielded from the cancellation that called it."""
    try:
        await db.rollback()
        conn.sync_claimed_at = None
        await asyncio.shield(db.commit())
    except Exception:
        logger.warning("Garmin: could not release the sync claim of user %d", user_id)


async def _sync_in_background(user_id: int) -> None:
    from app.database import async_session_factory

    async with async_session_factory() as db:
        conn = await connection_for(db, user_id)
        if conn:
            await run_sync(db, conn)


def schedule_sync(user_id: int) -> bool:
    """Start a sync in this process without waiting for it (after connecting,
    on opening Santé). False when syncs are off here."""
    if not settings.GARMIN_SYNC:
        return False
    try:
        _keep(asyncio.get_running_loop().create_task(_sync_in_background(user_id)))
    except RuntimeError:
        return False
    return True


async def disconnect(db: AsyncSession, conn: GarminConnection) -> None:
    """Forget the tokens (Garmin has no revocation for them: the athlete can
    also sign out of the devices in Garmin Connect). Data already received stays."""
    await db.delete(conn)
    await db.flush()
    logger.info("Garmin disconnected for user %d", conn.user_id)


# ── settings status ─────────────────────────────────────────────────────────

async def garmin_status(db: AsyncSession, user_id: int) -> dict:
    from app.services.coros import synced_counts

    conn = await connection_for(db, user_id)
    if conn is None:
        return {"connected": False}
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
        "per_metric": await synced_counts(db, user_id, SOURCE),
    }
