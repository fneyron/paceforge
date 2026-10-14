"""Expired Strava link attempts (oauth_attempts, 10 minutes) go every hour, even
when nobody starts another one: an attempt begun with app keys typed in the
setup wizard holds them, encrypted, until it is purged."""
import asyncio

from app.celery_app import celery_app


@celery_app.task(name="paceforge.purge_oauth_attempts", ignore_result=True)
def purge_oauth_attempts() -> int:
    return asyncio.run(_run())


async def _run() -> int:
    from app.database import get_task_session
    from app.services.oauth_attempts import purge_expired

    async with get_task_session() as db:
        purged = await purge_expired(db)
        await db.commit()
    return purged
