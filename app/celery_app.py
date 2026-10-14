import logging

from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue

from app.config import settings

logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs can carry tokens

celery_app = Celery(
    "paceforge",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.initial_sync", "app.tasks.poll_activities", "app.tasks.webhook_sync",
             "app.tasks.coros_sync", "app.tasks.garmin_sync"],
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
            "schedule": crontab(minute=7),  # hourly: the webhook's fallback (tasks/poll_activities)
        },
        "sync-coros": {
            "task": "paceforge.sync_coros",
            "schedule": crontab(minute=17),  # hourly; each link is synced every 2 h
        },
        "sync-garmin": {
            "task": "paceforge.sync_garmin",
            "schedule": crontab(minute=47),  # hourly; each link is synced every 2 h
        },
        # disabled: weekly-digest
    },
)

# ── Strava rounds and long syncs ────────────────────────────────────────────
# Beat runs inside the worker (`worker --beat`, docker-compose.prod.yml): that worker must never run as two
# replicas, or every beat entry fires twice (each athlete's poll queued twice, two Garmin or COROS rounds at once).
# More workers can be added without --beat; beat stays in exactly one process.
#
# A task taken but not yet acknowledged (acks_late) is handed out again after the broker's visibility timeout: it
# must outlast the longest task (a first Strava import, a Garmin or COROS round), else a slow one runs twice at once.
# A countdown longer than it would be delivered twice too: the waits after a failure are the round's, not ETAs.
celery_app.conf.broker_transport_options = {"visibility_timeout": 6 * 3600}
# The long syncs have a queue of their own. A worker started without -Q consumes both (as deployed); a worker
# `-Q celery` beside one `-Q long` keeps the hourly round, the polls and the webhook events moving during them.
celery_app.conf.task_queues = tuple(  # each its own binding (two on one key would both get every message)
    Queue(name, Exchange(name, type="direct"), routing_key=name) for name in ("celery", "long"))
celery_app.conf.task_default_queue = "celery"
celery_app.conf.task_routes = {name: {"queue": "long"} for name in (
    "paceforge.initial_sync", "paceforge.sync_coros", "paceforge.sync_garmin")}

# Expired Strava link attempts are purged every hour (app.tasks.oauth_cleanup)
celery_app.conf.include = [*celery_app.conf.include, "app.tasks.oauth_cleanup"]
celery_app.conf.beat_schedule["purge-oauth-attempts"] = {
    "task": "paceforge.purge_oauth_attempts",
    "schedule": crontab(minute=37),
}
