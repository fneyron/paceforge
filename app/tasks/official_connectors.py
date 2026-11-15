"""The official connectors' work in the Celery worker (app.connectors): the web request that receives a push only
checks it and queues it here. Every task does nothing once its connector is switched off (a message queued before)."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.celery_app import celery_app
from app.connectors import ready

logger = logging.getLogger(__name__)

NIGHT_RETRY_S = 30 * 60  # a night value waiting for its night is tried again every 30 min…
NIGHT_TRIES = 8  # … for 4 hours (Garmin sends the sleep summary when the watch syncs after waking)
REFRESH_WITHIN = timedelta(days=7)  # tokens refreshed by the hourly round when they (or the refresh token) lapse sooner


@celery_app.task(name="paceforge.official_garmin_push", ignore_result=True)
def garmin_push(summary_type: str, entries: list[dict]) -> None:
    """One Garmin push (its items already matched to their links, health ones only for consenting athletes)."""
    if not ready("garmin"):
        return
    waiting = asyncio.run(_garmin_push(summary_type, entries))
    for w in waiting:
        garmin_night.apply_async(args=[w["conn"], w["day"], w["metric"], w["readings"], w["fallback"], 1],
                                 countdown=NIGHT_RETRY_S)


async def _garmin_push(summary_type: str, entries: list[dict]) -> list[dict]:
    from app.connectors import garmin_connect
    from app.database import get_task_session

    async with get_task_session() as db:
        return await garmin_connect.process(db, summary_type, entries)


@celery_app.task(name="paceforge.official_garmin_night", ignore_result=True)
def garmin_night(conn_id: int, day: str, metric: str, readings: list, fallback: list | None, attempt: int) -> None:
    """A night's HRV or heart rate waiting for that night's sleep summary: tried again, then (at the last try) the HRV
    on its own window, the heart rate dropped."""
    if not ready("garmin"):
        return
    final = attempt >= NIGHT_TRIES
    outcome = asyncio.run(_garmin_night(conn_id, day, metric, readings, fallback, final))
    if outcome == "waiting":
        garmin_night.apply_async(args=[conn_id, day, metric, readings, fallback, attempt + 1], countdown=NIGHT_RETRY_S)


async def _garmin_night(conn_id: int, day: str, metric: str, readings, fallback, final: bool) -> str:
    from app.connectors import garmin_connect
    from app.database import get_task_session

    async with get_task_session() as db:
        return await garmin_connect.deferred_night(db, conn_id, day, metric, readings, fallback, final)


@celery_app.task(name="paceforge.official_coros_push", ignore_result=True)
def coros_push(entries: list[dict]) -> None:
    """One COROS push: workout summaries, each for its link ({"conn": id, "item": …})."""
    if not ready("coros"):
        return
    asyncio.run(_coros_push(entries))


async def _coros_push(entries: list[dict]) -> None:
    from app.connectors import base, coros_partner
    from app.database import get_task_session
    from app.models.official_connection import OfficialConnection

    by_conn: dict[int, list[dict]] = {}
    for e in entries:
        if isinstance(e.get("conn"), int) and isinstance(e.get("item"), dict):
            by_conn.setdefault(e["conn"], []).append(e["item"])
    async with get_task_session() as db:
        for conn_id, items in by_conn.items():
            conn = await db.get(OfficialConnection, conn_id)
            if conn is None or conn.provider != "coros" or conn.status != base.ACTIVE:
                continue
            mine = [i for i in items if coros_partner.open_id(i) == conn.provider_user_id]  # never trust a mismatch
            try:
                await coros_partner.process_push(db, conn, mine)
                await db.commit()
            except Exception:
                logger.exception("COROS official push failed for user %d", conn.user_id)
                await db.rollback()


@celery_app.task(name="paceforge.official_coros_sync", ignore_result=True)
def coros_sync(conn_id: int, backfill: bool = False) -> dict | None:
    """One COROS link's pull: its three months right after connecting (backfill), else its last week."""
    if not ready("coros"):
        return None
    return asyncio.run(_coros_sync(conn_id, backfill))


async def _coros_sync(conn_id: int, backfill: bool) -> dict | None:
    from app.connectors import coros_partner
    from app.database import get_task_session
    from app.models.official_connection import OfficialConnection

    async with get_task_session() as db:
        conn = await db.get(OfficialConnection, conn_id)
        if conn is None or conn.provider != "coros":
            return None
        outcome = await coros_partner.run_sync(db, conn, backfill=backfill)
        return None if outcome is None else {"ok": bool(outcome.get("ok"))}


@celery_app.task(name="paceforge.official_round")
def official_round() -> dict:
    """Hourly (beat, only while a connector is active): the COROS links not pulled for 2 hours, and every official
    link whose tokens lapse within a week refreshed (Garmin's push-only links included: deregistering one later
    needs a working refresh token)."""
    try:
        return asyncio.run(_round())
    except Exception:
        logger.exception("Official connectors round failed")
        return {"error": "failed"}


async def _round() -> dict:
    from app.connectors import base, coros_partner
    from app.database import get_task_session
    from app.models.official_connection import OfficialConnection

    pulled = refreshed = failed = 0
    async with get_task_session() as db:
        providers = [p for p in ("coros", "garmin") if ready(p)]
        ids = (await db.execute(select(OfficialConnection.id).where(
            OfficialConnection.provider.in_(providers), OfficialConnection.status == base.ACTIVE)
            .order_by(OfficialConnection.id))).scalars().all()
        for conn_id in ids:
            conn = await db.get(OfficialConnection, conn_id)
            if conn is None:
                continue
            try:
                if await base.refresh_due(db, conn, REFRESH_WITHIN):
                    refreshed += 1
            except Exception:
                logger.warning("Official tokens of connection %d not refreshed", conn_id, exc_info=True)
                await db.rollback()
                failed += 1
        due_before = datetime.now(timezone.utc) - coros_partner.SYNC_EVERY + timedelta(minutes=10)
        if "coros" in providers:
            coros_ids = (await db.execute(select(OfficialConnection.id).where(
                OfficialConnection.provider == "coros", OfficialConnection.status == base.ACTIVE,
                or_(OfficialConnection.last_sync_at.is_(None), OfficialConnection.last_sync_at < due_before))
                .order_by(OfficialConnection.last_sync_at.asc().nulls_first()))).scalars().all()
            for conn_id in coros_ids:
                conn = await db.get(OfficialConnection, conn_id)
                if conn is None:
                    continue
                try:
                    outcome = await coros_partner.run_sync(db, conn)
                except Exception:
                    logger.exception("COROS official pull crashed for connection %d", conn_id)
                    await db.rollback()
                    outcome = {"ok": False}
                if outcome and outcome.get("ok"):
                    pulled += 1
                elif outcome:
                    failed += 1
                await asyncio.sleep(1)
    logger.info("Official connectors round: %d pulled, %d refreshed, %d failed", pulled, refreshed, failed)
    return {"pulled": pulled, "refreshed": refreshed, "failed": failed}
