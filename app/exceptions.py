import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
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


class ActivityNotFoundError(PaceForgeError):
    """Activity not found (or not this user's): the 404 page says it in French."""

    def __init__(self, activity_id: int) -> None:
        self.activity_id = activity_id
        super().__init__("Cette activité n'existe plus.")


class RouteNotFoundError(PaceForgeError):
    """A course (a saved route) not found, or not this user's: the 404 page says it in French."""

    def __init__(self, route_id: int | None = None) -> None:
        self.route_id = route_id
        super().__init__("Cette course n'existe plus.")


class UserNotFoundError(PaceForgeError):
    """User not found."""

    def __init__(self, identifier: str | int) -> None:
        self.identifier = identifier
        super().__init__("Ce compte n'existe plus.")


def _api_call(request: Request) -> bool:
    """A JSON API caller (the phone app, a script): /api/*, unless a browser opened it as a page."""
    return request.url.path.startswith("/api/") and "text/html" not in request.headers.get("accept", "")


async def _viewer(request: Request):
    """The signed-in user, so that an error page keeps the app's layout (its tabs, no « Se connecter »): the one a
    route's dependency already read (request.state.user), else this browser's session; None for a visitor or when
    the session cannot be read (the page then falls back to the public layout)."""
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    if "session" not in request.scope or not request.session.get("user_id"):
        return None
    from app.dependencies import get_db, get_optional_user

    provider = request.app.dependency_overrides.get(get_db, get_db)  # the app's database (a test's own, overridden)
    sessions = provider()
    if not hasattr(sessions, "__anext__"):
        return None
    try:
        return await get_optional_user(request, await sessions.__anext__())
    except Exception:
        logger.warning("404 page: the signed-in user could not be read", exc_info=True)
        return None
    finally:
        await sessions.aclose()


async def _not_found_page(request: Request, message: str | None = None) -> HTMLResponse:
    context = {"user": await _viewer(request)}
    if message:
        context["message"] = message
    return templates.TemplateResponse(request, "404.html", context=context, status_code=404)


# Starlette's own refusals of a form it will not parse (Request.form: a field past 1 MiB, too many fields or files),
# an English JSON detail by default
FORM_REFUSALS = {"Field exceeded maximum size": "Envoi trop volumineux : un champ dépasse 1 Mo.",
                 "Part exceeded maximum size": "Envoi trop volumineux : un champ dépasse 1 Mo.",
                 "Too many fields": "Envoi refusé : trop de champs.",
                 "Too many files": "Envoi refusé : trop de fichiers."}


def form_refusal(detail: object) -> str | None:
    """The French message for one of Starlette's form refusals, None for any other 400."""
    if isinstance(detail, str):
        return next((french for english, french in FORM_REFUSALS.items() if detail.startswith(english)), None)
    return None


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(400)
    async def bad_request_handler(request: Request, exc: StarletteHTTPException) -> Response:
        """A form Starlette refused to parse is told in French: HTML for a page or htmx, JSON for an API caller.
        Every other 400 keeps FastAPI's own answer."""
        message = form_refusal(exc.detail)
        if message is None:
            return await http_exception_handler(request, exc)
        if _api_call(request):
            return JSONResponse({"detail": message}, status_code=400)
        return HTMLResponse(f'<div class="text-sm text-red-600" role="alert">{message}</div>', status_code=400)

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: Exception) -> HTMLResponse | JSONResponse:
        if _api_call(request):
            return JSONResponse({"detail": getattr(exc, "detail", None) or "Not Found"}, status_code=404)
        return await _not_found_page(request)

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
        return await _not_found_page(request, exc.message)

    @app.exception_handler(RouteNotFoundError)
    async def route_not_found_handler(
        request: Request, exc: RouteNotFoundError
    ) -> HTMLResponse:
        return await _not_found_page(request, exc.message)

    @app.exception_handler(UserNotFoundError)
    async def user_not_found_handler(
        request: Request, exc: UserNotFoundError
    ) -> HTMLResponse:
        return await _not_found_page(request, exc.message)
