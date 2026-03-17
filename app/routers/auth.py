import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_secret
from app.dependencies import get_db
from app.models.user import User
from app.services.strava import StravaService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, error: str | None = None):
    """Onboarding wizard to configure Strava app credentials."""
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
    """Store credentials in session and redirect to Strava OAuth."""
    client_id = client_id.strip()
    client_secret = client_secret.strip()

    if not client_id or not client_secret:
        return RedirectResponse(url="/setup?error=missing_credentials", status_code=302)

    # Store in session temporarily (will be persisted after successful OAuth)
    request.session["pending_client_id"] = client_id
    request.session["pending_client_secret"] = client_secret

    # Redirect to Strava OAuth using user's credentials
    strava = StravaService(request.state.db if hasattr(request.state, "db") else None,
                           client_id=client_id, client_secret=client_secret)
    return RedirectResponse(url=strava.get_authorize_url(), status_code=302)


@router.get("/auth/strava")
async def strava_login(request: Request, db: AsyncSession = Depends(get_db)):
    """Redirect to Strava OAuth. Uses pending credentials from session if available."""
    pending_id = request.session.get("pending_client_id")
    pending_secret = request.session.get("pending_client_secret")

    if pending_id and pending_secret:
        strava = StravaService(db, client_id=pending_id, client_secret=pending_secret)
    else:
        # Check if user is re-authenticating (has credentials in DB)
        user_id = request.session.get("user_id")
        if user_id:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user and user.has_own_strava_app:
                strava = StravaService.for_user(db, user)
            else:
                return RedirectResponse(url="/setup", status_code=302)
        else:
            # No credentials anywhere — send to setup
            return RedirectResponse(url="/setup", status_code=302)

    return RedirectResponse(url=strava.get_authorize_url(), status_code=302)


@router.post("/auth/reconnect")
async def reconnect(
    request: Request,
    db: AsyncSession = Depends(get_db),
    client_id: str = Form(...),
):
    """Re-login: look up user by their Client ID, use stored credentials for OAuth."""
    client_id = client_id.strip()
    if not client_id:
        return RedirectResponse(url="/?error=missing_client_id", status_code=302)

    result = await db.execute(
        select(User).where(User.strava_client_id == client_id)
    )
    user = result.scalar_one_or_none()

    if not user or not user.has_own_strava_app:
        return RedirectResponse(url="/?error=unknown_client_id", status_code=302)

    strava = StravaService.for_user(db, user)
    # Store client_id in session so callback knows this is a reconnect
    request.session["reconnect_client_id"] = client_id
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

    # Determine which credentials to use for token exchange
    pending_id = request.session.get("pending_client_id")
    pending_secret = request.session.get("pending_client_secret")
    reconnect_client_id = request.session.get("reconnect_client_id")

    if pending_id and pending_secret:
        # New user from setup wizard
        strava = StravaService(db, client_id=pending_id, client_secret=pending_secret)
    elif reconnect_client_id:
        # Returning user via reconnect
        result = await db.execute(
            select(User).where(User.strava_client_id == reconnect_client_id)
        )
        existing_user = result.scalar_one_or_none()
        if existing_user and existing_user.has_own_strava_app:
            strava = StravaService.for_user(db, existing_user)
        else:
            return RedirectResponse(url="/?error=unknown_client_id", status_code=302)
        request.session.pop("reconnect_client_id", None)
    else:
        # Re-auth: look up user's stored credentials by session
        user_id = request.session.get("user_id")
        if user_id:
            result = await db.execute(select(User).where(User.id == user_id))
            existing_user = result.scalar_one_or_none()
            if existing_user and existing_user.has_own_strava_app:
                strava = StravaService.for_user(db, existing_user)
            else:
                return RedirectResponse(url="/setup?error=missing_credentials", status_code=302)
        else:
            return RedirectResponse(url="/setup?error=missing_credentials", status_code=302)

    # Exchange code for tokens
    try:
        token_data = await strava.exchange_token(code)
    except Exception:
        logger.exception("Strava token exchange failed")
        # Clear pending credentials on failure
        request.session.pop("pending_client_id", None)
        request.session.pop("pending_client_secret", None)
        return RedirectResponse(url="/setup?error=invalid_credentials", status_code=302)

    athlete = token_data.get("athlete", {})
    strava_athlete_id = athlete.get("id")

    if not strava_athlete_id:
        logger.error("No athlete ID in Strava response")
        return RedirectResponse(url="/setup?error=auth_failed", status_code=302)

    # Upsert user
    result = await db.execute(
        select(User).where(User.strava_athlete_id == strava_athlete_id)
    )
    user = result.scalar_one_or_none()

    if user:
        user.strava_access_token = token_data["access_token"]
        user.strava_refresh_token = token_data["refresh_token"]
        user.strava_token_expires_at = token_data["expires_at"]
        user.firstname = athlete.get("firstname")
        user.lastname = athlete.get("lastname")
        user.profile_picture_url = athlete.get("profile")
    else:
        user = User(
            strava_athlete_id=strava_athlete_id,
            strava_access_token=token_data["access_token"],
            strava_refresh_token=token_data["refresh_token"],
            strava_token_expires_at=token_data["expires_at"],
            firstname=athlete.get("firstname"),
            lastname=athlete.get("lastname"),
            profile_picture_url=athlete.get("profile"),
        )
        db.add(user)

    # Persist per-user Strava app credentials (from setup wizard)
    if pending_id and pending_secret:
        user.strava_client_id = pending_id
        user.strava_client_secret_encrypted = encrypt_secret(pending_secret)
        user.strava_credentials_valid = True
        # Clear from session
        request.session.pop("pending_client_id", None)
        request.session.pop("pending_client_secret", None)

    await db.flush()
    await db.refresh(user)

    # Set session
    request.session["user_id"] = user.id
    logger.info("User %d authenticated via Strava", user.id)

    # Create webhook subscription for the user's app
    if user.has_own_strava_app and not user.strava_webhook_subscription_id:
        try:
            user_strava = StravaService.for_user(db, user)
            sub_id = await user_strava.create_webhook_subscription(user)
            if sub_id:
                user.strava_webhook_subscription_id = sub_id
                await db.flush()
        except Exception:
            logger.exception("Failed to create webhook for user %d", user.id)

    # Trigger initial sync if not done yet
    if not user.initial_sync_done:
        from app.tasks.initial_sync import initial_sync
        initial_sync.delay(user.id)
        logger.info("Triggered initial sync for user %d", user.id)

    await db.commit()
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})
