"""Optional error reporting (Sentry). Nothing is imported, let alone sent, while SENTRY_DSN is empty. No
performance tracing, no default PII: an event carries the exception, the logger line and the route, never
cookies, user ids or addresses. The integrations are named rather than auto-enabled, so no breadcrumb records
the query strings of outgoing requests (Strava's can carry a token).

One initialisation per process, whichever module asks first: the web app imports app.celery_app to queue a
task and a worker imports the app's modules, and a second sentry_sdk.init would replace the client with one
missing the first caller's integrations. All three are given every time, each inert where its framework is
not running."""
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_initialised = False


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
        integrations=[StarletteIntegration(), FastApiIntegration(), CeleryIntegration()],
        auto_enabling_integrations=False,
    )
    _initialised = True
    logger.info("Sentry reporting on")
    return True
