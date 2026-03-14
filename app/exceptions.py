import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.templating import Jinja2Templates

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/templates")


class PaceForgeError(Exception):
    """Base exception for PaceForge."""

    def __init__(self, message: str = "An error occurred") -> None:
        self.message = message
        super().__init__(self.message)


class StravaAPIError(PaceForgeError):
    """Error communicating with Strava API."""

    def __init__(self, message: str = "Strava API error", status_code: int = 500) -> None:
        self.status_code = status_code
        super().__init__(message)


class StravaRateLimitError(StravaAPIError):
    """Strava API rate limit exceeded."""

    def __init__(self) -> None:
        super().__init__("Strava API rate limit exceeded", status_code=429)


class StravaTokenError(StravaAPIError):
    """Error refreshing Strava token."""

    def __init__(self) -> None:
        super().__init__("Failed to refresh Strava token", status_code=401)


class ClaudeAPIError(PaceForgeError):
    """Error communicating with Claude API."""


class ActivityNotFoundError(PaceForgeError):
    """Activity not found."""

    def __init__(self, activity_id: int) -> None:
        super().__init__(f"Activity {activity_id} not found")


class AnalysisNotFoundError(PaceForgeError):
    """Analysis not found for activity."""

    def __init__(self, activity_id: int) -> None:
        super().__init__(f"Analysis not found for activity {activity_id}")


class UserNotFoundError(PaceForgeError):
    """User not found."""

    def __init__(self, identifier: str | int) -> None:
        super().__init__(f"User {identifier} not found")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(404)
    async def not_found_handler(request: Request, _exc: Exception) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "404.html", status_code=404
        )

    @app.exception_handler(500)
    async def server_error_handler(request: Request, _exc: Exception) -> HTMLResponse:
        logger.exception("Internal server error")
        return templates.TemplateResponse(
            request, "500.html", status_code=500
        )

    @app.exception_handler(ActivityNotFoundError)
    async def activity_not_found_handler(
        request: Request, exc: ActivityNotFoundError
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "404.html", context={"message": exc.message}, status_code=404
        )

    @app.exception_handler(UserNotFoundError)
    async def user_not_found_handler(
        request: Request, exc: UserNotFoundError
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "404.html", context={"message": exc.message}, status_code=404
        )
