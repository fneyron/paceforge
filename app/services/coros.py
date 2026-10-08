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
  health.store_daily, source "COROS"): the main sleep (window, minutes
  asleep), the day's naps (local windows, guarded), nightly HRV computed from
  the raw readings inside the main window, the « Sleep HR » line of the main
  sleep's summary, plus the day's heart rate and steps as « watch worn »
  markers. No brand value is read (recovery, load, stress, sleep score, HRV
  range, fitness and VO2 max, the daytime resting HR), and no stage interval
  is written (COROS has no stage timeline). Sessions go to Activity.
  60 days the first time, then the last 7 days, at most every 2 hours (Celery
  beat, app.tasks.coros_sync), right after connecting and on demand.

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
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

import httpx
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models.activity import Activity
from app.models.coros import CorosConnection, OAuthClient
from app.models.health import HealthMetric
from app.services.activity_sources import find_twin, merge_twins
from app.services.health import (
    DAILY_LABELS,
    DAILY_METRICS,
    HRV_METHOD,
    Daily,
    _ago,
    _today,
    iso_min,
    nap_daily,
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
PARTIAL = "COROS a cessé de répondre en cours de route, le reste de ton historique arrive à la prochaine synchro."
REFRESH_MARGIN = timedelta(days=1)  # access tokens last ~30 days
CALL_DELAY_S = 0.5
MAX_CALLS = 45  # a 60-day backfill takes 13 at most, the sessions 3 + their details
ACTIVITY_BACKFILL_DAYS = 180  # the sessions' history, as Garmin's
SESSION_CHUNK_DAYS = 60
SESSION_LIMIT = 200  # records per querySportRecords call
DETAILS_PER_SYNC = 10  # getActivityDetail (the D+) for sessions only COROS has, newest first

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


async def access_token(db: AsyncSession, conn: CorosConnection, client: httpx.AsyncClient,
                       rejected: str | None = None) -> str:
    """A usable access token, refreshed when it expires within a day or when
    the server just `rejected` it. Commits: a rotated refresh token must never
    be lost to a later rollback."""
    if rejected is None and _fresh(conn):
        return decrypt_secret(conn.access_token_encrypted)
    async with _lock_for(conn.user_id):
        # another worker may have rotated the pair meanwhile: re-read it, locked
        await db.refresh(conn, with_for_update=True)
        current = decrypt_secret(conn.access_token_encrypted)
        if conn.needs_reauth:
            await db.commit()
            raise CorosAuthError(conn.last_error or "Reconnecte COROS.")
        if (rejected is None and _fresh(conn)) or (rejected is not None and current != rejected):
            await db.commit()  # releases the row lock
            return current
        if not conn.refresh_token_encrypted:
            raise await _mark_reauth(db, conn, "La connexion à COROS a expiré : reconnecte-toi.")
        try:
            tokens = await _token_request(client, conn.token_endpoint, {
                "grant_type": "refresh_token",
                "refresh_token": decrypt_secret(conn.refresh_token_encrypted),
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


def parse_sport_records(text: str) -> list[dict]:
    """querySportRecords: one dict per session {label, code, name, day, start,
    end (UTC timestamps), duration (s), km, hr, kcal}."""
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
        out.append({
            "label": int(label.group(1)), "code": int(code.group(1)), "type": h.group(1).strip(),
            "name": name.group(1) if name else None, "day": "-".join(h.groups()[1:]),
            "start": int(start.group(1)), "end": int(end.group(1)) if end else None,
            "duration": _clock_s(dur.group(1)) if dur else None,
            "km": (_num(dist.group(1)) / (1000 if dist.group(2) == "m" else 1)) if dist else None,
            "hr": _num(hr.group(1)) if hr else None,
            "kcal": _int(kcal.group(1)) if kcal else None,
        })
    return out


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
    raw = {**rec, "source": "coros"}
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


def sleep_dailies(overview: dict[date, dict], tz: dict[date, int] | None = None) -> list[Daily]:
    """One `sleep` value per main night: minutes asleep in the main episode,
    its window (local ISO), period and timezone. COROS gives no stage
    timeline, so no stage intervals are written (timeline False)."""
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
        out.append(Daily("sleep", d, asleep, det))
    return out


def hrv_dailies(points: list[tuple[int, int, float]], overview: dict[date, dict],
                days: set[date] | None = None) -> list[Daily]:
    """PaceForge's nightly HRV for each main night whose two local days (the
    evening's and the morning's) were both read: a night computed from half its
    readings would overwrite a whole one."""
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
    guards of health.nap_guard applied against that day's main window."""
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
    points = data.get("hrv_points") or []
    hrv = hrv_dailies(points, overview, data.get("hrv_days"))
    tz = {r.day: r.details["tz"] // 15 for r in hrv}
    out += sleep_dailies(overview, tz)
    out += hrv
    out += hr_night_dailies(data.get("daily") or {}, overview, data.get("naps") or {})
    out += nap_dailies(data.get("naps") or {}, overview)
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


class _Fetcher:
    """Tool calls with a small pause between them and a cap per sync. A tool
    error, a timeout or a 5xx only loses that piece: the rest of the sync goes
    on (a rejected token still stops it)."""

    def __init__(self, mcp: McpSession):
        self.mcp, self.calls, self.failed, self.down, self.stopped = mcp, 0, 0, 0, False

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
        """A `days`-only tool: all at once, else the last `fallback` days."""
        text = await self(tool, {"days": days})
        if text is None and days > fallback:
            text = await self(tool, {"days": fallback})
        return text


def _parsed(parser, text):
    try:
        return parser(text) if text else {}
    except Exception:  # an unexpected wording must never break the sync
        logger.exception("COROS text not understood by %s", parser.__name__)
        return {}


async def _fetch(mcp: McpSession, days: int, today: date, activity_days: int = 0,
                 on_sessions=None) -> tuple[dict, int, int, bool]:
    """`on_sessions(records, utc_offset)` stores the sessions and returns the
    (labelId, sport code) whose detail (D+) to read now."""
    await mcp.initialize()
    call = _Fetcher(mcp)
    # COROS dates nights in the athlete's time zone: ahead of the server's
    # UTC day in Asia, this morning's night is « tomorrow » here. The window
    # ends a day later (an empty future day costs nothing) and keeps its size.
    hi = today + timedelta(days=1)
    lo = hi - timedelta(days=days - 1)
    data: dict = {"overview": {}, "naps": {}, "hrv_points": [], "hrv_days": set(), "daily": {}}
    # the nights first: last night is what the morning's decision reads
    for a, b in _ranges(lo, hi, SLEEP_CHUNK_DAYS):
        text = await call("querySleepOverview", {"startDate": _ymd(a), "endDate": _ymd(b)})
        data["overview"].update(_parsed(parse_sleep_overview, text))
        data["naps"].update(_parsed(parse_naps, text))
    points: dict[int, tuple] = {}
    for a, b in _ranges(lo, hi, HRV_CHUNK_DAYS):
        text = await call("querySleepHrv", {"startDate": _ymd(a), "endDate": _ymd(b), "days": (b - a).days + 1})
        if text is None:
            continue
        points.update((p[0], p) for p in _parsed(parse_hrv_points, text) or [])
        # every day of the answered range counts as read (COROS may leave an empty day out)
        data["hrv_days"] |= {a + timedelta(days=i) for i in range((b - a).days + 1)}
        if data.get("utc_offset") is None:  # latest chunk first: the athlete's current offset
            tz = _parsed(parse_tz_offset, text)
            data["utc_offset"] = tz if isinstance(tz, float) else None
    data["hrv_points"] = [points[k] for k in sorted(points)]
    data["hr_day"] = _parsed(parse_avg_hr, await call.recent("queryAvgHeartRate", days))
    text = await call.recent("queryDailyHealthData", days)
    data["daily"] = _parsed(parse_daily_sleep, text)
    data["activity"] = _parsed(parse_daily_activity, text)
    # the sessions last: the nights matter more on a morning when COROS is slow; their
    # failures never make the health history count as cut short
    data["health_stopped"] = call.stopped
    data["sessions"], data["details"], data["sessions_complete"] = [], {}, False
    if activity_days and on_sessions and not call.stopped:
        data["sessions_complete"] = True
        for a, b in _ranges(hi - timedelta(days=activity_days - 1), hi, SESSION_CHUNK_DAYS):
            text = await call("querySportRecords", {
                "startDate": _ymd(a), "endDate": _ymd(b), "sportTypeCodes": [65535], "minDistanceKm": 0,
                "maxDistanceKm": 10000, "minDurationMinutes": 0, "maxDurationMinutes": 100000,
                "maxAveragePace": "", "locationKeyword": "", "limit": SESSION_LIMIT})
            if text is None:
                data["sessions_complete"] = False  # that piece is asked again next time
            got = _parsed(parse_sport_records, text) or []
            if len(got) >= SESSION_LIMIT:
                logger.warning("COROS: %d sessions between %s and %s, some may be missing", len(got), a, b)
            data["sessions"] += got
        for label, code in (await on_sessions(data["sessions"], data.get("utc_offset")))[:DETAILS_PER_SYNC]:
            text = await call("getActivityDetail", {"labelId": str(label), "sportType": int(code)})
            if text is not None:  # a call lost to a timeout is asked again next time
                data["details"][label] = _parsed(parse_activity_detail, text)
    return data, call.calls, call.failed, call.stopped


async def import_sessions(db: AsyncSession, user_id: int, records: list[dict],
                          utc_offset: float | None) -> tuple[dict, list[tuple[int, int]]]:
    """COROS sessions into Activity, one row per outing (activity_sources): the
    one already imported is kept current; a session another service already
    brought only gets its COROS id; else a new row. Returns the counts and the
    sessions only COROS has whose detail (D+) is still to read, newest first."""
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
        elif _coros_only(act):  # COROS's own row: kept current, its detail kept
            detail = (act.raw_data or {}).get("detail")
            from_detail = {"total_elevation_gain", "moving_time", "elapsed_time", "average_speed"}
            for k, v in f.items():
                if detail is not None and k in from_detail:  # the detail is more precise than the list
                    continue
                setattr(act, k, v)
            if detail is not None:
                act.raw_data = {**f["raw_data"], "detail": detail}
            updated += 1
    await db.flush()
    merged = await merge_twins(db, user_id, min(starts)) if starts else 0
    rows = (await db.execute(select(Activity).where(
        Activity.user_id == user_id, Activity.coros_activity_id.is_not(None),
        Activity.strava_activity_id.is_(None), Activity.garmin_activity_id.is_(None))
        .order_by(Activity.start_date.desc()))).scalars().all()
    todo = [(a.coros_activity_id, (a.raw_data or {}).get("code") or 100) for a in rows
            if "detail" not in (a.raw_data or {})]
    return {"inserted": inserted, "linked": linked, "updated": updated, "merged": merged}, todo


def _coros_only(act: Activity) -> bool:
    return act.strava_activity_id is None and act.garmin_activity_id is None


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


async def apply_details(db: AsyncSession, user_id: int, details: dict[int, dict]) -> int:
    """The detail of sessions only COROS has: D+, total and moving time, cadence, power."""
    n = 0
    for label, d in details.items():
        act = (await db.execute(select(Activity).where(
            Activity.coros_activity_id == label, Activity.user_id == user_id))).scalar_one_or_none()
        if act is None or not _coros_only(act):
            continue
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
        if d.get("hr_max"):
            act.max_heartrate = d["hr_max"]
        if d.get("watts"):
            act.average_watts = d["watts"]
        if d.get("cadence"):
            run = act.sport_type in ("Run", "TrailRun", "VirtualRun", "Hike", "Walk")
            act.average_cadence = d["cadence"] / 2 if run else d["cadence"]
        if act.distance and act.moving_time:
            act.average_speed = act.distance / act.moving_time
        act.raw_data = {**(act.raw_data or {}), "detail": d}  # read once, even when it said nothing new
        n += 1
    await db.flush()
    return n


async def sync_connection(db: AsyncSession, conn: CorosConnection) -> dict:
    """Fetch and store; returns store_daily's counts. Raises CorosAuthError
    when the athlete must reconnect, CorosError when COROS can't be read."""
    # the history comes once: on the first sync, or while no daily value
    # arrived yet (a first sync that read nothing must not lose the 60 days;
    # a link made before the daily values were read gets them too), and once
    # more when the nights are still in their pre-2026-10 format
    have = (await db.execute(
        select(func.count(HealthMetric.id)).where(
            HealthMetric.user_id == conn.user_id, HealthMetric.source == SOURCE,
            HealthMetric.metric.in_(("hr_day", "steps", "sleep")))
    )).scalar()
    owed = (conn.last_sync_at is None or not have or conn.last_error == PARTIAL
            or not await nights_upgraded(db, conn.user_id, SOURCE, days=BACKFILL_DAYS))
    days = BACKFILL_DAYS if owed else RECENT_DAYS
    # the sessions' history comes once too: until every piece of it was read
    activity_days = ACTIVITY_BACKFILL_DAYS if conn.sessions_synced_at is None else RECENT_DAYS
    sessions: dict = {}

    async def on_sessions(records, utc_offset):
        counts, todo = await import_sessions(db, conn.user_id, records, utc_offset)
        sessions.update(counts)
        return todo

    today = datetime.now(timezone.utc).date()
    async with http_client() as client:
        token = await access_token(db, conn, client)
        for attempt in range(2):
            mcp = McpSession(client, conn.resource_url, token)
            try:
                data, calls, failed, stopped = await _fetch(mcp, days, today, activity_days, on_sessions)
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
    daily = await store_daily(db, conn.user_id, build_daily(data, today), SOURCE)
    result["inserted"] += daily["inserted"]
    result["updated"] += daily["updated"]
    result["by_metric"] = daily["by_metric"]
    if data.get("details"):
        sessions["detailed"] = await apply_details(db, conn.user_id, data["details"])
    result["activities"] = sessions
    result["inserted"] += sessions.get("inserted", 0)
    result["updated"] += sessions.get("updated", 0) + sessions.get("linked", 0)
    if activity_days == ACTIVITY_BACKFILL_DAYS and data.get("sessions_complete"):
        conn.sessions_synced_at = datetime.now(timezone.utc)
    # COROS stopped answering halfway through the health history: keep it, but ask for it all again next
    # time (the sessions have their own flag above)
    result["backfill_partial"] = bool(data.get("health_stopped")) and days == BACKFILL_DAYS
    logger.info("COROS sync user %d (%d days, %d calls, %d failed%s): %s, sessions %s",
                conn.user_id, days, calls, failed, ", stopped" if stopped else "", result["by_metric"], sessions)
    return result


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
    conn.sync_claimed_at = now  # so that releasing it later is seen as a change
    return True


async def run_sync(db: AsyncSession, conn: CorosConnection) -> dict | None:
    """Claim, sync, record how it went. None when a sync is already running."""
    if conn.needs_reauth or not await claim(db, conn):
        return None
    try:
        result = await sync_connection(db, conn)
        conn.last_sync_at = datetime.now(timezone.utc)
        # a history cut short is not done: said so, and the next sync asks for all of it again
        conn.last_error = PARTIAL if result.get("backfill_partial") else None
        outcome = {"ok": True, "result": result}
    except CorosAuthError as e:
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
    return outcome


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
            await run_sync(db, conn)


def schedule_sync(user_id: int) -> bool:
    """Start a sync in this process without waiting for it (after connecting,
    on opening Santé). False when syncs are off here."""
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
        except (httpx.HTTPError, ValueError) as e:
            logger.info("COROS revocation failed: %r", e)
    await db.delete(conn)
    await db.flush()
    logger.info("COROS disconnected for user %d", conn.user_id)


# ── settings status ─────────────────────────────────────────────────────────

async def coros_status(db: AsyncSession, user_id: int) -> dict:
    conn = await connection_for(db, user_id)
    if conn is None:
        return {"connected": False}
    return {
        "connected": True,
        "needs_reauth": conn.needs_reauth,
        "last_sync_at": conn.last_sync_at,
        "last_sync_ago": _ago(conn.last_sync_at),
        "syncing": bool((claimed := _as_utc(conn.sync_claimed_at))
                        and datetime.now(timezone.utc) - claimed < CLAIM_TTL),
        "last_error": conn.last_error,
        "per_metric": await synced_counts(db, user_id, SOURCE),
    }


async def synced_counts(db: AsyncSession, user_id: int, source: str) -> list[tuple[str, int]]:
    """What Réglages lists: days stored per value from this watch (brand rows
    stored before 2026-10 included, as they are still there)."""
    rows = await db.execute(
        select(HealthMetric.metric, func.count(HealthMetric.id))
        .where(HealthMetric.user_id == user_id, HealthMetric.source == source,
               HealthMetric.metric.in_(DAILY_METRICS))
        .group_by(HealthMetric.metric))
    daily = dict(rows.all())
    return [(label, daily[m]) for m, label in DAILY_LABELS.items() if daily.get(m)]
