import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_secret
from app.dependencies import get_db
from app.models.user import User
from app.services.auth import generate_token, hash_password, verify_password
from app.services.email import send_password_reset_email, send_verification_email
from app.services.strava import StravaService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


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

    if not email or not password:
        return templates.TemplateResponse(
            request, "auth/register.html",
            context={"error": "Email et mot de passe requis.", "email": email, "firstname": firstname, "lastname": lastname},
        )

    if len(password) < 8:
        return templates.TemplateResponse(
            request, "auth/register.html",
            context={"error": "Le mot de passe doit faire au moins 8 caractères.", "email": email, "firstname": firstname, "lastname": lastname},
        )

    # Check if email already exists
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        return templates.TemplateResponse(
            request, "auth/register.html",
            context={"error": "Un compte existe déjà avec cet email.", "email": email, "firstname": firstname, "lastname": lastname},
        )

    verify_token = generate_token()
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
    send_verification_email(email, verify_token)

    request.session["user_id"] = user.id
    logger.info("User %d registered: %s", user.id, email)

    return RedirectResponse(url="/auth/check-email", status_code=302)


@router.get("/auth/check-email", response_class=HTMLResponse)
async def check_email_page(request: Request):
    return templates.TemplateResponse(request, "auth/check_email.html", context={})


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

    if not user:
        return templates.TemplateResponse(
            request, "auth/verify_result.html",
            context={"success": False, "error": "Lien invalide ou expiré."},
        )

    user.email_verified = True
    user.email_verify_token = None
    await db.flush()

    request.session["user_id"] = user.id
    logger.info("Email verified for user %d", user.id)

    return RedirectResponse(url="/dashboard", status_code=302)


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

    request.session["user_id"] = user.id
    logger.info("User %d logged in: %s", user.id, email)

    return RedirectResponse(url="/dashboard", status_code=302)


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
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "auth/reset_password.html",
            context={"token": token, "error": "Le mot de passe doit faire au moins 8 caractères."},
        )

    result = await db.execute(
        select(User).where(User.password_reset_token == token)
    )
    user = result.scalar_one_or_none()

    if not user or not user.password_reset_expires_at or user.password_reset_expires_at < datetime.now(timezone.utc):
        return templates.TemplateResponse(
            request, "auth/reset_password.html",
            context={"token": token, "error": "Lien expiré. Redemande un nouveau lien."},
        )

    user.password_hash = hash_password(password)
    user.password_reset_token = None
    user.password_reset_expires_at = None
    await db.flush()

    request.session["user_id"] = user.id
    logger.info("Password reset for user %d", user.id)

    return RedirectResponse(url="/dashboard", status_code=302)


# ---------------------------------------------------------------------------
# Strava linking (no longer auth — just account linking)
# ---------------------------------------------------------------------------

@router.get("/auth/strava")
async def strava_link(request: Request, db: AsyncSession = Depends(get_db)):
    """Redirect to Strava OAuth to link account. User must be logged in."""
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/auth/login", status_code=302)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)

    # Check for pending credentials from setup wizard
    pending_id = request.session.get("pending_client_id")
    pending_secret = request.session.get("pending_client_secret")

    if pending_id and pending_secret:
        strava = StravaService(db, client_id=pending_id, client_secret=pending_secret)
    elif user.has_own_strava_app:
        strava = StravaService.for_user(db, user)
    else:
        return RedirectResponse(url="/setup", status_code=302)

    return RedirectResponse(url=strava.get_authorize_url(), status_code=302)


@router.get("/auth/strava/callback")
async def strava_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if error or not code:
        logger.error("Strava OAuth error: %s", error)
        return RedirectResponse(url="/setup?error=auth_failed", status_code=302)

    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/auth/login", status_code=302)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)

    # Determine credentials for token exchange
    pending_id = request.session.get("pending_client_id")
    pending_secret = request.session.get("pending_client_secret")

    if pending_id and pending_secret:
        strava = StravaService(db, client_id=pending_id, client_secret=pending_secret)
    elif user.has_own_strava_app:
        strava = StravaService.for_user(db, user)
    else:
        return RedirectResponse(url="/setup?error=missing_credentials", status_code=302)

    # Exchange code for tokens
    try:
        token_data = await strava.exchange_token(code)
    except Exception:
        logger.exception("Strava token exchange failed")
        request.session.pop("pending_client_id", None)
        request.session.pop("pending_client_secret", None)
        return RedirectResponse(url="/setup?error=invalid_credentials", status_code=302)

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
        # Migrate activities from old account to current user
        from app.models.activity import Activity

        await db.execute(
            Activity.__table__.update()
            .where(Activity.__table__.c.user_id == old_user.id)
            .values(user_id=user.id)
        )
        # Carry over settings that the new user doesn't have yet
        user.initial_sync_done = old_user.initial_sync_done
        if old_user.preferred_sports and not user.preferred_sports:
            user.preferred_sports = old_user.preferred_sports
        if old_user.weekly_volume_target_km and not user.weekly_volume_target_km:
            user.weekly_volume_target_km = old_user.weekly_volume_target_km
        if old_user.weight_kg and not user.weight_kg:
            user.weight_kg = old_user.weight_kg
        if old_user.race_name and not user.race_name:
            user.race_name = old_user.race_name
            user.race_date = old_user.race_date
            user.race_distance_km = old_user.race_distance_km

        # Remove old user (strava_athlete_id unique constraint)
        await db.delete(old_user)
        await db.flush()
        logger.info("Merged old user %d into user %d (athlete %d)", old_user.id, user.id, strava_athlete_id)

    # Link Strava to existing user
    user.strava_athlete_id = strava_athlete_id
    user.strava_access_token = token_data["access_token"]
    user.strava_refresh_token = token_data["refresh_token"]
    user.strava_token_expires_at = token_data["expires_at"]
    user.firstname = user.firstname or athlete.get("firstname")
    user.lastname = user.lastname or athlete.get("lastname")
    user.profile_picture_url = athlete.get("profile")

    # Persist Strava app credentials from setup wizard
    if pending_id and pending_secret:
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

    # Trigger initial sync
    if not user.initial_sync_done:
        from app.tasks.initial_sync import initial_sync
        initial_sync.delay(user.id)
        logger.info("Triggered initial sync for user %d", user.id)

    await db.commit()
    return RedirectResponse(url="/dashboard", status_code=302)


# ---------------------------------------------------------------------------
# Setup (Strava API credentials)
# ---------------------------------------------------------------------------

@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, error: str | None = None):
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
):
    client_id = client_id.strip()
    client_secret = client_secret.strip()

    if not client_id or not client_secret:
        return RedirectResponse(url="/setup?error=missing_credentials", status_code=302)

    request.session["pending_client_id"] = client_id
    request.session["pending_client_secret"] = client_secret

    strava = StravaService(None, client_id=client_id, client_secret=client_secret)
    return RedirectResponse(url=strava.get_authorize_url(), status_code=302)


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------

@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})
