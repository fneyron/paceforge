import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.celery_app import celery_app
from app.models.coros import CorosConnection

logger = logging.getLogger(__name__)


@celery_app.task(name="paceforge.sync_coros_user", ignore_result=True)  # nothing reads it: no backend call
def sync_coros_user(conn_id: int, claimed_at: str, pressed: bool = False) -> dict | None:
    """One athlete's sync, queued with its reservation by services.coros.queue_sync
    (a press of « Synchroniser maintenant », a new link, Santé opening): the web
    request only queues it. A duplicate or late delivery finds the reservation
    taken or replaced and does nothing. `pressed`: a press of the button (an owed
    history is read now, not tomorrow)."""
    return asyncio.run(_run_one(conn_id, claimed_at, pressed))


async def _run_one(conn_id: int, claimed_at: str, pressed: bool = False) -> dict | None:
    from app.database import get_task_session
    from app.services.coros import run_sync

    handed_over = False  # run_sync takes the reservation (or finds it gone) and lets go of what it took
    try:
        async with get_task_session() as db:
            conn = await db.get(CorosConnection, conn_id)
            handed_over = conn is None
            if conn is None:
                return None
            outcome = await run_sync(db, conn, claimed_at=claimed_at, pressed=pressed)
            handed_over = True
            return None if outcome is None else {"ok": bool(outcome.get("ok"))}
    finally:
        if not handed_over:  # died before (no database, a crash): never « en cours » for the claim's 15 min
            await _let_go(conn_id, claimed_at)


async def _let_go(conn_id: int, claimed_at: str) -> None:
    from app.database import get_task_session
    from app.services.coros import release_reservation

    try:
        async with get_task_session() as db:
            await release_reservation(db, conn_id, claimed_at)
    except Exception:
        logger.warning("COROS reservation of connection %d not released: it expires with its claim", conn_id,
                       exc_info=True)


@celery_app.task(name="paceforge.sync_coros")
def sync_coros() -> dict:
    """Hourly (beat): sync every COROS link not synced for 2 hours, so the
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
        # a sync is stamped when it ends, seconds after its round began: the
        # margin keeps 2 h from becoming 3 (two rounds later, not three)
        due_before = datetime.now(timezone.utc) - SYNC_EVERY + timedelta(minutes=10)
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
