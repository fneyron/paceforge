import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.datastructures import MutableHeaders

from app.build import BUILD_ID
from app.config import settings
from app.exceptions import register_exception_handlers
from app.log_redaction import install as mask_secrets_in_access_logs
from app.middleware.body_limit import BodySizeLimitMiddleware
from app.middleware.security import CrossSiteRequestMiddleware, SecurityHeadersMiddleware
from app.sentry import init_sentry

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# the pages a search engine may list (sitemap.xml; base.html gives each its canonical URL)
PUBLIC_PAGES = ("/", "/methode", "/privacy", "/mentions-legales", "/cgu")
logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO, query string included (Strava's
# deauthorize and webhook calls carry a token or the app secret there)
logging.getLogger("httpx").setLevel(logging.WARNING)


class PageTimingMiddleware:
    """Expose page preparation separately from network/rendering in Web Inspector.

    Only a duration is sent: no measurement, account, query or provider data.
    Use ASGI directly so timing does not add a task or buffer the response.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path in {"/sante", "/activities", "/simulator"}
            or path.startswith(("/activity/", "/simulator/routes/"))
        ):
            return await self.app(scope, receive, send)
        started = perf_counter()

        async def timed_send(message):
            if message["type"] == "http.response.start":
                duration = (perf_counter() - started) * 1000
                MutableHeaders(scope=message).append("Server-Timing", f"app;dur={duration:.1f}")
                if duration >= 1000:
                    route = getattr(scope.get("route"), "path", "page")
                    logger.warning("Slow page %s: %.0f ms before response", route, duration)
            await send(message)

        await self.app(scope, receive, timed_send)


def register_font_types() -> None:
    """python:3.12-slim has no /etc/mime.types and Python's own table lacks the web fonts: the .woff2 files went out
    as application/octet-stream, and gzipped (GZipMiddleware skips font/woff2, already compressed, not that)."""
    mimetypes.add_type("font/woff2", ".woff2")
    mimetypes.add_type("font/woff", ".woff")


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
DEFAULT_WEBHOOK_VERIFY_TOKEN = type(settings).model_fields["STRAVA_WEBHOOK_VERIFY_TOKEN"].default


def check_secrets() -> None:
    """No start with a SECRET_KEY anyone can guess (it signs the sessions: a forged one is any account),
    unless DEBUG, nor with an ENCRYPTION_KEYS entry that is not a Fernet key. A webhook verify token left at its
    default is warned about (anyone can then validate a subscription against this server), not refused."""
    from app.crypto import check_keys

    if not settings.DEBUG and (not settings.SECRET_KEY.strip() or settings.SECRET_KEY in DEFAULT_SECRET_KEYS):
        raise RuntimeError(
            "SECRET_KEY is empty or a known default: set a long random value. If tokens were stored under the "
            "old one, first append legacy_key(old SECRET_KEY) to ENCRYPTION_KEYS (app.crypto), or they become "
            "unreadable.")
    if not settings.DEBUG and settings.STRAVA_WEBHOOK_VERIFY_TOKEN == DEFAULT_WEBHOOK_VERIFY_TOKEN:
        logger.warning("STRAVA_WEBHOOK_VERIFY_TOKEN is the default: anyone can validate a Strava webhook subscription "
                       "against this server. Set a random value in the environment")
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
        # the schema and its docs are for development: in production they listed every route to anyone
        openapi_url="/openapi.json" if settings.DEBUG else None,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.build_id = BUILD_ID
    # the access log's lines without the tokens some links carry (?token=, ?code=…): in each worker, after uvicorn
    # has set its logging up (it does so before it imports the app)
    mask_secrets_in_access_logs()

    # Middleware, innermost first (the last added runs first): the session is read before the headers middleware
    # answers, which needs it to tell a signed-in page; the cross-site check and the body size limit refuse before
    # any route runs (the limit before any body is read or parsed), and their answers get the headers too
    app.add_middleware(CrossSiteRequestMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    # level 6: most of level 9's gain for a fraction of its CPU on every page and partial
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
    app.add_middleware(PageTimingMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="paceforge_session",
        max_age=60 * 60 * 24 * 30,  # 30 days
        same_site="lax",
        https_only=not settings.DEBUG,
    )

    # Static files (their types first: StaticFiles reads them per response, from mimetypes)
    register_font_types()
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", VersionedStaticFiles(directory=str(static_dir)), name="static")

    # Exception handlers
    register_exception_handlers(app)

    # Routers
    from app.routers import activity, auth, coros, dashboard, garmin, mobile, sante, lactate, simulator, webhook
    from app.routers import settings as settings_router
    from app.routers.garmin_consent import consent_before_connect

    app.include_router(auth.router)
    app.include_router(dashboard.router)  # landing (/), activities, sync
    app.include_router(activity.router)   # activity detail (light: no AI analysis)
    app.include_router(simulator.router)
    app.include_router(settings_router.router)
    app.include_router(coros.router)      # COROS link (OAuth) and sync
    # Garmin link (login, MFA) and sync; its login asks for the consent to health data first
    app.include_router(garmin.router, dependencies=[Depends(consent_before_connect)])
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

    # Search engines (owner, 2026-10-17: « référencer paceforge sur Google »): the public pages in the sitemap, the
    # app's own pages out of the crawl (they ask for a login anyway), /landing a copy of /. The sign-in pages stay
    # crawlable: they say noindex themselves, which a crawler only reads on a page it may fetch.
    @app.get("/robots.txt", include_in_schema=False)
    async def robots():
        private = ("/api/", "/mobile/", "/partials/", "/webhook", "/setup", "/landing", "/accueil",
                   "/dashboard", "/sante", "/activities", "/activity/", "/settings", "/simulator", "/connections",
                   "/coros", "/garmin")
        body = "User-agent: *\n" + "".join(f"Disallow: {p}\n" for p in private)
        body += f"\nSitemap: {settings.BASE_URL.rstrip('/')}/sitemap.xml\n"
        return PlainTextResponse(body, headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/sitemap.xml", include_in_schema=False)
    async def sitemap():
        base = settings.BASE_URL.rstrip("/")
        urls = "".join(f"<url><loc>{base}{path}</loc></url>" for path in PUBLIC_PAGES)
        return Response('<?xml version="1.0" encoding="UTF-8"?>\n'
                        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n',
                        media_type="application/xml", headers={"Cache-Control": "public, max-age=86400"})

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
