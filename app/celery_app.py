import logging

from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue

from app.config import settings
from app.sentry import init_sentry

logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs can carry tokens

init_sentry()  # once per process; nothing while SENTRY_DSN is empty

celery_app = Celery(
    "paceforge",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.initial_sync", "app.tasks.poll_activities", "app.tasks.webhook_sync",
             "app.tasks.coros_sync", "app.tasks.garmin_sync"],
    # disabled: "app.tasks.analysis", "app.tasks.weekly_digest"
)

# ── Time limits ─────────────────────────────────────────────────────────────
# The longest single runs, measured from the code: a first Strava import stops at the app's share of its quota
# and returns (services.strava._note_quota), some 250 requests at most, minutes; one Garmin link, 160 calls
# within a 10-min budget (services.garmin); one COROS link, 45 calls of 30 s at most, 23 min at worst
# (services.coros); the hourly rounds sync every link due, one after the other. The hard limit bounds a round
# to about five Garmin links or three slow COROS ones: what it did not reach leads the next round (oldest
# first), nothing runs twice. The soft limit raises SoftTimeLimitExceeded in the task first: the rounds log it
# as a failed round, a first import counts it as a failed attempt (retried after its countdown).
TASK_SOFT_TIME_LIMIT_S = 55 * 60
TASK_TIME_LIMIT_S = 60 * 60

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    # nothing reads the results: none is written to Redis (the few a caller asks for expire after an hour)
    task_ignore_result=True,
    result_expires=3600,
    task_soft_time_limit=TASK_SOFT_TIME_LIMIT_S,
    task_time_limit=TASK_TIME_LIMIT_S,
    # a child past this (KiB) is replaced between two tasks: one the kernel kills instead loses its task to an
    # immediate retry (task_reject_on_worker_lost), a first import that cannot fit would loop
    worker_max_memory_per_child=700 * 1024,
    beat_schedule={
        "poll-new-activities": {
            "task": "paceforge.poll_new_activities",
            "schedule": crontab(minute="7-59/10"),  # every 10 min: the webhook's fallback (tasks/poll_activities)
        },
        # the hourly rounds, off the poll's minutes (:07, :17… :57) and half an hour apart; each link is synced
        # every 2 h
        "sync-coros": {
            "task": "paceforge.sync_coros",
            "schedule": crontab(minute=12),
        },
        "sync-garmin": {
            "task": "paceforge.sync_garmin",
            "schedule": crontab(minute=42),
        },
        # disabled: weekly-digest
    },
)

# ── Strava rounds and long syncs ────────────────────────────────────────────
# Beat runs inside the worker (`worker --beat`, docker-compose.prod.yml): that worker must never run as two
# replicas, or every beat entry fires twice (each athlete's poll queued twice, two Garmin or COROS rounds at once).
# More workers can be added without --beat; beat stays in exactly one process.
#
# A task taken but not yet acknowledged (acks_late) is handed out again after the broker's visibility timeout.
# Every task that ends — done, failed, or killed by the hard limit, acknowledged all the same
# (acks_on_failure_or_timeout) — is acknowledged within TASK_TIME_LIMIT_S of being taken, and a worker takes
# at most one task per process (prefetch 1), so none waits taken. Twice the limit is the smallest value no
# running task can reach — a slow one would otherwise run twice at once. Only a task lost with its worker (a
# crash, the kernel's OOM kill, a deploy's SIGKILL after the 12 min of grace) comes back: within 2 h, not 6.
# A countdown longer than it would be delivered twice too: the longest retry wait is the webhook's 15 min; the
# waits after a failed first import are the round's, not ETAs.
celery_app.conf.broker_transport_options = {"visibility_timeout": 2 * TASK_TIME_LIMIT_S}
# The long syncs have a queue of their own: the first imports, the rounds, and one link's own sync (a press of
# « Synchroniser maintenant », 10 to 23 min). A worker started without -Q consumes both; as deployed, a worker
# `-Q celery` beside the `-Q long` one keeps the polls and the webhook events moving during them.
celery_app.conf.task_queues = tuple(  # each its own binding (two on one key would both get every message)
    Queue(name, Exchange(name, type="direct"), routing_key=name) for name in ("celery", "long"))
celery_app.conf.task_default_queue = "celery"
celery_app.conf.task_routes = {name: {"queue": "long"} for name in (
    "paceforge.initial_sync", "paceforge.sync_coros", "paceforge.sync_garmin",
    "paceforge.sync_coros_user", "paceforge.sync_garmin_user")}

# Expired Strava link attempts are purged every hour (app.tasks.oauth_cleanup)
celery_app.conf.include = [*celery_app.conf.include, "app.tasks.oauth_cleanup"]
celery_app.conf.beat_schedule["purge-oauth-attempts"] = {
    "task": "paceforge.purge_oauth_attempts",
    "schedule": crontab(minute=37),
}
