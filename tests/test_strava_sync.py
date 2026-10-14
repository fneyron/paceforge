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
