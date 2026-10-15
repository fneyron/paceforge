import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

from app.build import BUILD_ID
from app.config import settings
from app.exceptions import register_exception_handlers
from app.sentry import init_sentry

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO, query string included (Strava's
# deauthorize and webhook calls carry a token or the app secret there)
logging.getLogger("httpx").setLevel(logging.WARNING)


class VersionedStaticFiles(StaticFiles):
    """Reuse build-tagged assets without a request on every page navigation."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in {200, 304}:
            # Do not freeze an old URL if its request reaches a newer deployment.
            # Private is intentional: SessionMiddleware may refresh its cookie.
            current = Request(scope).query_params.get("v") == BUILD_ID
            response.headers["Cache-Control"] = (
                "private, max-age=31536000, immutable" if current else "no-cache"
            )
        return response


DEFAULT_SECRET_KEYS = {"change-me-to-a-random-secret-key"}  # config.py's and .env.example's


def check_secrets() -> None:
    """No start with a SECRET_KEY anyone can guess (it signs the sessions: a forged one is any account),
    unless DEBUG, nor with an ENCRYPTION_KEYS entry that is not a Fernet key."""
    from app.crypto import check_keys

    if not settings.DEBUG and (not settings.SECRET_KEY.strip() or settings.SECRET_KEY in DEFAULT_SECRET_KEYS):
        raise RuntimeError(
            "SECRET_KEY is empty or a known default: set a long random value. If tokens were stored under the "
            "old one, first append legacy_key(old SECRET_KEY) to ENCRYPTION_KEYS (app.crypto), or they become "
            "unreadable.")
    try:
        check_keys()
    except ValueError as e:
        raise RuntimeError("ENCRYPTION_KEYS: each entry must be a Fernet key (comma-separated).") from e
    if not settings.ENCRYPTION_KEYS.strip():
        logger.warning("ENCRYPTION_KEYS is not set: stored tokens are encrypted with a key derived from SECRET_KEY")


KEY_HINT = ("Put back the key that encrypted them in ENCRYPTION_KEYS (any place after the first), or, after a "
            "SECRET_KEY change, set ENCRYPTION_KEYS = new,legacy_key(old SECRET_KEY) (app.crypto).")


async def scan_stored_secrets(engine=None) -> None:
    """List in the logs, one ERROR per kind with its rows, the stored secrets no configured key decrypts. Never
    stops the start: such links are only paused, untouched, until the key is back. A database that does not
    answer within 10 s leaves them unlisted (warned)."""
    from app.crypto import unreadable_rows

    if engine is None:
        from app.database import engine
    try:
        async with asyncio.timeout(10), engine.connect() as conn:
            unreadable = await unreadable_rows(conn)
    except Exception:
        logger.warning("Stored secrets not checked at start: the database did not answer", exc_info=True)
        return
    for kind, ids in unreadable.items():
        logger.error("Stored %s secrets no configured key decrypts, rows %s: those links are paused, nothing "
                     "is changed. %s", kind, ", ".join(map(str, ids)), KEY_HINT)


async def _first_start_of_this_build() -> bool:
    """Whether this worker is the first of the deploy to start (a short Redis claim on the code and the keys: a
    redeploy that only changes ENCRYPTION_KEYS scans again); every worker is, when Redis does not answer."""
    import hashlib

    keys = hashlib.sha256(f"{settings.SECRET_KEY}|{settings.ENCRYPTION_KEYS}".encode()).hexdigest()[:16]
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis.from_url(settings.REDIS_URL, socket_timeout=0.5, socket_connect_timeout=0.5)
        try:
            return bool(await r.set(f"paceforge:start-scan:{BUILD_ID}:{keys}", "1", nx=True, ex=15 * 60))
        finally:
            await r.aclose()
    except Exception:  # Redis down or slow: scan on this start all the same
        logger.warning("Start-up secret scan: Redis unavailable, scanning on this start", exc_info=True)
        return True


async def _scan_once_per_deploy() -> None:
    if await _first_start_of_this_build():
        await scan_stored_secrets()


async def _db_answers() -> bool:
    """SELECT 1 through the app's pool within 2 s (a pool with no free connection fails it too: worth a page)."""
    from sqlalchemy import text

    from app import database

    try:
        async with asyncio.timeout(2), database.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("Health: the database did not answer", exc_info=True)
        return False


async def _redis_answers() -> bool:
    """PING within 2 s."""
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
        try:
            async with asyncio.timeout(2):
                return bool(await r.ping())
        finally:
            await r.aclose()
    except Exception:
        logger.warning("Health: Redis did not answer", exc_info=True)
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    check_secrets()  # configuration only: stored data never stops the start
    scan = asyncio.get_running_loop().create_task(_scan_once_per_deploy())  # in the background: no delay
    logger.info("PaceForge starting up")
    yield
    scan.cancel()
    logger.info("PaceForge shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.build_id = BUILD_ID

    # Middleware
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="paceforge_session",
        max_age=60 * 60 * 24 * 30,  # 30 days
        same_site="lax",
        https_only=not settings.DEBUG,
    )

    # Static files
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", VersionedStaticFiles(directory=str(static_dir)), name="static")

    # Exception handlers
    register_exception_handlers(app)

    # Routers
    from app.routers import activity, auth, coros, dashboard, garmin, mobile, sante, lactate, simulator, webhook
    from app.routers import settings as settings_router

    app.include_router(auth.router)
    app.include_router(dashboard.router)  # landing (/), activities, sync
    app.include_router(activity.router)   # activity detail (light: no AI analysis)
    app.include_router(simulator.router)
    app.include_router(settings_router.router)
    app.include_router(coros.router)      # COROS link (OAuth) and sync
    app.include_router(garmin.router)     # Garmin link (login, MFA) and sync
    app.include_router(mobile.router)
    app.include_router(lactate.router)
    app.include_router(sante.router)      # Santé: recovery, load, trends from the watch
    app.include_router(webhook.router)

    # Browsers ask for /favicon.ico at the root, whatever the page links to
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        from fastapi.responses import FileResponse
        return FileResponse("app/static/favicon.ico", media_type="image/x-icon",
                            headers={"Cache-Control": "public, max-age=86400"})

    # Health check: fast for the container's and the proxy's probes. ?deep=1 (an external monitor) also asks
    # the database and Redis: 503 with the failing flag when one does not answer within 2 s.
    @app.get("/health")
    async def health_check(deep: bool = False):
        body = {"status": "ok", "app": settings.APP_NAME, "build": BUILD_ID}
        status = 200
        if deep:
            body["db"], body["redis"] = await asyncio.gather(_db_answers(), _redis_answers())
            if not (body["db"] and body["redis"]):
                body["status"], status = "degraded", 503
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})

    return app


init_sentry()  # nothing while SENTRY_DSN is empty; before the app exists, for Starlette's integration
app = create_app()
