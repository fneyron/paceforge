"""Personal API keys: made, listed, revoked (Réglages › Accès API), and how a key is checked (/api/v1).

A key is shown once, when it is made: the session carries it encrypted (app.crypto) to the page that shows it,
which takes it out at once; the database holds its SHA-256 only, looked up by that hash (never compared in clear).
At most MAX_ACTIVE live keys per account. A password reset revokes them all; withdrawing the consent to health
data takes the health scope off them (health_consent.erase_health_data); the account's deletion deletes them.
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_token import ApiToken

PREFIX = "pf_"
SHOWN = 8  # the characters kept to tell keys apart (« pf_Ab3dE »)
MAX_ACTIVE = 5
NAME_MAX = 60
# a key's shape: pf_ + secrets.token_urlsafe(32) (43 characters); anything else is refused before a lookup
SHAPE = re.compile(r"pf_[A-Za-z0-9_-]{43}")
KEPT_REVOKED = timedelta(days=30)  # a revoked key is refused with its own message this long, then deleted
FLASH_KEY = "api_new"
FLASH_TTL_S = 300  # the fresh key is shown on the page that follows its creation, within five minutes


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def clean_name(raw: str | None) -> str:
    """The name as typed, its blanks collapsed, control characters out, NAME_MAX characters at most."""
    name = re.sub(r"\s+", " ", "".join(c for c in (raw or "") if c.isprintable() or c.isspace())).strip()
    return name[:NAME_MAX]


async def active(db: AsyncSession, user_id: int) -> list[ApiToken]:
    return list((await db.scalars(select(ApiToken).where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
                                  .order_by(ApiToken.created_at.desc(), ApiToken.id.desc()))).all())


async def active_count(db: AsyncSession, user_id: int) -> int:
    return int(await db.scalar(select(func.count(ApiToken.id)).where(
        ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))) or 0)


async def create(db: AsyncSession, user_id: int, name: str, health: bool) -> tuple[ApiToken, str]:
    """A new key for the account: its row (added, not committed) and the key itself, to show once."""
    token = new_token()
    row = ApiToken(user_id=user_id, name=name, prefix=token[:SHOWN], token_hash=digest(token),
                   scopes="read health" if health else "read")
    db.add(row)
    await db.flush()
    return row, token


async def revoke(db: AsyncSession, user_id: int, token_id: int) -> bool:
    """Revoke one of the account's live keys; whether there was one."""
    result = await db.execute(update(ApiToken).where(
        ApiToken.id == token_id, ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None),
    ).values(revoked_at=datetime.now(timezone.utc)).execution_options(synchronize_session=False))
    return bool(getattr(result, "rowcount", 0))


async def revoke_all(db: AsyncSession, user_id: int) -> None:
    """Every live key of the account (a password reset: whoever reset it makes new keys)."""
    await db.execute(update(ApiToken).where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
                     .values(revoked_at=datetime.now(timezone.utc)).execution_options(synchronize_session=False))


async def drop_health_scope(db: AsyncSession, user_id: int) -> None:
    """The consent to health data withdrawn: no key of the account reads them any more, even after a new consent
    (a key opened to them again is a new key, made with its box ticked)."""
    await db.execute(update(ApiToken).where(ApiToken.user_id == user_id, ApiToken.scopes != "read")
                     .values(scopes="read").execution_options(synchronize_session=False))


async def purge_revoked(db: AsyncSession, now: datetime | None = None) -> int:
    """Delete the keys revoked more than KEPT_REVOKED ago (hourly upkeep)."""
    cutoff = (now or datetime.now(timezone.utc)) - KEPT_REVOKED
    result = await db.execute(delete(ApiToken).where(ApiToken.revoked_at.is_not(None), ApiToken.revoked_at < cutoff)
                              .execution_options(synchronize_session=False))
    return getattr(result, "rowcount", 0) or 0


def flash(session: dict, name: str, token: str) -> None:
    """The fresh key for the next page only, encrypted: the session cookie is signed, not secret."""
    from app.crypto import encrypt_secret

    session[FLASH_KEY] = {"name": name, "sealed": encrypt_secret(token).decode()}


def take_flash(session: dict) -> dict | None:
    """{name, token} of the key made just before, once: taken out of the session whatever happens."""
    from cryptography.fernet import InvalidToken

    from app.crypto import decrypt_secret

    data = session.pop(FLASH_KEY, None)
    if not isinstance(data, dict) or not isinstance(data.get("sealed"), str):
        return None
    try:
        token = decrypt_secret(data["sealed"].encode(), ttl=FLASH_TTL_S)
    except (InvalidToken, ValueError):
        return None
    return {"name": str(data.get("name") or ""), "token": token} if SHAPE.fullmatch(token) else None
