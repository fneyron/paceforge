import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.user import User
from app.services.strava import StravaService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/auth/strava")
async def strava_login(db: AsyncSession = Depends(get_db)):
    strava = StravaService(db)
    return RedirectResponse(url=strava.get_authorize_url(), status_code=302)



@router.get("/auth/strava/callback")
async def strava_callback(
    request: Request,
    code: str,
    db: AsyncSession = Depends(get_db),
):
    strava = StravaService(db)
    token_data = await strava.exchange_token(code)

    athlete = token_data.get("athlete", {})
    strava_athlete_id = athlete.get("id")

    if not strava_athlete_id:
        logger.error("No athlete ID in Strava response")
        return RedirectResponse(url="/?error=auth_failed", status_code=302)

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

    await db.flush()
    await db.refresh(user)

    # Set session
    request.session["user_id"] = user.id
    logger.info("User %d authenticated via Strava", user.id)

    # Trigger initial sync if not done yet
    if not user.initial_sync_done:
        from app.tasks.initial_sync import initial_sync
        initial_sync.delay(user.id)
        logger.info("Triggered initial sync for user %d", user.id)

    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})
