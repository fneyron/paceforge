from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "paceforge",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.analysis", "app.tasks.weekly_digest", "app.tasks.initial_sync"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "weekly-digest": {
            "task": "paceforge.generate_weekly_digests",
            "schedule": crontab(hour=7, minute=0, day_of_week=1),  # Monday 07:00 UTC
        },
    },
)
