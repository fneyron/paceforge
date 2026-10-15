"""COROS: the athlete's nights and sessions, from their watch.

COROS publishes its data as an MCP server (https://mcp.coros.com/mcp) behind
OAuth 2.1. PaceForge is a plain server-side client of it, no AI involved:
- OAuth: endpoints discovered from the well-known documents (the region's
  server may differ), one public client registered per server and redirect URI
  for the whole app (OAuthClient), PKCE S256, `resource` on every request
  (RFC 8707).
- MCP over streamable HTTP: initialize, notifications/initialized, then
  tools/call; answers are plain JSON or server-sent events, the tool result is
  prose parsed here with regexes (pure functions, unknown text is skipped).
- Tokens are Fernet-encrypted. The refresh token ROTATES on each use (the old
  one is then rejected), so the new pair is committed before anything else, and
  one refresh at a time per athlete (row lock + per-process lock).
- Sync → PaceForge's own nightly values, one row per night (HealthMetric via
  health.store_daily, source "COROS"): the main sleep as COROS gives it
  (window, minutes asleep), the day's naps (local windows, guarded; one over
  the night or ending ≤ 30 min before it is kept: it is the night's start,
  folded into it when the nights are read, nights.build_nights, H), nightly
  HRV computed from the raw readings inside the main window (that start
  included: fold_overview), the « Sleep HR » line of the main
  sleep's summary, plus the day's heart rate and steps as « watch worn »
  markers, and the main night's stage minutes from its « Sleep Summary »
  when that summary is the main sleep's (its total is the main period within
  15 min, H: never the « daily » stage ratios, which mix the naps in). No
  brand value is read (recovery, load, stress, sleep score, HRV range,
  fitness and VO2 max, the daytime resting HR), and no stage interval is
  written (COROS has no stage timeline). Sessions go to Activity; for those
  only COROS has, their detail (D+, times) and their laps' whole-activity row
  (its max HR: hr_max() then has real peaks, Santé v4.4) are read once each;
  that same answer's auto laps (time, average HR) are kept as Strava's laps
  (Santé's Entraînement dial weighs them).
  60 days the first time, then the last 7 days, at most every 2 hours (Celery
  beat, app.tasks.coros_sync), right after connecting and on demand (Réglages),
  always in the Celery worker (a web request only queues it: queue_sync);
  the 60 days once more when the stored nights predate a format change. The
  nightly rows of the days a sync's parsers read that it no longer yields are
  deleted (covered_days: what was read, never what was asked for; a few at
  most, health.store_daily): a value COROS no longer gives never stays shown.
  Activities cover 180 days initially and weekly, otherwise 7 days. Full
  responses are split into smaller date windows; committed coverage in Redis
  lets bounded syncs resume unfinished windows without skipping history.

Times are the athlete's local wall clock (naive), as COROS writes them.
"""
import asyncio
import base64
import hashlib
import json
import logging
import math
import re
import secrets
import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models.activity import Activity
from app.models.coros import CorosConnection, OAuthClient
from app.models.health import HealthMetric
from app.services import activity_env
from app.services.activity_sources import find_twin, merge_twins
from app.services.health import (
    DAILY_LABELS,
    DAILY_METRICS,
    HRV_METHOD,
    NAPS_FOLDED,
    STAGES_READ,
    Daily,
    _ago,
    _today,
    fold_naps,
    iso_min,
    nap_daily,
    nap_shares,
    nights_upgraded,
    store_daily,
)

logger = logging.getLogger(__name__)

SOURCE = "COROS"
SCOPE = "openid mcp.tools offline_access"
PROTOCOL_VERSION = "2025-06-18"
CALLBACK_PATH = "/coros/callback"

BACKFILL_DAYS = 60
RECENT_DAYS = 7
HRV_CHUNK_DAYS = 7  # querySleepHrv: 7 days max per call
SLEEP_CHUNK_DAYS = 31
SYNC_EVERY = timedelta(hours=2)  # last night shows up the same morning
CLAIM_TTL = timedelta(minutes=15)  # a crashed sync frees its claim after this
# read after « Dernière synchro échouée : »; also how the next sync knows the history is owed
PARTIAL = ("COROS a cessé de répondre en cours de route, le reste de ton historique arrive avec la synchro complète "
           "de demain.")
# its wording until 2026-10-14 (the history then came at the next sync), still in some links' last_error
_LEGACY_PARTIAL = "COROS a cessé de répondre en cours de route, le reste de ton historique arrive à la prochaine synchro."
HRV_PARTIAL = "La lecture de la VFC est incomplète. Les autres données reçues sont conservées ; réessaie la synchro."
HRV_UNREADABLE = "Le format de la VFC renvoyé par COROS n’a pas pu être lu. Les autres données reçues sont conservées."
SLEEP_PARTIAL = "La lecture du sommeil est incomplète ; elle est nécessaire au calcul de la VFC. Les données déjà reçues sont conservées."
PHASES_PARTIAL = "Les phases du sommeil n’ont pas pu être entièrement actualisées. Réessaie la synchronisation."
ACTIVITIES_PARTIAL = "La lecture des activités COROS est incomplète. Les activités reçues sont conservées ; la prochaine synchro reprendra les périodes restantes."
REFRESH_MARGIN = timedelta(days=1)  # access tokens last ~30 days
CALL_DELAY_S = 0.5
MAX_CALLS = 45  # a 60-day backfill takes 15 at most, the sessions 3 + 10 × (detail + laps): 38
ACTIVITY_BACKFILL_DAYS = 180  # the sessions' history, as Garmin's
SESSION_CHUNK_DAYS = 60
SESSION_LIMIT = 200  # records per querySportRecords call
SESSION_CALLS = 12  # subdivisions share this budget; remaining windows resume after the next committed sync
SESSION_RECHECK = timedelta(days=7)  # also recover late uploads outside the recent window
DETAILS_PER_SYNC = 10  # sessions only COROS has whose detail (the D+) or laps (the max HR) are read, newest first
DETAIL, LAPS = "detail", "lap_data"  # raw_data keys: what was read once for a session only COROS has
REVISION = "paceforge_revision"
KEPT = (DETAIL, LAPS, activity_env.ENV_KEY, REVISION, "paceforge_heat", "paceforge_dplus")
RECORD_FORMAT = 2  # raw_data["format"]: the session list read with its « Start Coordinates » (Santé v4.4)

# tests swap in an httpx.MockTransport here
_transport: httpx.AsyncBaseTransport | None = None


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport, timeout=30, follow_redirects=False,
                             headers={"User-Agent": "PaceForge (paceforge.fr)"})


class CorosError(Exception):
    """A sync or connect failure; the message is plain French, shown as is."""


class CorosAuthError(CorosError):
    """The link is gone (refresh rejected): the athlete must reconnect."""


class TokenError(CorosError):
    def __init__(self, code: str):
        super().__init__(f"COROS a refusé le jeton ({code}).")
        self.code = code


class ToolError(CorosError):
    pass


class McpUnauthorized(CorosError):
    pass


class McpUnavailable(CorosError):
    """A 5xx: COROS is down or overloaded for that request."""


class CorosKeyError(CorosError):
    """A stored token no configured key decrypts (app.crypto: a key missing from ENCRYPTION_KEYS). Nothing is
    written about it: the link is only skipped, so putting the key back recovers it with no reconnecting."""


# ── OAuth: discovery, client registration, PKCE ─────────────────────────────

def redirect_uri() -> str:
    return f"{settings.BASE_URL.rstrip('/')}{CALLBACK_PATH}"


def _https(url, what: str) -> str:
    if not isinstance(url, str) or urlsplit(url).scheme != "https":
        raise CorosError(f"COROS : adresse {what} invalide.")
    return url


def _well_known(issuer: str, name: str) -> str:
    """RFC 8414: the well-known segment goes between the host and the issuer's path."""
    parts = urlsplit(issuer)
    return f"{parts.scheme}://{parts.netloc}/.well-known/{name}{parts.path.rstrip('/')}"


# A COROS account lives on one regional server, and only that one can log it
# in: the athlete picks it (mcp.coros.com just answers with the server nearest
# to PaceForge, which is not the athlete's).
REGIONS = {
    "monde": ("Monde", "https://mcpus.coros.com"),
    "europe": ("Europe", "https://mcpeu.coros.com"),
    "chine": ("Chine", "https://mcpcn.coros.com"),
}
DEFAULT_REGION = "monde"


def region_server(region: str | None) -> str:
    return REGIONS.get(region or "", REGIONS[DEFAULT_REGION])[1]


async def discover(client: httpx.AsyncClient, region: str | None = None) -> dict:
    """{issuer, resource, authorization_endpoint, token_endpoint,
    registration_endpoint, revocation_endpoint} from the region's MCP server
    protected-resource document, then its authorization server's metadata."""
    r = await client.get(f"{region_server(region)}/.well-known/oauth-protected-resource/mcp")
    r.raise_for_status()
    prm = r.json()
    resource = _https(prm.get("resource"), "du serveur")
    servers = prm.get("authorization_servers") or []
    issuer = _https(servers[0] if servers else None, "d'autorisation")
    meta = None
    for name in ("oauth-authorization-server", "openid-configuration"):
        r = await client.get(_well_known(issuer, name))
        if r.status_code == 200:
            meta = r.json()
            break
    if not meta:
        raise CorosError("COROS : configuration de connexion introuvable.")
    out = {
        "issuer": meta.get("issuer") or issuer,
        "resource": resource,
        "authorization_endpoint": _https(meta.get("authorization_endpoint"), "d'autorisation"),
        "token_endpoint": _https(meta.get("token_endpoint"), "des jetons"),
        "registration_endpoint": _https(meta.get("registration_endpoint"), "d'inscription"),
        "revocation_endpoint": meta.get("revocation_endpoint"),
    }
    if "S256" not in (meta.get("code_challenge_methods_supported") or ["S256"]):
        raise CorosError("COROS : PKCE S256 non proposé.")
    return out


async def register_client(client: httpx.AsyncClient, registration_endpoint: str, redirect: str) -> str:
    """Dynamic client registration (RFC 7591): a public client, PKCE only."""
    r = await client.post(registration_endpoint, json={
        "client_name": settings.APP_NAME,
        "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
    })
    if r.status_code not in (200, 201):
        raise CorosError(f"COROS : inscription de PaceForge refusée ({r.status_code}).")
    client_id = r.json().get("client_id")
    if not client_id:
        raise CorosError("COROS : inscription de PaceForge incomplète.")
    logger.info("COROS client registered at %s for %s", registration_endpoint, redirect)
    return client_id


async def client_id_for(db: AsyncSession, client: httpx.AsyncClient, disc: dict, redirect: str) -> str:
    """The app-wide client for this server and redirect URI, registered on first use."""
    q = select(OAuthClient.client_id).where(
        OAuthClient.issuer == disc["issuer"], OAuthClient.redirect_uri == redirect)
    existing = (await db.execute(q)).scalar_one_or_none()
    if existing:
        return existing
    client_id = await register_client(client, disc["registration_endpoint"], redirect)
    try:
        async with db.begin_nested():
            db.add(OAuthClient(issuer=disc["issuer"], redirect_uri=redirect, client_id=client_id))
    except IntegrityError:  # another worker registered at the same moment: use theirs
        return (await db.execute(q)).scalar_one()
    return client_id


async def forget_client(db: AsyncSession, issuer: str) -> None:
    """The server no longer knows our client: the next connect registers a new one."""
    await db.execute(delete(OAuthClient).where(OAuthClient.issuer == issuer))
    logger.warning("COROS client for %s forgotten (invalid_client)", issuer)


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(disc: dict, client_id: str, redirect: str, state: str, challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": disc["resource"],
    }
    sep = "&" if "?" in disc["authorization_endpoint"] else "?"
    return disc["authorization_endpoint"] + sep + urlencode(params, quote_via=quote)


async def _token_request(client: httpx.AsyncClient, endpoint: str, data: dict) -> dict:
    r = await client.post(endpoint, data=data, headers={"Accept": "application/json"})
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and isinstance(body, dict) and body.get("access_token"):
        return body
    code = body.get("error") if isinstance(body, dict) else None
    raise TokenError(code or f"http_{r.status_code}")


def _apply_tokens(conn: CorosConnection, tokens: dict) -> None:
    conn.access_token_encrypted = encrypt_secret(tokens["access_token"])
    if tokens.get("refresh_token"):
        conn.refresh_token_encrypted = encrypt_secret(tokens["refresh_token"])
    try:
        expires_in = int(tokens.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    conn.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None


async def complete_connection(db: AsyncSession, user_id: int, pending: dict, code: str) -> CorosConnection:
    """Exchange the authorization code and store the link (replacing an old one)."""
    async with http_client() as client:
        tokens = await _token_request(client, pending["token_endpoint"], {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
            "client_id": pending["client_id"],
            "code_verifier": pending["verifier"],
            "resource": pending["resource"],
        })
    conn = await connection_for(db, user_id)
    if conn is None:
        conn = CorosConnection(user_id=user_id)
        db.add(conn)
    _apply_tokens(conn, tokens)
    conn.issuer = pending["issuer"]
    conn.client_id = pending["client_id"]
    conn.resource_url = pending["resource"]
    conn.token_endpoint = pending["token_endpoint"]
    conn.revocation_endpoint = pending.get("revocation_endpoint")
    conn.connected_at = datetime.now(timezone.utc)
    conn.last_sync_at = None  # (re)connected: backfill again, the upsert is idempotent
    conn.sync_claimed_at = None
    conn.last_error = None
    conn.needs_reauth = False
    await db.flush()
    logger.info("COROS connected for user %d", user_id)
    return conn


async def connection_for(db: AsyncSession, user_id: int) -> CorosConnection | None:
    return (await db.execute(
        select(CorosConnection).where(CorosConnection.user_id == user_id))).scalar_one_or_none()


# ── tokens: refresh with rotation ───────────────────────────────────────────

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


def _fresh(conn: CorosConnection) -> bool:
    exp = _as_utc(conn.expires_at)
    return exp is None or exp - datetime.now(timezone.utc) > REFRESH_MARGIN


async def _mark_reauth(db: AsyncSession, conn: CorosConnection, message: str) -> CorosAuthError:
    conn.needs_reauth = True
    conn.last_error = message
    await db.commit()
    logger.warning("COROS link of user %d needs reconnecting: %s", conn.user_id, message)
    return CorosAuthError(message)


EXPIRED = "La connexion à COROS a expiré : reconnecte-toi."


async def _stored(db: AsyncSession, conn: CorosConnection, encrypted: bytes) -> str:
    """A stored token; one no configured key decrypts raises CorosKeyError, and nothing is written about it."""
    try:
        return decrypt_secret(encrypted)
    except InvalidToken:
        raise CorosKeyError(EXPIRED) from None


def tokens_readable(conn: CorosConnection) -> bool:
    """Whether the configured keys decrypt this link's tokens. Not, its link is paused, untouched: it shows
    « reconnecte-toi » (coros_status, computed when read) until the key is back, and is never synced meanwhile."""
    for encrypted in (conn.access_token_encrypted, conn.refresh_token_encrypted):
        if encrypted:
            try:
                decrypt_secret(encrypted)
            except InvalidToken:
                return False
    return True


def _key_missing(conn: CorosConnection) -> None:
    logger.error("COROS tokens of user %d: no configured key decrypts them; the link is skipped, nothing is "
                 "changed. Put back the key that encrypted them in ENCRYPTION_KEYS.", conn.user_id)


async def access_token(db: AsyncSession, conn: CorosConnection, client: httpx.AsyncClient,
                       rejected: str | None = None) -> str:
    """A usable access token, refreshed when it expires within a day or when
    the server just `rejected` it. Commits: a rotated refresh token must never
    be lost to a later rollback."""
    if rejected is None and _fresh(conn):
        return await _stored(db, conn, conn.access_token_encrypted)
    async with _lock_for(conn.user_id):
        # another worker may have rotated the pair meanwhile: re-read it, locked
        await db.refresh(conn, with_for_update=True)
        if conn.needs_reauth:
            await db.commit()
            raise CorosAuthError(conn.last_error or "Reconnecte COROS.")
        current = await _stored(db, conn, conn.access_token_encrypted)
        if (rejected is None and _fresh(conn)) or (rejected is not None and current != rejected):
            await db.commit()  # releases the row lock
            return current
        if not conn.refresh_token_encrypted:
            raise await _mark_reauth(db, conn, EXPIRED)
        refresh = await _stored(db, conn, conn.refresh_token_encrypted)
        try:
            tokens = await _token_request(client, conn.token_endpoint, {
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": conn.client_id,
                "resource": conn.resource_url,
            })
        except TokenError as e:
            if e.code in ("invalid_grant", "invalid_client", "unauthorized_client"):
                if e.code == "invalid_client":
                    await forget_client(db, conn.issuer)
                raise await _mark_reauth(db, conn, "La connexion à COROS a expiré ou a été retirée : reconnecte-toi.") from e
            await db.commit()
            raise CorosError("COROS ne répond pas pour l'instant.") from e
        _apply_tokens(conn, tokens)
        await db.commit()
        logger.info("COROS tokens refreshed for user %d", conn.user_id)
        return tokens["access_token"]


# ── MCP client (streamable HTTP) ────────────────────────────────────────────

def parse_rpc_response(content_type: str, body: str, request_id) -> dict:
    """The JSON-RPC result for `request_id`, from a plain JSON body or an SSE
    stream (`data: {...}` lines, events separated by a blank line)."""
    messages: list = []
    if "text/event-stream" in (content_type or "") or re.match(r"\s*(event|data|id|:)", body or ""):
        for event in re.split(r"\r?\n\r?\n", body or ""):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if data.strip():
                try:
                    messages.append(json.loads(data))
                except ValueError:
                    logger.info("COROS MCP: unreadable event skipped")
    else:
        try:
            parsed = json.loads(body or "")
        except ValueError as e:
            raise CorosError("COROS a renvoyé une réponse illisible.") from e
        messages = parsed if isinstance(parsed, list) else [parsed]
    for msg in messages:
        if isinstance(msg, dict) and msg.get("id") == request_id and ("result" in msg or "error" in msg):
            if "error" in msg:
                err = msg["error"] or {}
                raise CorosError(f"COROS : erreur {err.get('code')} ({str(err.get('message'))[:80]}).")
            return msg["result"] or {}
    raise CorosError("COROS n'a pas répondu à la requête.")


def _unquote(text: str) -> str:
    """COROS sends each text as a JSON string literal ("\"Resting…\\n2026-…\""):
    decoded, the lines are real lines again."""
    t = text.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        try:
            decoded = json.loads(t)
        except ValueError:
            return text
        if isinstance(decoded, str):
            return decoded
    return text


def tool_text(result: dict) -> str:
    """The text of a tools/call result; ToolError when the tool reported one."""
    text = "\n".join(_unquote(c.get("text", "")) for c in result.get("content") or []
                     if isinstance(c, dict) and c.get("type") == "text")
    if result.get("isError"):
        raise ToolError(text[:200] or "outil en erreur")
    return text


class McpSession:
    def __init__(self, client: httpx.AsyncClient, url: str, token: str):
        self.client, self.url, self.token = client, url, token
        self.session_id: str | None = None
        self.initialized = False
        self._id = 0

    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.initialized:
            h["MCP-Protocol-Version"] = PROTOCOL_VERSION
        return h

    async def _post(self, method: str, params: dict | None = None, notify: bool = False) -> dict | None:
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            self._id += 1
            payload["id"] = self._id
        r = await self.client.post(self.url, json=payload, headers=self._headers())
        if r.status_code == 401:
            raise McpUnauthorized("COROS a refusé le jeton.")
        if r.status_code >= 400:
            error = McpUnavailable if r.status_code >= 500 else CorosError
            raise error(f"COROS ne répond pas pour l'instant (erreur {r.status_code}).")
        sid = r.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        if notify:
            return None
        return parse_rpc_response(r.headers.get("content-type", ""), r.text, payload["id"])

    async def initialize(self) -> None:
        await self._post("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": settings.APP_NAME, "version": "1.0"},
        })
        self.initialized = True
        await self._post("notifications/initialized", notify=True)

    async def call_tool(self, name: str, arguments: dict) -> str:
        return tool_text(await self._post("tools/call", {"name": name, "arguments": arguments}) or {})

    async def close(self) -> None:
        if not self.session_id:
            return
        try:
            await self.client.delete(self.url, headers=self._headers())
        except httpx.HTTPError:
            pass


# ── parsers (the tools answer in prose) ─────────────────────────────────────

_NUM = r"(\d+(?:[.,]\d+)?)"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _date(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_duration(text: str | None) -> int | None:
    """'9h 48min' → 588, '14 min' → 14, '1h 0min' → 60."""
    m = re.match(r"\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*min)?", text or "")
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def parse_hrv_points(text: str) -> list[tuple[int, int, float]]:
    """querySleepHrv's raw time series: one (UTC timestamp, timezone in quarter
    hours, RMSSD ms) per reading, all days together (the series is grouped by
    the athlete's local calendar day, not by night: a night spans two groups).
    The official « HRV Avg », its normal range and verdict are not read."""
    parts = re.split(r"^.*Time Series.*$", text or "", maxsplit=1, flags=re.M)
    if len(parts) < 2:
        return []
    out = {}
    for m in re.finditer(r"timestamp=(\d+),\s*timezone=(-?\d+),\s*hrv=" + _NUM + r"\s*ms", parts[1]):
        ts, tz, v = int(m.group(1)), int(m.group(2)), _num(m.group(3))
        if abs(tz) <= 14 * 4 and 5 <= v <= 300:
            out[ts] = (ts, tz, v)
    return [out[k] for k in sorted(out)]


def hrv_days(text: str) -> set[date]:
    """The local days querySleepHrv answered for (with or without readings)."""
    parts = re.split(r"^.*Time Series.*$", text or "", maxsplit=1, flags=re.M)
    if len(parts) < 2:
        return set()
    return {d for d in (_date(*m.groups()) for m in re.finditer(r"^\s*(\d{4})-(\d{2})-(\d{2}):", parts[1], re.M))
            if d}


def hrv_answer_read(text: str) -> bool:
    """An understood series (possibly empty), not merely an HTTP success.

    COROS can publish the assessment before the raw readings, or explicitly
    report no readings. Neither is a sync failure. Unknown prose or renamed
    raw data must not mark a whole requested range as successfully parsed.
    """
    parts = re.split(r"^.*Time Series.*$", text or "", maxsplit=1, flags=re.M)
    empty = r"No (?:HRV )?(?:readings|data)(?: available)?(?: for (?:this|the|selected) period)?\.?"
    if len(parts) == 2:
        for line in parts[1].splitlines():
            line = line.strip()
            if (not line or re.fullmatch(r"[=─-]+", line)
                    or re.fullmatch(r"\d{4}-\d{2}-\d{2}:", line)
                    or re.fullmatch(r"(?:\d{4}-\d{2}-\d{2}:\s*)?" + empty, line, re.I)):
                continue
            if not re.match(r"timestamp=\d+,\s*timezone=-?\d+,\s*hrv=" + _NUM + r"\s*ms\b", line):
                return False
        return True
    if re.search(r"\btimestamp\s*=", text or ""):
        return False
    return bool(re.search(r"\bHRV Avg:\s*" + _NUM + r"\s*ms\b", text or "")
                or re.search(r"\bNo (?:HRV )?(?:readings|data)(?: available)? for (?:this|the|selected) period\b",
                             text or "", re.I))


def local_time(ts: int, tz: int) -> datetime:
    """A reading's local wall clock: UTC + timezone × 15 min (checked on France,
    UTC+3 and UTC+9 nights and across the 2025 autumn clock change)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) + timedelta(minutes=15 * tz)


HRV_MIN_POINTS = 12  # (H) readings inside the main window for a nightly value


def night_hrv(points: list[tuple[int, int, float]], start: datetime, end: datetime) -> dict | None:
    """PaceForge's nightly HRV: exp(mean ln RMSSD) of the readings inside the
    main window [start, end] (naps and daytime readings out), with how many
    readings and their timezone (quarter hours). None under 12 readings (H)."""
    inside = [(tz, v) for ts, tz, v in points if start <= local_time(ts, tz) <= end]
    if len(inside) < HRV_MIN_POINTS:
        return None
    value = math.exp(statistics.fmean(math.log(v) for _, v in inside))
    return {"value": round(value, 1), "n": len(inside), "tz": inside[-1][0]}


def _field_minutes(block: str, label: str) -> int | None:
    m = re.search(label + r":\s*([^\n]+)", block)
    return parse_duration(m.group(1)) if m else None


def parse_sleep_overview(text: str) -> dict[date, dict]:
    """querySleepOverview, per wake-up day with a main sleep: its window, asleep
    and period minutes, awake time, and COROS's « Daily Sleep (incl. naps) »
    (a cross-check of PaceForge's own 24-h total). Older records have no
    « (asleep) » or « Period » line: their « Main Sleep » is the window's
    length. The sleep score and the stage ratios (daily scope: naps mixed in)
    are not read."""
    out = {}
    heads = list(re.finditer(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$", text or "", re.M))
    for i, h in enumerate(heads):
        day = _date(*h.groups())
        block = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        w = re.search(r"Main Sleep Window:\s*(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})\s*-\s*"
                      r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})", block)
        if not day or not w:
            continue
        try:
            g = [int(x) for x in w.groups()]
            start, end = datetime(*g[:5]), datetime(*g[5:])
        except ValueError:
            continue
        if not timedelta(0) < end - start <= timedelta(hours=16):
            continue

        out[day] = {
            "start": start, "end": end,
            "asleep": _field_minutes(block, r"Main Sleep \(asleep\)"),
            "period": _field_minutes(block, r"Main Sleep Period \(incl\. awake\)"),
            "awake": _field_minutes(block, r"Awake Time"),
            "daily": _field_minutes(block, r"Daily Sleep"),
        }
    return out


_RANGE = r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})\s*-\s*(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})"


def parse_naps(text: str) -> dict[date, dict]:
    """querySleepOverview: each wake-up day's naps {asleep, period (min),
    windows [(start, end)] local, legacy}, the main night apart (days without a
    nap left out). « (includes legacy reported durations) » sets legacy; the
    guards (health.nap_guard) come when the value is built."""
    out = {}
    heads = list(re.finditer(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$", text or "", re.M))
    for i, h in enumerate(heads):
        day = _date(*h.groups())
        block = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        asleep = _field_minutes(block, r"Naps Total(?: \(asleep\))?")
        if not day or not asleep:
            continue
        windows = []
        for line in re.findall(r"^\s*Nap Windows?:\s*(.+)$", block, re.M):
            for g in re.finditer(_RANGE, line):
                try:
                    a, b = datetime(*map(int, g.groups()[:5])), datetime(*map(int, g.groups()[5:]))
                except ValueError:
                    continue
                windows.append((a, b))
        legacy = bool(re.search(r"Naps Total[^\n]*legacy", block, re.I))
        out[day] = {"asleep": asleep, "period": _field_minutes(block, r"Naps Period(?: \(incl\. awake\))?"),
                    "windows": windows, "legacy": legacy}
    return out


def overview_read(text: str, overview: dict[date, dict], naps: dict[date, dict]) -> dict[str, set[date]]:
    """querySleepOverview: the wake-up days whose sleep and naps its parsers
    actually read (`overview`: parse_sleep_overview, `naps`: parse_naps, of
    that same text), what a stale row is judged against (covered_days):
    - sleep: the days with a main window read, and the nap-only days (naps
      read, no « Main Sleep » word in the block);
    - nap: the days with naps read, and the days with a main window read and
      « Naps Total: 0 min ».
    Never a « not available yet » day, nor one whose wording was not
    understood (a window the parser skipped is no day read)."""
    out: dict[str, set[date]] = {"sleep": set(overview), "nap": set(naps)}
    for day, block in _day_blocks(text, r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$"):
        if day in naps and "Main Sleep" not in block:
            out["sleep"].add(day)
        if day in overview and _field_minutes(block, r"Naps Total(?: \(asleep\))?") == 0:
            out["nap"].add(day)
    return out


def parse_daily_sleep(text: str) -> dict[date, dict]:
    """queryDailyHealthData: each wake-up day's « Sleep Summary » {total,
    deep, core, rem, awake (min), hr: {avg, min, max} | None}. The block
    describes the episode COROS selected: the main sleep when there is one (its
    total is the main period, naps out), else the lone nap (2026-09-25)."""
    out = {}
    for day, block in _day_blocks(text, r"^\s*---\s*(\d{4})(\d{2})(\d{2})\s*---\s*$"):
        if "Sleep Summary" not in block:
            continue
        block = block[block.index("Sleep Summary"):]
        row: dict = {}
        for kind, label in (("total", "Total"), ("deep", "Deep"), ("core", "Light"), ("rem", "REM"),
                            ("awake", "Awake")):
            m = re.search(r"\b" + label + r":\s*([^|\n]+)", block)
            v = parse_duration(m.group(1)) if m else None
            if v is not None:
                row[kind] = v
        m = re.search(r"Sleep HR:\s*Avg\s*(\d+)\s*bpm\s*\|\s*Min\s*(\d+)\s*bpm\s*\|\s*Max\s*(\d+)\s*bpm", block)
        row["hr"] = {"avg": float(m.group(1)), "min": float(m.group(2)), "max": float(m.group(3))} if m else None
        if row.get("total") is not None:
            out[day] = row
    return out


# COROS sport codes → Strava's sport types (what the rest of PaceForge reads)
SPORTS = {100: "Run", 101: "Run", 102: "TrailRun", 103: "Run", 104: "Hike", 105: "Hike", 106: "RockClimbing",
          200: "Ride", 201: "VirtualRide", 202: "EBikeRide", 203: "GravelRide", 204: "MountainBikeRide",
          205: "EMountainBikeRide", 299: "Ride", 300: "Swim", 301: "Swim", 400: "Workout", 401: "Workout",
          402: "WeightTraining", 500: "AlpineSki", 501: "Snowboard", 502: "NordicSki", 503: "BackcountrySki",
          700: "Rowing", 701: "Rowing", 702: "Kayaking", 704: "Kayaking", 705: "Windsurf", 800: "RockClimbing",
          801: "RockClimbing", 802: "RockClimbing", 900: "Walk", 902: "StairStepper", 903: "Elliptical",
          904: "Yoga", 905: "Pilates", 1000: "Badminton", 1001: "TableTennis", 1003: "Soccer", 1004: "Pickleball",
          1005: "Tennis", 1006: "Padel"}
INDOOR = {101, 201, 400, 701}
MULTISPORT = {10000, 10001, 10002, 10003}  # one COROS record; Strava has one row per leg


def _clock_s(text: str | None) -> int | None:
    """'16:53:27' → 60807, '30:06' → 1806."""
    parts = (text or "").strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    out = 0
    for p in parts:
        out = out * 60 + int(p)
    return out


_COORD = r"(-?\d{1,3}(?:\.\d+)?)"


def _latlng(m) -> list[float] | None:
    """« Start Coordinates: 33.245998, 126.510002 » → [lat, lon]; None when absent, out of range or 0, 0."""
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return [lat, lon] if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0) else None


def parse_sport_records(text: str) -> list[dict]:
    """querySportRecords: one dict per session {label, code, name, day, start,
    end (UTC timestamps), duration (s), km, hr, kcal, start_latlng (« Start
    Coordinates », outdoor GPS sessions only: Strava's key, R2 and R4)}."""
    out = []
    heads = list(re.finditer(r"^\s*\d+\.\s+(.+?)\s+—\s+(\d{4})-(\d{2})-(\d{2})\s*$", text or "", re.M))
    for i, h in enumerate(heads):
        block = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        label = re.search(r"LabelId:\s*(\d+)", block)
        code = re.search(r"SportType:\s*(\d+)", block)
        start = re.search(r"startTimestamp=(\d+)", block)
        if not (label and code and start):
            continue
        end = re.search(r"endTimestamp=(\d+)", block)
        name = re.search(r"^\s*Location:\s*(.+?)\s*$", block, re.M)
        dur = re.search(r"Duration:\s*([\d:]+)", block)
        dist = re.search(r"Distance:\s*" + _NUM + r"\s*(km|m)\b", block)  # under 1 km COROS writes metres
        hr = re.search(r"Avg HR:\s*" + _NUM + r"\s*bpm", block)
        kcal = re.search(r"Calories:\s*([\d,  ]+)\s*kcal", block)
        coords = re.search(r"Start Coordinates:\s*" + _COORD + r"\s*,\s*" + _COORD, block)
        out.append({
            "label": int(label.group(1)), "code": int(code.group(1)), "type": h.group(1).strip(),
            "name": name.group(1) if name else None, "day": "-".join(h.groups()[1:]),
            "start": int(start.group(1)), "end": int(end.group(1)) if end else None,
            "duration": _clock_s(dur.group(1)) if dur else None,
            "km": (_num(dist.group(1)) / (1000 if dist.group(2) == "m" else 1)) if dist else None,
            "hr": _num(hr.group(1)) if hr else None,
            "kcal": _int(kcal.group(1)) if kcal else None,
            "start_latlng": _latlng(coords),
        })
    return out


def _sport_records_count(text: str | None) -> int | None:
    count = re.search(r"^\s*Sport Records\s+[—–-]\s+[^\n]*\((\d+) records?\)\s*$", text or "", re.M)
    return int(count.group(1)) if count else None


def sport_records_readable(text: str | None, records: list[dict]) -> bool:
    """Only an explicit, fully parsed record count proves a window was read.

    In particular an unknown answer and a recognised zero-record answer are
    different. Keep valid rows from a partially understood answer, but retry it.
    """
    if _sport_records_count(text) != len(records):
        return False
    numbered = re.findall(r"^\s*\d+\.\s+", text or "", re.M)
    return (len(numbered) == len(records)
            and len({r["label"] for r in records}) == len(records)
            and all(session_fields(r, None) is not None for r in records))


def parse_activity_detail(text: str) -> dict:
    """getActivityDetail: what the session list lacks (D+, total and moving time, cadence, power)."""
    out = {}
    m = re.search(r"Elevation Gain(?:\s*/\s*Loss)?:\s*" + _NUM + r"\s*m", text or "")
    if m:
        out["dplus"] = _num(m.group(1))
    for key, label in (("total", "Total Time"), ("moving", "Workout Time")):
        m = re.search(label + r":\s*([\d:]+)", text or "")
        if m and _clock_s(m.group(1)):
            out[key] = _clock_s(m.group(1))
    m = re.search(r"^\s*Distance:\s*" + _NUM + r"\s*(km|m)\b", text or "", re.M)
    if m:
        out["km"] = _num(m.group(1)) / (1000 if m.group(2) == "m" else 1)
    m = re.search(r"Moving Average Pace:\s*(\d+):(\d{2})\s*/km", text or "")
    if m and out.get("km"):  # stops left out, as Strava's and Garmin's moving time
        out["moving_pace"] = int(m.group(1)) * 60 + int(m.group(2))
    for key, label in (("hr_max", "Max(?:imum)? Heart Rate"), ("cadence", "Average Cadence"),
                       ("watts", "Average Power")):
        v = _field(text or "", label)
        if v is not None:
            out[key] = v
    return out


HR_PLAUSIBLE = (40, 250)  # bpm: a lap value outside is a wrist or sensor glitch


def _lap_hr(rows) -> list[float]:
    out = []
    for lap in rows if isinstance(rows, list) else []:
        v = lap.get("maxHr") if isinstance(lap, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and HR_PLAUSIBLE[0] <= v <= HR_PLAUSIBLE[1]:
            out.append(float(v))
    return out


def _block_max_hr(block: dict) -> float | None:
    """One record's (or one leg's) max HR: its whole-activity row (lap group type −1), else its highest lap."""
    groups = [g for g in block.get("lapGroups") or [] if isinstance(g, dict)]
    whole = [v for g in groups if g.get("type") == -1 for v in _lap_hr(g.get("laps"))]
    every = [v for g in groups for v in _lap_hr(g.get("laps"))]
    return max(whole or every, default=None)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _strava_lap(lap) -> dict | None:
    """One COROS lap in Strava's shape — moving_time (s), average_heartrate, and its max_heartrate and distance (m:
    COROS counts centimetres) when it has them — or None without a time or a plausible average HR."""
    if not isinstance(lap, dict):
        return None
    t, hr = lap.get("time"), lap.get("avgHr")
    if not (_number(t) and round(t) > 0 and _number(hr) and HR_PLAUSIBLE[0] <= hr <= HR_PLAUSIBLE[1]):
        return None
    out = {"moving_time": round(t), "average_heartrate": float(hr)}
    if _number(peak := lap.get("maxHr")) and HR_PLAUSIBLE[0] <= peak <= HR_PLAUSIBLE[1]:
        out["max_heartrate"] = float(peak)
    if _number(cm := lap.get("distance")) and cm > 0:
        out["distance"] = cm / 100
    return out


def _block_laps(block: dict) -> list[dict]:
    """One record's (or one leg's) laps in Strava's shape: its first lap group other than the whole-activity row
    whose laps carry a time and an average HR (COROS's auto laps), else that whole-activity row (type −1)."""
    groups = [g for g in block.get("lapGroups") or [] if isinstance(g, dict)]
    for g in sorted(groups, key=lambda g: g.get("type") == -1):  # the whole-activity row last
        laps = [x for x in map(_strava_lap, g.get("laps") if isinstance(g.get("laps"), list) else []) if x]
        if laps:
            return laps
    return []


def parse_laps(text: str) -> dict:
    """queryActivityLapData: the session's highest heart rate and its laps, {"hr_max": bpm, "laps": [...]} with
    what it has, or {} (Live 2026-10-08: a JSON object, its `lapGroups` hold the auto laps — each one's `time`,
    `avgHr` — and a whole-activity row of type −1 with `maxHr`; a yoga or pilates session has one plain lap, a
    multisport record one block per leg in `sportDataDetails`: its laps are its legs'). The laps in Strava's shape
    (_strava_lap), what nights.hr_segments reads: Santé's Entraînement dial and « sortie intense le soir » see a
    COROS-only session's laps as a Strava one's (no other call: the max HR's answer). Not JSON: any « maxHr » or
    « Max HR » figure the text carries, no laps."""
    try:
        data = json.loads(text or "")
    except ValueError:
        data = None
    laps = []
    if isinstance(data, dict):
        legs = [c for c in data.get("sportDataDetails") or [] if isinstance(c, dict)]
        peak = max((v for b in [data] + legs if (v := _block_max_hr(b)) is not None), default=None)
        laps = [x for b in legs or [data] for x in _block_laps(b)]
    else:
        found = [_num(m) for m in re.findall(r"max\s*_?\s*(?:hr|heart\s*rate)\"?\s*[:=]\s*" + _NUM, text or "", re.I)]
        peak = max((v for v in found if HR_PLAUSIBLE[0] <= v <= HR_PLAUSIBLE[1]), default=None)
    out = {"hr_max": peak} if peak is not None else {}
    if laps:
        out["laps"] = laps
    return out


def session_offset(start_ts: int, day: str | None, hint: float | None) -> float | None:
    """A session's UTC offset (s): the quarter hour nearest the athlete's
    current offset (else UTC) that puts its start on COROS's own local date —
    right for a session recorded in another time zone; the hint without that date."""
    try:
        d = date.fromisoformat(str(day))
        start = datetime.fromtimestamp(int(start_ts), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError, OSError):
        return hint
    base = hint if hint is not None else 0.0
    fits = [q * 900 for q in range(-48, 57) if (start + timedelta(seconds=q * 900)).date() == d]
    return float(min(fits, key=lambda off: abs(off - base))) if fits else hint


def session_fields(rec: dict, utc_offset: float | None) -> dict | None:
    """The Activity columns of one COROS session, in Strava's units (m, s, m/s, run cadence per leg)."""
    try:
        start = datetime.fromtimestamp(int(rec["start"]), tz=timezone.utc)
        label = int(rec["label"])
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    span = (rec["end"] - rec["start"]) if rec.get("end") and rec["end"] > rec["start"] else None
    moving = rec.get("duration") or span or 0
    if moving <= 0:
        return None
    meters = (rec.get("km") or 0) * 1000
    raw = {**rec, "source": "coros", "format": RECORD_FORMAT}
    offset = session_offset(rec["start"], rec.get("day"), utc_offset)
    if offset is not None:
        raw["utc_offset"] = offset  # the key Strava uses: the session's local day
    if rec.get("code") in INDOOR:
        raw["trainer"] = True
    if rec.get("code") in MULTISPORT:
        raw["multisport"] = True
    return {
        "coros_activity_id": label,
        "sport_type": SPORTS.get(rec.get("code"), "Workout"),
        "name": (rec.get("name") or rec.get("type") or "Activité COROS")[:255],
        "start_date": start,
        "distance": meters,
        "moving_time": int(moving),
        "elapsed_time": int(max(span or 0, moving)),
        "total_elevation_gain": 0.0,  # in the detail, read afterwards
        "average_speed": meters / moving if meters else None,
        "average_heartrate": rec.get("hr"),
        "calories": rec.get("kcal"),
        "raw_data": raw,
    }


def parse_tz_offset(text: str) -> float | None:
    """querySleepHrv's time series: the athlete's UTC offset (s) from its last
    `timezone=` (quarter hours)."""
    found = re.findall(r"timezone=(-?\d+)", text or "")
    if not found:
        return None
    q = int(found[-1])
    return q * 900.0 if abs(q) <= 14 * 4 else None


def _int(s: str) -> int:
    """'18,055' → 18055 (thousands separators: comma, space, narrow space)."""
    return int(re.sub(r"[,\s  ]", "", s))


def _day_blocks(text: str, head: str):
    """(day, block) for each block of `text` headed by a line matching `head`
    (three groups: year, month, day)."""
    heads = list(re.finditer(head, text or "", re.M))
    for i, h in enumerate(heads):
        day = _date(*h.groups()[:3])
        if day:
            yield day, text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]


def _field(block: str, label: str) -> float | None:
    m = re.search(r"\b" + label + r":\s*" + _NUM, block, re.I)
    return _num(m.group(1)) if m else None


def parse_avg_hr(text: str) -> dict[date, dict]:
    """queryAvgHeartRate: '2026-10-04: 53 bpm (Min: 36, Max: 98)' lines."""
    out = {}
    for m in re.finditer(r"^\s*(\d{4})-(\d{2})-(\d{2}):\s*(\d+)\s*bpm(?:\s*\(Min:\s*(\d+),\s*Max:\s*(\d+)\))?",
                         text or "", re.M):
        d = _date(*m.groups()[:3])
        if d:
            out[d] = {"avg": float(m.group(4)),
                      "min": int(m.group(5)) if m.group(5) else None,
                      "max": int(m.group(6)) if m.group(6) else None}
    return out


def parse_daily_activity(text: str) -> dict[date, dict]:
    """queryDailyHealthData, per day: steps, calories (kcal) and exercise
    minutes (the watch's stress score is not read)."""
    out = {}
    for day, block in _day_blocks(text, r"^\s*---\s*(\d{4})(\d{2})(\d{2})\s*---\s*$"):
        row: dict = {}
        m = re.search(r"Steps:\s*([\d,\s  ]*\d)", block)
        if m:
            row["steps"] = _int(m.group(1))
        m = re.search(r"Calories:\s*([\d,\s  ]*\d)\s*kcal", block)
        if m:
            row["kcal"] = _int(m.group(1))
        m = re.search(r"Exercise:\s*([^|\n]+)", block)
        if m and parse_duration(m.group(1)) is not None:
            row["exercise"] = parse_duration(m.group(1))
        if row:
            out[day] = row
    return out


# ── the nightly values ──────────────────────────────────────────────────────

HR_PERIOD_TOLERANCE = 15  # (H) min between the summary's total and the main period


def _ok(value, lo: float, hi: float) -> bool:
    return isinstance(value, (int, float)) and lo <= value <= hi


def _main_period(ov: dict) -> int:
    """The main episode's minutes, awake included: the period line, else the window."""
    return ov.get("period") or round((ov["end"] - ov["start"]).total_seconds() / 60)


def night_stages(row: dict | None, ov: dict) -> dict | None:
    """The main night's stage minutes {deep, light, rem, awake} from its
    « Sleep Summary » (parse_daily_sleep), only when that summary is the main
    sleep's: its total is the main period within 15 min (H, as hr_night) and
    its stages add up to that total within 15 min (H). Never the « Sleep
    metrics scope: daily » ratios (the naps are mixed in)."""
    if not row or row.get("total") is None or abs(row["total"] - _main_period(ov)) > HR_PERIOD_TOLERANCE:
        return None
    parts = {"deep": row.get("deep"), "light": row.get("core"), "rem": row.get("rem"), "awake": row.get("awake") or 0}
    if any(parts[k] is None for k in ("deep", "light", "rem")) or not parts["deep"] + parts["light"] + parts["rem"]:
        return None
    if abs(sum(parts.values()) - row["total"]) > HR_PERIOD_TOLERANCE:
        return None
    return parts


def daily_sleep_readable(text: str | None, daily: dict, activity: dict) -> bool:
    """Only understood daily data can replace the known sleep phases.

    A renamed summary or unreadable stage field is not an explicit absence.
    Ordinary activity-only days and explicit empty responses remain valid.
    """
    empty = r"No (?:(?:daily health|sleep|sleep stage) )?(?:data|records|stages)(?: available)?(?: for (?:this|the|selected) period)?[.!]?"
    blocks = list(_day_blocks(text or "", r"^\s*---\s*(\d{4})(\d{2})(\d{2})\s*---\s*$"))
    if not blocks:
        body = re.sub(r"(?m)^Daily Health Data[^\n]*\n?|^=+\n?|^Note: sleep entries are dated by their wake-up day\.\n?",
                      "", text or "").strip()
        return bool(re.fullmatch(empty, body, re.I))
    for day, block in blocks:
        summary = block.split("Sleep Summary:", 1)
        if (re.fullmatch(empty, block.strip(), re.I)
                or len(summary) == 2 and re.fullmatch(empty, summary[1].strip(), re.I)):
            continue
        if re.search(r"\bsleep\b", block, re.I):
            if day not in daily or not {"total", "deep", "core", "rem"} <= daily[day].keys():
                return False
        elif day not in activity:
            return False
    return True


async def preserve_unread_stages(db: AsyncSession, user_id: int, rows: list[Daily],
                                 read: tuple[date, date] | None) -> None:
    """Retain old COROS phase details only for the exact same unread episode."""
    pending = {r.day: r for r in rows if r.metric == "sleep" and not (r.details or {}).get("stages")
               and not (read and read[0] <= r.day <= read[1])}
    if not pending:
        return
    existing = (await db.execute(select(HealthMetric).where(
        HealthMetric.user_id == user_id, HealthMetric.source == SOURCE, HealthMetric.metric == "sleep",
        HealthMetric.date.in_(sorted(pending))))).scalars()
    for old in existing:
        row, before = pending[old.date], old.details or {}
        after = row.details or {}
        if (abs(old.value - row.value) > 1e-6 or any(before.get(k) is None or before.get(k) != after.get(k)
                                                    for k in ("main_start", "main_end", "period"))):
            continue
        # Do not carry timestamps, windows, totals or another source's data.
        if before.get("stages"):
            after["stages"] = dict(before["stages"])
        if before.get(STAGES_READ):
            after[STAGES_READ] = True  # this unchanged episode was read earlier, not during this failed attempt
        row.details = after


def fold_overview(overview: dict[date, dict], naps: dict[date, dict]) -> dict[date, dict]:
    """The main nights with the « naps » that are their start or end folded
    into their window (health.fold_naps, H: one overlapping the main window or
    ending ≤ 30 min before it, listed under that day or the day before), the
    window the nightly HRV is read inside (hrv_dailies: the night's first hour
    is the night's). Nothing else reads it: the sleep and nap rows are stored
    as COROS gives them and folded when read (nights.build_nights), so a later
    sync that reads a night without the day before its nap is listed under
    never rewrites it shorter, nor loses the nap."""
    parts = {d: nap_shares(v.get("asleep") or 0, v.get("windows")) for d, v in naps.items()}
    nights = dict(overview)
    for d in sorted(overview):
        ov = overview[d]
        days = (d - timedelta(days=1), d)
        cands = [p for x in days for p in parts.get(x, [])]
        if not cands:
            continue
        start, end, _, joined, _ = fold_naps(ov["start"], ov["end"], 0, cands)
        if joined:
            nights[d] = {**ov, "start": start, "end": end}
            for x in days:
                parts[x] = [p for p in parts.get(x, []) if p not in joined]
    return nights


def sleep_dailies(overview: dict[date, dict], tz: dict[date, int] | None = None, daily: dict[date, dict] | None = None,
                  read: tuple[date, date] | None = None) -> list[Daily]:
    """One `sleep` value per main night, as COROS gives it: minutes asleep in
    the main episode, its window (local ISO), period and timezone, and its
    stage minutes (night_stages) from `daily` (parse_daily_sleep). COROS gives
    no stage timeline, so no stage intervals are written (timeline False).
    `read`: the days queryDailyHealthData answered for: their nights are
    marked STAGES_READ (their stages were looked for, found or not). Every
    night is marked NAPS_FOLDED (its naps over it kept: folded when read)."""
    out = []
    for d, ov in overview.items():
        period = _main_period(ov)
        asleep = ov.get("asleep")
        if asleep is None:  # older records: « Main Sleep » is the window, awake inside it
            asleep = period - (ov.get("awake") or 0)
        if not _ok(asleep, 1, 16 * 60):
            continue
        det = {"main_start": iso_min(ov["start"]), "main_end": iso_min(ov["end"]), "period": period,
               "bedtime": ov["start"].strftime("%H:%M"), "wake": ov["end"].strftime("%H:%M"), "timeline": False}
        if (tz or {}).get(d) is not None:
            det["tz"] = tz[d] * 15
        if _ok(ov.get("daily"), 1, 24 * 60):
            det["daily"] = ov["daily"]
        stages = night_stages((daily or {}).get(d), ov)
        if stages:
            det["stages"] = stages
        if read and read[0] <= d <= read[1]:
            det[STAGES_READ] = True
        det[NAPS_FOLDED] = True
        out.append(Daily("sleep", d, asleep, det))
    return out


def hrv_dailies(points: list[tuple[int, int, float]], overview: dict[date, dict],
                days: set[date] | None = None) -> list[Daily]:
    """PaceForge's nightly HRV for each main night whose two local days (the
    evening's and the morning's) were both read (`days`: querySleepHrv's, and
    querySleepOverview's for the « nap » folded into the window,
    fold_overview): a night computed from half its readings, or over half its
    window, would overwrite a whole one."""
    out = []
    for d, ov in overview.items():
        if days is not None and not {d, d - timedelta(days=1)} <= days:
            continue
        v = night_hrv(points, ov["start"], ov["end"])
        if v:
            out.append(Daily("hrv", d, v["value"], {"n": v["n"], "tz": v["tz"] * 15, "method": HRV_METHOD}))
    return out


def hr_night_dailies(daily: dict[date, dict], overview: dict[date, dict], naps: dict[date, dict]) -> list[Daily]:
    """COROS's nightly heart rate: the « Sleep HR » line of the sleep summary,
    kept only when that summary is the main sleep's (a main window exists and
    its total is the main period within 15 min (H): the nap-only day and the
    malformed 2026-07-17 record go). A day with a nap is marked: whether COROS
    leaves the nap out of that line could not be checked (no HR curve)."""
    out = []
    for d, row in daily.items():
        hr, ov = row.get("hr"), overview.get(d)
        if not hr or not ov or abs(row["total"] - _main_period(ov)) > HR_PERIOD_TOLERANCE:
            continue
        if not (_ok(hr["avg"], 25, 120) and hr["min"] <= hr["avg"] <= hr["max"]):
            continue
        out.append(Daily("hr_night", d, hr["avg"], {"min": hr["min"], "max": hr["max"],
                                                     "method": "coros_sleep_summary", "nap_day": d in naps}))
    return out


def nap_dailies(naps: dict[date, dict], overview: dict[date, dict] | None = None) -> list[Daily]:
    """One `nap` value per day with naps (Garmin's are built the same way), the
    guards of health.nap_guard applied (one over the main window kept: folded
    into the night when read)."""
    out = []
    for d, v in naps.items():
        ov = (overview or {}).get(d)
        row = nap_daily(d, v.get("asleep"), v.get("period"), v.get("windows"),
                        (ov["start"], ov["end"]) if ov else None, legacy=bool(v.get("legacy")))
        if row:
            out.append(row)
    return out


def build_daily(data: dict, today: date) -> list[Daily]:
    """The per-day values, implausible ones left out (an odd wording must not
    write a 50 000 bpm day). No brand value: recovery, load, stress, sleep score,
    HRV range, fitness and the daytime resting HR are not read."""
    out: list[Daily] = []
    overview = data.get("overview") or {}
    for d, v in (data.get("hr_day") or {}).items():
        if _ok(v["avg"], 25, 230):
            out.append(Daily("hr_day", d, v["avg"], {"min": v["min"], "max": v["max"]}))
    for d, v in (data.get("activity") or {}).items():
        # 0 steps and 0 kcal: the watch wasn't worn that day
        if _ok(v.get("steps"), 1, 200_000) or _ok(v.get("kcal"), 1, 20_000):
            out.append(Daily("steps", d, v.get("steps") or 0,
                             {"kcal": v.get("kcal"), "exercise": v.get("exercise")}))
    # the nights and naps as COROS gives them (the « naps » that are a night's start folded into it when read, H);
    # the HRV read inside the night's window with that start (fold_overview)
    naps = data.get("naps") or {}
    hrv = hrv_dailies(data.get("hrv_points") or [], fold_overview(overview, naps), hrv_read(data))
    tz = {r.day: r.details["tz"] // 15 for r in hrv}
    out += sleep_dailies(overview, tz, data.get("daily") or {}, data.get("daily_read"))
    out += hrv
    out += hr_night_dailies(data.get("daily") or {}, overview, naps)
    out += nap_dailies(naps, overview)
    return out


def hrv_read(data: dict) -> set[date] | None:
    """The days a nightly HRV may be computed for (hrv_dailies): querySleepHrv's
    answered days, and querySleepOverview's read whole when known (the « nap »
    of the day before that is a night's start: fold_overview). None: no gate."""
    days, whole = data.get("hrv_days"), data.get("overview_days")
    if whole is None:
        return days
    return set(whole) if days is None else set(days) & set(whole)


def covered_days(data: dict) -> dict[str, set[date]]:
    """The days this sync's parsers read for each nightly value (store_daily
    deletes the COROS rows of them it no longer yields), never the days it
    asked for: an answer worded anew must delete nothing (owner's data is
    history).
    - sleep, nap: overview_read's days of the answers read whole (`read`);
    - hr_night: those of them whose « Sleep Summary » was read (`daily`: a
      rejected summary still deletes a stale nightly HR; a day without one,
      or an answer not understood, deletes nothing);
    - hrv: the main nights of them whose HRV hrv_dailies may compute
      (hrv_read: both local days answered and parsed) and whose window holds
      at least one parsed reading: under 12 there, the stale value goes; a
      night without any reading (an answer worded anew, COROS's empty but
      ordinary answers) deletes nothing."""
    read = data.get("read") or {}
    sleep, nap = set(read.get("sleep") or ()), set(read.get("nap") or ())
    out = {"sleep": sleep, "nap": nap, "hr_night": {d for d in sleep if d in (data.get("daily") or {})}}
    gate, points = hrv_read(data), data.get("hrv_points") or []
    times = sorted(local_time(ts, tz) for ts, tz, _ in points)
    windows = fold_overview(data.get("overview") or {}, data.get("naps") or {})
    out["hrv"] = {d for d, ov in windows.items()
                  if d in sleep and (gate is None or {d - timedelta(days=1), d} <= gate)
                  and bisect_right(times, ov["end"]) > bisect_left(times, ov["start"])}
    return out


# ── sync ────────────────────────────────────────────────────────────────────

def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _ranges(lo: date, hi: date, size: int) -> list[tuple[date, date]]:
    """[lo, hi] cut in pieces of `size` days, latest first."""
    out, end = [], hi
    while end >= lo:
        start = max(lo, end - timedelta(days=size - 1))
        out.append((start, end))
        end = start - timedelta(days=1)
    return out


def _history_key(conn: CorosConnection) -> str:
    # A new connection must never inherit an old account's completed history.
    return f"paceforge:coros:sessions:v1:{conn.id}:{conn.connected_at.isoformat()}"


def _history_store(key: str, value: dict | None = None) -> dict | None:
    """Redis only remembers committed coverage. Losing it safely starts a new read."""
    try:
        from app.services.strava_state import _redis

        if value is not None:
            _redis().set(key, json.dumps(value), ex=30 * 86400)
            return value
        raw = _redis().get(key)
        state = json.loads(raw) if raw else None
        if not isinstance(state, dict) or not isinstance(state.get("pending"), list):
            return None
        date.fromisoformat(state["through"])
        datetime.fromisoformat(state["checked_at"])
        for a, b in state["pending"]:
            if date.fromisoformat(a) > date.fromisoformat(b):
                return None
        return state
    except Exception:
        logger.warning("COROS activity-history state unavailable; history remains retryable")
        return None


async def _fetch_sessions(call, ranges: list[tuple[date, date]], *,
                          budget: int | None = None) -> tuple[list[dict], list[tuple[date, date]]]:
    """Read bounded windows, bisect a full page and retain every unproven window.

    There is no offset pagination on querySportRecords. Even a full single day
    cannot be advertised as complete; it stays pending while other days progress.
    """
    queue, pending, records = list(ranges), [], {}
    used = 0
    budget = SESSION_CALLS if budget is None else min(budget, SESSION_CALLS)
    while queue and used < budget and not call.stopped and call.calls < MAX_CALLS:
        a, b = queue.pop(0)
        used += 1
        text = await call("querySportRecords", {
            "startDate": _ymd(a), "endDate": _ymd(b), "sportTypeCodes": [65535], "minDistanceKm": 0,
            "maxDistanceKm": 10000, "minDurationMinutes": 0, "maxDurationMinutes": 100000,
            "maxAveragePace": "", "locationKeyword": "", "limit": SESSION_LIMIT})
        got = _parsed(parse_sport_records, text) or []
        records.update((r["label"], r) for r in got)
        if max(len(got), _sport_records_count(text) or 0) >= SESSION_LIMIT:
            if a < b:
                middle = a + (b - a) // 2
                queue[:0] = [(middle + timedelta(days=1), b), (a, middle)]
            else:
                pending.append((a, b))
                logger.warning("COROS activity limit reached for one day: %s", a)
        elif not sport_records_readable(text, got):
            pending.append((a, b))
            logger.warning("COROS activity response incomplete for %s to %s", a, b)
    # A broken window is retried after the as-yet unread ones, so it cannot starve them.
    return list(records.values()), queue + pending


def _without_ranges(ranges: list[tuple[date, date]], covered: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Inclusive date windows left after removing already covered dates."""
    remaining = list(ranges)
    for lo, hi in covered:
        out = []
        for a, b in remaining:
            if hi < a or lo > b:
                out.append((a, b))
            else:
                if b > hi:
                    out.append((hi + timedelta(days=1), b))
                if a < lo:
                    out.append((a, lo - timedelta(days=1)))
        remaining = out
    return remaining


async def _resume_sessions(call, ranges: list[tuple[date, date]], hi: date) -> tuple[list[dict], list[tuple[date, date]]]:
    """One fresh recent read, then the existing backlog gets the remaining budget.

    Previously read dates (including tomorrow) may gain activities later. A
    permanently unreadable old day must never stop reading today's activities.
    Recent subdivisions join the end of the queue so they cannot starve history.
    """
    recent = [(hi - timedelta(days=RECENT_DAYS - 1), hi)]
    before = call.calls
    fresh, owed = await _fetch_sessions(call, recent, budget=1)
    queue = (list(ranges) + _without_ranges(owed, ranges) if owed
             else _without_ranges(ranges, recent))
    older, pending = await _fetch_sessions(call, queue, budget=max(0, SESSION_CALLS - (call.calls - before)))
    records = {r["label"]: r for r in fresh}
    records.update((r["label"], r) for r in older)
    return list(records.values()), pending


class _Fetcher:
    """Tool calls with a small pause between them and a cap per sync. A tool
    error, a timeout or a 5xx only loses that piece: the rest of the sync goes
    on (a rejected token still stops it)."""

    def __init__(self, mcp: McpSession):
        self.mcp, self.calls, self.failed, self.down, self.stopped = mcp, 0, 0, 0, False
        self.answered = 0

    async def __call__(self, tool: str, args: dict) -> str | None:
        if self.stopped:
            return None
        if self.calls >= MAX_CALLS:
            logger.info("COROS call cap reached, %s skipped", tool)
            return None
        if self.calls:
            await asyncio.sleep(CALL_DELAY_S)
        self.calls += 1
        try:
            out = await self.mcp.call_tool(tool, args)
        except (McpUnavailable, httpx.TransportError) as e:
            # one slow call is skipped; after 3 failures in a row COROS is down: fail at once when
            # nothing came back, else stop calling and keep what arrived (a backfill is then redone)
            self.failed += 1
            self.down += 1
            logger.info("COROS %s %s failed: %r", tool, args, e)
            if self.down >= 3:
                if self.failed == self.calls:
                    raise CorosError("COROS ne répond pas pour l'instant.") from e
                self.stopped = True
            return None
        except (ToolError, httpx.HTTPError) as e:
            self.failed += 1
            self.down = 0  # an answer, not an outage
            logger.info("COROS %s %s failed: %r", tool, args, e)
            return None
        self.down = 0
        return out

    async def recent(self, tool: str, days: int, fallback: int = RECENT_DAYS) -> str | None:
        """A `days`-only tool: all at once, else the last `fallback` days
        (`self.answered`: how many days the answer covers, 0 without one)."""
        text = await self(tool, {"days": days})
        self.answered = days if text is not None else 0
        if text is None and days > fallback:
            text = await self(tool, {"days": fallback})
            self.answered = fallback if text is not None else 0
        return text


def _try_parse(parser, text):
    """parser(text) ({} without a text), None when it raised: an unexpected
    wording must never break the sync, nor count as an answer read whole."""
    try:
        return parser(text) if text else {}
    except Exception:
        logger.exception("COROS text not understood by %s", parser.__name__)
        return None


def _parsed(parser, text):
    out = _try_parse(parser, text)
    return {} if out is None else out


async def _fetch(mcp: McpSession, days: int, today: date, activity_days: int = 0,
                 on_sessions=None, activity_ranges: list[tuple[date, date]] | None = None) -> tuple[dict, int, int, bool]:
    """`on_sessions(records, utc_offset)` stores the sessions and returns the
    (labelId, sport code, detail missing, laps missing) of the sessions whose
    detail (D+) and laps (max HR) to read now."""
    await mcp.initialize()
    call = _Fetcher(mcp)
    # COROS dates nights in the athlete's time zone: ahead of the server's
    # UTC day in Asia, this morning's night is « tomorrow » here. The window
    # ends a day later (an empty future day costs nothing) and keeps its size.
    hi = today + timedelta(days=1)
    lo = hi - timedelta(days=days - 1)
    data: dict = {"overview": {}, "naps": {}, "hrv_points": [], "hrv_days": set(), "hrv_failed_days": set(),
                  "hrv_unreadable_days": set(), "daily": {},
                  "read": {"sleep": set(), "nap": set()}, "overview_days": set(), "overview_failed_days": set()}

    async def read_overview(a, b):
        text = await call("querySleepOverview", {"startDate": _ymd(a), "endDate": _ymd(b)})
        asked = {a + timedelta(days=i) for i in range((b - a).days + 1)}
        overview, naps = _try_parse(parse_sleep_overview, text), _try_parse(parse_naps, text)
        if text is None or overview is None or naps is None:
            data["overview_failed_days"] |= asked
            return
        data["overview"].update(overview or {})
        data["naps"].update(naps or {})
        data["overview_failed_days"] -= asked
        if text:  # read whole: its days' stale rows may go; empty successful responses are not outages
            data["overview_days"] |= asked
            for key, days_read in overview_read(text, overview, naps).items():
                data["read"][key] |= {d for d in days_read if a <= d <= b}

    # The nights first: last night is what the morning's decision reads.
    for a, b in _ranges(lo, hi, SLEEP_CHUNK_DAYS):
        await read_overview(a, b)
    points: dict[int, tuple] = {}

    async def read_hrv(a, b):
        text = await call("querySleepHrv", {"startDate": _ymd(a), "endDate": _ymd(b), "days": (b - a).days + 1})
        asked = {a + timedelta(days=i) for i in range((b - a).days + 1)}
        got = _try_parse(parse_hrv_points, text) if text and hrv_answer_read(text) else None
        if got is None:
            data["hrv_failed_days"] |= asked
            if text is not None:
                data["hrv_unreadable_days"] |= asked
                call.failed += 1
                logger.warning("COROS HRV response not understood for %s to %s", a, b)
            return
        points.update((p[0], p) for p in got)
        # Empty days may be omitted from a recognised answer, but an unknown
        # answer cannot contribute half a night to a neighbouring chunk.
        data["hrv_days"] |= asked
        data["hrv_failed_days"] -= asked
        data["hrv_unreadable_days"] -= asked
        if data.get("utc_offset") is None:  # latest chunk first: the athlete's current offset
            tz = _parsed(parse_tz_offset, text)
            data["utc_offset"] = tz if isinstance(tz, float) else None

    latest_night = max(data["overview"], default=None)
    retried_latest = False
    retried_overview = False
    recent_lo = max(lo, today - timedelta(days=2))
    recent_days = {recent_lo + timedelta(days=i) for i in range((hi - recent_lo).days + 1)}
    for a, b in _ranges(lo, hi, HRV_CHUNK_DAYS):
        await read_hrv(a, b)
        if not retried_overview and not call.stopped and recent_days & data["overview_failed_days"]:
            # Cover each possible local wake day and its previous evening.
            # Try the latest HRV first: three failures of the sleep tool alone
            # must not exhaust the outage limit before other data can arrive.
            retried_overview = True
            await read_overview(recent_lo, hi)
            latest_night = max(data["overview"], default=None)
        if (latest_night and latest_night >= today - timedelta(days=1)
                and not call.stopped and not retried_latest):
            start, end = latest_night - timedelta(days=1), latest_night
            if {start, end} & data["hrv_failed_days"]:
                # Retry this morning before older backfill requests exhaust
                # the outage limit. At most one short retry per sync.
                retried_latest = True
                await read_hrv(start, end)
    data["hrv_points"] = [points[k] for k in sorted(points)]
    data["hr_day"] = _parsed(parse_avg_hr, await call.recent("queryAvgHeartRate", days))
    text = await call.recent("queryDailyHealthData", days)
    data["daily"] = _parsed(parse_daily_sleep, text)
    data["activity"] = _parsed(parse_daily_activity, text)
    # the nights whose stages were looked for: the days that answer covers (a day ahead, as the nights)
    readable = daily_sleep_readable(text, data["daily"], data["activity"])
    data["daily_read"] = (hi - timedelta(days=call.answered - 1), hi) if call.answered and readable else None
    data["daily_incomplete"] = call.answered < days or not readable
    # the sessions last: the nights matter more on a morning when COROS is slow; their
    # failures never make the health history count as cut short
    data["health_stopped"] = call.stopped
    data["sessions"], data["details"], data["laps"], data["sessions_complete"] = [], {}, {}, False
    data["sessions_pending"] = (activity_ranges if activity_ranges is not None else
                                _ranges(hi - timedelta(days=activity_days - 1), hi, SESSION_CHUNK_DAYS))
    if activity_days and on_sessions and not call.stopped:
        if activity_ranges is not None:
            data["sessions"], data["sessions_pending"] = await _resume_sessions(call, data["sessions_pending"], hi)
        else:
            data["sessions"], data["sessions_pending"] = await _fetch_sessions(call, data["sessions_pending"])
        data["sessions_complete"] = not data["sessions_pending"]
        for label, code, detail, laps in (await on_sessions(data["sessions"], data.get("utc_offset")))[
                :DETAILS_PER_SYNC]:
            args = {"labelId": str(label), "sportType": int(code)}
            # a call lost to a timeout is asked again next time
            if detail and (text := await call("getActivityDetail", args)) is not None:
                data["details"][label] = _parsed(parse_activity_detail, text)
            if laps and (text := await call("queryActivityLapData", args)) is not None:
                data["laps"][label] = _parsed(parse_laps, text)
    return data, call.calls, call.failed, call.stopped


async def import_sessions(db: AsyncSession, user_id: int, records: list[dict],
                          utc_offset: float | None) -> tuple[dict, list[tuple[int, int]]]:
    """COROS sessions into Activity, one row per outing (activity_sources): the
    one already imported is kept current; a session another service already
    brought only gets its COROS id; else a new row. Returns the counts and the
    sessions only COROS has whose detail (D+) or laps (max HR) are still to read,
    newest first: (labelId, code, detail missing, laps missing)."""
    inserted = linked = updated = 0
    starts = []
    for rec in records:
        f = session_fields(rec, utc_offset)
        if not f:
            continue
        starts.append(f["start_date"])
        act = (await db.execute(select(Activity).where(
            Activity.coros_activity_id == f["coros_activity_id"]))).scalar_one_or_none()
        if act is not None and act.user_id != user_id:
            continue
        if rec.get("code") in MULTISPORT:
            n = await _multisport(db, user_id, f, act)
            if n == "linked":
                linked += 1
                continue
            if n == "kept":
                continue
        if act is None:
            twin = await find_twin(db, user_id, f["sport_type"], f["start_date"], f["distance"], f["moving_time"],
                                   Activity.coros_activity_id.is_(None))
            if twin is not None:
                twin.coros_activity_id = f["coros_activity_id"]
                linked += 1
            else:
                db.add(Activity(user_id=user_id, **f))
                inserted += 1
        elif _coros_only(act):  # COROS's own row: kept current, what was read once for it kept
            before = _session_snapshot(act)
            old = act.raw_data or {}
            detail = old.get(DETAIL)
            from_detail = {"total_elevation_gain", "moving_time", "elapsed_time", "average_speed"}
            for k, v in f.items():
                if detail is not None and k in from_detail:  # the detail is more precise than the list
                    continue
                setattr(act, k, v)
            act.raw_data = {**f["raw_data"], **{k: old[k] for k in KEPT if k in old}}
            _revise_session(act, before)
            updated += 1
    await db.flush()
    merged = await merge_twins(db, user_id, min(starts)) if starts else 0
    rows = (await db.execute(select(Activity).where(
        Activity.user_id == user_id, Activity.coros_activity_id.is_not(None),
        Activity.strava_activity_id.is_(None), Activity.garmin_activity_id.is_(None))
        .order_by(Activity.start_date.desc()))).scalars().all()
    todo = []
    for a in rows:
        raw = a.raw_data or {}
        if DETAIL not in raw or LAPS not in raw:
            todo.append((a.coros_activity_id, raw.get("code") or 100, DETAIL not in raw, LAPS not in raw))
    return {"inserted": inserted, "linked": linked, "updated": updated, "merged": merged}, todo


async def sessions_upgraded(db: AsyncSession, user_id: int) -> bool:
    """False while the sessions only COROS has, of the 180 days a history
    reads, were all read before their « Start Coordinates » were (no
    RECORD_FORMAT): the history is then read once more (3 calls). Older ones,
    or none, never ask again (as health.nights_upgraded)."""
    fmt = Activity.raw_data["format"].as_integer()
    base = (Activity.user_id == user_id, Activity.coros_activity_id.is_not(None),
            Activity.strava_activity_id.is_(None), Activity.garmin_activity_id.is_(None))
    since = datetime.now(timezone.utc) - timedelta(days=ACTIVITY_BACKFILL_DAYS)
    old = (await db.execute(select(func.count(Activity.id)).where(
        *base, Activity.start_date >= since, fmt.is_distinct_from(RECORD_FORMAT)))).scalar()
    if not old:
        return True
    return bool((await db.execute(select(func.count(Activity.id)).where(*base, fmt == RECORD_FORMAT))).scalar())


def _coros_only(act: Activity) -> bool:
    return act.strava_activity_id is None and act.garmin_activity_id is None


def _session_snapshot(act: Activity) -> dict:
    return {"start_date": _as_utc(act.start_date), **{key: getattr(act, key) for key in (
        "sport_type", "name", "distance", "moving_time", "elapsed_time", "total_elevation_gain",
        "average_heartrate", "max_heartrate", "average_speed", "average_cadence", "average_watts", "laps", "raw_data")}}


def _revise_session(act: Activity, before: dict) -> None:
    """A real correction invalidates other workers' cached Santé calculations."""
    after = _session_snapshot(act)
    changed = {key for key in after if before[key] != after[key]}
    if changed:
        raw = act.raw_data or {}
        if changed - {"name", "laps", "raw_data"}:
            act.streams_data = None
            act.computed_metrics = None
            raw = {k: v for k, v in raw.items() if k not in ("paceforge_heat", "paceforge_dplus")}
        elif "laps" in changed:
            act.computed_metrics = None
        if "start_date" in changed or raw.get("start_latlng") != (before["raw_data"] or {}).get("start_latlng"):
            raw = {k: v for k, v in raw.items() if k not in (activity_env.ENV_KEY, "paceforge_heat")}
        previous = raw.get(REVISION, 0)
        revision = previous if type(previous) is int and previous >= 0 else 0
        act.raw_data = {**raw, REVISION: revision + 1}


async def _multisport(db: AsyncSession, user_id: int, f: dict, act: Activity | None) -> str | None:
    """A triathlon is one COROS record but one row per leg elsewhere: when the
    legs are there, the COROS id goes on the first and no row of its own is kept
    (« linked » or « kept »); None: no leg yet, the record is saved as one session."""
    start = f["start_date"]
    end = start + timedelta(seconds=f["elapsed_time"])
    legs = (await db.execute(select(Activity).where(
        Activity.user_id == user_id, Activity.start_date >= start - timedelta(minutes=3),
        Activity.start_date <= end, Activity.coros_activity_id.is_distinct_from(f["coros_activity_id"]))
        .order_by(Activity.start_date))).scalars().all()
    if not legs:
        return None
    if act is not None and not _coros_only(act):
        return "kept"  # already on a leg
    if act is not None:
        await db.delete(act)  # saved before the legs arrived: they replace it
        await db.flush()
    first = next((x for x in legs if x.coros_activity_id is None), None)
    if first is None:
        return "kept"
    first.coros_activity_id = f["coros_activity_id"]
    return "linked"


async def apply_details(db: AsyncSession, user_id: int, details: dict[int, dict],
                        laps: dict[int, dict] | None = None) -> int:
    """What was read for sessions only COROS has: the detail (D+, total and
    moving time, cadence, power) and the laps (the max HR of the whole
    activity: COROS's detail has none; the auto laps' time and average HR, in
    Activity.laps as Strava's laps are: what Santé's Entraînement dial reads).
    Each is kept on the session, read once even when it said nothing new."""
    laps = laps or {}
    n = 0
    for label in sorted(set(details) | set(laps)):
        act = (await db.execute(select(Activity).where(
            Activity.coros_activity_id == label, Activity.user_id == user_id))).scalar_one_or_none()
        if act is None or not _coros_only(act):
            continue
        before = _session_snapshot(act)
        if label in laps:
            lp = laps[label]
            if lp.get("hr_max"):
                act.max_heartrate = lp["hr_max"]
            if lp.get("laps"):
                act.laps = lp["laps"]
            act.raw_data = {**(act.raw_data or {}), LAPS: {k: v for k, v in lp.items() if k != "laps"}}
        if label not in details:
            _revise_session(act, before)
            n += 1
            continue
        d = details[label]
        if d.get("dplus") is not None:
            act.total_elevation_gain = d["dplus"]
        if not act.distance and d.get("km"):
            act.distance = d["km"] * 1000
        moving = round(d["km"] * d["moving_pace"]) if d.get("moving_pace") and d.get("km") else None
        if moving and moving <= (d.get("moving") or moving) * 1.02:
            act.moving_time = moving
        elif d.get("moving"):
            act.moving_time = d["moving"]
        if d.get("total"):
            act.elapsed_time = max(d["total"], act.moving_time or 0)
        if d.get("hr_max") and not (laps.get(label) or (act.raw_data or {}).get(LAPS) or {}).get("hr_max"):
            act.max_heartrate = d["hr_max"]  # the laps' whole-activity row first
        if d.get("watts"):
            act.average_watts = d["watts"]
        if d.get("cadence"):
            run = act.sport_type in ("Run", "TrailRun", "VirtualRun", "Hike", "Walk")
            act.average_cadence = d["cadence"] / 2 if run else d["cadence"]
        if act.distance and act.moving_time:
            act.average_speed = act.distance / act.moving_time
        act.raw_data = {**(act.raw_data or {}), DETAIL: d}  # read once, even when it said nothing new
        _revise_session(act, before)
        n += 1
    await db.flush()
    return n


async def sync_connection(db: AsyncSession, conn: CorosConnection) -> dict:
    """Fetch and store; returns store_daily's counts. Raises CorosAuthError
    when the athlete must reconnect, CorosError when COROS can't be read."""
    # the history comes once: on the first sync, or while no daily value
    # arrived yet (a first sync that read nothing must not lose the 60 days;
    # a link made before the daily values were read gets them too), and once
    # more when the nights are still in their pre-2026-10 format, or were
    # written before their stage minutes were read (2026-10-08), or before the
    # naps that are a night's start were folded into it (2026-10-09); while it
    # is owed, it is read whole at most once a (UTC) day, the first sync of the
    # day: a history COROS keeps cutting short (or never sends) is not 45 calls
    # every 2 hours; the syncs in between read the last week
    have = (await db.execute(
        select(func.count(HealthMetric.id)).where(
            HealthMetric.user_id == conn.user_id, HealthMetric.source == SOURCE,
            HealthMetric.metric.in_(("hr_day", "steps", "sleep")))
    )).scalar()
    owed = (conn.last_sync_at is None or not have or _has_partial(conn.last_error, PARTIAL, HRV_UNREADABLE)
            or not await nights_upgraded(db, conn.user_id, SOURCE, days=BACKFILL_DAYS)
            or not await nights_upgraded(db, conn.user_id, SOURCE, days=BACKFILL_DAYS, key=STAGES_READ)
            or not await nights_upgraded(db, conn.user_id, SOURCE, days=BACKFILL_DAYS, key=NAPS_FOLDED))
    full = owed and (conn.last_sync_at is None
                     or _as_utc(conn.last_sync_at).date() < datetime.now(timezone.utc).date())
    days = BACKFILL_DAYS if full else RECENT_DAYS
    now = datetime.now(timezone.utc)
    today = now.date()
    history = await asyncio.to_thread(_history_store, _history_key(conn))
    # Absence of the versioned coverage proof repairs links marked complete by
    # the old parser, including accounts with no COROS-only rows to version.
    activity_full = (conn.sessions_synced_at is None or not history or bool(history["pending"])
                     or now - _as_utc(datetime.fromisoformat(history["checked_at"])) >= SESSION_RECHECK
                     or not await sessions_upgraded(db, conn.user_id))
    activity_days = ACTIVITY_BACKFILL_DAYS if activity_full else RECENT_DAYS
    activity_ranges = None
    if activity_full:
        conn.sessions_synced_at = None
        if history and history["pending"]:
            activity_ranges = [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in history["pending"]]
            hi = today + timedelta(days=1)
            lo = date.fromisoformat(history["through"]) + timedelta(days=1)
            # Never add the same dense recent window on every continuation:
            # it could consume the budget forever and starve older periods.
            if lo <= hi:
                activity_ranges = _ranges(lo, hi, SESSION_CHUNK_DAYS) + activity_ranges
    sessions: dict = {}

    async def on_sessions(records, utc_offset):
        counts, todo = await import_sessions(db, conn.user_id, records, utc_offset)
        sessions.update(counts)
        return todo

    async with http_client() as client:
        token = await access_token(db, conn, client)
        for attempt in range(2):
            mcp = McpSession(client, conn.resource_url, token)
            try:
                data, calls, failed, stopped = await _fetch(mcp, days, today, activity_days, on_sessions,
                                                          activity_ranges=activity_ranges)
                break
            except McpUnauthorized:
                if attempt:
                    raise await _mark_reauth(db, conn, "COROS refuse l'accès : reconnecte-toi.") from None
                token = await access_token(db, conn, client, rejected=token)
            finally:
                await mcp.close()
    if calls and failed == calls:
        raise CorosError("COROS n'a renvoyé aucune donnée lisible.")

    # the athlete's today (a day ahead of the server's in Asia mornings)
    latest = max([*data["overview"], *data["activity"], *data["hr_day"], *data["naps"]], default=None)
    today = _today(latest)
    result = {"received": 0, "inserted": 0, "updated": 0, "unchanged": 0, "by_metric": {}, "days": {}}
    # the nightly rows of the days its parsers read that this sync no longer yields go (a few at most)
    rows = build_daily(data, today)
    await preserve_unread_stages(db, conn.user_id, rows, data.get("daily_read"))
    daily = await store_daily(db, conn.user_id, rows, SOURCE, covered_days(data))
    result["inserted"] += daily["inserted"]
    result["updated"] += daily["updated"]
    result["deleted"], result["stale_kept"] = daily["deleted"], daily["stale_kept"]
    result["by_metric"] = daily["by_metric"]
    if data.get("details") or data.get("laps"):
        sessions["detailed"] = await apply_details(db, conn.user_id, data["details"], data.get("laps"))
    result["activities"] = sessions
    result["inserted"] += sessions.get("inserted", 0)
    result["updated"] += sessions.get("updated", 0) + sessions.get("linked", 0)
    result["activities_incomplete"] = not data.get("sessions_complete", False)
    if activity_full and not result["activities_incomplete"]:
        conn.sessions_synced_at = datetime.now(timezone.utc)
    elif result["activities_incomplete"]:
        conn.sessions_synced_at = None
    result["_sessions_state"] = {
        "through": (now.date() + timedelta(days=1)).isoformat(),
        "checked_at": (now.isoformat() if activity_full or not history else history["checked_at"]),
        "pending": [[a.isoformat(), b.isoformat()] for a, b in data["sessions_pending"]],
    }
    # each new outdoor session's altitude and weather (Open-Meteo), the backlog a few at a time; never fails a sync
    result["env"] = await activity_env.enrich(db, conn.user_id)
    # COROS stopped answering halfway through the health history: keep it, but ask for it all again next
    # time (the sessions have their own flag above)
    result["hrv_incomplete"] = bool(data.get("hrv_failed_days"))
    result["hrv_unreadable"] = bool(data.get("hrv_unreadable_days"))
    result["sleep_incomplete"] = bool(data.get("overview_failed_days"))
    result["phases_incomplete"] = bool(data.get("daily_incomplete"))
    # a history cut short (or not understood) earlier, left for the day's whole read: still owed, still said
    deferred = owed and not full and _has_partial(conn.last_error, PARTIAL, HRV_UNREADABLE)
    result["backfill_partial"] = ((bool(data.get("health_stopped")) or result["hrv_incomplete"]
                                   or result["sleep_incomplete"] or result["phases_incomplete"])
                                  and days == BACKFILL_DAYS) or deferred
    logger.info("COROS sync user %d (%d days, %d calls, %d failed%s): %s, sessions %s",
                conn.user_id, days, calls, failed, ", stopped" if stopped else "", result["by_metric"], sessions)
    return result


def _said(error: str | None) -> str | None:
    """A link's last error as said today (the former wording of PARTIAL read as PARTIAL)."""
    return PARTIAL if error == _LEGACY_PARTIAL else error


def _has_partial(error: str | None, *messages: str) -> bool:
    return any(message in (_said(error) or "") for message in messages)


async def claim(db: AsyncSession, conn: CorosConnection) -> bool:
    """Take the sync for this worker; False when another one holds it."""
    now = datetime.now(timezone.utc)
    res = await db.execute(
        update(CorosConnection)
        .where(CorosConnection.id == conn.id,
               or_(CorosConnection.sync_claimed_at.is_(None),
                   CorosConnection.sync_claimed_at < now - CLAIM_TTL))
        .values(sync_claimed_at=now)
        .execution_options(synchronize_session=False))
    await db.commit()
    if res.rowcount != 1:
        return False
    # as if read back: releasing it is then a change, even when nothing was written in between
    # (a plain assignment over a loaded None, set back to None, writes nothing)
    set_committed_value(conn, "sync_claimed_at", now)
    return True


async def run_sync(db: AsyncSession, conn: CorosConnection, *, claimed_at: str | None = None) -> dict | None:
    """Claim, sync, record how it went. None when a sync is already running, or when the link is paused (to
    reconnect, or its tokens unreadable until a key is put back: nothing written then)."""
    if conn.needs_reauth:
        return None
    if not tokens_readable(conn):
        _key_missing(conn)
        if claimed_at is not None:  # a job queued before: its reservation goes, nothing else
            await release_reservation(db, conn.id, claimed_at)
        return None
    if claimed_at is None:
        if not await claim(db, conn):
            return None
    else:
        # Consume the reservation exactly once, before doing any upstream work.
        now = datetime.now(timezone.utc)
        taken = await db.execute(update(CorosConnection).where(
            CorosConnection.id == conn.id,
            CorosConnection.sync_claimed_at == datetime.fromisoformat(claimed_at),
        ).values(sync_claimed_at=now).execution_options(synchronize_session=False))
        await db.commit()
        if taken.rowcount != 1:
            return None
        set_committed_value(conn, "sync_claimed_at", now)
    history_update = None
    try:
        result = await sync_connection(db, conn)
        history_update = result.pop("_sessions_state", None)
        conn.last_sync_at = datetime.now(timezone.utc)
        # a history cut short is not done: said so, and the next sync asks for all of it again
        conn.last_error = (HRV_UNREADABLE if result.get("hrv_unreadable") else
                           PARTIAL if result.get("backfill_partial") else
                           SLEEP_PARTIAL if result.get("sleep_incomplete") else
                           HRV_PARTIAL if result.get("hrv_incomplete") else
                           PHASES_PARTIAL if result.get("phases_incomplete") else None)
        if result.get("activities_incomplete"):
            conn.last_error = " ".join(filter(None, (conn.last_error, ACTIVITIES_PARTIAL)))
        outcome = {"ok": True, "result": result}
    except CorosAuthError as e:
        outcome = {"ok": False, "error": str(e)}
    except CorosKeyError as e:  # unreadable mid-way: nothing about it is written (no error stored either)
        _key_missing(conn)
        await db.rollback()  # lets go of the row lock taken to refresh
        outcome = {"ok": False, "error": str(e)}
    except (CorosError, httpx.HTTPError) as e:
        logger.warning("COROS sync failed for user %d: %r", conn.user_id, e)
        conn.last_error = str(e) if isinstance(e, CorosError) else "COROS ne répond pas pour l'instant."
        outcome = {"ok": False, "error": conn.last_error}
    except asyncio.CancelledError:  # the worker stops (a deploy): never leave the link claimed
        await _release(db, conn, conn.user_id)
        raise
    except Exception:  # a bug must not leave the link « en cours » for the claim's 15 min
        logger.exception("COROS sync crashed for user %d", conn.user_id)
        await db.rollback()
        conn.last_error = "La synchro a échoué : réessaie plus tard."
        outcome = {"ok": False, "error": conn.last_error}
    conn.sync_claimed_at = None
    await db.commit()
    if history_update is not None and outcome["ok"]:
        # Never advance coverage before the activity rows have survived commit.
        await asyncio.to_thread(_history_store, _history_key(conn), history_update)
    return outcome


async def release_reservation(db: AsyncSession, conn_id: int, claimed_at: str) -> bool:
    """Let go of a queued reservation its job never took (the job died before run_sync could consume it). Only
    that reservation: once taken (its stamp replaced), released, or replaced by a newer one, nothing changes."""
    res = await db.execute(update(CorosConnection).where(
        CorosConnection.id == conn_id, CorosConnection.sync_claimed_at == datetime.fromisoformat(claimed_at),
    ).values(sync_claimed_at=None).execution_options(synchronize_session=False))
    await db.commit()
    return res.rowcount == 1


async def _release(db: AsyncSession, conn, user_id: int) -> None:
    """Best effort, shielded from the cancellation that called it."""
    try:
        await db.rollback()
        conn.sync_claimed_at = None
        await asyncio.shield(db.commit())
    except Exception:
        logger.warning("COROS: could not release the sync claim of user %d", user_id)


_pending: set[asyncio.Task] = set()


async def _sync_in_background(user_id: int) -> None:
    from app.database import async_session_factory

    async with async_session_factory() as db:
        conn = await connection_for(db, user_id)
        if conn:
            await queue_sync(db, conn)


async def queue_sync(db: AsyncSession, conn: CorosConnection) -> bool:
    """Reserve, then hand the sync to the worker (app.tasks.coros_sync) with the
    reservation: a web request never runs it (a minute of calls and parsing on
    the web workers' loop, lost at a deploy). Reserving first lets polling see
    this attempt. False when it is not reserved (another sync holds it, or the
    link must be reconnected) or could not be queued (reservation released)."""
    from app.tasks.coros_sync import sync_coros_user

    if conn.needs_reauth or not tokens_readable(conn) or not await claim(db, conn):
        return False
    stamp = _as_utc(conn.sync_claimed_at).isoformat()
    try:
        await asyncio.to_thread(sync_coros_user.apply_async, args=[conn.id, stamp], retry=False)
    except Exception:
        logger.exception("COROS sync could not be queued for user %d", conn.user_id)
        conn.sync_claimed_at = None
        await db.commit()
        return False
    return True


def schedule_sync(user_id: int) -> bool:
    """Queue a sync without waiting for it (after connecting, on opening
    Santé). False when syncs are off here."""
    if not settings.COROS_SYNC:
        return False
    try:
        task = asyncio.get_running_loop().create_task(_sync_in_background(user_id))
    except RuntimeError:
        return False
    _pending.add(task)
    task.add_done_callback(_done)
    return True


def _done(task: asyncio.Task) -> None:
    _pending.discard(task)
    if not task.cancelled() and task.exception():
        logger.warning("COROS background sync failed: %r", task.exception())


async def disconnect(db: AsyncSession, conn: CorosConnection) -> None:
    """Revoke (best effort: COROS may refuse a public client) and forget the
    tokens. Samples already received stay."""
    if conn.revocation_endpoint:
        try:
            async with http_client() as client:
                for enc, hint in ((conn.refresh_token_encrypted, "refresh_token"),
                                  (conn.access_token_encrypted, "access_token")):
                    if enc:
                        r = await client.post(conn.revocation_endpoint, data={
                            "token": decrypt_secret(enc), "token_type_hint": hint, "client_id": conn.client_id})
                        if r.status_code != 200:
                            logger.info("COROS revocation answered %s", r.status_code)
        except (httpx.HTTPError, ValueError, InvalidToken) as e:  # unreadable tokens: nothing to revoke with
            logger.info("COROS revocation failed: %r", e)
    await db.delete(conn)
    await db.flush()
    logger.info("COROS disconnected for user %d", conn.user_id)


# ── settings status ─────────────────────────────────────────────────────────

def link_flags(conn: CorosConnection) -> tuple[bool, str | None]:
    """(needs_reauth, last_error) of a link as Réglages and Santé say them: tokens no configured key decrypts need
    a reconnection (« reconnecte-toi », computed when read, never stored), a stored error in today's wording."""
    readable = tokens_readable(conn)
    return conn.needs_reauth or not readable, _said(conn.last_error) if readable else EXPIRED


async def coros_status(db: AsyncSession, user_id: int) -> dict:
    conn = await connection_for(db, user_id)
    if conn is None:
        return {"connected": False}
    counts, latest = await _synced_measurements(db, user_id, SOURCE)
    sleep, hrv = latest.get("sleep"), latest.get("hrv")
    needs_reauth, error = link_flags(conn)
    hrv_note = f"Dernière VFC reçue : {hrv.strftime('%d/%m/%Y')}." if hrv else None
    if sleep and (hrv is None or hrv < sleep):
        hrv_note = f"VFC de la nuit du {sleep.strftime('%d/%m')} pas encore reçue." + (f" {hrv_note}" if hrv_note else "")
    return {
        "connected": True,
        "needs_reauth": needs_reauth,
        "last_sync_at": conn.last_sync_at,
        "last_sync_ago": _ago(conn.last_sync_at),
        "syncing": bool((claimed := _as_utc(conn.sync_claimed_at))
                        and datetime.now(timezone.utc) - claimed < CLAIM_TTL),
        "last_error": error,
        "retryable_partial": _has_partial(error, PARTIAL, HRV_PARTIAL, SLEEP_PARTIAL, PHASES_PARTIAL,
                                         ACTIVITIES_PARTIAL) and conn.last_sync_at is not None,
        "partial": _has_partial(error, PARTIAL, HRV_PARTIAL, HRV_UNREADABLE, SLEEP_PARTIAL, PHASES_PARTIAL,
                                ACTIVITIES_PARTIAL),
        "hrv_note": hrv_note,
        "per_metric": counts,
    }


async def synced_counts(db: AsyncSession, user_id: int, source: str) -> list[tuple[str, int]]:
    """What Réglages lists: days stored per value from this watch (brand rows
    stored before 2026-10 included, as they are still there)."""
    return (await _synced_measurements(db, user_id, source))[0]


async def _synced_measurements(db: AsyncSession, user_id: int, source: str) -> tuple[list[tuple[str, int]], dict]:
    """Counts and last measurement dates in one query, distinct from last sync.

    Legacy COROS HRV averages cannot be advertised as accepted nightly VFC.
    """
    rows = (await db.execute(
        select(HealthMetric.metric, func.count(HealthMetric.id), func.max(HealthMetric.date),
               func.max(case((HealthMetric.details["method"].as_string() == HRV_METHOD, HealthMetric.date))))
        .where(HealthMetric.user_id == user_id, HealthMetric.source == source,
               HealthMetric.metric.in_(DAILY_METRICS))
        .group_by(HealthMetric.metric))).all()
    daily = {metric: count for metric, count, _, _ in rows}
    latest = {metric: accepted if metric == "hrv" else last for metric, _, last, accepted in rows}
    return [(label, daily[m]) for m, label in DAILY_LABELS.items() if daily.get(m)], latest
