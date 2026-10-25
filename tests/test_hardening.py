"""Server-side hardening after the site audit: the SMTP link checks its server."""
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
