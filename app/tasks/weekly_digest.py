import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from celery import Task
from sqlalchemy import func, select

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
    name="paceforge.generate_weekly_digests",
)
def generate_weekly_digests(self: Task) -> dict:
    """Generate weekly digests for all users with activities last week."""
    logger.info("Starting weekly digest generation")
    try:
        result = asyncio.run(_generate_all_digests())
        logger.info("Weekly digest generation complete: %s", result)
        return result
    except Exception as exc:
        logger.exception("Failed to generate weekly digests")
        raise self.retry(exc=exc, countdown=300 * (2 ** self.request.retries))


async def _generate_all_digests() -> dict:
    from app.database import get_task_session
    from app.models.activity import Activity
    from app.models.user import User
    from app.models.weekly_digest import WeeklyDigest
    from app.services.claude import ClaudeService

    now = datetime.now(timezone.utc)
    # Last week: Monday to Sunday
    today = now.date()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    week_start_dt = datetime.combine(last_monday, datetime.min.time(), tzinfo=timezone.utc)
    week_end_dt = datetime.combine(last_sunday, datetime.max.time(), tzinfo=timezone.utc)

    generated = 0
    skipped = 0
    errors = 0

    async with get_task_session() as db:
        # Get all users
        users_result = await db.execute(select(User))
        users = users_result.scalars().all()

        claude = ClaudeService()

        for user in users:
            try:
                # Check if digest already exists for this week
                existing = await db.execute(
                    select(WeeklyDigest).where(
                        WeeklyDigest.user_id == user.id,
                        WeeklyDigest.week_start == last_monday,
                    )
                )
                if existing.scalar_one_or_none():
                    skipped += 1
                    continue

                # Get activities for the week
                acts_result = await db.execute(
                    select(Activity)
                    .where(
                        Activity.user_id == user.id,
                        Activity.start_date >= week_start_dt,
                        Activity.start_date <= week_end_dt,
                    )
                    .order_by(Activity.start_date)
                )
                week_activities = acts_result.scalars().all()

                if not week_activities:
                    skipped += 1
                    continue

                # Prepare activity data
                activities_data = [
                    {
                        "sport_type": a.sport_type,
                        "name": a.name,
                        "distance": a.distance,
                        "moving_time": a.moving_time,
                        "start_date": a.start_date,
                        "average_speed": a.average_speed,
                        "average_heartrate": a.average_heartrate,
                        "total_elevation_gain": a.total_elevation_gain,
                    }
                    for a in week_activities
                ]

                # This week's load
                total_dist = sum(a.distance for a in week_activities)
                total_time = sum(a.moving_time for a in week_activities)
                this_week_load = {
                    "distance_km": round(total_dist / 1000, 1),
                    "duration_hours": round(total_time / 3600, 1),
                    "count": len(week_activities),
                }

                # Previous week's load
                prev_monday = last_monday - timedelta(weeks=1)
                prev_sunday = prev_monday + timedelta(days=6)
                prev_start = datetime.combine(prev_monday, datetime.min.time(), tzinfo=timezone.utc)
                prev_end = datetime.combine(prev_sunday, datetime.max.time(), tzinfo=timezone.utc)
                prev_result = await db.execute(
                    select(
                        func.coalesce(func.sum(Activity.distance), 0),
                        func.coalesce(func.sum(Activity.moving_time), 0),
                        func.count(Activity.id),
                    ).where(
                        Activity.user_id == user.id,
                        Activity.start_date >= prev_start,
                        Activity.start_date <= prev_end,
                    )
                )
                prev_row = prev_result.one()
                prev_week_load = {
                    "distance_km": round(prev_row[0] / 1000, 1),
                    "duration_hours": round(prev_row[1] / 3600, 1),
                    "count": prev_row[2],
                }

                # Average 4 weeks load
                four_weeks_ago = datetime.combine(
                    last_monday - timedelta(weeks=4),
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                )
                avg_result = await db.execute(
                    select(
                        func.coalesce(func.sum(Activity.distance), 0),
                        func.coalesce(func.sum(Activity.moving_time), 0),
                        func.count(Activity.id),
                    ).where(
                        Activity.user_id == user.id,
                        Activity.start_date >= four_weeks_ago,
                        Activity.start_date < week_start_dt,
                    )
                )
                avg_row = avg_result.one()
                avg_4w_load = {
                    "distance_km": round(avg_row[0] / 1000 / 4, 1),
                    "duration_hours": round(avg_row[1] / 3600 / 4, 1),
                    "count": round(avg_row[2] / 4, 1),
                }

                # Race goal
                race_goal = None
                if user.race_name and user.race_date and user.race_date > now:
                    race_goal = {
                        "name": user.race_name,
                        "date": user.race_date.strftime("%d/%m/%Y"),
                        "distance_km": user.race_distance_km,
                        "days_remaining": (user.race_date - now).days,
                    }

                # Generate digest with Claude
                digest_output, raw_response = await claude.generate_weekly_digest(
                    week_activities=activities_data,
                    this_week_load=this_week_load,
                    prev_week_load=prev_week_load,
                    avg_4w_load=avg_4w_load,
                    race_goal=race_goal,
                )

                from app.config import settings

                digest = WeeklyDigest(
                    user_id=user.id,
                    week_start=last_monday,
                    week_end=last_sunday,
                    summary=digest_output.summary,
                    highlights=digest_output.highlights,
                    recommendations=digest_output.recommendations,
                    volume_assessment=digest_output.volume_assessment,
                    training_load_summary={
                        "this_week": this_week_load,
                        "prev_week": prev_week_load,
                        "avg_4w": avg_4w_load,
                    },
                    model_used=settings.CLAUDE_MODEL,
                    raw_response=raw_response,
                )
                db.add(digest)
                await db.flush()
                generated += 1
                logger.info("Generated digest for user %d (week %s)", user.id, last_monday)

            except Exception:
                errors += 1
                logger.exception("Failed to generate digest for user %d", user.id)

        await db.commit()

    return {"generated": generated, "skipped": skipped, "errors": errors}
