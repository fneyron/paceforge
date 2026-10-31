"""Request bodies refused past their route's size, before anything reads them (wired by app.main).

Starlette reads a whole body before a route runs: a form or an upload is parsed (spooled to disk past 1 MB) before
FastAPI resolves the route's dependencies, the sign-in check included, and a JSON body (the Strava webhook) is read
without any cap. Here, a declared Content-Length over the route's cap is answered 413 at once, nothing read; a body
without one (chunked) is counted as it streams, and cut with a 413 as soon as it goes past the cap (whatever the
route answers then gives way to the 413).

The caps, one table: each route that takes a file or a plan gets what it needs plus a margin, everything else
DEFAULT_LIMIT. The routes keep their own, finer checks (a GPX past simulator.MAX_GPX_BYTES gets its message from the
route); tests/test_body_limit checks the two stay consistent. A route that needs a signed-in session (a GPX, a plan)
gets its cap only from a request that carries one (SessionMiddleware runs before this one, app.main): a request
without one gets DEFAULT_LIMIT, so no one reaches the sign-in redirect with a 13 MB body.
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

FORM_FIELD = 1 * MiB  # Starlette's own cap on one form field (Request.form's max_part_size): a 400 past it
SIGNED_IN, ANYONE = True, False  # who gets a route's cap: a signed-in session only, or any request

# (path, bytes allowed, who gets them): the first pattern matching the whole path wins
BODY_LIMITS: tuple[tuple[re.Pattern[str], int, bool], ...] = tuple(
    (re.compile(pattern), limit, who) for pattern, limit, who in (
        # one GPX file (multipart)
        (r"/partials/simulator/(bike-)?gpx-upload", GPX + MULTIPART_MARGIN, SIGNED_IN),
        (r"/api/simulator/routes/\d+/reimport", GPX + MULTIPART_MARGIN, SIGNED_IN),
        # a triathlon: a bike GPX and a run GPX
        (r"/api/simulator/triathlon", 2 * GPX + MULTIPART_MARGIN, SIGNED_IN),
        # a plan not saved yet posts its whole course, URL-encoded, in one field: Starlette refuses a field past
        # FORM_FIELD (1 MiB), so the course, its points and its weather fit in twice that
        (r"/api/simulator/routes|/partials/simulator/passage-times", 2 * FORM_FIELD, SIGNED_IN),
        # the phone's uploads (its bearer token, no session): the same caps as its routes
        # (routers.mobile.MAX_NIGHT_BYTES, MAX_DAILY_BYTES)
        (r"/api/mobile/nights", 2 * MiB, ANYONE),
        (r"/api/mobile/daily", 64 * KiB, ANYONE),
    ))
DEFAULT_LIMIT = 1 * MiB  # forms, the webhook's JSON, everything else

TOO_LARGE = "Envoi trop volumineux."  # the phone's routes say the same (routers.mobile.capped_body)
GPX_TOO_LARGE = f"Ce fichier GPX est trop lourd ({GPX // MiB} Mo au plus)."  # as routers.simulator says it
# a GPX form posted without a session (it ended while the page stayed open): the file may well be within its cap
SIGNED_OUT = "Ta session a expiré : reconnecte-toi, puis importe ton fichier à nouveau."
# the GPX forms show the answer in their own error box (app.js swaps a 413 there, as a 429)
GPX_FORMS = re.compile(r"/partials/simulator/(bike-)?gpx-upload|/api/simulator/routes/\d+/reimport"
                       r"|/api/simulator/triathlon")


def body_limit(path: str, signed_in: bool = True) -> int:
    """The bytes a request to `path` may send, with a signed-in session or without one."""
    for pattern, limit, who in BODY_LIMITS:
        if pattern.fullmatch(path):
            return limit if signed_in or who is ANYONE else DEFAULT_LIMIT
    return DEFAULT_LIMIT


def too_large(path: str, signed_in: bool = True) -> Response:
    if GPX_FORMS.fullmatch(path):
        return HTMLResponse('<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">'
                            f"{GPX_TOO_LARGE if signed_in else SIGNED_OUT}</div>", status_code=413)
    return JSONResponse({"detail": TOO_LARGE}, status_code=413)


def _signed_in(scope: Scope) -> bool:
    """A session that names an account (its signature checked by SessionMiddleware; the route checks the rest)."""
    session = scope.get("session")
    return bool(session and session.get("user_id"))


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
        signed_in = _signed_in(scope)
        limit = body_limit(path, signed_in)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            logger.warning("%s %s refused: %s bytes declared, %d allowed", scope["method"], path, declared[:20], limit)
            await too_large(path, signed_in)(scope, receive, send)
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
                await too_large(path, signed_in)(scope, receive, send)
