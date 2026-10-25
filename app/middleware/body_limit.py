"""Request bodies refused past their route's size, before anything reads them (wired by app.main).

Starlette reads a whole body before a route runs: a form or an upload is parsed (spooled to disk past 1 MB) before
FastAPI resolves the route's dependencies, the sign-in check included, and a JSON body (the Strava webhook) is read
without any cap. Here, a declared Content-Length over the route's cap is answered 413 at once, nothing read; a body
without one (chunked) is counted as it streams, and cut with a 413 as soon as it goes past the cap (whatever the
route answers then gives way to the 413).

The caps, one table: each route that takes a file or a plan gets what it needs plus a margin, everything else
DEFAULT_LIMIT. The routes keep their own, finer checks (a GPX past simulator.MAX_GPX_BYTES gets its message from the
route); tests/test_body_limit checks the two stay consistent.
"""
import logging
import re

from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

KiB = 1024
MiB = 1024 * KiB
GPX = 12 * MiB  # app.routers.simulator.MAX_GPX_BYTES
MULTIPART_MARGIN = 1 * MiB  # the form's framing and its other fields

# (path, bytes allowed): the first pattern matching the whole path wins
BODY_LIMITS: tuple[tuple[re.Pattern[str], int], ...] = tuple((re.compile(pattern), limit) for pattern, limit in (
    # one GPX file (multipart)
    (r"/partials/simulator/(bike-)?gpx-upload", GPX + MULTIPART_MARGIN),
    (r"/api/simulator/routes/\d+/reimport", GPX + MULTIPART_MARGIN),
    # a triathlon: a bike GPX and a run GPX
    (r"/api/simulator/triathlon", 2 * GPX + MULTIPART_MARGIN),
    # a plan not saved yet posts its whole course, URL-encoded (route_validation reads 8 M characters at most;
    # 1.6 MB for a 150 000-point GPX, 2.2 MB encoded)
    (r"/api/simulator/routes|/partials/simulator/passage-times", 16 * MiB),
    # the phone's uploads: the same caps as its routes (routers.mobile.MAX_NIGHT_BYTES, MAX_DAILY_BYTES)
    (r"/api/mobile/nights", 2 * MiB),
    (r"/api/mobile/daily", 64 * KiB),
))
DEFAULT_LIMIT = 1 * MiB  # forms, the webhook's JSON, everything else

TOO_LARGE = "Envoi trop volumineux."  # the phone's routes say the same (routers.mobile.capped_body)
GPX_TOO_LARGE = f"Ce fichier GPX est trop lourd ({GPX // MiB} Mo au plus)."  # as routers.simulator says it
# the GPX forms show the answer in their own error box (app.js swaps a 413 there, as a 429)
GPX_FORMS = re.compile(r"/partials/simulator/(bike-)?gpx-upload|/api/simulator/routes/\d+/reimport"
                       r"|/api/simulator/triathlon")


def body_limit(path: str) -> int:
    for pattern, limit in BODY_LIMITS:
        if pattern.fullmatch(path):
            return limit
    return DEFAULT_LIMIT


def too_large(path: str) -> Response:
    if GPX_FORMS.fullmatch(path):
        return HTMLResponse('<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">'
                            f"{GPX_TOO_LARGE}</div>", status_code=413)
    return JSONResponse({"detail": TOO_LARGE}, status_code=413)


class BodyTooLargeError(Exception):
    """Raised by the counted receive: the body went past its cap while streaming."""


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        limit = body_limit(path)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            logger.warning("%s %s refused: %s bytes declared, %d allowed", scope["method"], path, declared[:20], limit)
            await too_large(path)(scope, receive, send)
            return

        received, over, started = 0, False, False

        async def counted_receive() -> Message:
            nonlocal received, over
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    over = True
                    raise BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if over:  # the body was cut: whatever the route answers (a 400, its own error page) gives way to the 413
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            if not over:
                raise
        if over:
            logger.warning("%s %s refused: body past %d bytes", scope["method"], path, limit)
            if not started:
                await too_large(path)(scope, receive, send)
