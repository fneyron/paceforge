"""Santé: recovery, training load, trends and fitness from the athlete's COROS or Garmin watch."""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.services import coros, garmin
from app.services.sante import PERIODS, health_page

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["sante"])


@router.get("/sante", response_class=HTMLResponse)
async def sante_page(
    request: Request,
    jours: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    days = jours if jours in PERIODS else PERIODS[0]
    status = await coros.coros_status(db, user.id)
    garmin_link = await garmin.garmin_status(db, user.id)
    try:
        page = await health_page(db, user.id, days)
    except Exception:  # shown as an error, never as "connect your watch"
        logger.exception("Santé page failed for user %d", user.id)
        page = None
    return templates.TemplateResponse(
        request, "sante.html",
        context={"user": user, "coros": status, "garmin": garmin_link, "page": page, "days": days, "periods": PERIODS},
    )
