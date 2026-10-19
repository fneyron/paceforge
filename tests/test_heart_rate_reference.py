"""Every consumer uses the same reference, provenance and missing-data behavior."""
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest

from app.models.activity import Activity
from app.models.user import User
from app.services import heart_rate_reference as reference
from app.services import sante_training as st
from app.services.training_load import calculate_training_load
from app.services.training_zones import estimate_training_zones
from tests.test_heart_rate_zones import supported_run
from tests.test_sante_score import D


def test_history_ignores_later_and_unfinished_evidence_and_manual_has_priority():
    runs = [supported_run(12, 178), supported_run(10, 179), supported_run(2, 195), supported_run(1, 194)]
    then = D - timedelta(days=9)
    assert reference.resolve(runs, then)["reference"] == 179
    assert st.hr_max(runs, then) == 179
    assert reference.resolve(runs, D)["reference"] == 195
    assert st.hr_max(runs, D, 183) == 183
    assert reference.resolve(runs, D, 183)["source"] == "confirmed"
    unfinished = [supported_run(1), replace(supported_run(0), elapsed=1500)]
    assert reference.resolve(unfinished, D)["reference"] is None


def test_no_reference_means_estimated_time_load_and_no_easy_hr_model():
    run = supported_run(1)
    result = st.loads([run], D, 45, None)
    assert result[run.id] == (run.minutes * st.RATE_DEFAULT, False)
    assert st.easy_runs([run], None) == []
    assert st.easy_model([run], D, None) is None


@pytest.mark.parametrize("maximum,expected,source", [(None, 179, "observed"), (183, 183, "confirmed")])
async def test_health_charge_and_plan_share_reference_and_zone_boundaries(
        db_session, test_user, maximum, expected, source):
    from app.services.heart_rate_zones import running_zones

    test_user.max_heart_rate = maximum
    # This other athlete's much higher maxima can never enter this athlete's reference.
    other = User(firstname="Other", max_heart_rate=220)
    db_session.add(other)
    await db_session.flush()
    for day, peak in ((1, 179), (3, 178), (4, 195), (5, 195)):
        s = supported_run(day, peak)
        db_session.add(Activity(user_id=test_user.id, sport_type="Run", name="Course", start_date=s.start,
                               distance=7500, moving_time=2700, elapsed_time=2700,
                               average_heartrate=140, max_heartrate=peak,
                               laps=[{"moving_time": m * 60, "average_heartrate": hr}
                                     for m, hr in ((5, 140), (4, min(174, peak - 5)), (4, min(173, peak - 6)))],
                               raw_data={}))
    await db_session.flush()
    st._CACHE.clear()
    now = datetime.combine(D, time(23), tzinfo=UTC)
    sessions = await st.load_sessions(db_session, test_user.id, D)
    ref = await reference.for_athlete(db_session, test_user.id, D, sessions=sessions)
    health_zones = running_zones(maximum, sessions, D)
    plan_zones = await estimate_training_zones(db_session, test_user.id, today=D)
    load = await calculate_training_load(db_session, test_user.id, now, zone=UTC)
    assert ref["reference"] == health_zones["reference"] == plan_zones["max_hr"] == expected
    assert ref["source"] == plan_zones["hr_reference"]["source"] == load.hr_reference["source"] == source
    assert load.hr_reference == plan_zones["hr_reference"] == ref
    assert [(r["low"], r["high"]) for r in health_zones["rows"]] == [
        (r["min"], r["max"]) for r in plan_zones["hr_zones"]]
    assert test_user.max_heart_rate == maximum  # estimation never becomes a confirmation


async def test_summary_spikes_do_not_generate_plan_caps_or_measured_load(db_session, test_user):
    s = supported_run(1, 195)
    db_session.add(Activity(user_id=test_user.id, sport_type="Run", name="Course", start_date=s.start,
                           distance=7500, moving_time=2700, elapsed_time=2700,
                           average_heartrate=140, max_heartrate=195, raw_data={}))
    await db_session.flush()
    st._CACHE.clear()
    zones = await estimate_training_zones(db_session, test_user.id, today=D)
    now = datetime.combine(D, time(23), tzinfo=UTC)
    load = await calculate_training_load(db_session, test_user.id, now, zone=UTC)
    assert "max_hr" not in zones and "hr_zones" not in zones
    assert zones["hr_reference"]["source"] == "unavailable"
    assert load.hr_reference == zones["hr_reference"]
    assert load.load_7d == 45 * st.RATE_DEFAULT


async def test_historical_reference_keeps_its_full_evidence_window_when_read_later(db_session, test_user):
    then = D - timedelta(days=13)
    for days, peak in ((370, 178), (15, 179)):
        s = supported_run(days, peak)
        db_session.add(Activity(user_id=test_user.id, sport_type="Run", name="Course", start_date=s.start,
                               distance=7500, moving_time=2700, elapsed_time=2700,
                               average_heartrate=140, max_heartrate=peak,
                               laps=[{"moving_time": m * 60, "average_heartrate": hr} for m, hr in s.segs],
                               raw_data={}))
    await db_session.flush()
    st._CACHE.clear()
    current_sessions = await st.load_sessions(db_session, test_user.id, D)
    await reference.for_athlete(db_session, test_user.id, D, sessions=current_sessions)
    retrospective = reference.resolve(current_sessions, then)
    direct = await reference.for_athlete(db_session, test_user.id, then)
    assert retrospective == direct
    assert retrospective["reference"] == 179
    assert len(retrospective["evidence"]) == 2
