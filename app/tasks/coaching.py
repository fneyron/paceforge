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


@celery_app.task(name="paceforge.coaching_morning", ignore_result=True)
def coaching_morning() -> int:
    return asyncio.run(_run(morning=True))


async def _run(morning: bool = False) -> int:
    from app import features
    from app.database import get_task_session

    if not features.coaching_enabled():
        return 0
    async with get_task_session() as db:
        return await (run_morning(db) if morning else run(db))


MORNING = (5, 12)  # choix pratique: the athlete's morning, from 5:00 to noon on their clock


async def run_morning(db, now=None) -> int:
    """The form's round (hourly, beat: paceforge.coaching_morning): the plans whose athlete turned « Adapter mes
    séances à ma forme » on, in their own morning, until this morning's index has been read — the night reaches
    Santé once the watch syncs, so the plan is read again each hour until it has. Nothing else is touched: a plan
    without the option is never read here, and nobody's health data is read for it. How many plans were read."""
    from sqlalchemy import select

    from app import features
    from app.models.coaching import CoachingPlan
    from app.models.user import User
    from app.services import coaching

    done = 0
    for plan_id, user_id in (await db.execute(select(CoachingPlan.id, CoachingPlan.user_id).where(
            CoachingPlan.form_on_at.is_not(None)).order_by(CoachingPlan.id))).all():
        try:
            user = await db.get(User, user_id)
            plan = await db.get(CoachingPlan, plan_id)
            if user is None or plan is None or not features.coaching_for(user):
                continue
            c = await coaching.clock(db, user, now)
            hour = c.now.astimezone(c.zone).hour
            state = plan.form_state or {}
            if not MORNING[0] <= hour < MORNING[1] or (state.get("day") == c.today.isoformat() and state.get("seen")):
                continue
            await coaching.sync(db, plan, user, now)
            await db.commit()
            done += 1
        except Exception:
            await db.rollback()
            logger.exception("Coaching: the morning round failed for user %d", user_id)
    return done


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


# ── « Mon agenda »: the athletes' calendars, read here and nowhere else ─────

@celery_app.task(name="paceforge.coaching_agenda", ignore_result=True)
def coaching_agenda(user_id: int) -> int:
    """One athlete's calendars read (an address just saved, « Actualiser »)."""
    return asyncio.run(_agenda(user_id))


@celery_app.task(name="paceforge.coaching_agenda_round", ignore_result=True)
def coaching_agenda_round() -> int:
    """Every 3 hours: the calendars of every athlete with a plan and an address."""
    return asyncio.run(_agenda(None))


async def _agenda(user_id: int | None) -> int:
    from app import features
    from app.database import get_task_session

    if not features.coaching_enabled():
        return 0
    async with get_task_session() as db:
        return await (read_agenda(db, user_id) if user_id is not None else run_agenda(db))


async def read_agenda(db, user_id: int, now=None) -> int:
    """The athlete's calendar addresses fetched (services/safe_fetch: the one place a user-typed address is
    requested) and read for their busy time (coaching_ical), then the plan read again: each session finds its time.
    How many calendars were read well. Nothing without a plan or without Coaching."""
    from app import features
    from app.models.user import User
    from app.services import coaching, coaching_agenda

    user = await db.get(User, user_id)
    plan = await coaching.get_plan(db, user_id) if user is not None else None
    if user is None or plan is None or not features.coaching_for(user):
        return 0
    c = await coaching.clock(db, user, now)
    statuses = await coaching_agenda.read_all(db, user_id, c.zone, c.now)
    if statuses:
        await coaching.sync(db, plan, user, now)
    await db.commit()
    return sum(1 for s in statuses.values() if s == coaching_agenda.OK)


async def run_agenda(db, now=None) -> int:
    """The 3-hourly round: one commit per athlete, a failure never stops the others; how many athletes."""
    from sqlalchemy import select

    from app.models.coaching import CoachingCalendar, CoachingPlan

    done = 0
    ids = (await db.execute(select(CoachingCalendar.user_id).join(
        CoachingPlan, CoachingPlan.user_id == CoachingCalendar.user_id).distinct().order_by(
        CoachingCalendar.user_id))).scalars().all()
    for user_id in ids:
        try:
            await read_agenda(db, user_id, now)
            done += 1
        except Exception:
            await db.rollback()
            logger.exception("Coaching: the calendars' round failed for user %d", user_id)
    return done
