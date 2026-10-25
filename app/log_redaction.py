"""Secrets out of the logs: the query values that carry one are masked wherever a URL is logged.

uvicorn's access log writes every request's path with its query string, and some links carry a secret there: an
email's token (/auth/verify-email?token=…, /auth/reset-password?token=…), the phone's sign-in ticket, an OAuth
callback's code and state, the Strava webhook's hub.verify_token. A filter on the « uvicorn.access » logger
(installed by app.main.create_app, in every worker, after uvicorn has configured its logging) masks their values in
each line, the request line kept: method, path, status. The same masking serves Sentry's breadcrumbs (app.sentry).
"""
import logging
import re

# a query parameter whose name ends with one of these words (token, verify_token, access_token, client_secret…),
# its value up to the next parameter, fragment, space or quote
SECRET_QUERY = re.compile(r"(?i)([?&;][\w.\-]*?(?:token|ticket|code|state|secret|password)=)[^&;#\s\"']*")
MASK = "***"


def redact(text: str) -> str:
    """`text` with the values of the secret-bearing query parameters masked."""
    return SECRET_QUERY.sub(lambda m: m.group(1) + MASK, text)


class SecretQueryFilter(logging.Filter):
    """Masks the secret query values in a record's arguments and message; the record keeps its shape (uvicorn's
    access formatter unpacks exactly five arguments) and is never dropped."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install(logger_name: str = "uvicorn.access") -> None:
    """Once per process, whatever the number of calls (each create_app)."""
    logger = logging.getLogger(logger_name)
    if not any(isinstance(f, SecretQueryFilter) for f in logger.filters):
        logger.addFilter(SecretQueryFilter())
