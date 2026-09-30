import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.celery_app import celery_app
from app.models.coros import CorosConnection

logger = logging.getLogger(__name__)


@celery_app.task(name="paceforge.sync_coros")
def sync_coros() -> dict:
    """Hourly (beat): sync every COROS link not synced for 6 hours, so the
    data arrives even when the athlete doesn't open PaceForge."""
    try:
        return asyncio.run(_run())
    except Exception:
        logger.exception("COROS sync round failed")
        return {"synced": 0, "failed": 0, "error": "failed"}


async def _run() -> dict:
    from app.database import get_task_session
    from app.services.coros import SYNC_EVERY, run_sync

    synced = failed = 0
    async with get_task_session() as db:
        due_before = datetime.now(timezone.utc) - SYNC_EVERY
        ids = (await db.execute(
            select(CorosConnection.id).where(
                CorosConnection.needs_reauth.is_(False),
                or_(CorosConnection.last_sync_at.is_(None), CorosConnection.last_sync_at < due_before),
            ).order_by(CorosConnection.last_sync_at.asc().nulls_first())
        )).scalars().all()
        for conn_id in ids:
            conn = await db.get(CorosConnection, conn_id)
            if conn is None:
                continue
            try:
                outcome = await run_sync(db, conn)
            except Exception:
                logger.exception("COROS sync failed for connection %d", conn_id)
                await db.rollback()
                outcome = {"ok": False}
            if outcome and outcome.get("ok"):
                synced += 1
            elif outcome:
                failed += 1
            await asyncio.sleep(2)  # spread the load on COROS
    logger.info("COROS sync round: %d synced, %d failed, %d due", synced, failed, len(ids))
    return {"synced": synced, "failed": failed}
