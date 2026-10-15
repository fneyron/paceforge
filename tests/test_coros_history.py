"""COROS coverage is proved by parsed windows and advances only after commit."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.activity import Activity
from app.services import coros
from tests import test_coros

_link, fake, no_commit = test_coros._link, test_coros.fake, test_coros.no_commit


def records_text(rows, lo, hi, limit=200):
    selected = [(label, day) for label, day in rows if lo <= day <= hi][:limit]
    lines = [f"Sport Records — {lo} to {hi} ({len(selected)} records)"]
    for i, (label, day) in enumerate(selected, 1):
        start = int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp())
        lines.extend((f"{i}. Outdoor Run — {day}",
                      f"Time Window: startTimestamp={start} | endTimestamp={start + 1800}",
                      "Duration: 30:00 | Distance: 5 km", f"LabelId: {label} | SportType: 100"))
    return "\n".join(lines)


class Calls:
    def __init__(self, rows):
        self.rows, self.calls, self.stopped = rows, 0, False
        self.asked = []

    async def __call__(self, tool, args):
        assert tool == "querySportRecords"
        self.calls += 1
        lo, hi = (datetime.strptime(args[k], "%Y%m%d").date() for k in ("startDate", "endDate"))
        self.asked.append((lo, hi))
        return records_text(self.rows, lo, hi, args["limit"])


def test_empty_history_requires_an_explicit_recognised_answer():
    assert coros.sport_records_readable("Sport Records — 2026-10-01 to 2026-10-15 (0 records)", [])
    for text in (None, "", "Temporary error", "New sport record format: []"):
        assert not coros.sport_records_readable(text, [])


def test_declared_count_and_every_record_must_be_read():
    day = date(2026, 10, 1)
    text = records_text([(1, day), (2, day)], day, day)
    parsed = coros.parse_sport_records(text)
    assert coros.sport_records_readable(text, parsed)
    broken = text.replace("LabelId: 2", "RecordId: 2")
    assert len(coros.parse_sport_records(broken)) == 1
    assert not coros.sport_records_readable(broken, coros.parse_sport_records(broken))
    duplicate = text.replace("LabelId: 2", "LabelId: 1")
    assert not coros.sport_records_readable(duplicate, coros.parse_sport_records(duplicate))


async def test_capped_windows_resume_until_every_day_has_been_verified(monkeypatch):
    monkeypatch.setattr(coros, "SESSION_LIMIT", 3)
    monkeypatch.setattr(coros, "SESSION_CALLS", 2)
    lo, hi = date(2026, 10, 1), date(2026, 10, 10)
    rows = [(i + 1, lo + timedelta(days=i)) for i in range(10)]
    pending, found, rounds = [(lo, hi)], set(), 0
    while pending and rounds < 15:
        calls = Calls(rows)
        records, pending = await coros._fetch_sessions(calls, pending)
        found.update(r["label"] for r in records)
        assert calls.calls <= 2
        rounds += 1
    assert not pending and rounds > 1
    assert found == set(range(1, 11))


async def test_saturated_single_day_stays_pending_without_starving_other_days(monkeypatch):
    monkeypatch.setattr(coros, "SESSION_LIMIT", 2)
    day = date(2026, 10, 1)
    calls = Calls([(1, day), (2, day), (3, day + timedelta(days=1))])
    records, pending = await coros._fetch_sessions(calls, [(day, day), (day + timedelta(days=1), day + timedelta(days=1))])
    assert pending == [(day, day)]
    assert {r["label"] for r in records} == {1, 2, 3}


async def test_declared_saturation_splits_even_when_truncated_text_loses_a_record(monkeypatch):
    monkeypatch.setattr(coros, "SESSION_LIMIT", 3)
    lo, hi = date(2026, 10, 1), date(2026, 10, 3)

    class Truncated(Calls):
        async def __call__(self, tool, args):
            text = await super().__call__(tool, args)
            return text.replace("LabelId: 3", "RecordId: 3") if args["startDate"] == "20261001" and args["endDate"] == "20261003" else text

    call = Truncated([(1, lo), (2, lo + timedelta(days=1)), (3, hi)])
    records, pending = await coros._fetch_sessions(call, [(lo, hi)])
    assert not pending and call.calls == 3
    assert {r["label"] for r in records} == {1, 2, 3}


async def test_malformed_response_keeps_valid_rows_but_owes_the_window():
    day = date(2026, 10, 1)
    call = Calls([(1, day), (2, day)])
    original = call.__call__

    class Broken(Calls):
        async def __call__(self, tool, args):
            return (await original(tool, args)).replace("LabelId: 2", "RecordId: 2")

    records, pending = await coros._fetch_sessions(Broken([]), [(day, day)])
    assert [r["label"] for r in records] == [1]
    assert pending == [(day, day)]


async def test_global_call_budget_leaves_unread_windows_pending():
    day = date(2026, 10, 1)
    call = Calls([])
    call.calls = coros.MAX_CALLS
    records, pending = await coros._fetch_sessions(call, [(day, day)])
    assert records == [] and pending == [(day, day)] and call.calls == coros.MAX_CALLS


async def test_pending_old_day_never_hides_later_uploads_today_or_tomorrow(monkeypatch):
    monkeypatch.setattr(coros, "SESSION_CALLS", 3)
    today, old = date(2026, 10, 15), date(2026, 8, 1)

    class OldUnreadable(Calls):
        async def __call__(self, tool, args):
            text = await super().__call__(tool, args)
            return "Changed format" if args["startDate"] == "20260801" else text

    rows, pending = [(1, today)], [(old, old)]
    for hi, additions, expected in (
            (today + timedelta(days=1), [], {1}),
            (today + timedelta(days=1), [(2, today)], {1, 2}),
            (today + timedelta(days=2), [(3, today + timedelta(days=1))], {1, 2, 3})):
        rows.extend(additions)
        call = OldUnreadable(rows)
        records, pending = await coros._resume_sessions(call, pending, hi)
        assert {r["label"] for r in records} == expected
        assert pending == [(old, old)] and call.calls <= 3


async def test_recent_saturation_joins_queue_after_existing_history(monkeypatch):
    monkeypatch.setattr(coros, "SESSION_CALLS", 2)
    monkeypatch.setattr(coros, "SESSION_LIMIT", 2)
    today, old = date(2026, 10, 15), date(2026, 8, 1)
    call = Calls([(1, today), (2, today - timedelta(days=1))])
    records, pending = await coros._resume_sessions(call, [(old, old)], today + timedelta(days=1))
    assert call.calls == 2 and call.asked[1] == (old, old)
    assert {r["label"] for r in records} == {1, 2}
    assert pending and all(a > old for a, _ in pending)


async def test_recent_reservation_still_respects_global_call_cap():
    today, old = date(2026, 10, 15), date(2026, 8, 1)
    call = Calls([(1, today)])
    call.calls = coros.MAX_CALLS - 1
    records, pending = await coros._resume_sessions(call, [(old, old)], today + timedelta(days=1))
    assert [r["label"] for r in records] == [1]
    assert pending == [(old, old)] and call.calls == coros.MAX_CALLS


def test_corrections_clear_derived_activity_caches_without_invalidating_noops():
    day = date(2026, 10, 1)
    record = coros.parse_sport_records(records_text([(1, day)], day, day))[0]
    act = Activity(**coros.session_fields(record, None), streams_data={"hr": [100]}, computed_metrics={"load": 10})
    act.raw_data = {**act.raw_data, "paceforge_heat": {"load": 10}}
    before = coros._session_snapshot(act)
    coros._revise_session(act, before)
    assert act.streams_data and act.computed_metrics and coros.REVISION not in act.raw_data
    act.moving_time += 60
    coros._revise_session(act, before)
    assert act.streams_data is None and act.computed_metrics is None
    assert act.raw_data[coros.REVISION] == 1 and "paceforge_heat" not in act.raw_data
    act.computed_metrics = {"load": 10}
    before = coros._session_snapshot(act)
    act.laps = [{"moving_time": 1800, "average_heartrate": 145}]
    coros._revise_session(act, before)
    assert act.computed_metrics is None and act.raw_data[coros.REVISION] == 2


@pytest.mark.parametrize("commit_fails", [False, True])
async def test_coverage_is_written_only_after_a_successful_db_commit(monkeypatch, commit_fails):
    conn = SimpleNamespace(id=1, user_id=1, connected_at=datetime.now(timezone.utc), needs_reauth=False)
    state = {"pending": [], "through": "2026-10-15", "checked_at": "2026-10-15T00:00:00+00:00"}
    events = []

    async def commit():
        events.append("commit")
        if commit_fails:
            raise RuntimeError("database commit failed")

    monkeypatch.setattr(coros, "tokens_readable", lambda _: True)
    monkeypatch.setattr(coros, "claim", AsyncMock(return_value=True))
    monkeypatch.setattr(coros, "sync_connection", AsyncMock(return_value={"_sessions_state": state}))
    monkeypatch.setattr(coros, "_history_store", lambda key, value: events.append("coverage"))
    db = SimpleNamespace(commit=commit)
    if commit_fails:
        with pytest.raises(RuntimeError, match="commit failed"):
            await coros.run_sync(db, conn)
        assert events == ["commit"]
    else:
        assert (await coros.run_sync(db, conn))["ok"]
        assert events == ["commit", "coverage"]


async def test_legacy_completion_is_repaired_even_without_coros_rows(db_session, test_user, fake, no_commit):
    now = datetime.now(timezone.utc)
    conn = await _link(db_session, test_user, sessions_synced_at=now)
    fake.sessions = [{"label": 101, "code": 102, "start": now - timedelta(days=40), "seconds": 3000, "km": 10}]
    result = await coros.run_sync(db_session, conn)
    assert result["ok"] and not result["result"]["activities_incomplete"]
    assert (await db_session.execute(select(Activity.coros_activity_id).where(Activity.user_id == test_user.id))).scalar() == 101
    assert len([1 for name, _ in fake.tool_calls if name == "querySportRecords"]) == 3


async def test_history_rechecks_weekly_to_find_late_uploads(db_session, test_user, fake, no_commit):
    now = datetime.now(timezone.utc)
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    state = coros._history_store(coros._history_key(conn))
    state["checked_at"] = (now - coros.SESSION_RECHECK).isoformat()
    fake.sessions = [{"label": 202, "code": 100, "start": now - timedelta(days=35), "seconds": 3000, "km": 10}]
    fake.tool_calls.clear()
    assert (await coros.run_sync(db_session, conn))["ok"]
    assert (await db_session.execute(select(Activity.coros_activity_id).where(Activity.user_id == test_user.id))).scalar() == 202
    assert len([1 for name, _ in fake.tool_calls if name == "querySportRecords"]) == 3


async def test_bounded_syncs_persist_remaining_windows_until_history_is_complete(
        db_session, test_user, fake, no_commit, monkeypatch):
    monkeypatch.setattr(coros, "SESSION_LIMIT", 3)
    monkeypatch.setattr(coros, "SESSION_CALLS", 2)
    today = datetime.now(timezone.utc).date()
    rows = [(400 + i, today - timedelta(days=i * 4)) for i in range(10)]

    def records(args):
        lo, hi = (datetime.strptime(args[k], "%Y%m%d").date() for k in ("startDate", "endDate"))
        return records_text(rows, lo, hi, args["limit"])

    fake.records = records
    conn = await _link(db_session, test_user)
    for attempt in range(20):
        fake.tool_calls.clear()
        result = await coros.run_sync(db_session, conn)
        assert result["ok"]
        assert len([1 for name, _ in fake.tool_calls if name == "querySportRecords"]) <= 2
        if not result["result"]["activities_incomplete"]:
            break
        assert conn.sessions_synced_at is None
    else:
        pytest.fail("the history cursor did not progress across bounded syncs")
    assert attempt > 0 and conn.sessions_synced_at is not None and conn.last_error is None
    received = (await db_session.execute(select(Activity.coros_activity_id).where(Activity.user_id == test_user.id))).scalars().all()
    assert set(received) == {label for label, _ in rows}


async def test_real_corrections_increment_revision_but_noop_syncs_do_not(db_session, test_user):
    day = datetime.now(timezone.utc).date()
    record = coros.parse_sport_records(records_text([(303, day)], day, day))[0]
    await coros.import_sessions(db_session, test_user.id, [record], None)
    act = (await db_session.execute(select(Activity).where(Activity.coros_activity_id == 303))).scalar_one()
    await coros.import_sessions(db_session, test_user.id, [record], None)
    assert act.raw_data.get(coros.REVISION, 0) == 0
    await coros.import_sessions(db_session, test_user.id, [{**record, "hr": 145}], None)
    assert act.raw_data[coros.REVISION] == 1
    await coros.apply_details(db_session, test_user.id, {}, {303: {"hr_max": 180, "laps": [{"moving_time": 1800, "average_heartrate": 145}]}})
    assert act.raw_data[coros.REVISION] == 2
    await coros.apply_details(db_session, test_user.id, {}, {303: {"hr_max": 180, "laps": [{"moving_time": 1800, "average_heartrate": 145}]}})
    assert act.raw_data[coros.REVISION] == 2
