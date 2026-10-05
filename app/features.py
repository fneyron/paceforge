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
