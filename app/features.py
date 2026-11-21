"""Feature switches, always read at request time so tests can flip them."""

from fastapi import HTTPException

from app.config import settings

# Route sports that only exist while cycling is on (a triathlon needs a bike leg).
CYCLING_SPORTS = ("bike", "triathlon")


def cycling_enabled() -> bool:
    return bool(settings.CYCLING_ENABLED)


def hidden_sports() -> tuple[str, ...]:
    """Route sport types to keep out of sight right now."""
    return () if cycling_enabled() else CYCLING_SPORTS


def sport_hidden(sport_type: str | None) -> bool:
    return sport_type in hidden_sports()


def require_cycling() -> None:
    """Route dependency: bike and triathlon endpoints 404 while cycling is off."""
    if not cycling_enabled():
        raise HTTPException(status_code=404)


# ── Coaching (beta, docs/coaching.md) ───────────────────────────────────────

def coaching_beta_emails() -> frozenset[str]:
    """The accounts that have the Coaching beta (COACHING_BETA_USERS: emails, comma-separated, case-insensitive)."""
    return frozenset(e for e in (x.strip().lower() for x in settings.COACHING_BETA_USERS.split(",")) if e)


def coaching_enabled() -> bool:
    """Whether the feature exists at all: with an empty list, no route, menu entry, card, privacy line nor task."""
    return bool(coaching_beta_emails())


def coaching_for(user) -> bool:
    """Whether this account has the beta (its email is in the list)."""
    email = (getattr(user, "email", None) or "").strip().lower()
    return bool(email) and email in coaching_beta_emails()


def coaching_beat_schedule() -> dict:
    """What celery beat runs for Coaching: nothing while the list is empty (read once, when the worker starts, as
    any setting). Daily at 03:27 UTC, off the other rounds' minutes: yesterday's sessions matched or marked missed,
    the adaptation re-read, the plan extended (app.tasks.coaching)."""
    if not coaching_enabled():
        return {}
    from celery.schedules import crontab

    return {"coaching-daily": {"task": "paceforge.coaching_daily", "schedule": crontab(hour=3, minute=27)}}
