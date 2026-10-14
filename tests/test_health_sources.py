"""Regressions: import order, source isolation, missing nights and migration safety."""

from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

from app.models.health import HealthMetric, HealthSample
from app.services import nights, sante
from app.services.health import HRV_METHOD, Daily, Sample, store_daily, store_samples
from app.services.health_sources import select_metrics
from app.services.sante_daily import daily_context
from tests import test_coros
from tests.test_sante_score import D, _day, _history, _runs, night_rows
from tests.test_sleep_need_migrations import _load, _run

as_user, no_commit = test_coros.as_user, test_coros.no_commit


@pytest.mark.parametrize("gap", [1, 2, 3])
def test_historical_heart_means_cannot_create_a_new_recovery_score(gap):
    rows = night_rows(range(gap, 61), asleep=480)
    day = _day(rows, _runs(), base=None)
    assert all(s["value"] is not None and s["normal"] for s in day["stats"].values())
    assert day["tst24"] is None
    assert day["score"]["value"] is None and not day["score"]["measured"] and day["state"] is None
    history = {d: score for d, _, score in _history(rows, _runs(), base=None)}
    assert history[D - timedelta(days=gap)]["value"] is not None
    assert all(s["value"] is None for d, s in history.items() if d > D - timedelta(days=gap))


def test_previous_cycle_is_kept_before_noon_then_missing_night_is_empty():
    nd = nights.build_nights(night_rows(range(1, 40)), D)
    assert sante.cycle_day(
        nd, D, datetime.combine(D, datetime.min.time()).replace(hour=9)
    ) == D - timedelta(days=1)
    assert sante.cycle_day(nd, D, datetime.combine(D, datetime.min.time()).replace(hour=12)) == D


def test_a_measured_zero_sleep_is_distinct_from_no_night():
    rows = night_rows(range(0, 40), asleep=480)
    _, detail, source = rows["sleep"][D]
    rows["sleep"][D] = (0, detail, source)
    day = _day(rows, base=None)
    assert day["tst24"] == 0 and day["score"]["measured"] and day["score"]["value"] is not None


def test_a_new_watch_without_a_baseline_cannot_recycle_old_other_signals():
    """One COROS night after 60 Garmin ones: never read against Garmin's baseline; the series stays the watch worn
    most (owner, 2026-10-14: scientific basis — it switched at once and had no baseline), and as last night is not
    on it, no state."""
    rows = night_rows(range(1, 61), asleep=480)
    _, details, _ = rows["hrv"][D - timedelta(days=1)]
    rows["hrv"][D] = (80, details, "COROS")
    day = _day(rows, base=None)
    assert day["stats"]["hr"]["value"] is not None  # old FC trend remains readable
    assert day["trends"]["hrv"]["source"] == "Garmin" and not day["trends"]["hrv"]["last"]
    assert day["score"]["value"] is None


@pytest.mark.asyncio
async def test_preferred_watch_can_be_saved_cleared_and_is_validated(as_user, test_user):
    response = await as_user.post("/settings", data={"health_source": "Garmin"})
    assert response.status_code == 200 and test_user.health_source == "Garmin"
    assert (await as_user.post("/settings", data={"health_source": "unsupported"})).status_code == 422
    assert test_user.health_source == "Garmin"
    response = await as_user.post("/settings", data={"health_source": "auto"})
    assert response.status_code == 200 and test_user.health_source is None


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [("COROS", "Garmin"), ("Garmin", "COROS")])
async def test_sync_preserves_both_sources_and_respects_user_choice(db_session, test_user, order):
    uid = test_user.id
    for source in order:
        value = 42 if source == "COROS" else 50
        await store_daily(db_session, uid, [Daily("hr_night", D, value)], source)
    await store_daily(db_session, uid, [Daily("hr_night", D, 43)], "COROS")
    rows = (
        await db_session.scalars(sa.select(HealthMetric).where(HealthMetric.user_id == uid))
    ).all()
    assert {(r.source, r.value) for r in rows} == {("COROS", 43), ("Garmin", 50)}
    assert (await nights.read_rows(db_session, uid, D, D))["hr_night"][D][0] == 43
    test_user.health_source = "Garmin"
    assert (await nights.read_rows(db_session, uid, D, D))["hr_night"][D][0] == 50
    # A correction removing COROS's row cannot delete Garmin's measurement.
    await store_daily(db_session, uid, [], "COROS", covered={"hr_night": {D}})
    assert (await nights.read_rows(db_session, uid, D, D))["hr_night"][D][0] == 50


def test_sleep_chooses_one_watch_and_does_not_add_the_others_naps():
    def row(metric, source, value):
        return HealthMetric(date=D, metric=metric, value=value, source=source)

    rows = [
        row("sleep", "Garmin", 400),
        row("hr_night", "COROS", 42),
        row("hr_night", "Garmin", 50),
        row("nap", "COROS", 100),
        row("steps", "COROS", 2000),
        row("steps", "Garmin", 8000),
    ]
    chosen = select_metrics(rows)
    assert chosen[D, "hr_night"].source == "Garmin"
    assert (D, "nap") not in chosen
    assert chosen[D, "steps"].value == 2000
    assert select_metrics(reversed(rows)) == chosen


def test_two_apple_writers_rank_by_the_nights_they_hold_not_by_their_name():
    """Two Apple devices writing the same nights (owner, 2026-10-14: scientific basis — they were ranked by their
    hash, so a device's name decided the night and the series): the one with the most nights of the 28 days up to
    each day, never a later day's; the name only breaks a tie."""
    def row(source, day, value):
        return HealthMetric(date=day, metric="sleep", value=value, source=source)

    worn = [row("Apple Santé:zzz", D - timedelta(days=k), 420) for k in range(20)]
    other = [row("Apple Santé:aaa", D - timedelta(days=k), 430) for k in (0, 5)]
    chosen = select_metrics(worn + other)
    assert chosen[D, "sleep"].source == "Apple Santé:zzz"  # by name, aaa would win
    first = D - timedelta(days=19)  # zzz's first night: the 28 days before it hold none of aaa's either
    tie = select_metrics([row("Apple Santé:zzz", first, 420), row("Apple Santé:aaa", first, 430)])
    assert tie[first, "sleep"].source == "Apple Santé:aaa"  # one night each: the name


@pytest.mark.asyncio
async def test_legacy_hrv_cannot_hide_a_usable_other_watch(db_session, test_user):
    await store_daily(db_session, test_user.id, [Daily("hrv", D, 90)], "COROS")
    await store_daily(
        db_session, test_user.id, [Daily("hrv", D, 65, {"method": HRV_METHOD})], "Garmin"
    )
    rows = await nights.read_rows(db_session, test_user.id, D, D)
    assert rows["hrv"][D][0] == 65


@pytest.mark.asyncio
async def test_reaggregating_raw_samples_preserves_direct_watch(db_session, test_user):
    """Raw samples from other writers never touch a direct watch's nightly row, and never make one of their own
    (2026-10-14: no daily value is made from samples any more; it re-aggregated them per source)."""
    await store_daily(
        db_session, test_user.id, [Daily("hrv", D, 65, {"method": HRV_METHOD})], "Garmin"
    )
    at = datetime.combine(D, datetime.min.time())
    samples = [Sample("hrv", "", at, at, value, source) for source, value in (("Apple Watch", 30), ("Other watch", 40))]
    for _ in range(2):
        result = await store_samples(db_session, test_user.id, samples)
        assert result["days"] == {}
    rows = (
        await db_session.scalars(
            sa.select(HealthMetric).where(HealthMetric.user_id == test_user.id)
        )
    ).all()
    assert {(r.source, r.value) for r in rows} == {("Garmin", 65)}
    assert (await db_session.scalar(sa.select(sa.func.count(HealthSample.id)).where(
        HealthSample.user_id == test_user.id))) == 2


@pytest.mark.asyncio
async def test_coros_stress_is_never_shown_even_when_recent(db_session, test_user, as_user, monkeypatch):
    """COROS's stress is an archive its connection no longer sends: never a card (owner, 2026-10-14 — its « Stress
    COROS · archive » card was built on every load, its rows all read, then left out by the template)."""
    await store_daily(db_session, test_user.id, [Daily("stress", D, 13)], "COROS")
    await store_daily(db_session, test_user.id, [Daily("stress", D, 25)], "Garmin")
    cards = {c["key"]: c for c in await daily_context(db_session, test_user.id, D)}
    assert cards["stress"]["source"] == "Garmin" and "stress_archive" not in cards
    from unittest.mock import AsyncMock
    monkeypatch.setattr(sante, "athlete_today", AsyncMock(return_value=D))
    html = (await as_user.get("/sante")).text
    assert 'id="daily-stress-title">Stress</h3>' in html
    assert "Stress COROS" not in html and 'id="daily-stress_archive-title"' not in html



def test_migration_preserves_unknown_source_and_refuses_lossy_downgrade():
    migration = _load("e5b6c7d8e9f0_health_metric_sources")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        c.execute(
            sa.text(
                "CREATE TABLE health_metrics (id INTEGER PRIMARY KEY, user_id INTEGER, date DATE, "
                "metric VARCHAR(12), value FLOAT, source VARCHAR(100), "
                "CONSTRAINT uq_health_metrics_day UNIQUE(user_id, date, metric))"
            )
        )
        c.execute(
            sa.text("INSERT INTO health_metrics VALUES (1, 1, '2026-10-08', 'hr_night', 42, NULL)")
        )
        _run(c, migration.upgrade)
        assert c.execute(sa.text("SELECT source, value FROM health_metrics")).one() == ("", 42)
        c.execute(
            sa.text(
                "INSERT INTO health_metrics VALUES (2, 1, '2026-10-08', 'hr_night', 50, 'Garmin')"
            )
        )
        with pytest.raises(RuntimeError, match="without losing"):
            _run(c, migration.downgrade)
        assert c.execute(sa.text("SELECT count(*) FROM health_metrics")).scalar() == 2
        c.execute(sa.text("DELETE FROM health_metrics WHERE id = 2"))
        _run(c, migration.downgrade)
        assert c.execute(sa.text("SELECT value FROM health_metrics")).scalar() == 42
