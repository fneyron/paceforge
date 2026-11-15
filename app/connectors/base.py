"""What the official connectors share: OAuth, tokens, the link's lifecycle, activities into Activity.

- OAuth 2.0 authorization code, server side: the state (and the PKCE verifier, Fernet-encrypted, 10 minutes) ride in
  the signed session cookie between /connect/{provider} and its callback, as COROS's MCP link does
  (app.routers.coros): a callback reached in another browser, or with another state, is refused.
- Tokens are Fernet-encrypted (app.crypto). A refresh may ROTATE the refresh token (Garmin returns a new one each
  time): one refresh at a time per link (row lock + per-process lock), and the new pair is committed before anything
  else, as services.coros.access_token does.
- One link per athlete and provider (official_connections), one PaceForge account per provider account: a provider
  account already linked to another PaceForge account is refused (its data would reach two accounts).
- Making the official link replaces that brand's former link (COROS's MCP link, the Garmin Connect login): one link
  per brand, nothing received is lost (same source label, same activity ids).
- Disconnecting tells the provider (COROS deauthorize, Garmin's user registration deleted, which Garmin's program
  requires for a « Déconnecter » or a deleted account) best effort, then forgets the tokens. Data received stays.
- Activities: one row per outing (activity_sources): found by the provider's id, else the twin another service
  brought gets that id, else a new row.
"""
import asyncio
import base64
import hashlib
import json
import logging
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import ModuleType

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.connectors import NAMES, ready
from app.crypto import decrypt_secret, encrypt_secret
from app.models.activity import Activity
from app.models.official_connection import OfficialConnection
from app.services import activity_env
from app.services.activity_sources import find_twin, merge_twins
from app.services.health import _ago

logger = logging.getLogger(__name__)

ACTIVE, REAUTH = "active", "reauth"
PENDING_TTL_S = 10 * 60  # a connect not completed within it starts again
CALLBACK = "/connect/{provider}/callback"  # registered with each provider: https://paceforge.fr/connect/…/callback
# activity raw_data keys a later write keeps (what was looked up or computed for the row)
KEPT = (activity_env.ENV_KEY, "paceforge_revision", "paceforge_heat", "paceforge_dplus", "laps_read")

# tests swap in an httpx.MockTransport here
_transport: httpx.AsyncBaseTransport | None = None


def http_client(timeout: float = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport, timeout=timeout, follow_redirects=False,
                             headers={"User-Agent": "PaceForge (paceforge.fr)"})


class ConnectorError(Exception):
    """A provider failure; the message is plain French, shown as is."""


class ConnectorAuthError(ConnectorError):
    """The link is gone (refresh refused, access withdrawn): the athlete must reconnect."""


class ConnectorKeyError(ConnectorError):
    """A stored token no configured key decrypts (app.crypto): the link is only skipped, nothing is written."""


class TokenError(ConnectorError):
    def __init__(self, code: str):
        super().__init__(f"Le service a refusé le jeton ({code}).")
        self.code = code


class AlreadyLinkedError(ConnectorError):
    """This provider account is linked to another PaceForge account."""


def module(provider: str) -> ModuleType:
    """The provider's own module (imported late: it imports this one)."""
    if provider == "coros":
        from app.connectors import coros_partner
        return coros_partner
    if provider == "garmin":
        from app.connectors import garmin_connect
        return garmin_connect
    raise KeyError(provider)


def endpoints(defaults: dict[str, str], overrides: str) -> dict[str, str]:
    """The provider's endpoint URLs: `defaults`, each overridable by the setting's JSON object (an https URL only;
    an unreadable setting or an unknown key is ignored, and logged)."""
    out = dict(defaults)
    if not overrides.strip():
        return out
    try:
        given = json.loads(overrides)
    except ValueError:
        logger.error("Official connector URL overrides are not a JSON object: ignored")
        return out
    for key, url in (given.items() if isinstance(given, dict) else ()):
        if key in out and isinstance(url, str) and url.startswith("https://"):
            out[key] = url
        else:
            logger.error("Official connector URL override %r ignored (unknown key or not https)", key)
    return out


def redirect_uri(provider: str) -> str:
    return settings.BASE_URL.rstrip("/") + CALLBACK.format(provider=provider)


# ── OAuth ───────────────────────────────────────────────────────────────────

def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge): 86 URL-safe characters (RFC 7636: 43 to 128)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(24)


def seal(value: str) -> str:
    return encrypt_secret(value).decode()


def unseal(value: str | None) -> str | None:
    """A value sealed within the last PENDING_TTL_S, else None (expired, or not ours)."""
    try:
        return decrypt_secret(str(value).encode(), ttl=PENDING_TTL_S)
    except (InvalidToken, ValueError):
        return None


def state_matches(given: str | None, expected: str | None) -> bool:
    return bool(given and expected) and secrets.compare_digest(str(given).encode(), str(expected).encode())


async def token_request(client: httpx.AsyncClient, url: str, data: dict) -> dict:
    """POST a token request (form-encoded); the JSON answer when it holds an access token, else TokenError with the
    OAuth error code (or http_<status>)."""
    r = await client.post(url, data=data, headers={"Accept": "application/json"})
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and isinstance(body, dict) and body.get("access_token"):
        return body
    code = (body.get("error") or body.get("result")) if isinstance(body, dict) else None
    raise TokenError(str(code or f"http_{r.status_code}")[:40])


def _seconds(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def apply_tokens(conn: OfficialConnection, tokens: dict) -> None:
    now = datetime.now(timezone.utc)
    conn.access_token_encrypted = encrypt_secret(str(tokens["access_token"]))
    if tokens.get("refresh_token"):
        conn.refresh_token_encrypted = encrypt_secret(str(tokens["refresh_token"]))
    expires_in = _seconds(tokens.get("expires_in"))
    conn.expires_at = now + timedelta(seconds=expires_in) if expires_in else None
    refresh_in = _seconds(tokens.get("refresh_token_expires_in"))
    if refresh_in:
        conn.refresh_expires_at = now + timedelta(seconds=refresh_in)


# ── links ───────────────────────────────────────────────────────────────────

async def connection_for(db: AsyncSession, user_id: int, provider: str) -> OfficialConnection | None:
    return (await db.execute(select(OfficialConnection).where(
        OfficialConnection.user_id == user_id, OfficialConnection.provider == provider))).scalar_one_or_none()


async def connections_of(db: AsyncSession, provider: str, provider_user_ids) -> dict[str, OfficialConnection]:
    """The active links of these provider account ids (unknown ones are simply absent)."""
    ids = sorted({str(i) for i in provider_user_ids if i is not None and str(i).strip()})
    if not ids:
        return {}
    rows = (await db.execute(select(OfficialConnection).where(
        OfficialConnection.provider == provider, OfficialConnection.provider_user_id.in_(ids),
        OfficialConnection.status == ACTIVE))).scalars().all()
    return {c.provider_user_id: c for c in rows}


async def store_connection(db: AsyncSession, user_id: int, provider: str, provider_user_id: str, tokens: dict,
                           scopes: str | None = None) -> OfficialConnection:
    """The link made at the callback (replacing this athlete's previous one for that provider). AlreadyLinked when
    the provider account belongs to another PaceForge account (AlreadyLinkedError)."""
    other = (await db.execute(select(OfficialConnection.user_id).where(
        OfficialConnection.provider == provider, OfficialConnection.provider_user_id == provider_user_id,
        OfficialConnection.user_id != user_id))).scalar()
    if other is not None:
        raise AlreadyLinkedError(f"Ce compte {NAMES[provider]} est déjà relié à un autre compte PaceForge. "
                            "Déconnecte-le de cet autre compte d’abord.")
    conn = await connection_for(db, user_id, provider)
    if conn is None:
        conn = OfficialConnection(user_id=user_id, provider=provider)
        db.add(conn)
    conn.provider_user_id = provider_user_id
    apply_tokens(conn, tokens)
    conn.scopes = scopes
    conn.status = ACTIVE
    conn.last_error = None
    conn.sync_claimed_at = None
    conn.last_sync_at = None
    await db.flush()
    logger.info("%s official link made for user %d", NAMES[provider], user_id)
    return conn


async def legacy_connected(db: AsyncSession, user_id: int, provider: str) -> bool:
    """Whether the brand's former link exists (COROS's MCP link, the Garmin Connect login)."""
    if provider == "coros":
        from app.services import coros
        return await coros.connection_for(db, user_id) is not None
    from app.services import garmin
    return await garmin.connection_for(db, user_id) is not None


LEGACY_TIMEOUT_S = 8


async def replace_legacy(db: AsyncSession, user_id: int, provider: str) -> bool:
    """The brand's former link let go once the official one is made (one link per brand; what it brought stays).
    Best effort and time-bounded: a failure leaves it in place, logged, the official link stands."""
    from app.services import coros, garmin

    service = coros if provider == "coros" else garmin
    old = await service.connection_for(db, user_id)
    if old is None:
        return False
    try:
        async with db.begin_nested():
            await asyncio.wait_for(service.disconnect(db, old), LEGACY_TIMEOUT_S)
    except Exception:
        logger.warning("%s former link of user %d not let go", NAMES[provider], user_id, exc_info=True)
        return False
    logger.info("%s former link of user %d replaced by the official one", NAMES[provider], user_id)
    return True


# ── tokens: refresh with rotation ───────────────────────────────────────────

_locks: dict[int, tuple[asyncio.AbstractEventLoop, dict[int, asyncio.Lock]]] = {}


def _lock_for(conn_id: int) -> asyncio.Lock:
    """One lock per link and event loop (Celery runs each task in a new loop)."""
    loop = asyncio.get_running_loop()
    for key in [k for k, (lp, _) in _locks.items() if lp.is_closed()]:
        del _locks[key]
    entry = _locks.get(id(loop))
    if entry is None or entry[0] is not loop:
        entry = _locks[id(loop)] = (loop, defaultdict(asyncio.Lock))
    return entry[1][conn_id]


def as_utc(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _fresh(conn: OfficialConnection, margin: timedelta) -> bool:
    exp = as_utc(conn.expires_at)
    return exp is None or exp - datetime.now(timezone.utc) > margin


def stored(encrypted: bytes | None) -> str:
    """A stored token; ConnectorKeyError when no configured key decrypts it (nothing is written about it)."""
    try:
        return decrypt_secret(encrypted or b"")
    except InvalidToken:
        raise ConnectorKeyError("La connexion est à refaire.") from None


def tokens_readable(conn: OfficialConnection) -> bool:
    for encrypted in (conn.access_token_encrypted, conn.refresh_token_encrypted):
        if encrypted:
            try:
                decrypt_secret(encrypted)
            except InvalidToken:
                return False
    return True


async def mark_reauth(db: AsyncSession, conn: OfficialConnection, message: str) -> ConnectorAuthError:
    conn.status = REAUTH
    conn.last_error = message
    await db.commit()
    logger.warning("%s official link of user %d needs reconnecting", NAMES.get(conn.provider), conn.user_id)
    return ConnectorAuthError(message)


REFUSED = ("invalid_grant", "invalid_client", "unauthorized_client", "http_400", "http_401")


async def access_token(db: AsyncSession, conn: OfficialConnection, client: httpx.AsyncClient,
                       rejected: str | None = None) -> str:
    """A usable access token, refreshed when it expires soon (the provider's REFRESH_MARGIN) or when the provider
    just `rejected` it. Commits: a rotated refresh token must never be lost to a later rollback."""
    spec = module(conn.provider)
    if rejected is None and _fresh(conn, spec.REFRESH_MARGIN):
        return stored(conn.access_token_encrypted)
    async with _lock_for(conn.id):
        # another worker may have rotated the pair meanwhile: read it again, locked
        await db.refresh(conn, with_for_update=True)
        if conn.status != ACTIVE:
            await db.commit()
            raise ConnectorAuthError(conn.last_error or f"Reconnecte {NAMES[conn.provider]}.")
        current = stored(conn.access_token_encrypted)
        if (rejected is None and _fresh(conn, spec.REFRESH_MARGIN)) or (rejected is not None and current != rejected):
            await db.commit()  # releases the row lock
            return current
        if not conn.refresh_token_encrypted:
            raise await mark_reauth(db, conn, spec.EXPIRED)
        refresh = stored(conn.refresh_token_encrypted)
        try:
            tokens = await spec.refresh(client, refresh, current)
        except TokenError as e:
            if e.code in REFUSED:
                raise await mark_reauth(db, conn, spec.EXPIRED) from e
            await db.commit()
            raise ConnectorError(f"{NAMES[conn.provider]} ne répond pas pour l’instant.") from e
        except httpx.HTTPError as e:
            await db.commit()
            raise ConnectorError(f"{NAMES[conn.provider]} ne répond pas pour l’instant.") from e
        apply_tokens(conn, tokens)
        await db.commit()
        logger.info("%s official tokens refreshed for user %d", NAMES[conn.provider], conn.user_id)
        return str(tokens["access_token"])


async def refresh_due(db: AsyncSession, conn: OfficialConnection, within: timedelta) -> bool:
    """Refresh the pair now when the access token, or the refresh token, lapses within `within` (a link nobody
    reads, as Garmin's push-only ones, keeps a working refresh token: deregistering it later needs one)."""
    now = datetime.now(timezone.utc)
    soon = [t for t in (as_utc(conn.expires_at), as_utc(conn.refresh_expires_at)) if t is not None]
    if conn.status != ACTIVE or not soon or min(soon) - now > within or not tokens_readable(conn):
        return False
    async with http_client() as client:
        await access_token(db, conn, client, rejected=stored(conn.access_token_encrypted))
    return True


# ── disconnecting ───────────────────────────────────────────────────────────

async def disconnect(db: AsyncSession, conn: OfficialConnection, *, tell_provider: bool = True) -> None:
    """Tell the provider (best effort: an expired access token is refreshed for it, not stored, the row goes), then
    forget the link. Data already received stays."""
    spec = module(conn.provider)
    if tell_provider and conn.status == ACTIVE:
        try:
            async with http_client(timeout=10) as client:
                token = stored(conn.access_token_encrypted)
                if not _fresh(conn, timedelta(0)) and conn.refresh_token_encrypted:
                    token = str((await spec.refresh(client, stored(conn.refresh_token_encrypted), token))
                                ["access_token"])
                await spec.deregister(client, conn, token)
        except (ConnectorError, httpx.HTTPError, KeyError, ValueError) as e:
            logger.info("%s official deregistration failed for user %d: %r", NAMES[conn.provider], conn.user_id, e)
    await db.delete(conn)
    await db.flush()
    logger.info("%s official link of user %d forgotten", NAMES[conn.provider], conn.user_id)


# ── what Réglages and the app show ──────────────────────────────────────────

def _latest(conn: OfficialConnection) -> datetime | None:
    stamps = [t for t in (as_utc(conn.last_push_at), as_utc(conn.last_sync_at)) if t is not None]
    return max(stamps) if stamps else None


async def status(db: AsyncSession, user_id: int, provider: str) -> dict:
    """The official link as the pages show it: connected, to reconnect, when data last came, the former link it
    would replace, the permissions the athlete did not grant."""
    conn = await connection_for(db, user_id, provider)
    out = {"provider": provider, "name": NAMES[provider], "official": True, "active": ready(provider),
           "connected": conn is not None, "legacy": await legacy_connected(db, user_id, provider)}
    if conn is None:
        return out
    readable = tokens_readable(conn)
    latest = _latest(conn)
    out.update({
        "needs_reauth": conn.status != ACTIVE or not readable,
        "last_error": conn.last_error if readable else "La connexion est à refaire.",
        "last_sync_at": latest, "last_sync_ago": _ago(latest),
        "connected_at": as_utc(conn.created_at),
        "missing": module(provider).missing_permissions(conn.scopes),
        "syncing": False,
    })
    return out


async def statuses(db: AsyncSession, user_id: int, session: dict | None = None) -> list[dict]:
    """The official connections this account may see (none while every connector is off), each with its one-time
    flashes from the session (Post/Redirect/Get)."""
    from app.connectors import PROVIDERS, visible_to

    out = []
    for provider in PROVIDERS:
        if not visible_to(provider, user_id):
            continue
        s = await status(db, user_id, provider)
        if session is not None:
            s["ok"] = session.pop(f"official_{provider}_ok", None)
            s["error"] = session.pop(f"official_{provider}_error", None)
        out.append(s)
    return out


# ── activities ──────────────────────────────────────────────────────────────

ID_COLUMNS = {"coros": "coros_activity_id", "garmin": "garmin_activity_id"}
_CORE = ("sport_type", "start_date", "distance", "moving_time", "elapsed_time", "total_elevation_gain",
         "average_heartrate", "max_heartrate", "average_speed")


def _own_row(act: Activity, provider: str) -> bool:
    """A row only this provider brought (Strava's and the other watch's fields are never overwritten)."""
    other = "coros_activity_id" if provider == "garmin" else "garmin_activity_id"
    return act.strava_activity_id is None and getattr(act, other) is None


def _changed(act: Activity, fields: dict) -> bool:
    for key in _CORE:
        old, new = getattr(act, key), fields.get(key)
        if key == "start_date":
            old, new = as_utc(old), as_utc(new)
        if old != new:
            return True
    return False


async def import_activity(db: AsyncSession, user_id: int, provider: str, fields: dict) -> tuple[str, Activity | None]:
    """One session into Activity: (outcome, row). « updated » the provider's own row kept current, « linked » the
    twin another service (or the former link, under another id) brought, « inserted » a new row, « kept » a row
    another service owns, « foreign » an id another account holds (never touched)."""
    column = ID_COLUMNS[provider]
    pid = fields[column]
    act = (await db.execute(select(Activity).where(getattr(Activity, column) == pid))).scalar_one_or_none()
    if act is not None and act.user_id != user_id:
        return "foreign", None
    if act is not None:
        if not _own_row(act, provider):
            return "kept", act
        old = act.raw_data or {}
        changed = _changed(act, fields)
        for key, value in fields.items():
            if key == "laps" and value is None:
                continue
            setattr(act, key, value)
        raw = {**fields["raw_data"], **{k: old[k] for k in KEPT if k in old}}
        if changed:  # the per-worker caches key on the revision; the derived values are computed again
            act.streams_data = None
            act.computed_metrics = None
            raw = {k: v for k, v in raw.items() if k not in ("paceforge_heat", "paceforge_dplus")}
            previous = raw.get("paceforge_revision", 0)
            raw["paceforge_revision"] = (previous if type(previous) is int and previous >= 0 else 0) + 1
        act.raw_data = raw
        return "updated", act
    twin = await find_twin(db, user_id, fields["sport_type"], fields["start_date"], fields["distance"],
                           fields["moving_time"])
    if twin is not None:
        if getattr(twin, column) is None:
            setattr(twin, column, pid)
        return "linked", twin
    act = Activity(user_id=user_id, **fields)
    db.add(act)
    await db.flush()
    return "inserted", act


async def import_activities(db: AsyncSession, user_id: int, provider: str, rows: list[dict]) -> dict:
    """Several sessions, then the twins Strava saved apart merged (activity_sources.merge_twins) and each new outdoor
    session's altitude and weather looked up (never fails the import)."""
    counts: dict[str, int] = defaultdict(int)
    starts = []
    for fields in rows:
        outcome, _ = await import_activity(db, user_id, provider, fields)
        counts[outcome] += 1
        starts.append(fields["start_date"])
    await db.flush()
    if starts:
        counts["merged"] = await merge_twins(db, user_id, min(starts))
        await activity_env.enrich(db, user_id)
    return dict(counts)
