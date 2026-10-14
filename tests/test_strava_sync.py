"""The Strava syncs (audit A1): the hourly round and each athlete's poll, the webhook's events checked with Strava,
the app's quota, a failed first import tried again, one Strava → Activity builder, Activités in the athlete's
weeks and on one clock, the « Synchroniser » cooldown."""
import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.crypto import encrypt_secret
from app.database import Base
from app.dependencies import get_current_user
from app.exceptions import StravaAPIError, StravaRateLimitError
from app.models.activity import Activity
from app.models.user import User
from app.services import strava_state
from app.services.strava import StravaService, strava_status


class FakeRedis:
    """What strava_state asks of Redis: SET (NX, EX), GET, DELETE."""

    def __init__(self):
        self.data = {}

    def _alive(self, key):
        value = self.data.get(key)
        if value and value[1] is not None and value[1] <= time.time():
            del self.data[key]
            return None
        return value

    def set(self, key, value, nx=False, ex=None):
        if nx and self._alive(key):
            return None
        self.data[key] = (str(value), time.time() + ex if ex else None)
        return True

    def get(self, key):
        value = self._alive(key)
        return value[0] if value else None

    def delete(self, *keys):
        return sum(self.data.pop(k, None) is not None for k in keys)


@pytest.fixture(autouse=True)
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(strava_state, "_client", fake)
    return fake


@pytest.fixture
def task_session(db_session: AsyncSession, monkeypatch):
    """The tasks' sessions are the test's (their commits flush, the test's rollback cleans up)."""
    import app.database

    @asynccontextmanager
    async def session():
        yield db_session
    monkeypatch.setattr(app.database, "get_task_session", session)
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
async def task_db(tmp_path, monkeypatch):
    """A database of its own whose sessions really commit and roll back, a fresh engine per task as in production
    (so a task can run in a thread, as Celery runs it)."""
    import app.database

    url = f"sqlite+aiosqlite:///{tmp_path}/tasks.db"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    @asynccontextmanager
    async def session():
        task_engine = create_async_engine(url)
        try:
            async with async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)() as s:
                yield s
        finally:
            await task_engine.dispose()
    monkeypatch.setattr(app.database, "get_task_session", session)
    return session


async def _own_app(db: AsyncSession, user: User, client_id="4242", sub=None, done=True) -> User:
    user.strava_client_id = client_id
    user.strava_client_secret_encrypted = encrypt_secret("s3cret")
    user.strava_webhook_subscription_id = sub
    user.initial_sync_done = done
    await db.flush()
    return user


async def _athlete(db: AsyncSession, athlete_id: int, **fields) -> User:
    user = User(strava_athlete_id=athlete_id, strava_access_token="at", strava_refresh_token="rt",
                strava_token_expires_at=9999999999, firstname="A", **fields)
    db.add(user)
    await db.flush()
    return user


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _data(sid: int, start: datetime, **extra) -> dict:
    """A Strava activity as /athlete/activities lists it."""
    return {"id": sid, "sport_type": "Run", "type": "Run", "name": f"Sortie {sid}", "start_date": _iso(start),
            "distance": 10_000.0, "moving_time": 3600, "elapsed_time": 3700, "total_elevation_gain": 120.0,
            "athlete": {"id": extra.pop("athlete_id", 12345678)}, **extra}


async def _row(db: AsyncSession, sid: int) -> Activity | None:
    return (await db.execute(select(Activity).where(Activity.strava_activity_id == sid))).scalar()


def _api_error(status: int):
    return StravaAPIError(f"Strava API error: {status}", status_code=status)


# ── the app's quota ──────────────────────────────────────────────────────────

def _response(status=200, usage=None, limit="200,2000", read_usage=None, read_limit="100,1000"):
    headers = {}
    if usage is not None:
        headers.update({"X-RateLimit-Usage": usage, "X-RateLimit-Limit": limit})
    if read_usage is not None:
        headers.update({"X-ReadRateLimit-Usage": read_usage, "X-ReadRateLimit-Limit": read_limit})
    return httpx.Response(status, json=[], headers=headers,
                          request=httpx.Request("GET", "https://www.strava.com/api/v3/athlete/activities"))


async def test_the_background_syncs_back_off_near_the_apps_quota(db_session, test_user, monkeypatch):
    """Strava's read quota (100 / 15 min, 1,000 a day by default) is shared by every athlete of the shared app:
    past 80 % of a window, or on a 429, the background syncs wait until the window turns."""
    await _own_app(db_session, test_user)
    strava = StravaService.for_user(db_session, test_user)
    get = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "request", get)

    get.return_value = _response(usage="10,300", read_usage="79,500")
    await strava.get_recent_activities(test_user)
    assert not strava.backing_off()
    get.return_value = _response(usage="10,300", read_usage="80,500")  # 80 % of the reads' quarter hour
    await strava.get_recent_activities(test_user)
    until = strava_state.backoff_until("4242")
    assert strava.backing_off() and until % 900 == 0 and 0 < until - time.time() <= 900
    assert not StravaService(db_session, client_id="9999").backing_off()  # per app

    redis_until = strava_state.backoff_until
    strava_state.release("backoff:4242")
    get.return_value = _response(usage="10,1700", read_usage="10,810")  # the day's
    await strava.get_recent_activities(test_user)
    assert redis_until("4242") % 86400 == 0 and redis_until("4242") - time.time() > 0

    strava_state.release("backoff:4242")
    get.return_value = _response(429)  # no header: the quarter hour
    with pytest.raises(StravaRateLimitError):
        await strava.get_recent_activities(test_user)
    assert strava.backing_off() and redis_until("4242") - time.time() <= 900
    get.return_value = _response(200, usage="garbage")
    await strava.get_recent_activities(test_user)  # a malformed header never fails the request


# ── the first import, one builder, the athlete's zone ────────────────────────

async def test_the_first_imports_details_stop_at_the_quota(db_session, test_user, task_session, monkeypatch):
    """One request per activity for its detail: past the app's 80 %, the rest keep the list's data (the base code
    asked on, each answer a 429)."""
    from app.tasks.initial_sync import _run_initial_sync

    await _own_app(db_session, test_user, done=False)
    now = datetime.now(timezone.utc)
    listed = [_data(760_000 + i, now - timedelta(days=i)) for i in range(5)]
    monkeypatch.setattr(StravaService, "get_all_activities_since", AsyncMock(return_value=listed))
    calls = []

    async def detail(self, user, sid):
        calls.append(sid)
        if len(calls) == 2:
            strava_state.back_off("4242", time.time() + 600)
        return {**_data(sid, now), "laps": [{"lap_index": 1}], "calories": 500.0}
    monkeypatch.setattr(StravaService, "get_activity", detail)
    result = await _run_initial_sync(test_user.id)
    assert result["synced"] == 5 and result["enriched"] == 2 and len(calls) == 2
    assert (await _row(db_session, 760_000)).laps == [{"lap_index": 1}]
    assert (await _row(db_session, 760_004)).laps is None and test_user.initial_sync_done


def _run(user, sid, start, name="Footing") -> Activity:
    return Activity(user_id=user.id, strava_activity_id=sid, sport_type="Run", name=name, start_date=start,
                    distance=8000, moving_time=2400, elapsed_time=2500, total_elevation_gain=50, raw_data={})


async def test_the_athletes_zone_comes_from_their_strava_activities(db_session, test_user):
    """users.timezone, while none was chosen (empty, or still the default): the most frequent zone of the last
    activities (« (GMT-05:00) America/New_York »), a trip abroad not moving it; a zone set otherwise is kept."""
    from app.services.strava_activities import adopt_zone

    start = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)

    def act(sid, label, days):
        a = _run(test_user, sid, start + timedelta(days=days))
        a.raw_data = {"timezone": label}
        return a
    db_session.add_all([act(790_000 + d, "(GMT-05:00) America/New_York", d) for d in range(6)]
                       + [act(790_100 + d, "(GMT+09:00) Asia/Seoul", 10 + d) for d in range(3)])  # the newest
    await db_session.flush()
    assert test_user.timezone is None
    await adopt_zone(db_session, test_user)
    assert test_user.timezone == "America/New_York"
    test_user.timezone = "Europe/Paris"  # still the default: learnt again
    await adopt_zone(db_session, test_user)
    assert test_user.timezone == "America/New_York"
    test_user.timezone = "Asia/Tokyo"  # chosen otherwise: kept
    await adopt_zone(db_session, test_user)
    assert test_user.timezone == "Asia/Tokyo"


# ── the hourly round and each athlete's poll; Celery ─────────────────────────

async def test_the_poll_finds_an_activity_uploaded_long_after_its_start(db_session, test_user, task_session,
                                                                         monkeypatch):
    """Base code: a run started 2 h ago and uploaded after the previous round (5 min ago then) was skipped, its
    start being before that round, and never found again ({'users_checked': 1, 'new_activities': 0})."""
    from app.tasks.poll_activities import _poll_user

    now = datetime.now(timezone.utc)
    await _own_app(db_session, test_user)
    test_user.last_activity_poll_at = now - timedelta(minutes=61)
    await db_session.flush()
    fetch = AsyncMock(return_value=[_data(700_001, now - timedelta(hours=2))])
    monkeypatch.setattr(StravaService, "get_recent_activities", fetch)
    assert await _poll_user(test_user.id) == {"user_id": test_user.id, "new_activities": 1}
    assert (await _row(db_session, 700_001)).name == "Sortie 700001"
    assert test_user.last_activity_poll_at > now - timedelta(seconds=5)  # Réglages' « Dernière synchro »
    assert (await _poll_user(test_user.id))["new_activities"] == 0  # known: never twice


async def test_the_poll_pages_until_a_page_is_all_known(db_session, test_user, task_session, monkeypatch):
    from app.tasks.poll_activities import POLL_PAGES, _poll_user

    now = datetime.now(timezone.utc)
    await _own_app(db_session, test_user)
    pages = {p: [_data(710_000 + 30 * p + i, now - timedelta(hours=30 * p + i)) for i in range(30)]
             for p in range(1, 6)}
    calls = []

    async def fetch(self, user, per_page=30, page=1):
        calls.append(page)
        return pages[page]
    monkeypatch.setattr(StravaService, "get_recent_activities", fetch)
    assert (await _poll_user(test_user.id))["new_activities"] == 30 * POLL_PAGES and calls == [1, 2, 3]
    calls.clear()  # the second round: page 1 is all known, nothing more is asked
    assert (await _poll_user(test_user.id))["new_activities"] == 0 and calls == [1]


async def test_one_athletes_failure_leaves_the_others_import_whole(task_db, monkeypatch):
    """Base code: one session and one commit for the whole round; a flush error of one athlete (e.g. the webhook
    saving the same activity meanwhile) failed the session, the next athletes raised PendingRollbackError and the
    round's imports were all lost. Now each athlete's poll is a task and a transaction of its own."""
    from app.services import activity_env
    from app.tasks import poll_activities

    async with task_db() as db:
        bad = await _own_app(db, await _athlete(db, 801), client_id="801")
        good = await _own_app(db, await _athlete(db, 802), client_id="802")
        await db.commit()
    now = datetime.now(timezone.utc)

    async def fetch(self, user, per_page=30, page=1):
        return [_data(800_000 + user.strava_athlete_id, now - timedelta(hours=1), athlete_id=user.strava_athlete_id)]
    monkeypatch.setattr(StravaService, "get_recent_activities", fetch)
    real_enrich = activity_env.enrich

    async def enrich(db, user_id, now=None):
        if user_id == bad.id:
            raise RuntimeError("flush failed")  # after its row was added
        return await real_enrich(db, user_id, now)
    monkeypatch.setattr(activity_env, "enrich", enrich)
    queued = []
    monkeypatch.setattr(poll_activities.poll_strava_user, "delay", lambda uid: queued.append(uid))

    assert (await poll_activities._run_poll())["users_checked"] == 2 and sorted(queued) == sorted([bad.id, good.id])
    # each poll as Celery runs it (its own event loop, its own session)
    results = {uid: await asyncio.to_thread(poll_activities.poll_strava_user.run, uid) for uid in queued}
    assert results[bad.id] == {"user_id": bad.id, "error": "failed"}
    assert results[good.id] == {"user_id": good.id, "new_activities": 1}
    async with task_db() as db:
        assert await _row(db, 800_802) is not None and await _row(db, 800_801) is None  # rolled back alone
        assert (await db.get(User, good.id)).last_activity_poll_at is not None
    assert strava_state.read(f"poll:{bad.id}") is None  # claims freed: the next round polls them again


async def test_the_round_is_hourly_and_leaves_the_webhooks_athletes_alone(db_session, test_user, task_session,
                                                                          monkeypatch):
    """Base code: every athlete, every 5 minutes (≥ 288 reads a day each: the shared app's 1,000 were gone with 4
    athletes). Now hourly, and only for those the webhook has not reached for 24 h; one queued poll per athlete."""
    from celery.schedules import crontab

    from app.celery_app import celery_app
    from app.tasks import poll_activities

    assert celery_app.conf.beat_schedule["poll-new-activities"]["schedule"] == crontab(minute=7)
    await _own_app(db_session, test_user)
    quiet = await _own_app(db_session, await _athlete(db_session, 901), client_id="901")
    backing = await _own_app(db_session, await _athlete(db_session, 902), client_id="902")
    queued = []
    monkeypatch.setattr(poll_activities.poll_strava_user, "delay", lambda uid: queued.append(uid))
    strava_state.webhook_reached(test_user.id)
    strava_state.back_off("902", time.time() + 600)
    assert (await poll_activities._run_poll())["users_checked"] == 1 and queued == [quiet.id]
    assert (await poll_activities._run_poll())["users_checked"] == 0  # still queued (claimed): never twice
    strava_state.release(f"poll:{quiet.id}")  # its poll ran
    strava_state.webhook_forget(test_user.id)  # 24 h without an event
    strava_state.release("backoff:902")
    queued.clear()
    await poll_activities._run_poll()
    assert sorted(queued) == sorted([test_user.id, quiet.id, backing.id])

    # a poll queued before the backoff began asks nothing
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(StravaService, "get_recent_activities", fetch)
    strava_state.back_off("901", time.time() + 600)
    assert (await poll_activities._poll_user(quiet.id))["status"] == "backoff" and fetch.await_count == 0


async def test_a_failed_first_import_is_queued_again_later_and_said(task_db, monkeypatch):
    """Base code: three failures (a 429…) left initial_sync_done False, which the poll's filter excluded forever,
    Réglages still saying « Première synchro en cours… ». Now the failure is kept (strava_status: sync_error), and
    the round queues the import again after 1 h, 2 h, 4 h… (24 h at most)."""
    from app.tasks import initial_sync as task
    from app.tasks import poll_activities

    async with task_db() as db:
        uid = (await _own_app(db, await _athlete(db, 12345678), done=False)).id
        await db.commit()

    async def user() -> User:
        async with task_db() as db:
            return await db.get(User, uid)

    def run():  # as Celery runs it: its own event loop
        return asyncio.to_thread(task.initial_sync.run, uid)

    monkeypatch.setattr(StravaService, "get_all_activities_since", AsyncMock(side_effect=StravaRateLimitError()))
    assert (await run())["error"] == "rate_limited"
    status = strava_status(await user())
    assert status["connected"] and status["sync_error"] == "rate_limited"
    assert timedelta(minutes=59) < status["sync_retry_at"] - datetime.now(timezone.utc) <= timedelta(hours=1)
    assert strava_state.read(strava_state.initial_lock(uid)) is None  # not left running

    requeued = []
    monkeypatch.setattr(task.initial_sync, "delay", lambda u: requeued.append(u))
    assert (await poll_activities._run_poll())["initial_requeued"] == 0  # its hour is not over
    failed = strava_state.initial_failed(uid, "failed")  # a second failure: 2 h
    assert failed["failures"] == 2 and 7190 < failed["next_at"] - time.time() <= 7200
    assert not strava_state.initial_due(uid) and strava_state.initial_due(uid, failed["next_at"])
    for _ in range(8):
        failed = strava_state.initial_failed(uid, "failed")
    assert failed["next_at"] - time.time() <= 24 * 3600
    strava_state.mark(f"initial:{uid}:failed", 3600, '{"failures": 3, "next_at": 0, "error": "failed"}')
    assert (await poll_activities._run_poll())["initial_requeued"] == 1 and requeued == [uid]
    # never two at once
    assert strava_state.claim(strava_state.initial_lock(uid), 60)
    assert (await run())["status"] == "already_running"
    assert (await poll_activities._run_poll())["initial_requeued"] == 0  # not queued again while it runs
    strava_state.release(strava_state.initial_lock(uid))

    # it works at last: done, the failure gone, « Dernière synchro » set
    monkeypatch.setattr(StravaService, "get_all_activities_since", AsyncMock(return_value=[]))
    assert (await run())["synced"] == 0
    done = await user()
    assert done.initial_sync_done and done.last_activity_poll_at is not None
    assert strava_state.initial_failure(uid) is None and strava_status(done)["sync_error"] is None
    assert (await run())["status"] == "already_done"  # a copy the round queued meanwhile


def test_long_syncs_have_their_queue_and_are_never_redelivered_while_running():
    """Base code: Redis's default 1-h visibility timeout redelivered (acks_late) a round still running; every task on
    one queue, a long sync holding the 2 slots while the 5-min poll waited."""
    from app.celery_app import celery_app

    assert celery_app.conf.broker_transport_options["visibility_timeout"] >= 4 * 3600
    route = celery_app.amqp.router.route
    for name in ("paceforge.initial_sync", "paceforge.sync_coros", "paceforge.sync_garmin"):
        assert route({}, name)["queue"].name == "long", name
    for name in ("paceforge.poll_new_activities", "paceforge.poll_strava_user",
                 "paceforge.sync_activity_from_webhook", "paceforge.strava_deauthorized"):
        assert route({}, name)["queue"].name == "celery", name
    queues = celery_app.amqp.queues
    assert sorted(queues.consume_from) == ["celery", "long"]  # a worker without -Q (as deployed) takes both
    # one binding each: two queues on one routing key would both receive every message
    assert len({(q.exchange.name, q.routing_key) for q in queues.values()}) == len(queues)
