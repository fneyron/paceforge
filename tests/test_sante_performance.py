"""Repeated sleep-history calculations keep their results and request isolation."""
from datetime import date, timedelta
from unittest.mock import patch

from app.services import nights as nt
from app.services import sante_sleep as sl


TODAY = date(2026, 10, 13)


def nights(minutes=500, source="COROS"):
    return {TODAY - timedelta(days=i): nt.Night(
        day=TODAY - timedelta(days=i), source=source, asleep=minutes + i % 4 * 5,
    ) for i in range(150)}


def test_sleep_history_reuses_each_baseline_without_changing_the_need():
    rows = nights()
    rows[TODAY - timedelta(days=3)].tags.add("long")
    raced = {TODAY - timedelta(days=11)}
    days = [TODAY - timedelta(days=i) for i in range(14)]
    expected = [sl.sleep_need(rows, day, raced) for day in days]
    with nt.memo():
        nt.freeze(rows)
        with patch.object(sl, "_sleep_baseline", wraps=sl._sleep_baseline) as calculate:
            assert [sl.sleep_need(rows, day, raced) for day in days] == expected
            # Fourteen daily needs overlap: twenty-one distinct baseline days,
            # instead of rereading sixty nights eight times for every chart day.
            assert calculate.call_count == 21
            assert [sl.sleep_need(rows, day, raced) for day in days] == expected
            assert calculate.call_count == 21


def test_baselines_do_not_leak_between_accounts_requests_or_race_contexts():
    a, b = nights(500), nights(550, "Garmin")
    raced = {TODAY - timedelta(days=i) for i in range(1, 60)}
    expected_a, expected_b = sl.sleep_baseline(a, TODAY), sl.sleep_baseline(b, TODAY)
    expected_raced = sl.sleep_baseline(a, TODAY, raced)
    assert expected_a != expected_b and expected_a != expected_raced
    with nt.memo():
        nt.freeze(a)
        nt.freeze(b)
        assert sl.sleep_baseline(a, TODAY) == expected_a
        assert sl.sleep_baseline(b, TODAY) == expected_b
        assert sl.sleep_baseline(a, TODAY, raced) == expected_raced
        # Callers cannot accidentally alter the retained result.
        sl.sleep_baseline(a, TODAY)["value"] = 0
        assert sl.sleep_baseline(a, TODAY) == expected_a
    for night in a.values():
        night.asleep = 420
    with nt.memo():
        nt.freeze(a)
        assert sl.sleep_baseline(a, TODAY) != expected_a


def test_mutable_nights_are_not_cached_without_explicit_freeze():
    rows = nights(500)
    with nt.memo():
        before = sl.sleep_baseline(rows, TODAY)
        for night in rows.values():
            night.asleep = 420
        assert sl.sleep_baseline(rows, TODAY) != before
