"""One weight for every screen and every calculation: the most recent dated value wins."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.health import HealthMetric
from app.models.mobile import MobileDaily
from app.services.athlete_weight import athlete_weight

NOW = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)


def _phone(user_id, kg, at, platform="ios"):
    return MobileDaily(user_id=user_id, platform=platform, date=at.date(), metric="weight", value=kg,
                       sources=["SenssunHealth"], measured_at=at)


@pytest.mark.asyncio
async def test_a_recent_weigh_in_beats_an_older_typed_value(db_session, test_user):
    test_user.weight_kg, test_user.weight_set_at = 72.0, NOW - timedelta(days=40)
    db_session.add(_phone(test_user.id, 70.4, NOW - timedelta(days=1)))
    await db_session.flush()
    w = await athlete_weight(db_session, test_user, NOW)
    assert (w.kg, w.source, w.stale) == (70.4, "Apple Santé", False)


@pytest.mark.asyncio
async def test_a_value_typed_after_the_last_weigh_in_wins(db_session, test_user):
    db_session.add(_phone(test_user.id, 80.0, NOW - timedelta(days=60)))
    test_user.weight_kg, test_user.weight_set_at = 72.0, NOW - timedelta(days=2)
    await db_session.flush()
    w = await athlete_weight(db_session, test_user, NOW)
    assert (w.kg, w.source, w.stale) == (72.0, "Saisie manuelle", False)


@pytest.mark.asyncio
async def test_an_undated_typed_value_counts_only_without_a_weigh_in(db_session, test_user):
    test_user.weight_kg, test_user.weight_set_at = 72.0, None
    await db_session.flush()
    w = await athlete_weight(db_session, test_user, NOW)
    # its age is unknown, not old: never « Donnée ancienne » (review, 2026-10-14: every value typed before
    # weight_set_at existed read so)
    assert (w.kg, w.source, w.at, w.stale) == (72.0, "Saisie manuelle", None, False)
    db_session.add(_phone(test_user.id, 80.0, NOW - timedelta(days=60), platform="android"))
    await db_session.flush()
    w = await athlete_weight(db_session, test_user, NOW)
    assert (w.kg, w.source, w.stale) == (80.0, "Health Connect", True)  # old, so flagged, never averaged


@pytest.mark.asyncio
async def test_a_dated_watch_weigh_in_competes_with_the_phone(db_session, test_user):
    db_session.add(_phone(test_user.id, 70.0, NOW - timedelta(days=3)))
    db_session.add(HealthMetric(user_id=test_user.id, date=date(2026, 10, 13), metric="weight", value=69.6,
                                source="Garmin", details={"measured_at": (NOW - timedelta(days=1)).isoformat()}))
    await db_session.flush()
    w = await athlete_weight(db_session, test_user, NOW)
    assert (w.kg, w.source) == (69.6, "Garmin")


@pytest.mark.asyncio
async def test_a_dated_value_is_stale_after_30_days(db_session, test_user):
    test_user.weight_kg, test_user.weight_set_at = 72.0, NOW - timedelta(days=31)
    await db_session.flush()
    assert (await athlete_weight(db_session, test_user, NOW)).stale
    test_user.weight_set_at = NOW - timedelta(days=29)
    await db_session.flush()
    assert not (await athlete_weight(db_session, test_user, NOW)).stale


@pytest.mark.asyncio
async def test_no_weight_at_all(db_session, test_user):
    test_user.weight_kg = None
    await db_session.flush()
    assert await athlete_weight(db_session, test_user, NOW) is None
