import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.exceptions import register_exception_handlers

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


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

    # Middleware
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
    from app.routers import activity, api, auth, dashboard, digest, trends, webhook, workout
    from app.routers import settings as settings_router

    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(activity.router)
    app.include_router(workout.router)
    app.include_router(trends.router)
    app.include_router(digest.router)
    app.include_router(settings_router.router)
    app.include_router(webhook.router)
    app.include_router(api.router)

    # Health check
    @app.get("/health")
    async def health_check():
        return {"status": "ok", "app": settings.APP_NAME}

    return app


app = create_app()
