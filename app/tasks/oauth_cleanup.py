"""Hourly upkeep (beat: paceforge.purge_oauth_attempts).

- Expired Strava link attempts (oauth_attempts, 10 minutes) go, even when
  nobody starts another one: an attempt begun with app keys typed in the setup
  wizard holds them, encrypted, until it is purged.
- Strava tokens still stored in clear (written before they were encrypted)
  are written again, encrypted: a refresh token Strava hands back unchanged is
  never rewritten by the refresh itself. Done once; nothing to do afterwards.
"""
import asyncio

from app.celery_app import celery_app

CLEAR_BATCH = 500  # athletes rewritten per run; the next run goes on


@celery_app.task(name="paceforge.purge_oauth_attempts", ignore_result=True)
def purge_oauth_attempts() -> int:
    return asyncio.run(_run())


async def _run() -> int:
    from app.database import get_task_session
    from app.services.oauth_attempts import purge_expired

    async with get_task_session() as db:
        purged = await purge_expired(db)
        await db.commit()
        await encrypt_clear_strava_tokens(db)
    return purged


async def encrypt_clear_strava_tokens(db) -> int:
    """Rewrite, encrypted, the Strava tokens still stored in clear; how many athletes (0 once done). Their rows
    are locked and read again first, so a refresh running at the same moment is never undone."""
    from sqlalchemy import String, or_, select, type_coerce
    from sqlalchemy.orm.attributes import flag_modified

    from app.crypto import FERNET_PREFIX
    from app.models.user import User

    def in_clear(column):
        stored = type_coerce(column, String)  # the stored text, not what the column type reads from it
        return stored.is_not(None) & stored.not_like(f"{FERNET_PREFIX}%")

    tokens = ("strava_access_token", "strava_refresh_token")
    ids = (await db.execute(select(User.id).where(or_(*(in_clear(getattr(User, t)) for t in tokens)))
                            .limit(CLEAR_BATCH))).scalars().all()
    if not ids:
        return 0
    users = (await db.execute(select(User).where(User.id.in_(ids)).with_for_update()
                              .execution_options(populate_existing=True))).scalars().all()
    for user in users:
        for token in tokens:
            if getattr(user, token) is not None:
                flag_modified(user, token)  # same value: written again, through the encrypting column type
    await db.commit()
    return len(users)
