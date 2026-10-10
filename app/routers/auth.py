import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_secret
from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.services.auth import (
    PASSWORD_PREFIX,
    generate_token,
    hash_password,
    valid_email,
    valid_verification_token,
    verification_token,
    verify_password,
)
from app.services.email import send_password_reset_email, send_verification_email
from app.services.oauth_attempts import begin_strava, consume_strava
from app.services.strava import StravaService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
HOME = "/sante"  # where a sign-in, a verified email, a new password or a Strava link land (Santé first)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

@router.get("/auth/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "auth/register.html", context={})


@router.post("/auth/register")
async def register(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    firstname: str = Form(default=""),
    lastname: str = Form(default=""),
):
    email = email.strip().lower()
    firstname = firstname.strip()
    lastname = lastname.strip()

    if not valid_email(email) or not password or len(firstname) > 100 or len(lastname) > 100:
        return templates.TemplateResponse(
            request, "auth/register.html",
            context={"error": "Vérifie ton adresse email et tes informations.", "email": email, "firstname": firstname, "lastname": lastname},
        )

    if not 8 <= len(password) <= 1024:
        return templates.TemplateResponse(
            request, "auth/register.html",
            context={"error": "Le mot de passe doit contenir entre 8 et 1 024 caractères.", "email": email, "firstname": firstname, "lastname": lastname},
        )

    # Check if email already exists
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        return templates.TemplateResponse(
            request, "auth/register.html",
            context={"error": "Un compte existe déjà avec cet email.", "email": email, "firstname": firstname, "lastname": lastname},
        )

    verify_token = verification_token()
    user = User(
        email=email,
        password_hash=hash_password(password),
        firstname=firstname or None,
        lastname=lastname or None,
        email_verify_token=verify_token,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    # Send verification email
    sent = send_verification_email(email, verify_token)
    request.session.clear()
    request.session["pending_user_id"] = user.id
    request.session["verification_failed"] = not sent
    logger.info("User %d registered: %s", user.id, email)

    return RedirectResponse(url="/auth/check-email", status_code=302)


@router.get("/auth/check-email", response_class=HTMLResponse)
async def check_email_page(request: Request):
    return templates.TemplateResponse(request, "auth/check_email.html", context={
        "can_resend": bool(request.session.get("pending_user_id")),
        "failed": request.session.pop("verification_failed", False),
    })


@router.post("/auth/resend-verification")
async def resend_verification(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = request.session.get("pending_user_id")
    user = await db.get(User, user_id) if user_id else None
    recent = user and user.email_verify_token and valid_verification_token(user.email_verify_token, max_age=60)
    if user and user.email and not user.email_verified and not recent:
        user.email_verify_token = verification_token()
        await db.flush()
        sent = send_verification_email(user.email, user.email_verify_token)
        request.session["verification_failed"] = not sent
    return RedirectResponse("/auth/check-email", status_code=303)


@router.get("/auth/verify-email")
async def verify_email(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.email_verify_token == token)
    )
    user = result.scalar_one_or_none()

    if not user or not valid_verification_token(token):
        return templates.TemplateResponse(
            request, "auth/verify_result.html",
            context={"success": False, "error": "Lien invalide ou expiré."},
        )

    user.email_verified = True
    user.email_verify_token = None
    await db.flush()

    request.session.clear()
    request.session["user_id"] = user.id
    logger.info("Email verified for user %d", user.id)

    return RedirectResponse(url=HOME, status_code=302)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "auth/login.html", context={})


@router.post("/auth/login")
async def login(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    email = email.strip().lower()

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "auth/login.html",
            context={"error": "Email ou mot de passe incorrect.", "email": email},
        )

    if not user.password_hash.startswith(PASSWORD_PREFIX):
        user.password_hash = hash_password(password)
        await db.flush()
    request.session.clear()
    if not user.email_verified:
        request.session["pending_user_id"] = user.id
        return RedirectResponse("/auth/check-email", status_code=302)
    request.session["user_id"] = user.id
    logger.info("User %d logged in: %s", user.id, email)

    return RedirectResponse(url=HOME, status_code=302)


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


# ---------------------------------------------------------------------------
# Forgot / Reset password
# ---------------------------------------------------------------------------

@router.get("/auth/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "auth/forgot_password.html", context={})


@router.post("/auth/forgot-password")
async def forgot_password(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
):
    email = email.strip().lower()

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user and user.password_hash:
        token = generate_token()
        user.password_reset_token = token
        user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        await db.flush()
        send_password_reset_email(email, token)

    # Always show success (don't reveal if email exists)
    return templates.TemplateResponse(
        request, "auth/forgot_password.html",
        context={"sent": True},
    )


@router.get("/auth/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str):
    return templates.TemplateResponse(
        request, "auth/reset_password.html",
        context={"token": token},
    )


@router.post("/auth/reset-password")
async def reset_password(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: str = Form(...),
    password: str = Form(...),
):
    if not 8 <= len(password) <= 1024:
        return templates.TemplateResponse(
            request, "auth/reset_password.html",
            context={"token": token, "error": "Le mot de passe doit contenir entre 8 et 1 024 caractères."},
        )

    result = await db.execute(
        select(User).where(User.password_reset_token == token)
    )
    user = result.scalar_one_or_none()

    if (not user or not user.password_reset_expires_at
            or user.password_reset_expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)):
        return templates.TemplateResponse(
            request, "auth/reset_password.html",
            context={"token": token, "error": "Lien expiré. Redemande un nouveau lien."},
        )

    user.password_hash = hash_password(password)
    user.password_reset_token = None
    user.password_reset_expires_at = None
    await db.flush()

    request.session.clear()
    if not user.email_verified:
        request.session["pending_user_id"] = user.id
        return RedirectResponse("/auth/check-email", status_code=302)
    request.session["user_id"] = user.id
    logger.info("Password reset for user %d", user.id)

    return RedirectResponse(url=HOME, status_code=302)


# ---------------------------------------------------------------------------
# Strava linking (no longer auth — just account linking)
# ---------------------------------------------------------------------------

def _remember_next(request: Request, next: str | None) -> None:
    """A connect started from Réglages comes back there, whatever happens."""
    if next == "settings":
        request.session["strava_next"] = "/settings#strava"
    else:
        request.session.pop("strava_next", None)


def _back_to_settings(request: Request, error: str) -> RedirectResponse | None:
    url = request.session.pop("strava_next", None)
    if not url:
        return None
    request.session["strava_error"] = error
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/strava")
async def strava_link(request: Request, next: str | None = None,
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    _remember_next(request, next)
    request.session.pop("pending_client_id", None)
    request.session.pop("pending_client_secret", None)
    if not user.has_own_strava_app or not user.strava_credentials_valid:
        return RedirectResponse("/setup?next=settings" if next == "settings" else "/setup", status_code=302)
    state = await begin_strava(request, db, user.id)
    return RedirectResponse(StravaService.for_user(db, user).get_authorize_url(state), status_code=302)


@router.get("/auth/strava/callback")
async def strava_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pending = await consume_strava(request, db, user.id, state)
    if pending is None:
        back = _back_to_settings(request, "La connexion a expiré ou le lien est invalide. Recommence depuis Réglages.")
        return back or RedirectResponse("/setup?error=auth_failed", status_code=302)
    if error or not code:
        back = _back_to_settings(request, "Connexion à Strava annulée." if error == "access_denied"
                                 else "La connexion à Strava a échoué. Réessaie.")
        return back or RedirectResponse("/setup?error=auth_failed", status_code=302)

    pending_id, pending_secret = pending.get("client_id"), pending.get("client_secret")
    if pending_id and pending_secret:
        strava = StravaService(db, client_id=pending_id, client_secret=pending_secret)
    elif user.has_own_strava_app:
        strava = StravaService.for_user(db, user)
    else:
        return RedirectResponse(url="/setup?error=missing_credentials", status_code=302)

    # Exchange code for tokens
    try:
        token_data = await strava.exchange_token(code)
    except Exception as exc:
        logger.exception("Strava token exchange failed")
        typed = pending_id
        request.session.pop("pending_client_secret", None)
        # keys just typed in the wizard: type them again there (and still come
        # back to Réglages after); stored keys refused: say so in Réglages,
        # which then offers the wizard; anything else: just try again
        # only a 401 says the keys are wrong (a 400 is the code: reused by a
        # reload, or expired); never flag a link that is in place meanwhile
        refused = getattr(exc, "status_code", None) == 401
        if not typed and refused and user.has_own_strava_app and not user.strava_access_token:
            user.strava_credentials_valid = False
            await db.commit()
        back = None if typed else _back_to_settings(
            request, "Strava refuse les clés de ton app : change-les." if refused
            else "La connexion à Strava a échoué. Réessaie.")
        return back or RedirectResponse(url="/setup?error=invalid_credentials", status_code=302)

    athlete = token_data.get("athlete", {})
    strava_athlete_id = athlete.get("id")

    if not strava_athlete_id:
        return RedirectResponse(url="/setup?error=auth_failed", status_code=302)

    # Check if another user already has this Strava athlete ID
    existing = await db.execute(
        select(User).where(
            User.strava_athlete_id == strava_athlete_id,
            User.id != user.id,
        )
    )
    old_user = existing.scalar_one_or_none()
    if old_user:
        back = _back_to_settings(request, "Ce compte Strava est déjà lié à un autre compte PaceForge. Aucune donnée n’a été déplacée.")
        return back or RedirectResponse("/setup?error=already_linked", status_code=302)

    # Link Strava to existing user
    user.strava_athlete_id = strava_athlete_id
    user.strava_access_token = token_data["access_token"]
    user.strava_refresh_token = token_data["refresh_token"]
    user.strava_token_expires_at = token_data["expires_at"]
    user.strava_credentials_valid = True  # the keys just worked
    user.firstname = user.firstname or athlete.get("firstname")
    user.lastname = user.lastname or athlete.get("lastname")
    user.profile_picture_url = athlete.get("profile")

    # Persist Strava app credentials from setup wizard
    if pending_id and pending_secret:
        if user.strava_webhook_subscription_id and user.has_own_strava_app and user.strava_client_id != pending_id:
            # another app: its subscription goes (with its own keys), the new app gets one below
            await StravaService.for_user(db, user).delete_webhook_subscription(user.strava_webhook_subscription_id)
            user.strava_webhook_subscription_id = None
        user.strava_client_id = pending_id
        user.strava_client_secret_encrypted = encrypt_secret(pending_secret)
        user.strava_credentials_valid = True
        request.session.pop("pending_client_id", None)
        request.session.pop("pending_client_secret", None)

    await db.flush()
    logger.info("Strava linked for user %d (athlete %d)", user.id, strava_athlete_id)

    # Create webhook subscription
    if user.has_own_strava_app and not user.strava_webhook_subscription_id:
        try:
            user_strava = StravaService.for_user(db, user)
            sub_id = await user_strava.create_webhook_subscription(user)
            if sub_id:
                user.strava_webhook_subscription_id = sub_id
                await db.flush()
        except Exception:
            logger.exception("Failed to create webhook for user %d", user.id)

    # Workers must see the committed link; broker failure must not undo it.
    await db.commit()
    if not user.initial_sync_done:
        from app.tasks.initial_sync import initial_sync
        try:
            initial_sync.delay(user.id)
        except Exception:
            logger.exception("Initial sync could not be queued for user %d", user.id)
            request.session["strava_error"] = "Strava connecté. La synchronisation n’a pas démarré ; réessaie depuis Réglages."

    next_url = request.session.pop("strava_next", None)
    if next_url and not request.session.get("strava_error"):
        request.session["strava_ok"] = "Strava connecté. Tes activités arrivent."
    return RedirectResponse(url=next_url or HOME, status_code=302)  # an explicit « next » first


# ---------------------------------------------------------------------------
# Setup (Strava API credentials)
# ---------------------------------------------------------------------------

@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, error: str | None = None, next: str | None = None):
    if next is not None or error is None:  # a fresh visit; an error page keeps the way back
        _remember_next(request, next)
    return templates.TemplateResponse(
        request, "setup.html",
        context={
            "error": error,
            "client_id": request.session.get("pending_client_id", ""),
        },
    )


@router.post("/setup/credentials")
async def setup_credentials(
    request: Request,
    client_id: str = Form(...),
    client_secret: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.isascii() or not client_id.isdecimal() or len(client_id) > 20 or not 1 <= len(client_secret) <= 512:
        return RedirectResponse(url="/setup?error=missing_credentials", status_code=302)
    state = await begin_strava(request, db, user.id, {"client_id": client_id, "client_secret": client_secret})
    strava = StravaService(db, client_id=client_id, client_secret=client_secret)
    return RedirectResponse(strava.get_authorize_url(state), status_code=302)


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------

@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html")
