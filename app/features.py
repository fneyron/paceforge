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
# Open to every account since 21 November 2026 (owner: « Non rajoute à tout le monde »). One switch remains,
# COACHING_BETA_USERS: « * » (the default) every account; emails, only those; anything else (empty, « off ») nobody.

COACHING_EVERYONE = "*"


def _coaching_words() -> list[str]:
    return [x.strip().lower() for x in (settings.COACHING_BETA_USERS or "").split(",")]


def coaching_for_everyone() -> bool:
    """Whether every account has Coaching (COACHING_BETA_USERS holds « * », its default)."""
    return COACHING_EVERYONE in _coaching_words()


def coaching_beta_emails() -> frozenset[str]:
    """The accounts Coaching is restricted to when the switch lists emails (comma-separated, case-insensitive). A
    word that is not an email (« off ») names nobody."""
    return frozenset(w for w in _coaching_words() if "@" in w)


def coaching_enabled() -> bool:
    """Whether the feature exists at all: switched off, no route, menu entry, card, privacy line nor task."""
    return coaching_for_everyone() or bool(coaching_beta_emails())


def coaching_for(user) -> bool:
    """Whether this account has Coaching: any signed-in account by default, the listed emails when restricted."""
    if user is None:
        return False
    if coaching_for_everyone():
        return True
    email = (getattr(user, "email", None) or "").strip().lower()
    return bool(email) and email in coaching_beta_emails()


def coaching_beat_schedule() -> dict:
    """What celery beat runs for Coaching: nothing while it is switched off (read once, when the worker starts, as
    any setting). Daily at 03:27 UTC, off the other rounds' minutes: yesterday's sessions matched or marked missed,
    the adaptation re-read, the plan extended (app.tasks.coaching). It walks the plans that exist: an account that
    never made one costs nothing."""
    if not coaching_enabled():
        return {}
    from celery.schedules import crontab

    return {"coaching-daily": {"task": "paceforge.coaching_daily", "schedule": crontab(hour=3, minute=27)}}
