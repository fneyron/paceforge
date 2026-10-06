"""COROS: what the athlete's watch knows about recovery and fitness.

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
- Sync → HRV, resting HR, sleep and VO2 max as HealthSample rows (source
  "COROS") through health.store_samples; the values COROS already gives per day
  (training load, recovery, daily heart rate, stress, steps, fitness
  assessment, HRV normal range) straight to HealthMetric (health.store_daily).
  60 days the first time, then the last 7 days, at most every 2 hours (Celery
  beat, app.tasks.coros_sync), right after connecting and on demand.

Times are the athlete's local wall clock (naive), as COROS writes them.
"""
import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

import httpx
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models.coros import CorosConnection, OAuthClient
from app.models.health import HealthMetric, HealthSample
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
    reaggregate,
    store_daily,
    store_samples,
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
REFRESH_MARGIN = timedelta(days=1)  # access tokens last ~30 days
CALL_DELAY_S = 0.5
MAX_CALLS = 30  # a 60-day backfill takes 21 at most
LOAD_FALLBACK_DAYS = 14  # what queryTrainingLoadAssessment is known to take

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
            raise CorosError(f"COROS ne répond pas pour l'instant (erreur {r.status_code}).")
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


def parse_rhr(text: str) -> dict[date, float]:
    """queryRestingHeartRate: '2026-09-30: 39 bpm' lines ('No data' skipped)."""
    out = {}
    for m in re.finditer(r"^\s*(\d{4})-(\d{2})-(\d{2}):\s*" + _NUM + r"\s*bpm", text or "", re.M):
        d = _date(*m.groups()[:3])
        if d:
            out[d] = _num(m.group(4))
    return out


def parse_hrv(text: str) -> dict[date, float]:
    """querySleepHrv: the official 'HRV Avg' of each wake-up day, from the
    assessment section only (the raw time series that follows is ignored)."""
    head = re.split(r"^.*Time Series.*$", text or "", maxsplit=1, flags=re.M)[0]
    out, day = {}, None
    for line in head.splitlines():
        m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2}):\s*$", line)
        if m:
            day = _date(*m.groups())
            continue
        m = re.search(r"HRV Avg:\s*" + _NUM + r"\s*ms", line)
        if m and day:
            out[day] = _num(m.group(1))
            day = None
    return out


def _field_minutes(block: str, label: str) -> int | None:
    m = re.search(label + r":\s*([^\n]+)", block)
    return parse_duration(m.group(1)) if m else None


def _field_pct(block: str, label: str) -> float | None:
    m = re.search(label + r":\s*" + _NUM + r"\s*%", block)
    return _num(m.group(1)) if m else None


def parse_sleep_overview(text: str) -> dict[date, dict]:
    """querySleepOverview, per wake-up day: main sleep window, asleep and period
    minutes, awake time and the stage ratios (%)."""
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
            "ratios": {"deep": _field_pct(block, "Deep Sleep Ratio"), "core": _field_pct(block, "Light Sleep Ratio"),
                       "rem": _field_pct(block, "REM Ratio"), "awake": _field_pct(block, "Awake Ratio")},
        }
    return out


def parse_daily_sleep(text: str) -> dict[date, dict[str, int]]:
    """queryDailyHealthData: stage minutes of each wake-up day's sleep summary."""
    out = {}
    heads = list(re.finditer(r"^\s*---\s*(\d{4})(\d{2})(\d{2})\s*---\s*$", text or "", re.M))
    for i, h in enumerate(heads):
        day = _date(*h.groups())
        block = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        stages = {}
        for kind, label in (("deep", "Deep"), ("core", "Light"), ("rem", "REM"), ("awake", "Awake")):
            m = re.search(r"\b" + label + r":\s*([^|\n]+)", block)
            v = parse_duration(m.group(1)) if m else None
            if v is not None:
                stages[kind] = v
        if day and all(k in stages for k in ("deep", "core", "rem")):
            stages.setdefault("awake", 0)
            out[day] = stages
    return out


def parse_vo2max(text: str) -> float | None:
    m = re.search(r"VO2\s*max:\s*" + _NUM, text or "", re.I)
    return _num(m.group(1)) if m else None


def _int(s: str) -> int:
    """'18,055' → 18055 (thousands separators: comma, space, narrow space)."""
    return int(re.sub(r"[,\s  ]", "", s))


def _clock(s: str | None) -> int | None:
    """'15:50' → 950 s, '1:10:54' → 4254 s."""
    m = re.match(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\b", s or "")
    if not m:
        return None
    a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
    return a * 3600 + b * 60 + int(c) if c is not None else a * 60 + b


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


def parse_hrv_range(text: str) -> dict[date, dict]:
    """querySleepHrv: each wake-up day's normal range and baseline (ms)."""
    head = re.split(r"^.*Time Series.*$", text or "", maxsplit=1, flags=re.M)[0]
    out = {}
    for day, block in _day_blocks(head, r"^\s*(\d{4})-(\d{2})-(\d{2}):\s*$"):
        m = re.search(r"Normal Range:\s*" + _NUM + r"\s*-\s*" + _NUM, block)
        if m:
            out[day] = {"lo": _num(m.group(1)), "hi": _num(m.group(2)), "base": _field(block, "Baseline")}
    return out


def parse_training_load(text: str) -> dict[date, dict]:
    """queryTrainingLoadAssessment, per day: short- and long-term load, their
    ratio and COROS's one-word comment (Excessive, Optimized…)."""
    out = {}
    for day, block in _day_blocks(text, r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$"):
        short, long_ = _field(block, "Short-Term Load"), _field(block, "Long-Term Load")
        if short is None or long_ is None:
            continue
        ratio = _field(block, "Load Ratio")
        if ratio is None and long_ > 0:
            ratio = round(short / long_, 2)
        m = re.search(r"^\s*Comment:\s*([A-Za-z][A-Za-z \-]*?)\s*$", block, re.M)
        out[day] = {"short": short, "long": long_, "ratio": ratio, "comment": m.group(1) if m else None}
    return out


def parse_recovery(text: str) -> dict | None:
    """queryRecoveryStatus (today only): {pct, level, full_h}."""
    m = re.search(r"Recovery:\s*" + _NUM + r"\s*%", text or "")
    if not m:
        return None
    level = re.search(r"^\s*Level:\s*(.+?)\s*$", text, re.M)
    full = re.search(r"Full Recovery:\s*([^\n]+)", text)
    full_min = parse_duration(full.group(1)) if full else None
    return {"pct": _num(m.group(1)), "level": level.group(1) if level else None,
            "full_h": round(full_min / 60, 1) if full_min is not None else None}


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
    """queryDailyHealthData, per day: steps, calories (kcal), exercise minutes
    and average stress (absent when the watch wasn't worn)."""
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
        m = re.search(r"Stress:\s*Avg\s*(\d+)", block)
        if m:
            row["stress"] = int(m.group(1))
        if row:
            out[day] = row
    return out


_PREDICTIONS = (("5k", r"\b5\s*km"), ("10k", r"\b10\s*km"), ("half", r"\bHalf\s+Marathon"),
                ("marathon", r"(?<!Half )\bMarathon"))


def parse_fitness(text: str) -> dict:
    """queryFitnessAssessmentOverview: VO2max, running level, threshold pace
    (s/km) and race predictions (s). Missing lines are left out."""
    out: dict = {}
    vo2 = parse_vo2max(text)
    if vo2 is not None:
        out["vo2max"] = vo2
    level = _field(text or "", "Running Level")
    if level is not None:
        out["level"] = level
    m = re.search(r"Threshold Pace:\s*(\d{1,2}:\d{2})\s*/\s*km", text or "")
    if m:
        out["threshold_s"] = _clock(m.group(1))
    preds = {}
    for key, label in _PREDICTIONS:
        m = re.search(label + r"\s*Prediction:\s*([\d:]+)", text or "", re.I)
        if m and _clock(m.group(1)):
            preds[key] = _clock(m.group(1))
    if preds:
        out["pred"] = preds
    return out


# ── samples ─────────────────────────────────────────────────────────────────

HRV_AT = time(5, 0)  # inside the 22:00 → 10:00 night window of health._hrv_day
RHR_AT = time(23, 59)  # the last of the day: wins over an Apple value the same day
VO2_AT = time(12, 0)
STAGE_ORDER = ("deep", "core", "awake", "rem")
STAGE_TOLERANCE_MIN = 15


def night_stages(overview: dict, daily: dict[str, int] | None) -> dict[str, int] | None:
    """Minutes per stage of one main sleep: the daily summary's exact minutes
    when they add up to the main sleep, else the overview's ratios applied to it."""
    asleep = overview.get("asleep")
    if daily:
        total = daily["deep"] + daily["core"] + daily["rem"]
        if total > 0 and (asleep is None or abs(total - asleep) <= STAGE_TOLERANCE_MIN):
            return dict(daily)
    ratios = overview.get("ratios") or {}
    period = overview.get("period") or round((overview["end"] - overview["start"]).total_seconds() / 60)
    awake = overview.get("awake")
    if awake is None:
        awake = round(period * (ratios.get("awake") or 0) / 100)
    asleep = asleep if asleep is not None else period - awake
    parts = [ratios.get(k) or 0 for k in ("deep", "core", "rem")]
    if asleep <= 0 or sum(parts) <= 0:
        return None
    deep = round(asleep * parts[0] / sum(parts))
    rem = round(asleep * parts[2] / sum(parts))
    return {"deep": deep, "core": asleep - deep - rem, "rem": rem, "awake": awake}


def sleep_samples(overview: dict, stages: dict[str, int], source: str = SOURCE) -> list[Sample]:
    """Contiguous stage intervals from the start of the main sleep window. The
    awake minutes sit before the last REM block, so the night starts and ends
    asleep: bedtime and wake time come out as COROS's window."""
    start, end = overview["start"], overview["end"]
    total = sum(max(stages.get(k, 0), 0) for k in STAGE_ORDER)
    span = (end - start).total_seconds() / 60
    scale = span / total if total > span else 1.0
    out, t = [], start
    for kind in STAGE_ORDER:
        minutes = max(stages.get(kind, 0), 0) * scale
        if minutes <= 0:
            continue
        t1 = t + timedelta(seconds=round(minutes * 60))
        out.append(Sample("sleep", kind, t, t1, round((t1 - t).total_seconds() / 60, 2), source))
        t = t1
    return out


def _point(metric: str, day: date, at: time, value: float) -> Sample | None:
    lo, hi = _RANGES[metric]
    if not lo <= value <= hi:
        logger.info("COROS %s %s out of range (%s): skipped", metric, day, value)
        return None
    t = datetime.combine(day, at)
    return Sample(metric, "", t, t, round(value, 3), SOURCE)


def _ok(value, lo: float, hi: float) -> bool:
    return isinstance(value, (int, float)) and lo <= value <= hi


def build_daily(data: dict, today: date) -> list[Daily]:
    """The per-day values, implausible ones left out (an odd wording must not
    write a 0 % recovery or a 50 000 bpm day)."""
    out: list[Daily] = []
    for d, v in (data.get("load") or {}).items():
        if _ok(v["short"], 0, 3000) and _ok(v["long"], 0, 3000):
            ratio = v["ratio"] if _ok(v.get("ratio"), 0, 20) else None
            out.append(Daily("load", d, v["short"], {"long": v["long"], "ratio": ratio, "comment": v["comment"]}))
    rec = data.get("recovery")
    if rec and _ok(rec["pct"], 0, 100):
        full = rec["full_h"] if _ok(rec.get("full_h"), 0, 500) else None
        out.append(Daily("recovery", today, rec["pct"], {"level": rec["level"], "full_h": full}))
    for d, v in (data.get("hr_day") or {}).items():
        if _ok(v["avg"], 25, 230):
            out.append(Daily("hr_day", d, v["avg"], {"min": v["min"], "max": v["max"]}))
    for d, v in (data.get("activity") or {}).items():
        if _ok(v.get("stress"), 1, 100):
            out.append(Daily("stress", d, v["stress"]))
        # 0 steps and 0 kcal: the watch wasn't worn that day
        if _ok(v.get("steps"), 1, 200_000) or _ok(v.get("kcal"), 1, 20_000):
            out.append(Daily("steps", d, v.get("steps") or 0,
                             {"kcal": v.get("kcal"), "exercise": v.get("exercise")}))
    for d, v in (data.get("hrv_range") or {}).items():
        if _ok(v["lo"], 5, 300) and _ok(v["hi"], v["lo"], 300):
            base = v["base"] if _ok(v.get("base"), 5, 300) else round((v["lo"] + v["hi"]) / 2, 1)
            out.append(Daily("hrv_norm", d, base, {"lo": v["lo"], "hi": v["hi"]}))
    fit = dict(data.get("fitness") or {})
    if fit:
        if not _ok(fit.get("vo2max"), 10, 95):
            fit.pop("vo2max", None)
        if not _ok(fit.get("level"), 1, 200):
            fit.pop("level", None)
        if not _ok(fit.get("threshold_s"), 120, 900):
            fit.pop("threshold_s", None)
        preds = {k: s for k, s in (fit.get("pred") or {}).items() if _ok(s, 600, 12 * 3600)}
        if preds:
            fit["pred"] = preds
        else:
            fit.pop("pred", None)
        if fit:
            out.append(Daily("fitness", today, fit.get("level") or 0, fit))
    return out


def build_samples(hrv: dict[date, float], rhr: dict[date, float], overview: dict[date, dict],
                  daily: dict[date, dict[str, int]], vo2max: float | None, today: date) -> list[Sample]:
    out: list[Sample] = []
    out += [s for d, v in hrv.items() if (s := _point("hrv", d, HRV_AT, v))]
    out += [s for d, v in rhr.items() if (s := _point("rhr", d, RHR_AT, v))]
    for d, ov in overview.items():
        stages = night_stages(ov, daily.get(d))
        if stages:
            out += sleep_samples(ov, stages)
    if vo2max is not None and (s := _point("vo2max", today, VO2_AT, vo2max)):
        out.append(s)
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
    error only loses that piece: the rest of the sync goes on."""

    def __init__(self, mcp: McpSession):
        self.mcp, self.calls, self.failed = mcp, 0, 0

    async def __call__(self, tool: str, args: dict) -> str | None:
        if self.calls >= MAX_CALLS:
            logger.info("COROS call cap reached, %s skipped", tool)
            return None
        if self.calls:
            await asyncio.sleep(CALL_DELAY_S)
        self.calls += 1
        try:
            return await self.mcp.call_tool(tool, args)
        except ToolError as e:
            self.failed += 1
            logger.info("COROS %s %s failed: %s", tool, args, e)
            return None

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


async def _fetch(mcp: McpSession, days: int, today: date) -> tuple[dict, int, int]:
    await mcp.initialize()
    call = _Fetcher(mcp)
    # COROS dates nights in the athlete's time zone: ahead of the server's
    # UTC day in Asia, this morning's night is « tomorrow » here. The window
    # ends a day later (an empty future day costs nothing) and keeps its size.
    hi = today + timedelta(days=1)
    lo = hi - timedelta(days=days - 1)
    data: dict = {"hrv": {}, "hrv_range": {}, "rhr": {}, "overview": {}, "daily": {}, "vo2max": None}
    # the daily values first: every athlete has them, night data is optional
    data["load"] = _parsed(parse_training_load, await call.recent(
        "queryTrainingLoadAssessment", days, LOAD_FALLBACK_DAYS))
    data["recovery"] = _parsed(parse_recovery, await call("queryRecoveryStatus", {})) or None
    data["hr_day"] = _parsed(parse_avg_hr, await call.recent("queryAvgHeartRate", days))
    text = await call.recent("queryDailyHealthData", days)
    data["daily"] = _parsed(parse_daily_sleep, text)
    data["activity"] = _parsed(parse_daily_activity, text)
    data["fitness"] = _parsed(parse_fitness, await call("queryFitnessAssessmentOverview", {}))
    data["vo2max"] = data["fitness"].get("vo2max")
    data["rhr"] = _parsed(parse_rhr, await call.recent("queryRestingHeartRate", days))
    for a, b in _ranges(lo, hi, HRV_CHUNK_DAYS):
        text = await call("querySleepHrv", {"startDate": _ymd(a), "endDate": _ymd(b), "days": (b - a).days + 1})
        data["hrv"].update(_parsed(parse_hrv, text))
        data["hrv_range"].update(_parsed(parse_hrv_range, text))
    for a, b in _ranges(lo, hi, SLEEP_CHUNK_DAYS):
        text = await call("querySleepOverview", {"startDate": _ymd(a), "endDate": _ymd(b)})
        data["overview"].update(_parsed(parse_sleep_overview, text))
    return data, call.calls, call.failed


async def _drop_stale_intervals(db: AsyncSession, user_id: int, nights, samples: list[Sample],
                                source: str = SOURCE) -> None:
    """A night's stage boundaries move when the watch revises its minutes: the
    intervals of a rewritten night that are no longer produced go, or the old
    and new ones would add up."""
    keep = {(s.start, s.kind) for s in samples if s.metric == "sleep"}
    for d in nights:
        rows = await db.execute(select(HealthSample.id, HealthSample.start_at, HealthSample.kind).where(
            HealthSample.user_id == user_id, HealthSample.metric == "sleep", HealthSample.source == source,
            HealthSample.start_at >= datetime.combine(d - timedelta(days=1), time(18, 0)),
            HealthSample.start_at < datetime.combine(d, time(12, 0))))
        stale = [r.id for r in rows.all() if (r.start_at, r.kind) not in keep]
        if stale:
            await db.execute(delete(HealthSample).where(HealthSample.id.in_(stale)))
            await reaggregate(db, user_id, {"sleep": {d}})  # also when nothing replaces them


async def sync_connection(db: AsyncSession, conn: CorosConnection) -> dict:
    """Fetch and store; returns store_samples' counts. Raises CorosAuthError
    when the athlete must reconnect, CorosError when COROS can't be read."""
    # the history comes once: on the first sync, or while no daily value
    # arrived yet (a first sync that read nothing must not lose the 60 days;
    # a link made before the daily values were read gets them too)
    have = (await db.execute(
        select(func.count(HealthMetric.id)).where(
            HealthMetric.user_id == conn.user_id, HealthMetric.source == SOURCE,
            HealthMetric.metric.in_(("load", "hr_day", "steps")))
    )).scalar()
    days = BACKFILL_DAYS if conn.last_sync_at is None or not have else RECENT_DAYS
    today = datetime.now(timezone.utc).date()
    async with http_client() as client:
        token = await access_token(db, conn, client)
        for attempt in range(2):
            mcp = McpSession(client, conn.resource_url, token)
            try:
                data, calls, failed = await _fetch(mcp, days, today)
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
    latest = max([*data["hrv"], *data["rhr"], *data["overview"], *data["activity"],
                  *data["load"], *data["hr_day"]], default=None)
    today = _today(latest)
    samples = build_samples(data["hrv"], data["rhr"], data["overview"], data["daily"],
                            data["vo2max"], today)
    await _drop_stale_intervals(db, conn.user_id, data["overview"], samples)
    result = await store_samples(db, conn.user_id, samples) if samples else {
        "received": 0, "inserted": 0, "updated": 0, "unchanged": 0, "by_metric": {}, "days": {}}
    daily = await store_daily(db, conn.user_id, build_daily(data, today), SOURCE)
    result["inserted"] += daily["inserted"]
    result["updated"] += daily["updated"]
    result["by_metric"] = {**result["by_metric"], **daily["by_metric"]}
    logger.info("COROS sync user %d (%d days, %d calls, %d failed): %s",
                conn.user_id, days, calls, failed, result["by_metric"])
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
        conn.last_error = None
        outcome = {"ok": True, "result": result}
    except CorosAuthError as e:
        outcome = {"ok": False, "error": str(e)}
    except (CorosError, httpx.HTTPError) as e:
        logger.warning("COROS sync failed for user %d: %r", conn.user_id, e)
        conn.last_error = str(e) if isinstance(e, CorosError) else "COROS ne répond pas pour l'instant."
        outcome = {"ok": False, "error": conn.last_error}
    conn.sync_claimed_at = None
    await db.commit()
    return outcome


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
    claimed = _as_utc(conn.sync_claimed_at)
    return {
        "connected": True,
        "needs_reauth": conn.needs_reauth,
        "last_sync_at": conn.last_sync_at,
        "last_sync_ago": _ago(conn.last_sync_at),
        "syncing": bool(claimed and datetime.now(timezone.utc) - claimed < CLAIM_TTL),
        "last_error": conn.last_error,
        "per_metric": [(METRIC_LABELS[m], days[m]) for m in METRICS if days.get(m)]
                      + [(DAILY_LABELS[m], daily[m]) for m in DAILY_LABELS if daily.get(m)],
    }
