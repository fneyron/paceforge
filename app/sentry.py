"""Optional error reporting (Sentry). Nothing is imported, let alone sent, while SENTRY_DSN is empty. No
performance tracing, no default PII: an event carries the exception, the logger line and the route, never
cookies, user ids or addresses. The integrations are named rather than auto-enabled, so no breadcrumb records
the query strings of outgoing requests (Strava's can carry a token)."""
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def init_sentry(*, celery: bool = False) -> bool:
    """Initialise the SDK for the web app (Starlette and FastAPI) or for a Celery worker; whether it did."""
    dsn = settings.SENTRY_DSN.strip()
    if not dsn:
        return False
    import sentry_sdk

    from app.build import BUILD_ID

    if celery:
        from sentry_sdk.integrations.celery import CeleryIntegration

        integrations = [CeleryIntegration()]
    else:
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        integrations = [StarletteIntegration(), FastApiIntegration()]
    sentry_sdk.init(
        dsn=dsn,
        release=BUILD_ID[:12],  # the deployed code, as /health reports it
        send_default_pii=False,
        integrations=integrations,
        auto_enabling_integrations=False,
    )
    logger.info("Sentry reporting on (%s)", "celery" if celery else "web")
    return True
