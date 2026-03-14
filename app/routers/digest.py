import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.models.weekly_digest import WeeklyDigest

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["digest"])


@router.get("/digests", response_class=HTMLResponse)
async def digests_list(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WeeklyDigest)
        .where(WeeklyDigest.user_id == user.id)
        .order_by(WeeklyDigest.week_start.desc())
        .limit(20)
    )
    digests = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "digests.html",
        context={
            "user": user,
            "digests": digests,
        },
    )


@router.get("/digest/{digest_id}", response_class=HTMLResponse)
async def digest_detail(
    request: Request,
    digest_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WeeklyDigest).where(
            WeeklyDigest.id == digest_id,
            WeeklyDigest.user_id == user.id,
        )
    )
    digest = result.scalar_one_or_none()
    if not digest:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/digests", status_code=302)

    return templates.TemplateResponse(
        request,
        "digest_detail.html",
        context={
            "user": user,
            "digest": digest,
        },
    )
