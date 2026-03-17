import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, html_body: str) -> bool:
    """Send an email via SMTP. Returns True on success."""
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP not configured, skipping email to %s", to)
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, to, msg.as_string())

        logger.info("Email sent to %s: %s", to, subject)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False


def send_verification_email(to: str, token: str) -> bool:
    url = f"{settings.BASE_URL}/auth/verify-email?token={token}"
    html = f"""
    <div style="font-family: Inter, system-ui, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px 24px;">
        <h2 style="font-size: 20px; font-weight: 700; color: #111;">Bienvenue sur PaceForge</h2>
        <p style="color: #6b7280; font-size: 14px; line-height: 1.6;">
            Confirme ton adresse email pour activer ton compte.
        </p>
        <a href="{url}"
           style="display: inline-block; margin-top: 16px; padding: 12px 24px; background: #111; color: #fff; border-radius: 10px; text-decoration: none; font-size: 14px; font-weight: 600;">
            Confirmer mon email
        </a>
        <p style="margin-top: 24px; color: #9ca3af; font-size: 12px;">
            Ou copie ce lien : {url}
        </p>
    </div>
    """
    return send_email(to, "Confirme ton compte PaceForge", html)


def send_password_reset_email(to: str, token: str) -> bool:
    url = f"{settings.BASE_URL}/auth/reset-password?token={token}"
    html = f"""
    <div style="font-family: Inter, system-ui, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px 24px;">
        <h2 style="font-size: 20px; font-weight: 700; color: #111;">Réinitialisation du mot de passe</h2>
        <p style="color: #6b7280; font-size: 14px; line-height: 1.6;">
            Tu as demandé à réinitialiser ton mot de passe. Clique sur le bouton ci-dessous.
            Ce lien expire dans 1 heure.
        </p>
        <a href="{url}"
           style="display: inline-block; margin-top: 16px; padding: 12px 24px; background: #111; color: #fff; border-radius: 10px; text-decoration: none; font-size: 14px; font-weight: 600;">
            Réinitialiser mon mot de passe
        </a>
        <p style="margin-top: 24px; color: #9ca3af; font-size: 12px;">
            Si tu n'as pas fait cette demande, ignore cet email.
        </p>
    </div>
    """
    return send_email(to, "Réinitialise ton mot de passe — PaceForge", html)
