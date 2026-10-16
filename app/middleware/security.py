"""Browser-side hardening, two middlewares wired by app.main.

SecurityHeadersMiddleware: the security headers on every response, with a content security policy the pages
satisfy (every host they reach is listed here: the map styles, tiles, glyphs and sprites, the relief), HSTS
outside DEBUG, and « private, no-store » on every signed-in HTML page (not the static files, not /health).

CrossSiteRequestMiddleware (CSRF): an unsafe request (POST, PUT, PATCH, DELETE) is refused unless it comes from
this site. Its Origin, when sent, must be this origin (BASE_URL's, or the host the request was made to, which no
other site's page can make a browser send); without one, its Sec-Fetch-Site must say same-origin or none; a
client sending neither (a script, an old browser) goes through, as the session cookie's SameSite=Lax already
covers it. Exempt: the Strava webhook and the phone's bearer API, pairing exchange included (a browser cannot
send an Authorization header cross-site without a preflight, and the exchange carries no cookie).
"""
import logging
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

logger = logging.getLogger(__name__)

# The hosts the plan's map reaches (partials/gpx_result.html): OpenFreeMap's styles, tiles, glyphs and sprites,
# CARTO's dark style (basemaps.cartocdn.com) and its tiles, glyphs and sprites (tiles*.basemaps.cartocdn.com), the
# relief (AWS Terrain Tiles). MapLibre fetches them all itself, so connect-src is where they belong.
MAP_HOSTS = ("https://tiles.openfreemap.org", "https://basemaps.cartocdn.com", "https://*.basemaps.cartocdn.com",
             "https://s3.amazonaws.com")

CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    # inline scripts and handlers on every page; Alpine (setup, activity) and htmx's hx-on build functions from
    # attributes, which is eval to a browser
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    # the athlete's Strava avatar comes from Strava's CDNs (several, changing); MapLibre's control icons are data: SVGs
    "img-src 'self' data: blob: https:",
    "font-src 'self'",
    "connect-src 'self' " + " ".join(MAP_HOSTS),
    "worker-src 'self' blob:",  # MapLibre's workers, and the service worker
    "child-src 'self' blob:",  # the same for browsers without worker-src
    "object-src 'none'",
    "base-uri 'self'",
    # Chrome checks a posted form's redirect too: /setup/credentials sends to Strava's consent page
    "form-action 'self' https://www.strava.com",
    "frame-ancestors 'none'",
))

NO_STORE_EXEMPT = ("/static/", "/health")


def security_headers() -> tuple[tuple[str, str], ...]:
    """Added to every response unless the route set its own (the phone's pages say no-referrer)."""
    headers = (
        ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    )
    if settings.DEBUG:  # a local http:// server must stay reachable once the browser has seen the production site
        return headers
    return (("Strict-Transport-Security", "max-age=31536000"),) + headers


def _signed_in(scope: Scope) -> bool:
    session = scope.get("session")
    return bool(session and session.get("user_id"))


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in security_headers():
                    headers.setdefault(name, value)
                # a signed-in page never stays in a shared or on-disk cache (the next person at the browser, the
                # back button after logout); the route's own choice (no-store already, a public max-age) stands
                if ("cache-control" not in headers and headers.get("content-type", "").startswith("text/html")
                        and not path.startswith(NO_STORE_EXEMPT) and _signed_in(scope)):
                    headers["Cache-Control"] = "private, no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
EXEMPT_PATHS = frozenset({"/webhook/strava", "/api/mobile/exchange"})
OWN_SITE = frozenset({"same-origin", "none"})


def _origin(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def own_origins(scope: Scope, headers: Headers) -> set[str]:
    """BASE_URL's origin, and the one this request was made to (the scheme as the proxy forwarded it)."""
    ours = {_origin(settings.BASE_URL)}
    host = headers.get("host")
    if host:
        ours.add(f"{scope.get('scheme', 'http')}://{host.strip().lower()}")
    return ours


def cross_site(scope: Scope) -> bool:
    """Whether this unsafe request must be refused: it says it comes from another site."""
    headers = Headers(scope=scope)
    if scope["path"] in EXEMPT_PATHS or headers.get("authorization", "").startswith("Bearer "):
        return False
    origin = headers.get("origin")
    if origin is not None:  # « null » (a sandboxed frame, a redirect from elsewhere) matches nothing: refused
        return _origin(origin) not in own_origins(scope, headers)
    site = headers.get("sec-fetch-site")
    return site is not None and site not in OWN_SITE


class CrossSiteRequestMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS and cross_site(scope):
            headers = Headers(scope=scope)
            logger.warning("Cross-site %s %s refused (origin %s, sec-fetch-site %s)", scope["method"], scope["path"],
                           headers.get("origin"), headers.get("sec-fetch-site"))
            await JSONResponse({"detail": "Forbidden"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)
