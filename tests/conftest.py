import asyncio
import itertools
import os
import shutil

# no OpenStreetMap calls from the tests (read when app.config loads)
os.environ.setdefault("SURFACE_FETCH", "false")
# no background COROS syncs either (the tests call the sync themselves, mocked)
os.environ.setdefault("COROS_SYNC", "false")
os.environ.setdefault("GARMIN_SYNC", "false")
# no Open-Meteo lookups from the syncs either (the tests that read them mock Open-Meteo)
os.environ.setdefault("ACTIVITY_ENV_FETCH", "false")
# bike/triathlon off, whatever .env or the shell says (the cycling_on fixture turns it on)
os.environ["CYCLING_ENABLED"] = "false"
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base
from app.dependencies import get_db
from app.main import create_app
from app.models.activity import Activity
from app.models.user import User

# PostgreSQL, as in production, when TEST_DATABASE_URL names one (never the application's DATABASE_URL: the suite
# empties its tables; one suite at a time on it): build its schema with `alembic upgrade head` first (the models
# say JSONB where the migrated columns are json). Test values stay within int32 for it. There, each test runs in
# one transaction rolled back at its end: a commit, a route's included, never reaches the next test (a table
# dropped for real, or rows written through another engine, would). Without it, every test gets a temporary
# SQLite file of its own: nothing is written in the repository (two suites once shared ./test.db, one dropping
# the tables the other was reading) and nothing outlives the test.
SERVER_URL = os.getenv("TEST_DATABASE_URL", "")
assert not SERVER_URL.startswith("sqlite"), "TEST_DATABASE_URL names a PostgreSQL database: unset it for SQLite"
_databases = itertools.count()


def _same_database(a: str, b: str) -> bool:
    from sqlalchemy.engine import make_url

    x, y = make_url(a), make_url(b)
    return (x.host, x.port or 5432, x.database) == (y.host, y.port or 5432, y.database)


def pytest_sessionstart(session):
    """On the server: never the application's database (the suite empties the tables), and say which one."""
    if not SERVER_URL:
        return
    from sqlalchemy.engine import make_url

    from app.config import settings

    if any(url and _same_database(SERVER_URL, url) for url in (os.getenv("DATABASE_URL", ""), settings.DATABASE_URL)):
        pytest.exit("TEST_DATABASE_URL is the application's DATABASE_URL: the suite empties its tables", returncode=3)
    session.config.pluginmanager.get_plugin("terminalreporter").write_line(
        f"tests on PostgreSQL {make_url(SERVER_URL).render_as_string(hide_password=True)}, its tables emptied first")


@pytest.fixture(scope="session")
def database_template(tmp_path_factory) -> Path | None:
    """SQLite: the empty schema in one file per process (pytest's temporary directory), copied for each test
    rather than built again (the DDL costs ≈ 50 ms a test, a minute over the suite). PostgreSQL: the server's
    tables, built from the models when no migration did, emptied once per run."""
    if SERVER_URL:
        async def prepare():
            eng = create_async_engine(SERVER_URL, poolclass=NullPool)
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
                await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
            await eng.dispose()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(prepare())
        finally:
            loop.close()
        return None
    path = tmp_path_factory.mktemp("db") / "template.db"
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng)
    eng.dispose()
    return path


@asynccontextmanager
async def _own_database(template: Path | None, foreign_keys: bool = False):
    """An engine on a database of the test's own: the server's, through connections of its own (asyncpg's belong
    to the loop that opened them: none pooled), else a copy of the SQLite template, deleted afterwards."""
    if SERVER_URL:
        eng, path = create_async_engine(SERVER_URL, poolclass=NullPool), None
    else:
        path = template.with_name(f"test-{next(_databases)}.db")
        shutil.copyfile(template, path)
        eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
        if foreign_keys:
            _enforce_foreign_keys(eng)
    try:
        yield eng
    finally:
        await eng.dispose()
        if path is not None:
            path.unlink(missing_ok=True)


@pytest.fixture
async def engine(database_template):
    async with _own_database(database_template) as eng:
        yield eng


@asynccontextmanager
async def _in_one_transaction(eng):
    """A session bound to one outer transaction, rolled back at the end: its commit and rollback work for real
    (savepoints), and nothing it wrote outlives it."""
    async with eng.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """The test's session, the one the app's routes get. On the server its rows would outlive a commit (routes
    commit, so do some tests), so it lives in one transaction rolled back at the end; an SQLite file is the
    test's own and goes with it."""
    if SERVER_URL:
        async with _in_one_transaction(engine) as session:
            yield session
        return
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(db_session: AsyncSession):
    app = create_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    user = User(
        strava_athlete_id=12345678,
        strava_access_token="test_access_token",
        strava_refresh_token="test_refresh_token",
        strava_token_expires_at=2147483000,
        firstname="Test",
        lastname="Runner",
        profile_picture_url="https://example.com/avatar.jpg",
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def test_activity(db_session: AsyncSession, test_user: User) -> Activity:
    activity = Activity(
        strava_activity_id=987654321,
        user_id=test_user.id,
        sport_type="Run",
        name="Morning Run",
        start_date=datetime(2024, 3, 10, 8, 0, 0, tzinfo=timezone.utc),
        distance=10000.0,
        moving_time=3000,
        elapsed_time=3100,
        total_elevation_gain=50.0,
        average_speed=3.33,
        max_speed=4.0,
        average_heartrate=150.0,
        max_heartrate=168.0,
        average_cadence=85.0,
        raw_data={"id": 987654321, "name": "Morning Run"},
        splits_metric=[
            {"distance": 1000, "moving_time": 300, "average_heartrate": 145},
            {"distance": 1000, "moving_time": 298, "average_heartrate": 148},
            {"distance": 1000, "moving_time": 302, "average_heartrate": 150},
            {"distance": 1000, "moving_time": 295, "average_heartrate": 152},
            {"distance": 1000, "moving_time": 305, "average_heartrate": 155},
        ],
    )
    db_session.add(activity)
    await db_session.flush()
    await db_session.refresh(activity)
    return activity


@pytest.fixture
def authenticated_client(client: AsyncClient, test_user: User):
    """Client with session cookie set."""
    client.cookies.set("paceforge_session", "test_session")
    return client


@pytest.fixture
def cycling_on(monkeypatch):
    """Bike / triathlon planning is hidden by default: switch it on for one test."""
    from app.config import settings

    monkeypatch.setattr(settings, "CYCLING_ENABLED", True)


@pytest.fixture(autouse=True)
def _no_wall_clock(monkeypatch):
    """Santé's day follows the athlete's sleep before noon (sante.cycle_day): the suite never reads the real wall
    clock for it, whatever the hour it runs (a test of the cycle sets sante.local_clock itself)."""
    from app.services import sante

    async def none(*a, **k):
        return None
    monkeypatch.setattr(sante, "local_clock", none)


@pytest.fixture(autouse=True)
def _fresh_sessions_cache():
    """Santé keeps each athlete's sessions per worker: never across tests."""
    from app.services import sante_training
    sante_training._CACHE.clear()
    yield


@pytest.fixture
def same_today(monkeypatch):
    """The test and the pages read one « today »: the athlete's date moves with the seeded sessions' UTC offset
    (+2 h in tests/test_race_prep._seed), so between 22:00 and 24:00 UTC a page would be a day ahead of a date
    read before the seed. The real UTC date, the same for both, whatever the hour the suite runs."""
    from app.services import sante_training

    day = datetime.now(timezone.utc).date()

    async def today(*a, **k):
        return day
    monkeypatch.setattr(sante_training, "athlete_today", today)
    return day


# ── a session whose commit and rollback really run ──────────────────────────
# The fixtures above swap `commit` for `flush` in many tests, so code that must
# really commit (a rotated token kept through a later rollback, an account
# deletion) is tested with these: every commit releases a SAVEPOINT inside one
# outer transaction, which is rolled back when the test ends. Foreign keys are
# enforced, as on PostgreSQL (the SQLite engine above leaves them off).

def _enforce_foreign_keys(eng) -> None:
    """SQLite with its foreign keys on, its transactions SQLAlchemy's (BEGIN and SAVEPOINT emitted itself: the
    driver's own transaction handling breaks savepoints)."""
    @event.listens_for(eng.sync_engine, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(eng.sync_engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")


@pytest.fixture
async def savepoint_engine(database_template):
    async with _own_database(database_template, foreign_keys=True) as eng:
        yield eng


@pytest.fixture
async def committing_session(savepoint_engine) -> AsyncGenerator[AsyncSession, None]:
    """A session whose commit and rollback work for real, foreign keys enforced on SQLite too (savepoint_engine)."""
    async with _in_one_transaction(savepoint_engine) as session:
        yield session


@pytest.fixture
async def committing_client(committing_session: AsyncSession):
    """The app on `committing_session`, committed at the end of each request as get_db does."""
    app = create_app()

    async def override_get_db():
        try:
            yield committing_session
            await committing_session.commit()
        except Exception:
            await committing_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _fresh_race_models():
    """The race model per athlete (race_calibration) is kept per worker: never across tests, whose ids repeat."""
    from app.services import race_calibration

    race_calibration._RACE_MODELS.clear()
    yield


@pytest.fixture(autouse=True)
def _fresh_legacy_estimates():
    """The older page's estimates read again (routers.simulator._legacy_estimate_s) are kept per worker by route id:
    never across tests, whose ids repeat."""
    from app.routers import simulator

    simulator._LEGACY.clear()
    simulator._LEGACY_MODELS.clear()
    yield


@pytest.fixture(autouse=True)
def _open_meteo_marker_in_memory(monkeypatch):
    """The Open-Meteo outage marker in this process, fresh for each test: no test reaches a real Redis (production
    shares it between the workers there, weather.RedisMarker)."""
    from app.services import weather

    monkeypatch.setattr(weather, "_outage", weather.MemoryMarker())


@pytest.fixture(autouse=True)
def _cheap_password_hashes(monkeypatch):
    """Password hashes cost 4 bcrypt rounds under pytest; 12, bcrypt's default, in production (≈ 0.3 s each: the
    auth tests spent 33 s on them). The application's code is untouched, bcrypt.gensalt itself is swapped."""
    import bcrypt

    gensalt = bcrypt.gensalt
    monkeypatch.setattr(bcrypt, "gensalt", lambda rounds=12, prefix=b"2b": gensalt(rounds=4, prefix=prefix))


@pytest.fixture(autouse=True)
def _rate_limits_in_memory(monkeypatch):
    """The login, sign-up and reset counters in this process, fresh for each test (production keeps them in
    Redis, shared by the web workers)."""
    from app.services import auth

    monkeypatch.setattr(auth, "_counter", auth.MemoryCounter())
