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


KEY_HINT = ("put back the key that encrypted it in ENCRYPTION_KEYS (any place after the first; a former SECRET_KEY "
            "as legacy_key(old SECRET_KEY))")


async def check_stored_secrets(engine=None) -> None:
    """No start when the last written value of a kind of stored secret no configured key decrypts: the key in
    use was dropped (ENCRYPTION_KEYS emptied, SECRET_KEY changed without its legacy key), and started, the syncs
    would mark every such link « to reconnect » for good. An old row alone unreadable (written under a key gone
    long ago) is only logged as an error: it never stops the start. A database that does not answer within 10 s
    never blocks it either (warned, unchecked)."""
    from app.crypto import unreadable_secrets

    if engine is None:
        from app.database import engine
    try:
        async with asyncio.timeout(10), engine.connect() as conn:
            lost, old = await unreadable_secrets(conn)
    except Exception:
        logger.warning("Stored secrets not checked at start: the database did not answer", exc_info=True)
        return
    for kind, row_id in old:
        logger.error("Stored %s secret of row %d: no configured key decrypts it (written under an older key); that "
                     "link will ask to reconnect. To keep it, %s.", kind, row_id, KEY_HINT)
    if lost:
        raise RuntimeError(
            f"The last stored {', '.join(lost)} secrets: no configured key decrypts them. Before starting, "
            f"{KEY_HINT}: running now would ask those athletes to reconnect.")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    check_secrets()
    await check_stored_secrets()
    logger.info("PaceForge starting up")
    yield
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

    # Health check
    @app.get("/health")
    async def health_check():
        return JSONResponse({"status": "ok", "app": settings.APP_NAME, "build": BUILD_ID},
                            headers={"Cache-Control": "no-store"})

    return app


app = create_app()
