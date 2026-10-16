"""Santé uses imported outings only; optional planner result links remain local to its calibration."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.activity import Activity
from app.models.route import Route
from app.models.user import User
from app.routers.sante import data_version
from app.services import sante_training as st
from app.services import sante_context
from app.services.activity_dedupe import duplicate_representatives, find_duplicate_ids
from app.services.activity_identity import confirmed_races, races_on_retained_copies, revision_sum
from app.services.activity_sources import adopt_watch_twin, merge_twins
from app.services.race_calibration import collect_race_points
from app.services.strava_activities import apply_detail


NOW = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)


def activity(user_id, **kw):
    values = dict(user_id=user_id, coros_activity_id=501, sport_type="TrailRun", name="Trail Run",
                  start_date=NOW - timedelta(days=2), distance=20_000, moving_time=7200, elapsed_time=7500,
                  total_elevation_gain=500, average_heartrate=None, raw_data={"source": "coros", "workout_type": 0})
    return Activity(**{**values, **kw})


def route(user_id, activity_id=None, **kw):
    return Route(user_id=user_id, name="TransJeju", total_distance_km=20, sport_type="trail",
                 result_activity_id=activity_id, **kw)


def test_race_identity_follows_the_final_copy_when_duplicates_form_a_chain():
    copies = [activity(1, id=i, start_date=NOW + timedelta(seconds=start),
                       moving_time=7200 + i, average_heartrate=140 if i > 1 else None)
              for i, start in ((1, 0), (2, 100), (3, 250))]
    representatives = duplicate_representatives(copies)
    assert representatives == {1: 3, 2: 3} and find_duplicate_ids(copies) == {1, 2}
    assert races_on_retained_copies({1: "TransJeju"}, representatives)[3] == "TransJeju"


async def test_linked_plan_names_a_planner_result_but_never_relabels_health_activity(db_session, test_user):
    row = activity(test_user.id)
    db_session.add(row)
    await db_session.flush()
    db_session.add(route(test_user.id, row.id))
    await db_session.flush()
    assert await confirmed_races(db_session, test_user.id) == {row.id: "TransJeju"}
    sessions = await st.load_sessions(db_session, test_user.id, NOW.date())
    assert len(sessions) == 1 and not sessions[0].is_race and sessions[0].name == "Trail Run"
    assert not sante_context.recent_effort(st.efforts(sessions), sessions, NOW.date())
    assert sessions[0].workout_type == 0 and row.name == "Trail Run" and row.raw_data["source"] == "coros"
    points = await collect_race_points(db_session, test_user.id)
    assert len(points) == 1 and points[0]["is_race"] and points[0]["name"] == "TransJeju"


async def test_plan_dates_and_foreign_links_never_prove_a_race(db_session, test_user):
    other = User(firstname="Other")
    db_session.add(other)
    await db_session.flush()
    mine, theirs = activity(test_user.id), activity(other.id, coros_activity_id=502)
    db_session.add_all([mine, theirs])
    await db_session.flush()
    db_session.add_all([route(test_user.id, race_date=mine.start_date.date().isoformat()),
                        route(other.id, mine.id), route(test_user.id, theirs.id)])
    await db_session.flush()
    assert await confirmed_races(db_session, test_user.id) == {}
    assert await confirmed_races(db_session, other.id) == {}
    assert not (await st.load_sessions(db_session, test_user.id, NOW.date()))[0].is_race
    assert await collect_race_points(db_session, test_user.id) == []  # 20 km is not the long-effort fallback


async def test_creating_linking_renaming_and_unlinking_plan_never_changes_health(db_session, test_user):
    row = activity(test_user.id)
    db_session.add(row)
    await db_session.flush()
    before = await data_version(db_session, test_user.id)
    sessions = await st.load_sessions(db_session, test_user.id, NOW.date())
    session_key = st._CACHE[test_user.id][0]
    saved = route(test_user.id, race_date=row.start_date.date().isoformat())
    db_session.add(saved)
    await db_session.flush()
    assert await data_version(db_session, test_user.id) == before
    assert await st.load_sessions(db_session, test_user.id, NOW.date()) == sessions
    saved.result_activity_id = row.id
    await db_session.flush()
    assert await data_version(db_session, test_user.id) == before
    assert await st.load_sessions(db_session, test_user.id, NOW.date()) == sessions
    assert (await collect_race_points(db_session, test_user.id))[0]["name"] == "TransJeju"
    saved.name = "JejuTrail"  # same character count
    await db_session.flush()
    assert await data_version(db_session, test_user.id) == before
    assert await st.load_sessions(db_session, test_user.id, NOW.date()) == sessions
    assert (await collect_race_points(db_session, test_user.id))[0]["name"] == "JejuTrail"
    saved.result_activity_id = None
    await db_session.flush()
    assert await data_version(db_session, test_user.id) == before
    assert await st.load_sessions(db_session, test_user.id, NOW.date()) == sessions
    assert st._CACHE[test_user.id][0] == session_key
    assert await collect_race_points(db_session, test_user.id) == []


async def test_link_on_discarded_copy_follows_richer_recording(db_session, test_user):
    watch = activity(test_user.id)
    rich = activity(test_user.id, coros_activity_id=None, strava_activity_id=601,
                    start_date=watch.start_date + timedelta(seconds=30), average_heartrate=140,
                    raw_data={"workout_type": 0})
    db_session.add_all([watch, rich])
    await db_session.flush()
    db_session.add(route(test_user.id, watch.id))
    await db_session.flush()
    sessions = await st.load_sessions(db_session, test_user.id, NOW.date())
    assert [s.id for s in sessions] == [rich.id]
    assert not sessions[0].is_race and sessions[0].name == "Trail Run" and sessions[0].workout_type == 0
    points = await collect_race_points(db_session, test_user.id)
    assert len(points) == 1 and points[0]["id"] == rich.id and points[0]["name"] == "TransJeju"
    assert points[0]["is_race"]


@pytest.mark.parametrize("source", ["strava", "garmin", "coros"])
async def test_recorded_ultra_has_health_context_without_any_plan(db_session, test_user, source):
    row = activity(test_user.id, **{f"{source}_activity_id": 807, "coros_activity_id": 807 if source == "coros" else None},
                   name="Sortie réellement enregistrée", moving_time=11 * 3600, elapsed_time=12 * 3600,
                   distance=80_000, raw_data={"source": source})
    db_session.add(row)
    await db_session.flush()
    sessions = await st.load_sessions(db_session, test_user.id, NOW.date())
    context = sante_context.recent_effort(st.efforts(sessions), sessions, NOW.date())
    assert context[0]["activity_id"] == row.id and context[0]["kind"] == "effort"
    assert context[0]["label"] == "Après Sortie réellement enregistrée · J+2"
    assert not sessions[0].is_race  # duration alone cannot establish a competition


async def test_provider_race_tag_survives_richer_duplicate_without_a_plan(db_session, test_user):
    tagged = activity(test_user.id, coros_activity_id=None, strava_activity_id=802,
                      raw_data={"workout_type": 1})
    rich = activity(test_user.id, coros_activity_id=803, name="Enregistrement montre",
                    start_date=tagged.start_date + timedelta(seconds=30), average_heartrate=140)
    db_session.add_all([tagged, rich])
    await db_session.flush()
    sessions = await st.load_sessions(db_session, test_user.id, NOW.date())
    assert [s.id for s in sessions] == [rich.id]
    assert sessions[0].is_race and sessions[0].workout_type == 0
    context = sante_context.recent_effort(st.efforts(sessions), sessions, NOW.date())
    assert context[0]["kind"] == "race" and context[0]["activity_id"] == rich.id
    assert context[0]["label"] == "Après Enregistrement montre · J+2"
    # A corrected provider tag must remove race context in every worker too.
    before = await data_version(db_session, test_user.id)
    assert apply_detail(tagged, {"workout_type": 0})
    await db_session.flush()
    sessions = await st.load_sessions(db_session, test_user.id, NOW.date())
    assert not sessions[0].is_race
    assert not sante_context.recent_effort(st.efforts(sessions), sessions, NOW.date())
    assert await data_version(db_session, test_user.id) != before


async def test_equal_size_provider_correction_invalidates_sessions_and_page_version(db_session, test_user):
    row = activity(test_user.id, raw_data={"source": "coros", "average_temp": 10, "paceforge_revision": 1})
    db_session.add(row)
    await db_session.flush()
    version = await data_version(db_session, test_user.id)
    assert (await st.load_sessions(db_session, test_user.id, NOW.date()))[0].temp == 10
    size = len(json.dumps(row.raw_data))
    assert apply_detail(row, {"average_temp": 20})
    assert len(json.dumps(row.raw_data)) == size and row.raw_data["paceforge_revision"] == 2
    await db_session.flush()
    assert (await st.load_sessions(db_session, test_user.id, NOW.date()))[0].temp == 20
    assert await data_version(db_session, test_user.id) != version
    row.raw_data = {"paceforge_revision": "malformed"}
    await db_session.flush()
    assert (await db_session.execute(select(revision_sum(db_session)).where(Activity.user_id == test_user.id))).scalar() == 0


async def test_watch_adoption_preserves_revision_and_merge_transfers_owned_result(db_session, test_user):
    watch = activity(test_user.id, raw_data={"paceforge_revision": 7})
    db_session.add(watch)
    await db_session.flush()
    adopted = await adopt_watch_twin(db_session, activity(test_user.id, coros_activity_id=None,
                                                         strava_activity_id=701, raw_data={}))
    assert adopted.id == watch.id and adopted.raw_data["paceforge_revision"] == 8
    # A separately imported duplicate predating adoption keeps the route's explicit result link when merged.
    copy = activity(test_user.id, coros_activity_id=502, raw_data={"paceforge_revision": 10})
    db_session.add(copy)
    await db_session.flush()
    saved = route(test_user.id, copy.id, result_json={"activity_id": copy.id})
    db_session.add(saved)
    await db_session.flush()
    # Free the watch slot on the Strava recording so this older duplicate can be linked by merge_twins.
    adopted.coros_activity_id = None
    await db_session.flush()
    assert await merge_twins(db_session, test_user.id, NOW - timedelta(days=3)) == 1
    assert saved.result_activity_id == adopted.id and saved.result_json["activity_id"] == adopted.id
    assert adopted.raw_data["paceforge_revision"] == 11
    assert await confirmed_races(db_session, test_user.id) == {adopted.id: "TransJeju"}


def test_combined_coros_activity_error_retries_only_after_normal_backoff(monkeypatch):
    from app.routers import sante
    from app.services import coros

    started = []
    monkeypatch.setattr(sante, "_flags", lambda conn, service: (conn.needs_reauth, conn.last_error))
    monkeypatch.setattr(coros, "schedule_sync", lambda uid: started.append(uid) or True)
    retryable = (coros.PARTIAL, coros.HRV_PARTIAL, coros.SLEEP_PARTIAL, coros.PHASES_PARTIAL,
                 coros.ACTIVITIES_PARTIAL)
    for error in (coros.ACTIVITIES_PARTIAL, coros.PARTIAL + " " + coros.ACTIVITIES_PARTIAL,
                  coros.HRV_UNREADABLE + " " + coros.ACTIVITIES_PARTIAL):
        conn = SimpleNamespace(last_error=error, needs_reauth=False, sync_claimed_at=None,
                               last_sync_at=datetime.now(timezone.utc) - timedelta(hours=2))
        link = sante._link(conn, coros, retryable)
        assert link["retryable_partial"]
        assert sante._sync_stale(1, [(coros, link)], sante.STALE_AFTER)
        conn.last_sync_at = datetime.now(timezone.utc)
        assert not sante._sync_stale(1, [(coros, sante._link(conn, coros, retryable))], sante.STALE_AFTER)
        conn.needs_reauth = True
        conn.last_sync_at -= timedelta(hours=2)
        assert not sante._sync_stale(1, [(coros, sante._link(conn, coros, retryable))], sante.STALE_AFTER)
    assert started == [1, 1, 1]
