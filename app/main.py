import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

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


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
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
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Exception handlers
    register_exception_handlers(app)

    # Routers
    from app.routers import activity, auth, coros, dashboard, garmin, mobile, sante, simulator, webhook
    from app.routers import settings as settings_router

    app.include_router(auth.router)
    app.include_router(dashboard.router)  # landing (/), activities, sync
    app.include_router(activity.router)   # activity detail (light: no AI analysis)
    app.include_router(simulator.router)
    app.include_router(settings_router.router)
    app.include_router(coros.router)      # COROS link (OAuth) and sync
    app.include_router(garmin.router)     # Garmin link (login, MFA) and sync
    app.include_router(mobile.router)
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
