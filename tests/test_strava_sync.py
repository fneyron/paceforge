"""The Strava syncs (audit A1): the hourly round and each athlete's poll, the webhook's events checked with Strava,
the app's quota, a failed first import tried again, one Strava → Activity builder, Activités in the athlete's
weeks and on one clock, the « Synchroniser » cooldown."""
import asyncio
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.crypto import encrypt_secret
from app.database import Base
from app.dependencies import get_current_user
from app.exceptions import StravaAPIError, StravaRateLimitError, StravaTokenError
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
    (so a task can run in a thread, as Celery runs it): an SQLite file, or a schema of its own when the suite runs
    on PostgreSQL (TEST_DATABASE_URL), production's database."""
    import app.database

    url, options, schema = f"sqlite+aiosqlite:///{tmp_path}/tasks.db", {}, None
    server = os.getenv("TEST_DATABASE_URL", "")
    if server.startswith("postgresql"):
        schema = f"a1_tasks_{uuid.uuid4().hex[:12]}"
        url, options = server, {"poolclass": NullPool, "connect_args": {"server_settings": {"search_path": schema}}}
        await _on_server(server, f'CREATE SCHEMA "{schema}"')
    engine = create_async_engine(url, **options)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    @asynccontextmanager
    async def session():
        task_engine = create_async_engine(url, **options)
        try:
            async with async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)() as s:
                yield s
        finally:
            await task_engine.dispose()
    monkeypatch.setattr(app.database, "get_task_session", session)
    yield session
    if schema:
        await _on_server(server, f'DROP SCHEMA "{schema}" CASCADE')


async def _on_server(url: str, statement: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def _own_app(db: AsyncSession, user: User, client_id="4242", sub=None, done=True) -> User:
    user.strava_client_id = client_id
    user.strava_client_secret_encrypted = encrypt_secret("s3cret")
    user.strava_webhook_subscription_id = sub
    user.initial_sync_done = done
    await db.flush()
    return user


async def _athlete(db: AsyncSession, athlete_id: int, **fields) -> User:
    user = User(strava_athlete_id=athlete_id, strava_access_token="at", strava_refresh_token="rt",
                strava_token_expires_at=2147483000, firstname="A", **fields)
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


# ── the token refresh ────────────────────────────────────────────────────────

def _rotating_token_endpoint(monkeypatch, first="rt1") -> set:
    """Strava's /oauth/token: each refresh returns a new pair and the refresh token sent dies at once."""
    valid = {first}

    async def post(self, url, data=None, **kw):
        request = httpx.Request("POST", url)
        sent = data["refresh_token"]
        if sent not in valid:
            return httpx.Response(400, json={"message": "Bad Request"}, request=request)
        new = f"rt{int(sent[2:]) + 1}"
        valid.clear()
        valid.add(new)
        return httpx.Response(200, request=request, json={"access_token": f"at{new[2:]}", "refresh_token": new,
                                                          "expires_at": int(time.time()) + 6 * 3600})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return valid


async def _expiring_athlete(task_db) -> int:
    async with task_db() as db:
        user = await _own_app(db, await _athlete(db, 12345678))
        user.strava_refresh_token, user.strava_token_expires_at = "rt1", int(time.time()) + 60  # refreshed first
        await db.commit()
        return user.id


async def _stored_refresh_token(task_db, uid) -> str | None:
    async with task_db() as db:
        return (await db.get(User, uid)).strava_refresh_token


async def test_a_rotated_refresh_token_outlives_a_failed_poll(task_db, monkeypatch):
    """Review D1: the poll rolled back on a 503 and took the refresh it had just made with it; Strava had already
    invalidated the old refresh token, so the next refresh was refused (400) and the athlete disconnected."""
    from app.tasks.poll_activities import _poll_user

    uid = await _expiring_athlete(task_db)
    valid = _rotating_token_endpoint(monkeypatch)
    status = {"code": 503}

    async def request(self, method, path, **kw):
        return httpx.Response(status["code"], json=[], request=httpx.Request(method, "https://www.strava.com" + path))
    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    with pytest.raises(StravaAPIError):
        await _poll_user(uid)
    assert await _stored_refresh_token(task_db, uid) == "rt2" and valid == {"rt2"}
    async with task_db() as db:  # an hour later, the access token has expired too
        user = await db.get(User, uid)
        user.strava_token_expires_at = int(time.time()) - 10
        await db.commit()
    status["code"] = 200
    assert (await _poll_user(uid)) == {"user_id": uid, "new_activities": 0}
    assert await _stored_refresh_token(task_db, uid) == "rt3"  # still linked


@pytest.mark.parametrize("case", ["gone", "not_his", "still_authorized"])
async def test_a_rotated_refresh_token_outlives_an_early_return(task_db, monkeypatch, case):
    """Review D1: the webhook tasks' early returns (gone, not his, still authorized) closed the session without a
    commit, losing a refresh made on the way (a forged « not_his » event could disconnect an athlete)."""
    from app.tasks import webhook_sync

    uid = await _expiring_athlete(task_db)
    _rotating_token_endpoint(monkeypatch)

    async def request(self, method, path, **kw):
        req = httpx.Request(method, "https://www.strava.com" + path)
        if case == "gone":
            return httpx.Response(404, json={"message": "Record Not Found"}, request=req)
        if case == "not_his":
            return httpx.Response(200, json=_data(1, datetime.now(timezone.utc), athlete_id=999), request=req)
        return httpx.Response(200, json={"id": 12345678}, request=req)
    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    if case == "still_authorized":
        result = await webhook_sync._run_deauthorized(12345678)
    else:
        result = await webhook_sync._run_webhook_sync(1, 12345678)
    assert result["status"] == case and await _stored_refresh_token(task_db, uid) == "rt2"


async def test_a_token_endpoint_failure_is_tried_again_never_taken_for_a_refusal(task_db, monkeypatch):
    """Review D5: a 503 (or any odd answer) of Strava's token endpoint became « reconnect »: the webhook event was
    dropped without a retry (the athlete's 24 h marker kept the round away) and a real revocation was dropped as
    « unverified ». Nothing refused, the link intact: every task now raises, so Celery tries again; the webhook's
    last attempt asks the round to poll the athlete. Only a refusal forgets the link (test_settings_links)."""
    from app.tasks import initial_sync, poll_activities, webhook_sync

    async with task_db() as db:
        user = await _own_app(db, await _athlete(db, 12345678), sub=55)
        user.strava_token_expires_at = int(time.time()) - 60  # expired overnight
        await db.commit()
        uid = user.id

    async def post(self, url, **kw):
        return httpx.Response(503, text="upstream", request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    api = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "request", api)
    for run in (lambda: webhook_sync._run_webhook_sync(1, 12345678), lambda: webhook_sync._run_deauthorized(12345678),
                lambda: poll_activities._poll_user(uid)):
        with pytest.raises(StravaTokenError):
            await run()
    async with task_db() as db:
        (await db.get(User, uid)).initial_sync_done = False
        await db.commit()
    with pytest.raises(StravaTokenError):
        await initial_sync._run_initial_sync(uid)
    async with task_db() as db:
        user = await db.get(User, uid)
        assert user.strava_refresh_token == "rt" and user.strava_credentials_valid and user.strava_athlete_id
        user.initial_sync_done = True
        await db.commit()
    assert api.await_count == 0

    # as Celery runs the webhook and revocation tasks: retried, then the round is asked to poll the athlete (for a
    # revocation, re-review N6: the next token refresh settles it)
    queued = []
    monkeypatch.setattr(poll_activities.poll_strava_user, "delay", lambda u: queued.append(u))
    for task, args in ((webhook_sync.sync_activity_from_webhook, (1, 12345678)),
                       (webhook_sync.strava_deauthorized, (12345678,))):
        strava_state.webhook_reached(uid)
        strava_state.polled(uid)
        assert (await asyncio.to_thread(task.apply, args=args)).get() == {"status": "failed"}
        assert not strava_state.webhook_recent(uid) and not strava_state.polled_recently(uid)
        queued.clear()
        await poll_activities._run_poll()
        strava_state.release(f"poll:{uid}")
        assert queued == [uid], task.name


# ── the first import, one builder, the athlete's zone ────────────────────────

async def test_a_failed_first_import_shows_where_the_watches_errors_do(client: AsyncClient, db_session, test_user,
                                                                       monkeypatch):
    """Review D11: the first import's failure is strava_status' last_error, the key the connection page and the
    app's list of connections already read for the watches: the app's list says « attention », and
    /connections/strava shows the error itself (re-review N2: the watches' note, « Ouvre « Options et aide » pour
    la relancer », offers no relaunch for Strava: the round tries again by itself). Réglages are unchanged."""
    from app.dependencies import get_optional_user
    from tests.test_coros import _link

    monkeypatch.setattr(db_session, "commit", db_session.flush)
    await _own_app(db_session, test_user, done=False)
    overrides = client._transport.app.dependency_overrides
    overrides[get_current_user] = overrides[get_optional_user] = lambda: test_user
    error = "Strava a limité la première synchro ; nouvel essai automatique."
    watches_note = "La synchronisation est à vérifier. Ouvre « Options et aide » pour la relancer."
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        async def shown():
            providers = (await c.get("/mobile/connections?nonce=" + "a" * 64,
                                     headers={"accept": "application/json"})).json()["providers"]
            page = (await c.get("/connections/strava")).text
            return f'<p class="pf-note">{error}</p>' in page, watches_note in page, providers["strava"]
        assert await shown() == (False, False, "connected")
        strava_state.initial_failed(test_user.id, "rate_limited")
        assert await shown() == (True, False, "attention")
        assert "nouvel essai automatique" not in (await c.get("/settings")).text
        # a watch keeps its sentence
        (await _link(db_session, test_user)).last_error = "Previous network failure"
        await db_session.flush()
        assert watches_note in (await c.get("/connections/coros")).text


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


async def test_the_details_a_list_lacks_are_fetched_later_a_few_per_poll(db_session, test_user, task_session,
                                                                         monkeypatch):
    """Review D7: the first import stops asking for details at the app's 80 % (or a 429), and nothing asked later:
    an athlete who connected then kept a history without splits, laps, best efforts. Each poll now completes a few
    activities saved from the list (the newest races and long runs first), within the quota."""
    from app.services.strava_activities import DETAIL_ASKED, DETAIL_BACKLOG
    from app.tasks.initial_sync import _run_initial_sync
    from app.tasks.poll_activities import _poll_user

    await _own_app(db_session, test_user, done=False)
    now = datetime.now(timezone.utc)
    listed = [_data(960_000 + i, now - timedelta(days=i + 1), resource_state=2) for i in range(12)]
    listed[9]["workout_type"] = 1  # a race, older
    listed[10]["distance"] = 45_000.0  # a long run, older still
    monkeypatch.setattr(StravaService, "get_all_activities_since", AsyncMock(return_value=listed))
    asked = []

    async def detail(self, user, sid):
        asked.append(sid)
        if sid == 960_011:
            raise _api_error(404)  # gone meanwhile
        return {**next(d for d in listed if d["id"] == sid), "resource_state": 3, "splits_metric": [{"split": 1}],
                "laps": [{"lap_index": 1}]}
    monkeypatch.setattr(StravaService, "get_activity", detail)
    strava_state.back_off("4242", time.time() + 3600)  # the app's day is past 80 % (other athletes)
    result = await _run_initial_sync(test_user.id)
    assert result["synced"] == 12 and result["enriched"] == 0 and asked == []
    race = await _row(db_session, 960_009)
    race.raw_data = {**race.raw_data, "paceforge_dplus": {"v": 1, "gain": 812.0}}  # its D+, read from its stream
    monkeypatch.setattr(StravaService, "get_recent_activities", AsyncMock(return_value=listed[:5]))
    assert (await _poll_user(test_user.id))["status"] == "backoff" and asked == []
    strava_state.release("backoff:4242")  # the next day
    await _poll_user(test_user.id)
    assert len(asked) == DETAIL_BACKLOG and asked[:2] == [960_009, 960_010]  # the race, the long run, the newest
    for _ in range(3):
        await _poll_user(test_user.id)
    assert sorted(asked) == sorted(d["id"] for d in listed)  # each asked once
    await _poll_user(test_user.id)
    assert len(asked) == len(listed)  # never again
    rows = (await db_session.execute(select(Activity).where(Activity.user_id == test_user.id))).scalars().all()
    assert all(r.splits_metric and r.laps for r in rows if r.strava_activity_id != 960_011)
    assert (await _row(db_session, 960_011)).raw_data[DETAIL_ASKED]
    race = await _row(db_session, 960_009)
    assert race.raw_data["resource_state"] == 3 and race.raw_data["paceforge_dplus"]["gain"] == 812.0  # kept


async def test_the_athletes_zone_follows_a_clear_majority_of_their_activities(db_session, test_user):
    """users.timezone from the activities' Strava zone (« (GMT+01:00) Europe/Paris »), read again at each sync but
    switched only by a clear majority: 20 of the last 30 activities with a zone. Review D3: a zone learnt during a
    trip stuck for good (a non-default zone was never read again)."""
    from app.services.strava_activities import adopt_zone

    t, sid = datetime(2026, 1, 1, 7, tzinfo=timezone.utc), [790_000]

    async def runs(label, n, every=timedelta(days=1)):
        nonlocal t
        for _ in range(n):
            a = _run(test_user, sid[0], t)
            a.raw_data = {"timezone": label} if label else {}
            db_session.add(a)
            sid[0] += 1
            t += every
        await db_session.flush()
        await adopt_zone(db_session, test_user)
        return test_user.timezone
    paris, canary, ny = "(GMT+01:00) Europe/Paris", "(GMT+00:00) Atlantic/Canary", "(GMT-05:00) America/New_York"
    assert await runs(ny, 12) is None  # too few to tell: left as it is (the default zone applies)
    assert await runs(paris, 30) == "Europe/Paris"
    assert await runs(canary, 16, timedelta(hours=12)) == "Europe/Paris"  # a 2-week camp, twice a day: 16 of 30
    assert await runs(paris, 60) == "Europe/Paris"  # back home
    assert await runs(None, 40) == "Europe/Paris"  # activities without a zone count for nothing
    assert await runs(ny, 19) == "Europe/Paris" and await runs(ny, 1) == "America/New_York"  # a move: 20 of 30
    test_user.timezone = "Asia/Tokyo"  # set by the phone meanwhile
    await adopt_zone(db_session, test_user)
    assert test_user.timezone == "America/New_York"  # the majority is the only override
    test_user.timezone = "Asia/Tokyo"
    await runs(paris, 15)  # no clear majority any more (15 Paris, 15 New York): kept
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
    athletes). Now hourly, and only for those the webhook has not reached for 24 h (all are polled once a day all
    the same); one queued poll per athlete."""
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
    strava_state.polled(test_user.id)
    strava_state.back_off("902", time.time() + 600)
    assert (await poll_activities._run_poll())["users_checked"] == 1 and queued == [quiet.id]
    assert (await poll_activities._run_poll())["users_checked"] == 0  # still queued (claimed): never twice
    strava_state.release(f"poll:{quiet.id}")  # its poll ran
    strava_state.release(f"polled:{test_user.id}")  # a day after its last poll: polled although reached
    strava_state.release("backoff:902")
    queued.clear()
    await poll_activities._run_poll()
    assert sorted(queued) == sorted([test_user.id, quiet.id, backing.id])

    # a poll queued before the backoff began asks nothing
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(StravaService, "get_recent_activities", fetch)
    strava_state.back_off("901", time.time() + 600)
    assert (await poll_activities._poll_user(quiet.id))["status"] == "backoff" and fetch.await_count == 0


async def test_an_event_lost_is_recovered_by_the_daily_poll(db_session, test_user, task_session, monkeypatch):
    """Review D4: an athlete uploading every day kept their webhook marker fresh, so an event lost once (the web
    restarting during a deploy, the broker down at the enqueue) was never polled in. Every linked athlete is now
    polled at least once a day, the webhook's included."""
    from app.tasks import poll_activities, webhook_sync

    await _own_app(db_session, test_user, sub=55)
    now = datetime.now(timezone.utc)
    lost = _data(980_000, now - timedelta(days=7))  # its event never reached PaceForge
    uploads = [lost]
    queued = []
    monkeypatch.setattr(poll_activities.poll_strava_user, "delay", lambda uid: queued.append(uid))
    monkeypatch.setattr(StravaService, "get_recent_activities",
                        AsyncMock(side_effect=lambda user, per_page=30, page=1: uploads[::-1][:per_page]))
    polls = 0
    for day in range(1, 4):  # an upload a day, its event handled; 24 rounds a day
        detail = _data(980_000 + day, now - timedelta(days=7 - day))
        uploads.append(detail)
        monkeypatch.setattr(StravaService, "get_activity", AsyncMock(return_value=detail))
        assert (await webhook_sync._run_webhook_sync(detail["id"], test_user.strava_athlete_id))["status"] == "saved"
        for _ in range(24):
            queued.clear()
            await poll_activities._run_poll()
            for uid in queued:
                await poll_activities._poll_user(uid)
                strava_state.release(f"poll:{uid}")
                polls += 1
        strava_state.release(f"polled:{test_user.id}")  # the day goes by
    assert await _row(db_session, 980_000) is not None and polls == 3  # once a day, not hourly


async def test_the_round_starves_nobody_and_spares_a_live_apps_quiet_athletes(db_session, task_session, shared_app,
                                                                             client: AsyncClient, queued, monkeypatch):
    """Review D9: the athletes a round queued after the app's 80 % mark were skipped, and queued in the same order
    by the next round: the same ones were never polled. And a quiet athlete cost a read every round although the
    app's webhook worked (24 a day each: the shared app's 80 % of 1,000 reached with ~33 of them). The round now
    starts with the longest without news, and an app whose webhook brought an event within 24 h has its quiet
    athletes polled once a day."""
    from collections import Counter

    from app.tasks import poll_activities

    users = [await _athlete(db_session, 8000 + i, strava_client_id="90001", initial_sync_done=True)
             for i in range(5)]
    strava_state.mark("shared_sub", 24 * 3600, "123456")  # the shared app's subscription, read from Strava before
    polled, window = Counter(), {"reads": 0}

    async def fetch(self, user, per_page=30, page=1):
        polled[user.strava_athlete_id] += 1
        window["reads"] += 1
        if window["reads"] >= 3:  # the app's 80 % mark, reached after 3 polls
            strava_state.back_off("90001", time.time() + 600)
        return []
    monkeypatch.setattr(StravaService, "get_recent_activities", fetch)
    polls = []
    monkeypatch.setattr(poll_activities.poll_strava_user, "delay", lambda uid: polls.append(uid))

    async def round_():
        strava_state.release("backoff:90001")  # the window has turned by the next round
        window["reads"] = 0
        polls.clear()
        await poll_activities._run_poll()
        for uid in polls:
            await poll_activities._poll_user(uid)
            strava_state.release(f"poll:{uid}")
    await round_()
    await round_()
    assert all(polled[u.strava_athlete_id] for u in users), polled

    # the app's webhook works (an event for one athlete): the others, quiet, are polled once a day
    strava_state.release("backoff:90001")
    r = await client.post("/webhook/strava", json={
        "object_type": "activity", "object_id": 1, "aspect_type": "create", "owner_id": 8000,
        "subscription_id": 123456, "event_time": 1, "updates": {}})
    assert r.json()["status"] == "ok" and strava_state.app_webhook_alive("90001")
    polled.clear()
    for _ in range(24):
        await round_()
    assert sum(polled.values()) == 0  # all polled in the last 24 h already
    for u in users:
        strava_state.release(f"polled:{u.id}")  # a day later
    for _ in range(24):
        await round_()
    assert polled == Counter({u.strava_athlete_id: 1 for u in users})


async def test_a_failed_first_import_is_queued_again_later_and_said(task_db, monkeypatch):
    """Base code: three failures (a 429…) left initial_sync_done False, which the poll's filter excluded forever,
    Réglages still saying « Première synchro en cours… ». Now the failure is kept (strava_status: last_error), and
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
    assert status["connected"] and status["last_error"] == "Strava a limité la première synchro ; nouvel essai automatique."
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
    assert strava_state.initial_failure(uid) is None and strava_status(done)["last_error"] is None
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


# ── the webhook: known subscriptions only, every event checked with Strava ───

def _event(user, kind="create", sub=55, obj=1, **updates) -> dict:
    return {"object_type": "athlete" if kind == "deauth" else "activity", "object_id": obj,
            "aspect_type": "update" if kind == "deauth" else kind, "owner_id": user.strava_athlete_id,
            "subscription_id": sub, "event_time": 1_760_000_000,
            "updates": {"authorized": "false"} if kind == "deauth" else updates}


@pytest.fixture
def queued(monkeypatch):
    from app.tasks import webhook_sync

    calls = []
    # a message as sent: its positional arguments, then its keyword arguments if any
    monkeypatch.setattr(webhook_sync.sync_activity_from_webhook, "delay",
                        lambda *a, **k: calls.append((*a, k) if k else a))
    monkeypatch.setattr(webhook_sync.strava_deauthorized, "delay",
                        lambda *a, **k: calls.append(("deauth", *a, k) if k else ("deauth", *a)))
    return calls


async def test_the_webhook_acts_only_on_subscriptions_it_knows(client: AsyncClient, db_session, test_user,
                                                                queued, monkeypatch):
    """Base code: any POST was queued, whatever its subscription (anyone knowing an athlete id could make PaceForge
    call Strava with that athlete's token)."""
    await _own_app(db_session, test_user, sub=55)
    for sub in (999, 56):
        r = await client.post("/webhook/strava", json=_event(test_user, sub=sub))
        assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert queued == []
    assert (await client.post("/webhook/strava", json=_event(test_user, sub=55, obj=7))).json()["status"] == "ok"
    assert queued == [(7, test_user.strava_athlete_id)]  # a create as the previous worker takes it

    # the shared app: its subscription (STRAVA_SUBSCRIPTION_ID), for its athletes only
    monkeypatch.setattr(settings, "STRAVA_SHARED_APP_ENABLED", True)
    monkeypatch.setattr(settings, "STRAVA_CLIENT_ID", "90001")
    monkeypatch.setattr(settings, "STRAVA_CLIENT_SECRET", "shared-secret")
    monkeypatch.setenv("STRAVA_SUBSCRIPTION_ID", "777")
    shared = await _athlete(db_session, 5001, strava_client_id="90001", initial_sync_done=True)
    queued.clear()
    assert (await client.post("/webhook/strava", json=_event(shared, sub=55))).json()["status"] == "ignored"
    assert (await client.post("/webhook/strava", json=_event(test_user, sub=777))).json()["status"] == "ignored"
    assert (await client.post("/webhook/strava", json=_event(shared, sub=777, obj=8))).json()["status"] == "ok"
    assert queued == [(8, 5001)]
    # the owner's own app became the shared one: its stored subscription is the shared app's
    monkeypatch.delenv("STRAVA_SUBSCRIPTION_ID")
    owner = await _athlete(db_session, 5002)
    await _own_app(db_session, owner, client_id="90001", sub=4321)
    queued.clear()
    assert (await client.post("/webhook/strava", json=_event(shared, sub=4321, obj=9))).json()["status"] == "ok"
    assert queued == [(9, 5001)]
    # unknown athletes, unhandled objects, bad payloads: a 200 all the same (Strava retries anything else)
    stranger = User(strava_athlete_id=6000)
    for body in (_event(stranger, sub=55), {**_event(test_user), "object_type": "club"}, {"bad": 1}):
        r = await client.post("/webhook/strava", json=body)
        assert r.status_code == 200 and r.json()["status"] == "ignored"
    r = await client.post("/webhook/strava", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json()["status"] == "ignored"


@pytest.fixture
def shared_app(monkeypatch):
    monkeypatch.setattr(settings, "STRAVA_SHARED_APP_ENABLED", True)
    monkeypatch.setattr(settings, "STRAVA_CLIENT_ID", "90001")
    monkeypatch.setattr(settings, "STRAVA_CLIENT_SECRET", "shared-secret")
    monkeypatch.delenv("STRAVA_SUBSCRIPTION_ID", raising=False)


def _push_subscriptions(monkeypatch, answer):
    """Strava's GET /push_subscriptions for the shared app (an app-level request); `answer`: its subscriptions,
    or an HTTP status for a failure."""
    calls = []

    async def get(self, url, params=None, **kw):
        calls.append(params)
        request = httpx.Request("GET", url)
        if isinstance(answer, int):
            return httpx.Response(answer, text="busy", request=request)
        return httpx.Response(200, json=answer, request=request)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    return calls


async def test_the_shared_apps_subscription_is_learnt_from_strava(client: AsyncClient, db_session, task_session,
                                                                 shared_app, queued, monkeypatch):
    """Review D2: without STRAVA_SUBSCRIPTION_ID every shared-app event was dropped (the poll then brought the
    activity from the list, without its splits). Now the shared app's subscription is read from Strava (GET
    /push_subscriptions, kept, read again every 12 h), never during the webhook's 2-s answer: an event of an id not
    known yet is queued with it, and its task confirms it with Strava before anything else. A forged event costs
    that app-level lookup at most, never a request with the athlete's token."""
    from app.tasks import poll_activities
    from app.tasks.webhook_sync import _run_webhook_sync

    athlete = await _athlete(db_session, 5001, strava_client_id="90001", initial_sync_done=True)
    lookups = _push_subscriptions(monkeypatch, [{"id": 123456, "callback_url": "https://paceforge.fr/webhook/strava"}])
    detail = AsyncMock(return_value=_data(777, datetime.now(timezone.utc), athlete_id=5001,
                                          splits_metric=[{"split": 1}], laps=[{"lap_index": 1}]))
    monkeypatch.setattr(StravaService, "get_activity", detail)

    r = await client.post("/webhook/strava", json=_event(athlete, sub=123456, obj=777))
    assert r.json()["status"] == "ok" and queued == [(777, 5001, {"subscription_id": 123456})] and lookups == []
    assert (await _run_webhook_sync(777, 5001, "create", 123456))["status"] == "saved"
    row = await _row(db_session, 777)
    assert row.splits_metric == [{"split": 1}] and row.laps == [{"lap_index": 1}]
    assert lookups == [{"client_id": "90001", "client_secret": "shared-secret"}]

    # now known: the webhook checks the shared app's events itself
    queued.clear()
    assert (await client.post("/webhook/strava", json=_event(athlete, sub=123456, obj=778))).json()["status"] == "ok"
    assert queued == [(778, 5001)]
    # another id right after a lookup: ignored at once; a forged event never gets to the athlete's token
    assert (await client.post("/webhook/strava", json=_event(athlete, sub=999, obj=779))).json()["status"] == "ignored"
    detail.reset_mock()
    assert (await _run_webhook_sync(780, 5001, "create", 999))["status"] == "unknown_subscription"
    assert detail.await_count == 0 and len(lookups) == 1
    # 10 min later, another id asks Strava once more (the subscription may have been made again), queued to check
    strava_state.release("shared_sub:recheck")
    queued.clear()
    assert (await client.post("/webhook/strava", json=_event(athlete, sub=999, obj=781))).json()["status"] == "ok"
    assert queued == [(781, 5001, {"subscription_id": 999})]
    assert (await _run_webhook_sync(781, 5001, "create", 999))["status"] == "unknown_subscription"
    assert detail.await_count == 0 and len(lookups) == 2
    # STRAVA_SUBSCRIPTION_ID, when set, decides without asking Strava
    monkeypatch.setenv("STRAVA_SUBSCRIPTION_ID", "999")
    queued.clear()
    assert (await client.post("/webhook/strava", json=_event(athlete, sub=999, obj=782))).json()["status"] == "ok"
    assert (await client.post("/webhook/strava", json=_event(athlete, sub=123456))).json()["status"] == "ignored"
    assert queued == [(782, 5001)] and len(lookups) == 2

    # 12 h later the round reads it again; Strava failing, the id known is kept (re-review N3: a real event of the
    # shared app is never dropped for an id gone stale)
    monkeypatch.delenv("STRAVA_SUBSCRIPTION_ID")
    strava_state.release("shared_sub:fresh")
    lookups = _push_subscriptions(monkeypatch, 503)
    monkeypatch.setattr(poll_activities.poll_strava_user, "delay", lambda uid: None)
    await poll_activities._run_poll()
    await poll_activities._run_poll()  # a failure: not asked again before 5 min
    assert len(lookups) == 1 and strava_state.shared_subscription() == (True, 123456)
    queued.clear()
    assert (await client.post("/webhook/strava", json=_event(athlete, sub=123456, obj=784))).json()["status"] == "ok"
    assert queued == [(784, 5001)]
    strava_state.release("shared_sub:failed")
    lookups = _push_subscriptions(monkeypatch, [{"id": 123456}])
    await poll_activities._run_poll()
    assert len(lookups) == 1 and strava_state.shared_subscription_fresh()
    # never known, Strava failing: Celery asks again, the athlete untouched meanwhile
    strava_state.release("shared_sub")
    lookups = _push_subscriptions(monkeypatch, 503)
    with pytest.raises(StravaAPIError):
        await _run_webhook_sync(783, 5001, "create", 123456)
    assert detail.await_count == 0 and len(lookups) == 1


async def test_the_web_start_reads_the_shared_subscription_without_waiting(shared_app, monkeypatch):
    """Re-review N5: on the first deploy the shared app's id is not known yet, so its first creates went out with
    subscription_id=, which the previous worker (still running while the new web starts) refuses. The web's start
    now reads it from Strava in the background: never waited for, failing silently."""
    from app.main import create_app

    app = create_app()
    strava_answers = asyncio.Event()
    lookups = []

    async def get(self, url, params=None, **kw):
        lookups.append(url)
        await strava_answers.wait()  # Strava slow to answer
        return httpx.Response(200, json=[{"id": 123456}], request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    async with app.router.lifespan_context(app):  # started without waiting for Strava
        assert strava_state.shared_subscription() == (False, None)
        strava_answers.set()
        for _ in range(200):
            if strava_state.shared_subscription()[0]:
                break
            await asyncio.sleep(0.01)
        assert strava_state.shared_subscription() == (True, 123456) and len(lookups) == 1
    for key in ("shared_sub", "shared_sub:fresh", "shared_sub:starting", "shared_sub:recheck"):
        strava_state.release(key)

    async def unreachable(self, url, params=None, **kw):
        raise httpx.ConnectError("Strava unreachable")
    monkeypatch.setattr(httpx.AsyncClient, "get", unreachable)
    async with app.router.lifespan_context(app):  # Strava unreachable: the start goes on, silently
        await asyncio.sleep(0.05)
    assert strava_state.shared_subscription() == (False, None)


async def test_a_shared_event_given_up_on_asks_the_round_to_poll(task_db, shared_app, monkeypatch):
    """Re-review N3, N4: a shared-app event given up on was dropped without the round's poll when its subscription
    was passed, whether Strava could not say which subscription the app has, or confirmed it and then kept failing
    on the activity. The round now polls the athlete in both cases; an event Strava says is another
    subscription's is still dropped, without a poll."""
    from app.tasks import poll_activities
    from app.tasks.webhook_sync import sync_activity_from_webhook

    async with task_db() as db:
        uid = (await _athlete(db, 5001, strava_client_id="90001", initial_sync_done=True)).id
        await db.commit()
    strava_state.app_webhook_reached("90001")  # the app's webhook works: a daily poll only

    async def event(sub, obj=777):  # as Celery runs it, retries included
        strava_state.webhook_reached(uid)
        strava_state.polled(uid)  # polled this morning
        return (await asyncio.to_thread(sync_activity_from_webhook.apply, args=(obj, 5001),
                                        kwargs={"subscription_id": sub})).get()

    async def polled_next() -> bool:
        queued = []
        monkeypatch.setattr(poll_activities.poll_strava_user, "delay", lambda u: queued.append(u))
        await poll_activities._run_poll()
        for u in queued:
            strava_state.release(f"poll:{u}")
        return queued == [uid]

    _push_subscriptions(monkeypatch, 503)  # never known, and Strava cannot say
    assert await event(123456) == {"status": "failed"} and await polled_next()
    strava_state.release("shared_sub:failed")
    _push_subscriptions(monkeypatch, [{"id": 123456}])  # confirmed, then the activity's request keeps failing
    monkeypatch.setattr(httpx.AsyncClient, "request", AsyncMock(return_value=httpx.Response(
        503, text="busy", request=httpx.Request("GET", "https://www.strava.com/api/v3/activities/777"))))
    assert await event(123456) == {"status": "failed"} and strava_state.shared_subscription() == (True, 123456)
    assert await polled_next()
    assert await event(999, obj=778) == {"status": "unknown_subscription"} and not await polled_next()


async def test_the_webhook_queues_edits_deletions_and_revocations(client: AsyncClient, db_session, test_user,
                                                                   queued):
    """Base code: only « create » was queued; edits, deletions and revocations were dropped. A create keeps the
    message the previous worker takes (review D8: a deploy starts the new web while the old worker still runs)."""
    import inspect

    def previous_worker(activity_id, owner_id):  # the task's arguments before this branch (self aside)
        pass
    await _own_app(db_session, test_user, sub=55)
    for kind in ("create", "update", "delete", "deauth"):
        assert (await client.post("/webhook/strava", json=_event(test_user, kind, obj=3))).json()["status"] == "ok"
    athlete = test_user.strava_athlete_id
    assert queued == [(3, athlete), (3, athlete, "update"), (3, athlete, "delete"), ("deauth", athlete)]
    inspect.signature(previous_worker).bind(*queued[0])
    queued.clear()
    athlete_update = {**_event(test_user, "deauth"), "updates": {"authorized": "true"}}
    assert (await client.post("/webhook/strava", json=athlete_update)).json()["status"] == "ignored"
    assert queued == []


async def test_the_webhook_never_saves_another_athletes_activity(db_session, test_user, task_session, monkeypatch):
    """Base code: the fetched activity was saved whoever it belonged to (a forged event could name another
    athlete's public activity)."""
    from app.tasks.webhook_sync import _run_webhook_sync

    await _own_app(db_session, test_user, sub=55)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(StravaService, "get_activity",
                        AsyncMock(return_value=_data(720_001, now, athlete_id=424242)))
    assert (await _run_webhook_sync(720_001, test_user.strava_athlete_id))["status"] == "not_his"
    assert await _row(db_session, 720_001) is None and not strava_state.webhook_recent(test_user.id)

    detail = _data(720_002, now, calories=640.0, laps=[{"lap_index": 1}], timezone="(GMT+09:00) Asia/Seoul")
    monkeypatch.setattr(StravaService, "get_activity", AsyncMock(return_value=detail))
    assert (await _run_webhook_sync(720_002, test_user.strava_athlete_id))["status"] == "saved"
    row = await _row(db_session, 720_002)
    assert row.calories == 640.0 and row.laps == [{"lap_index": 1}]  # the one builder: what the detail has
    assert strava_state.webhook_recent(test_user.id)  # the round leaves this athlete to the webhook for 24 h
    assert test_user.last_activity_poll_at is not None


async def test_an_edit_on_strava_renames_the_activity(db_session, test_user, task_session, monkeypatch):
    from app.tasks.webhook_sync import _run_webhook_sync

    await _own_app(db_session, test_user, sub=55)
    now = datetime.now(timezone.utc)
    db_session.add(Activity(user_id=test_user.id, strava_activity_id=730_001, sport_type="Run", name="Morning Run",
                            start_date=now, distance=10_000, moving_time=3600, elapsed_time=3700, raw_data={}))
    await db_session.flush()
    fetch = AsyncMock(return_value=_data(730_001, now, name="Tour du Lac", sport_type="TrailRun",
                                         splits_metric=[{"split": 1}]))
    monkeypatch.setattr(StravaService, "get_activity", fetch)
    # what the event says is not trusted: the name and sport are Strava's own, read with the athlete's token
    result = await _run_webhook_sync(730_001, test_user.strava_athlete_id, "update")
    row = await _row(db_session, 730_001)
    assert result == {"status": "updated", "name": "Tour du Lac"} and row.sport_type == "TrailRun"
    assert row.splits_metric == [{"split": 1}]  # the detail read anyway is kept (review D7)
    # an edit of an activity never received brings it
    fetch.return_value = _data(730_002, now, name="Rattrapée")
    assert (await _run_webhook_sync(730_002, test_user.strava_athlete_id, "update"))["status"] == "saved"
    # gone from Strava meanwhile, or not visible: final, not tried again
    for status in (403, 404):
        fetch.side_effect = _api_error(status)
        assert (await _run_webhook_sync(730_003, test_user.strava_athlete_id, "create"))["status"] == "gone"


async def test_a_rename_keeps_what_a_watch_brought(db_session, test_user, task_session, monkeypatch):
    """Re-review N1: the edit's detail (apply_detail) wrote None over what COROS had brought to the merged row
    (merge_twins keeps the watch's power, calories, laps when Strava's row has none). Only what the detail has is
    set now."""
    from app.tasks.webhook_sync import _run_webhook_sync

    await _own_app(db_session, test_user, sub=55)
    now = datetime.now(timezone.utc)
    coros_laps = [{"lap_index": 1, "source": "coros"}]
    row = Activity(user_id=test_user.id, strava_activity_id=990_001, coros_activity_id=55_001, sport_type="Run",
                   name="Morning Run", start_date=now, distance=10_000, moving_time=3000, elapsed_time=3100,
                   max_watts=410.0, weighted_average_watts=260.0, calories=640.0, laps=coros_laps,
                   raw_data={"resource_state": 3})
    db_session.add(row)
    await db_session.flush()
    detail = _data(990_001, now, name="Footing du lac", resource_state=3, splits_metric=[{"split": 1}])
    monkeypatch.setattr(StravaService, "get_activity", AsyncMock(return_value=detail))  # no power, calories, laps
    assert (await _run_webhook_sync(990_001, test_user.strava_athlete_id, "update"))["status"] == "updated"
    assert row.name == "Footing du lac" and row.splits_metric == [{"split": 1}]
    assert (row.max_watts, row.weighted_average_watts, row.calories, row.laps) == (410.0, 260.0, 640.0, coros_laps)
    assert row.coros_activity_id == 55_001


async def test_a_deletion_on_strava_is_checked_then_applied(db_session, test_user, task_session, monkeypatch):
    from app.tasks.webhook_sync import _run_webhook_sync

    await _own_app(db_session, test_user, sub=55)
    now = datetime.now(timezone.utc)

    def act(sid, **ids):
        return Activity(user_id=test_user.id, strava_activity_id=sid, sport_type="Run", name="Footing",
                        start_date=now, distance=8000, moving_time=2400, elapsed_time=2500, raw_data={}, **ids)
    strava_only, watched = act(740_001), act(740_002, garmin_activity_id=99_001)
    db_session.add_all([strava_only, watched])
    await db_session.flush()
    athlete = test_user.strava_athlete_id
    # still on Strava: a forged (or stale) deletion changes nothing
    monkeypatch.setattr(StravaService, "get_activity", AsyncMock(return_value=_data(740_001, now)))
    assert (await _run_webhook_sync(740_001, athlete, "delete"))["status"] == "still_on_strava"
    assert await _row(db_session, 740_001) is not None
    monkeypatch.setattr(StravaService, "get_activity", AsyncMock(side_effect=_api_error(404)))
    assert (await _run_webhook_sync(740_001, athlete, "delete"))["status"] == "deleted"
    assert await _row(db_session, 740_001) is None
    assert (await _run_webhook_sync(740_002, athlete, "delete"))["status"] == "unlinked"
    await db_session.refresh(watched)
    assert watched.strava_activity_id is None and watched.garmin_activity_id == 99_001  # the watch's outing stays
    assert (await _run_webhook_sync(740_003, athlete, "delete"))["status"] == "unknown"
    # another error is not a deletion: Celery tries again
    db_session.add(act(740_004))
    await db_session.flush()
    monkeypatch.setattr(StravaService, "get_activity", AsyncMock(side_effect=_api_error(500)))
    with pytest.raises(StravaAPIError):
        await _run_webhook_sync(740_004, athlete, "delete")


async def test_a_revocation_on_strava_forgets_the_link_once_strava_confirms_it(db_session, test_user, task_session,
                                                                               monkeypatch):
    """Base code: « authorized: false » was dropped, the athlete's tokens kept. Now, once Strava refuses the access,
    the tokens and the athlete id go; the activities stay (privacy policy: deleting the account deletes them)."""
    from app.tasks.webhook_sync import _run_deauthorized

    await _own_app(db_session, test_user, sub=55)
    db_session.add(Activity(user_id=test_user.id, strava_activity_id=750_001, sport_type="Run", name="Footing",
                            start_date=datetime.now(timezone.utc), distance=8000, moving_time=2400,
                            elapsed_time=2500, raw_data={}))
    await db_session.flush()
    athlete = test_user.strava_athlete_id
    monkeypatch.setattr(StravaService, "get_athlete", AsyncMock(return_value={"id": athlete}))
    assert (await _run_deauthorized(athlete))["status"] == "still_authorized"  # a forged event
    assert test_user.strava_access_token == "test_access_token" and test_user.strava_athlete_id == athlete

    monkeypatch.setattr(StravaService, "get_athlete", AsyncMock(side_effect=_api_error(401)))
    assert (await _run_deauthorized(athlete))["status"] == "forgotten"
    assert test_user.strava_access_token is None and test_user.strava_refresh_token is None
    assert test_user.strava_athlete_id is None and test_user.initial_sync_done is False
    assert test_user.strava_client_id == "4242" and test_user.strava_webhook_subscription_id == 55  # the app's
    assert await _row(db_session, 750_001) is not None
    assert not strava_status(test_user)["connected"]


def test_the_verify_tokens_are_never_logged(caplog):
    from app.routers import webhook

    with caplog.at_level(logging.INFO, logger="app.routers.webhook"):
        assert webhook._is_valid_verify_token(settings.STRAVA_WEBHOOK_VERIFY_TOKEN)
    secret = "paceforge-1234567890abcdef-secret"

    async def call():
        await webhook.strava_webhook_validation(mode="subscribe", challenge="c", verify_token=secret)
        await webhook.strava_webhook_validation(mode="subscribe", challenge="c",
                                                verify_token=settings.STRAVA_WEBHOOK_VERIFY_TOKEN)
    with caplog.at_level(logging.INFO, logger="app.routers.webhook"):
        asyncio.run(call())
    text = caplog.text
    assert "Invalid webhook validation" in text and "Strava webhook validated" in text
    assert secret[:12] not in text and settings.STRAVA_WEBHOOK_VERIFY_TOKEN not in text


# ── Activités: the athlete's weeks, one clock, « Synchroniser » ──────────────

async def test_every_sync_saves_an_activity_alike(db_session, test_user, task_session, monkeypatch):
    """Base code: four builders, each with its own fields (the poll dropped laps, calories, power, best efforts…)."""
    from app.services.strava_activities import activity_from_strava
    from app.tasks.initial_sync import _run_initial_sync
    from app.tasks.poll_activities import _poll_user
    from app.tasks.webhook_sync import _run_webhook_sync

    await _own_app(db_session, test_user, done=False)
    now = datetime.now(timezone.utc)
    summary = dict(average_cadence=88.0, max_heartrate=180.0, average_heartrate=150.0, max_speed=5.0,
                   average_speed=3.0, average_watts=180.0)  # in the list too
    extra = dict(summary, calories=640.0, max_watts=300.0, weighted_average_watts=210.0, suffer_score=40,
                 laps=[{"lap_index": 1}], splits_metric=[{"split": 1}], best_efforts=[{"name": "1k"}])
    fields = [c for c in Activity.__table__.columns.keys()
              if c not in {"id", "created_at", "strava_activity_id", "raw_data", "name"}]

    def saved(row: Activity) -> dict:
        return {c: getattr(row, c) for c in fields}
    model = saved(activity_from_strava(_data(1, now, **extra), test_user.id))
    model["start_date"] = model["start_date"].replace(tzinfo=None)

    async def check(sid):
        row = await _row(db_session, sid)
        got = saved(row)
        got["start_date"] = got["start_date"].replace(tzinfo=None)
        assert got == model, sid

    # the first import (its detail completes the list's row)
    monkeypatch.setattr(StravaService, "get_all_activities_since",
                        AsyncMock(return_value=[_data(770_001, now, **summary)]))
    monkeypatch.setattr(StravaService, "get_activity",
                        AsyncMock(side_effect=lambda user, sid: _data(sid, now, **extra)))
    await _run_initial_sync(test_user.id)
    await check(770_001)
    # the poll and « Synchroniser » (the list), the webhook (the detail)
    monkeypatch.setattr(StravaService, "get_recent_activities", AsyncMock(return_value=[_data(770_002, now, **extra)]))
    await _poll_user(test_user.id)
    await check(770_002)
    from app.routers.dashboard import _sync_recent_activities

    monkeypatch.setattr(StravaService, "get_recent_activities", AsyncMock(return_value=[_data(770_003, now, **extra)]))
    assert await _sync_recent_activities(test_user, db_session) == 1
    await check(770_003)
    await _run_webhook_sync(770_004, test_user.strava_athlete_id)
    await check(770_004)


class _Frozen(datetime):
    at: datetime = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.at.astimezone(tz) if tz else cls.at.replace(tzinfo=None)


@pytest.fixture
def frozen(monkeypatch):
    """Activités' wall clock (dashboard reads datetime.now)."""
    from app.routers import dashboard

    monkeypatch.setattr(dashboard, "datetime", _Frozen)
    monkeypatch.setattr(_Frozen, "at", _Frozen.at)
    return _Frozen


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        yield c


def _weeks(html: str) -> list[tuple[str, str, list[str]]]:
    """(key, label, the rows' « day dd/mm ») of each week heading."""
    out = []
    for key, label, body in re.findall(r'<section id="week-([\d-]+)">\s*<div class="pf-week-heading">\s*'
                                       r'<h2 class="pf-h3">([^<]+)</h2>(.*?)</section>', html, re.S):
        out.append((key, label.strip(), re.findall(r'<span class="pf-item-sub block">(\w+ \d\d/\d\d)', body)))
    return out


async def test_activites_weeks_are_the_athletes(as_user, db_session, test_user, frozen):
    """Base code: UTC Mondays — a run at 00:30 on Monday in Paris (Sunday 22:30 UTC) was in the week before, its row
    saying « dim 04/10 »."""
    db_session.add_all([
        _run(test_user, 780_001, datetime(2026, 10, 4, 22, 30, tzinfo=timezone.utc), "Minuit lundi"),
        _run(test_user, 780_002, datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc), "Dimanche soir"),
        _run(test_user, 780_003, datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc)),
    ])
    await db_session.flush()
    frozen.at = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)  # Thursday
    assert _weeks((await as_user.get("/activities")).text) == [
        ("2026-10-05", "Cette semaine", ["mar 06/10", "lun 05/10"]),
        ("2026-09-28", "Semaine dernière", ["dim 04/10"]),
    ]
    # Monday 00:30 in Paris: already the new week (« today » too)
    frozen.at = datetime(2026, 10, 4, 22, 45, tzinfo=timezone.utc)
    assert [w[:2] for w in _weeks((await as_user.get("/activities")).text)] == [
        ("2026-10-05", "Cette semaine"), ("2026-09-28", "Semaine dernière")]
    # an athlete in New York: their own Mondays
    test_user.timezone = "America/New_York"
    await db_session.flush()
    frozen.at = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
    weeks = _weeks((await as_user.get("/activities")).text)
    assert weeks == [("2026-10-05", "Cette semaine", ["mar 06/10"]),
                     ("2026-09-28", "Semaine dernière", ["dim 04/10", "dim 04/10"])]
    # the « load more » partial reads the same weeks
    part = (await as_user.get("/partials/activities?page=1")).text
    assert [w[:2] for w in _weeks(part)] == [w[:2] for w in weeks]


async def test_synchroniser_asks_strava_once_a_minute(as_user, db_session, test_user, monkeypatch):
    """Base code: every click asked Strava (up to 5 pages each); `last_strava_sync` was written to the session and
    never read."""
    await _own_app(db_session, test_user)
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(StravaService, "get_recent_activities", fetch)
    for _ in range(3):
        r = await as_user.post("/api/sync")
        assert "Déjà à jour" in r.text
    assert fetch.await_count == 1
    headers = {"accept": "application/json", "x-paceforge-refresh": "1"}
    assert (await as_user.post("/api/sync", headers=headers)).json() == {"received": 0, "errors": []}
    assert fetch.await_count == 1
    assert "last_strava_sync" not in str(as_user.cookies.get("paceforge_session"))
    # a failed attempt can be tried again at once (last: its request rolls the test's session back, and the fixture
    # rows with it, so nothing is saved after it)
    strava_state.release(f"manual:{test_user.id}")
    fetch.side_effect = httpx.ConnectError("down")
    assert (await as_user.post("/api/sync", headers=headers)).json()["errors"]
    fetch.side_effect = None
    assert (await as_user.post("/api/sync", headers=headers)).json() == {"received": 0, "errors": []}
    assert fetch.await_count == 3  # asked again at once


async def test_synchroniser_says_what_it_saved_and_when_one_is_running(as_user, db_session, test_user, monkeypatch):
    """Review D10: (a) a sync that saved page 1 then failed on page 2 answered « Déjà à jour » (the rows were saved;
    the base said « 30 nouvelle(s) activité(s) importée(s). » and reloaded); (b) a click while another sync of the
    athlete runs (another tab) answered « aucune nouvelle activité ». Now (a) counts what was saved, (b) says
    « Synchro déjà en cours. » (the pull-to-refresh, which only reads errors, gets none), and a sync that failed
    before saving anything says so."""
    from sqlalchemy import func

    await _own_app(db_session, test_user)
    now = datetime.now(timezone.utc)

    async def fetch(self, user, per_page=30, page=1):
        if page == 1:
            return [_data(970_000 + i, now - timedelta(days=i + 1)) for i in range(30)]
        raise httpx.ConnectError("Strava down")
    monkeypatch.setattr(StravaService, "get_recent_activities", fetch)
    r = await as_user.post("/api/sync")
    saved = (await db_session.execute(select(func.count(Activity.id)).where(
        Activity.user_id == test_user.id))).scalar()
    assert "30 nouvelle(s) activité(s) importée(s)." in r.text and "location.reload" in r.text and saved == 30
    assert strava_state.read(f"manual:{test_user.id}") is None  # failed: may be tried again at once
    # failed before saving anything: said so (the pull-to-refresh's wording), not « Déjà à jour »
    monkeypatch.setattr(StravaService, "get_recent_activities", AsyncMock(side_effect=httpx.ConnectError("down")))
    r = await as_user.post("/api/sync")
    assert r.text == "<span>Strava : synchronisation incomplète. Réessaie dans un instant.</span>"

    fetch_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(StravaService, "get_recent_activities", fetch_mock)
    assert strava_state.claim(f"manual:{test_user.id}:running", 60)  # another tab's sync, still running
    r = await as_user.post("/api/sync")
    assert r.text == "<span>Synchro déjà en cours.</span>"
    headers = {"accept": "application/json", "x-paceforge-refresh": "1"}
    assert (await as_user.post("/api/sync", headers=headers)).json() == {"received": 0, "errors": []}
    assert fetch_mock.await_count == 0
    strava_state.release(f"manual:{test_user.id}:running")  # it ended
    assert "Déjà à jour" in (await as_user.post("/api/sync")).text and fetch_mock.await_count == 1
    assert strava_state.read(f"manual:{test_user.id}:running") is None


D = date(2026, 10, 8)


@pytest.fixture
def on_day(monkeypatch):
    """Santé's and Activités' « today » is D (as tests/test_sante_v43 pins it)."""
    from app.services import sante
    from app.services import sante_training as st

    async def today(*a, **k):
        return D
    monkeypatch.setattr(sante, "athlete_today", today)
    monkeypatch.setattr(st, "athlete_today", today)
    return D


@pytest.mark.parametrize("wall", [datetime(2026, 12, 28, 12, tzinfo=timezone.utc),
                                  datetime(2027, 3, 1, 12, tzinfo=timezone.utc),
                                  datetime(2027, 10, 15, 8, tzinfo=timezone.utc)])
async def test_activites_take_now_from_the_athletes_today(as_user, db_session, test_user, on_day, frozen, wall):
    """Base code: « Semaines » read the wall clock while « today » was pinned: from late December 2026 its 12 weeks no
    longer held the activities around D, and test_sante_v43's Strava-only user and cyclist lost their « Semaines »."""
    for k in range(1, 70, 2):
        start = datetime(D.year, D.month, D.day, 5, tzinfo=timezone.utc) - timedelta(days=k)
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=50_000 + k, sport_type="Run",
                                name="Footing", start_date=start, distance=7000, moving_time=3300,
                                elapsed_time=3400, total_elevation_gain=120, raw_data={"utc_offset": 7200}))
    await db_session.flush()
    frozen.at = wall
    html = (await as_user.get("/activities")).text
    assert re.findall(r'data-range="(\w+)"', html) == ["duree", "distance", "dplus"]
    assert _weeks(html)[0][:2] == ("2026-10-05", "Cette semaine")
    assert "7 j : 21 km · 28 j : 98 km" in html  # the volume of the days before D


async def test_activites_keep_the_wall_clock_when_the_athletes_day_agrees(db_session, test_user, monkeypatch,
                                                                         frozen):
    """A day apart at most (a watch already on tomorrow, another zone): « now » stays the wall clock."""
    from app.routers.dashboard import _clock
    from app.services import sante_training as st

    frozen.at = datetime(2026, 10, 8, 21, 30, tzinfo=timezone.utc)  # 23:30 in Paris
    for day in (date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9)):
        async def today(*a, _day=day, **k):
            return _day
        monkeypatch.setattr(st, "athlete_today", today)
        now, zone = await _clock(db_session, test_user)
        assert now == frozen.at and zone.key == "Europe/Paris"

    async def far(*a, **k):
        return date(2026, 10, 1)
    monkeypatch.setattr(st, "athlete_today", far)
    now, _ = await _clock(db_session, test_user)
    assert now == datetime(2026, 10, 1, 10, tzinfo=timezone.utc)  # noon in Paris on that day
