"""Server-side hardening after the site audit: the SMTP link checks its server, the access log masks the tokens
some links carry."""
import logging
import ssl
from unittest.mock import MagicMock

from app.config import settings

# ── SMTP: STARTTLS checks the server's certificate ──────────────────────────


def test_starttls_checks_the_servers_certificate(monkeypatch):
    """Without a context, smtplib's starttls() encrypts towards whoever answers: the password and the reset links
    would go to a man in the middle. A default context checks the certificate and the host name."""
    from app.services import email

    smtp = MagicMock()
    server = smtp.return_value.__enter__.return_value
    monkeypatch.setattr(email.smtplib, "SMTP", smtp)
    monkeypatch.setattr(settings, "SMTP_USER", "mailer")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret")
    assert email.send_email("a@b.fr", "Sujet", "<p>x</p>") is True
    smtp.assert_called_once_with(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
    context = server.starttls.call_args.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    server.login.assert_called_once_with("mailer", "secret")
    assert [name for name, *_ in server.method_calls][:2] == ["starttls", "login"]  # encrypted before the login


# ── access logs: the tokens some links carry are masked ─────────────────────


def test_the_access_log_masks_the_tokens_links_carry(caplog):
    """uvicorn logs each request's path with its query: an email's token, the phone's ticket, an OAuth code and
    state, the webhook's verify token are masked there; the request line stays readable."""
    from app.log_redaction import SecretQueryFilter, redact
    from app.main import create_app

    create_app()  # installs the filter (in every worker, after uvicorn's own logging set-up)
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, SecretQueryFilter) for f in access.filters) == 1
    create_app()
    assert sum(isinstance(f, SecretQueryFilter) for f in access.filters) == 1  # once, however many apps
    paths = {
        "/auth/reset-password?token=Zx_9-secret": "/auth/reset-password?token=***",
        "/auth/verify-email?token=abc.def.ghi&next=x": "/auth/verify-email?token=***&next=x",
        "/mobile/session?ticket=T1CK3T": "/mobile/session?ticket=***",
        "/auth/strava/callback?state=S7&code=C0DE&scope=read": "/auth/strava/callback?state=***&code=***&scope=read",
        "/webhook/strava?hub.mode=subscribe&hub.verify_token=V3R1FY&hub.challenge=c":
            "/webhook/strava?hub.mode=subscribe&hub.verify_token=***&hub.challenge=c",
        "/activities?page=2&sport=run": "/activities?page=2&sport=run",
    }
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        for path in paths:
            access.info('%s - "%s %s HTTP/%s" %d', "203.0.113.9:5123", "GET", path, "1.1", 200)
    records = [r for r in caplog.records if r.name == "uvicorn.access"]
    assert [r.getMessage() for r in records] == [f'203.0.113.9:5123 - "GET {masked} HTTP/1.1" 200'
                                                 for masked in paths.values()]
    for secret in ("Zx_9-secret", "abc.def", "T1CK3T", "C0DE", "V3R1FY"):
        assert secret not in caplog.text
    assert all(isinstance(r.args, tuple) and len(r.args) == 5 for r in records)  # uvicorn's formatter unpacks five
    assert redact("GET /auth/verify-email?TOKEN=x") == "GET /auth/verify-email?TOKEN=***"
