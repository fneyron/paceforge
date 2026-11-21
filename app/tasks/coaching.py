"""Coaching's daily round (beat: paceforge.coaching_daily, scheduled unless COACHING_BETA_USERS switches Coaching
off: app.features.coaching_beat_schedule).

Every plan whose owner has Coaching (every account by default) is brought up to date as its page would (services/coaching.sync): yesterday's
sessions matched with the runs or marked missed, the adaptation read again, the weeks to come regenerated and the
rolling block extended. So the calendar feed and a coach's page are right even when the athlete has not opened hers.
"""
import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="paceforge.coaching_daily", ignore_result=True)
def coaching_daily() -> int:
    return asyncio.run(_run())


async def _run() -> int:
    from app import features
    from app.database import get_task_session

    if not features.coaching_enabled():
        return 0
    async with get_task_session() as db:
        return await run(db)


async def run(db) -> int:
    """The plans brought up to date (one commit each: a plan that fails never stops the others); how many."""
    from sqlalchemy import select

    from app import features
    from app.models.coaching import CoachingPlan
    from app.models.user import User
    from app.services import coaching

    done = 0
    for plan_id, user_id in (await db.execute(select(CoachingPlan.id, CoachingPlan.user_id)
                                              .order_by(CoachingPlan.id))).all():
        try:
            user = await db.get(User, user_id)
            plan = await db.get(CoachingPlan, plan_id)
            if user is None or plan is None or not features.coaching_for(user):
                continue
            await coaching.sync(db, plan, user)
            await db.commit()
            done += 1
        except Exception:
            await db.rollback()
            logger.exception("Coaching: the daily round failed for user %d", user_id)
    return done
