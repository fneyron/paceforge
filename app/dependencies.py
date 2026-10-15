import time
from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.user import User
from app.services import sessions


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def sign_in(request: Request, user: User) -> None:
    """This browser's session for `user`, carrying the account's session version (a password reset bumps it, and
    every session holding an older one is refused, get_current_user), its own id (logout revokes it,
    app.services.sessions) and its birth time (no session outlives SESSION_LIFETIME_DAYS)."""
    request.session["user_id"] = user.id
    request.session["session_version"] = user.session_version or 0
    request.session["sid"] = sessions.new_id()
    request.session["iat"] = int(time.time())


def _current(request: Request, user: User | None) -> bool:
    """A session made before its account's last password reset is no longer valid (older sessions, made before
    the version was kept, count as version 0, the default)."""
    return user is not None and request.session.get("session_version", 0) == (user.session_version or 0)


async def _live(request: Request) -> bool:
    """Whether this browser's session may still be used: born with an id (one without, made before logout could
    revoke anything, is signed out once), within its absolute lifetime, and not signed out since."""
    sid = request.session.get("sid")
    if not sid or not sessions.alive(request.session.get("iat")):
        return False
    return not await sessions.is_revoked(sid)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise _redirect_to_login()
    if not await _live(request):
        request.session.clear()
        raise _redirect_to_login()

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not _current(request, user):
        request.session.clear()
        raise _redirect_to_login()

    if user.email and not user.email_verified:
        from fastapi import HTTPException
        request.session.pop("user_id", None)
        request.session["pending_user_id"] = user.id
        raise HTTPException(status_code=303, headers={"Location": "/auth/check-email"})

    return user


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id or not await _live(request):
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not _current(request, user):
        return None
    return user if not user.email or user.email_verified else None


def _redirect_to_login() -> Exception:
    from fastapi import HTTPException

    raise HTTPException(
        status_code=303,
        headers={"Location": "/auth/login"},
    )
