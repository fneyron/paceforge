"""The athlete's weight: one rule for every screen and every calculation.

The most recent DATED value wins: a weigh-in from the phone (Apple Santé, Health
Connect: MobileDaily.measured_at) or from a watch import (HealthMetric « weight »
with details.measured_at), or the value typed in Réglages (users.weight_kg, dated by
users.weight_set_at). A typed value with no date (typed before the date was kept)
counts only when no weigh-in exists. Values are never averaged. A reading older than
STALE_DAYS is still returned, flagged `stale`, so a screen can say how old it is; an
undated typed value is never flagged (its age is unknown, not old: every value typed
before weight_set_at existed is one).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.mobile import MobileDaily

STALE_DAYS = 30
RANGE = (25.0, 300.0)


@dataclass(frozen=True)
class WeightReading:
    kg: float
    at: datetime | None  # when it was measured or typed (UTC); None for an undated typed value
    source: str  # « Apple Santé », « Health Connect », a watch name, or « Saisie manuelle »
    stale: bool


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _plausible(kg) -> bool:
    try:
        return RANGE[0] <= float(kg) <= RANGE[1]
    except (TypeError, ValueError):
        return False


async def latest_weigh_in(db: AsyncSession, user_id: int) -> tuple[float, datetime, str] | None:
    """The most recent dated weigh-in from any source, or None."""
    best: tuple[float, datetime, str] | None = None
    phone = (await db.execute(
        select(MobileDaily).where(MobileDaily.user_id == user_id, MobileDaily.metric == "weight",
                                  MobileDaily.measured_at.is_not(None))
        .order_by(MobileDaily.measured_at.desc()).limit(1)
    )).scalar_one_or_none()
    if phone is not None and _plausible(phone.value):
        best = (float(phone.value), _utc(phone.measured_at), "Apple Santé" if phone.platform == "ios" else "Health Connect")
    rows = (await db.execute(
        select(HealthMetric).where(HealthMetric.user_id == user_id, HealthMetric.metric == "weight")
        .order_by(HealthMetric.date.desc()).limit(60)
    )).scalars().all()
    for row in rows:
        try:
            stamp = datetime.fromisoformat((row.details or {}).get("measured_at", ""))
        except (TypeError, ValueError):
            continue
        if stamp.tzinfo is None or not _plausible(row.value):
            continue
        stamp = stamp.astimezone(timezone.utc)
        if best is None or stamp > best[1]:
            best = (float(row.value), stamp, row.source or "Montre")
    return best


async def athlete_weight(db: AsyncSession, user, now: datetime | None = None) -> WeightReading | None:
    now = _utc(now) or datetime.now(timezone.utc)
    weigh_in = await latest_weigh_in(db, user.id)
    typed = float(user.weight_kg) if _plausible(getattr(user, "weight_kg", None)) else None
    typed_at = _utc(getattr(user, "weight_set_at", None))
    chosen: tuple[float, datetime | None, str] | None = None
    if weigh_in and (typed is None or typed_at is None or weigh_in[1] >= typed_at):
        chosen = weigh_in
    elif typed is not None:
        chosen = (typed, typed_at, "Saisie manuelle")
    if chosen is None:
        return None
    kg, at, source = chosen
    stale = at is not None and now - at > timedelta(days=STALE_DAYS)
    return WeightReading(kg=round(kg, 1), at=at, source=source, stale=stale)
