"""Optional error reporting (Sentry). Nothing is imported, let alone sent, while SENTRY_DSN is empty.

What an event carries: the exception and its stack trace (file, function, line, the source lines around it), the
logger line, the route (path without its query) and the request's method and headers. Never: a request body
(max_request_body_size="never": no form, password, GPX or health payload), a frame's local variables
(include_local_variables=False: they held tokens, passwords and athletes' data), the query string, cookies, the
Authorization and Referer headers, user ids or addresses (send_default_pii=False). before_send drops whatever an
integration still attached of these, and masks the secret-bearing query values (?token=, ?code=…) left in a
breadcrumb (its message and its data) or a message (app.log_redaction). No performance tracing. The integrations
are named rather than auto-enabled, so no breadcrumb records the query strings of outgoing requests (Strava's can
carry a token).

One initialisation per process, whichever module asks first: the web app imports app.celery_app to queue a
task and a worker imports the app's modules, and a second sentry_sdk.init would replace the client with one
missing the first caller's integrations. All three are given every time, each inert where its framework is
not running."""
import logging
from typing import Any

from app.config import settings
from app.log_redaction import redact

logger = logging.getLogger(__name__)

_initialised = False

# request fields an event never carries (the body, the query, the cookies), and headers it never carries (the
# Referer is the previous page's full URL: its query may hold a token, an OAuth code)
DROPPED_REQUEST_FIELDS = ("data", "query_string", "cookies")
DROPPED_HEADERS = frozenset({"cookie", "authorization", "x-forwarded-for", "x-real-ip", "forwarded", "referer"})


def _redacted(value: Any, query: bool = False) -> Any:
    """`value` with the secret query values masked in every string it holds (lists and dicts walked; a value under a
    key naming a query, « http.query », is a query string without its « ? »)."""
    if isinstance(value, str):
        return redact("?" + value)[1:] if query else redact(value)
    if isinstance(value, dict):
        return {k: _redacted(v, "query" in str(k).lower()) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redacted(v, query) for v in value]
    return value


def _scrub(event: Any, _hint: Any = None) -> Any:
    """before_send (an event as a dict, the SDK's TypedDict): the request without body, query, cookies, credentials
    or Referer; the secret query values masked in the messages and in the breadcrumbs, their data included."""
    request = event.get("request")
    if isinstance(request, dict):
        for field in DROPPED_REQUEST_FIELDS:
            request.pop(field, None)
        if isinstance(request.get("url"), str):
            request["url"] = request["url"].split("?", 1)[0]
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items() if str(k).lower() not in DROPPED_HEADERS}
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        for key in ("message", "formatted"):
            if isinstance(logentry.get(key), str):
                logentry[key] = redact(logentry[key])
        if isinstance(logentry.get("params"), list):
            logentry["params"] = [redact(p) if isinstance(p, str) else p for p in logentry["params"]]
    if isinstance(event.get("message"), str):
        event["message"] = redact(event["message"])
    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else breadcrumbs
    for crumb in values if isinstance(values, list) else ():
        if isinstance(crumb, dict) and isinstance(crumb.get("message"), str):
            crumb["message"] = redact(crumb["message"])
        if isinstance(crumb, dict) and isinstance(crumb.get("data"), dict):  # a logged URL, an HTTP call's url/query
            crumb["data"] = _redacted(crumb["data"])
    return event


def init_sentry() -> bool:
    """Initialise the SDK once per process, for the web app (Starlette and FastAPI) and Celery alike; whether
    reporting is on."""
    global _initialised
    dsn = settings.SENTRY_DSN.strip()
    if not dsn:
        return False
    import sentry_sdk

    if _initialised or sentry_sdk.is_initialized():
        return True
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    from app.build import BUILD_ID

    sentry_sdk.init(
        dsn=dsn,
        release=BUILD_ID[:12],  # the deployed code, as /health reports it
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        before_send=_scrub,
        integrations=[StarletteIntegration(), FastApiIntegration(), CeleryIntegration()],
        auto_enabling_integrations=False,
    )
    _initialised = True
    logger.info("Sentry reporting on")
    return True
