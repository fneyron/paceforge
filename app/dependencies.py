from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.user import User


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def sign_in(request: Request, user: User) -> None:
    """This browser's session for `user`, carrying the account's session version: a password reset bumps it,
    and every session holding an older one is refused (get_current_user)."""
    request.session["user_id"] = user.id
    request.session["session_version"] = user.session_version or 0


def _current(request: Request, user: User | None) -> bool:
    """A session made before its account's last password reset is no longer valid (older sessions, made before
    the version was kept, count as version 0, the default)."""
    return user is not None and request.session.get("session_version", 0) == (user.session_version or 0)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise _redirect_to_login()

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not _current(request, user):
        request.session.clear()
        raise _redirect_to_login()

    if user.email and not user.email_verified:
        request.session.pop("user_id", None)
        request.session["pending_user_id"] = user.id
        raise HTTPException(status_code=303, headers={"Location": "/auth/check-email"})

    return user


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not _current(request, user):
        return None
    return user if not user.email or user.email_verified else None


def _redirect_to_login() -> HTTPException:
    return HTTPException(status_code=303, headers={"Location": "/auth/login"})
