import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.celery_app import celery_app
from app.models.garmin import GarminConnection

logger = logging.getLogger(__name__)


@celery_app.task(name="paceforge.sync_garmin_user")
def sync_garmin_user(conn_id: int, claimed_at: str) -> dict | None:
    return asyncio.run(_run_one(conn_id, claimed_at))


async def _run_one(conn_id: int, claimed_at: str) -> dict | None:
    from app.database import get_task_session
    from app.services.garmin import run_sync

    async with get_task_session() as db:
        conn = await db.get(GarminConnection, conn_id)
        if conn is not None:
            return await run_sync(db, conn, claimed_at=claimed_at)


@celery_app.task(name="paceforge.sync_garmin")
def sync_garmin() -> dict:
    """Hourly (beat): sync every Garmin link not synced for 2 hours, so the
    data arrives even when the athlete doesn't open PaceForge."""
    try:
        return asyncio.run(_run())
    except Exception:
        logger.exception("Garmin sync round failed")
        return {"synced": 0, "failed": 0, "error": "failed"}


async def _run() -> dict:
    from app.database import get_task_session
    from app.services.garmin import SYNC_EVERY, run_sync

    synced = failed = 0
    async with get_task_session() as db:
        # a sync is stamped when it ends, seconds after its round began: the
        # margin keeps 2 h from becoming 3 (two rounds later, not three)
        due_before = datetime.now(timezone.utc) - SYNC_EVERY + timedelta(minutes=10)
        ids = (await db.execute(
            select(GarminConnection.id).where(
                GarminConnection.needs_reauth.is_(False),
                or_(GarminConnection.last_sync_at.is_(None), GarminConnection.last_sync_at < due_before),
            ).order_by(GarminConnection.last_sync_at.asc().nulls_first())
        )).scalars().all()
        for conn_id in ids:
            conn = await db.get(GarminConnection, conn_id)
            if conn is None:
                continue
            try:
                outcome = await run_sync(db, conn)
            except Exception:
                logger.exception("Garmin sync failed for connection %d", conn_id)
                await db.rollback()
                outcome = {"ok": False}
            if outcome and outcome.get("ok"):
                synced += 1
            elif outcome:
                failed += 1
            await asyncio.sleep(2)  # spread the load on Garmin
    logger.info("Garmin sync round: %d synced, %d failed, %d due", synced, failed, len(ids))
    return {"synced": synced, "failed": failed}
