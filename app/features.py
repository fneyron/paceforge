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
# COACHING_BETA_USERS: emails, only those accounts; an « off » word (COACHING_OFF) and no email, nobody; anything
# else every account — « * » (the default), and an empty value too: a deployment tool that keeps the variable empty
# (Coolify stored the empty default of the closed beta) must not switch the feature off in silence.

COACHING_EVERYONE = "*"
COACHING_OFF = frozenset({"off", "0", "false", "non", "no", "none"})


def _coaching_words() -> list[str]:
    words = (x.strip().strip("'\"").lower() for x in (settings.COACHING_BETA_USERS or "").split(","))
    return [w for w in words if w]


def coaching_beta_emails() -> frozenset[str]:
    """The accounts Coaching is restricted to when the switch lists emails (comma-separated, case-insensitive). A
    word that is not an email (« off ») names nobody."""
    return frozenset(w for w in _coaching_words() if "@" in w)


def coaching_for_everyone() -> bool:
    """Whether every account has Coaching: « * » anywhere in the switch, or no email and no « off » word in it (the
    default, an empty value, anything unreadable)."""
    words = _coaching_words()
    return COACHING_EVERYONE in words or not (coaching_beta_emails() or COACHING_OFF.intersection(words))


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

    # and, every hour at :12, the form's round: the plans whose athlete turned « Adapter mes séances à ma forme » on,
    # in their own morning, until this morning's index is read (none has the option: one small query, nothing more)
    # and, every 3 hours at :47, the calendars of the athletes who gave an address in « Mon agenda » are read again
    return {"coaching-daily": {"task": "paceforge.coaching_daily", "schedule": crontab(hour=3, minute=27)},
            "coaching-morning": {"task": "paceforge.coaching_morning", "schedule": crontab(minute=12)},
            "coaching-agenda": {"task": "paceforge.coaching_agenda_round",
                                "schedule": crontab(minute=47, hour="*/3")}}
