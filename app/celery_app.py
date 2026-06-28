from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "paceforge",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.initial_sync", "app.tasks.poll_activities", "app.tasks.webhook_sync"],
    # disabled: "app.tasks.analysis", "app.tasks.weekly_digest"
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
        "poll-new-activities": {
            "task": "paceforge.poll_new_activities",
            "schedule": crontab(minute="*/5"),  # Every 5 minutes
        },
        # disabled: weekly-digest
    },
)
