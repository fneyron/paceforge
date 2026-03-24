import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.chat_message import ChatMessage
from app.models.user import User

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["coach"])


@router.get("/coach", response_class=HTMLResponse)
async def coach_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Load recent chat history
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == user.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(50)
    )
    messages = list(reversed(result.scalars().all()))

    return templates.TemplateResponse(
        request,
        "coach.html",
        context={
            "user": user,
            "messages": messages,
        },
    )


@router.post("/partials/coach/send", response_class=HTMLResponse)
async def send_message(
    request: Request,
    message: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.prompts.coach_chat import build_coach_context
    from app.schemas.activity import ActivitySummary
    from app.services.claude import ClaudeService
    from app.services.training_load import calculate_training_load
    from app.services.training_zones import estimate_training_zones

    message = message.strip()
    if not message:
        return HTMLResponse("")

    # Save user message
    user_msg = ChatMessage(user_id=user.id, role="user", content=message)
    db.add(user_msg)
    await db.flush()

    try:
        # Build context
        now = datetime.now(timezone.utc)
        training_load = await calculate_training_load(db, user.id, now)
        zones = await estimate_training_zones(db, user.id)

        # Get recent activities for context
        from app.models.activity import Activity
        acts_result = await db.execute(
            select(Activity)
            .where(Activity.user_id == user.id)
            .order_by(Activity.start_date.desc())
            .limit(10)
        )
        recent_acts = acts_result.scalars().all()
        recent_data = []
        for a in recent_acts:
            summary = ActivitySummary(
                id=a.id, strava_activity_id=a.strava_activity_id,
                sport_type=a.sport_type, name=a.name, start_date=a.start_date,
                distance=a.distance, moving_time=a.moving_time,
                average_speed=a.average_speed, average_heartrate=a.average_heartrate,
                total_elevation_gain=a.total_elevation_gain,
            )
            recent_data.append({
                "name": a.name,
                "sport_type": a.sport_type,
                "distance_km": summary.distance_km,
                "duration_formatted": summary.duration_formatted,
                "pace_formatted": summary.pace_formatted,
                "average_heartrate": a.average_heartrate,
                "total_elevation_gain": a.total_elevation_gain,
            })

        # Race goal
        race_goal = None
        if user.race_name and user.race_date and user.race_date > now:
            race_goal = {
                "name": user.race_name,
                "date": user.race_date.strftime("%d/%m/%Y"),
                "distance_km": user.race_distance_km,
                "days_remaining": (user.race_date - now).days,
            }

        context = build_coach_context(
            user_data={"firstname": user.firstname, "weight_kg": user.weight_kg},
            training_load={
                "volume_7d_km": training_load.volume_7d_km,
                "volume_7d_hours": training_load.volume_7d_hours,
                "count_7d": training_load.count_7d,
                "volume_28d_km": training_load.volume_28d_km,
                "volume_28d_hours": training_load.volume_28d_hours,
                "count_28d": training_load.count_28d,
                "sport_breakdown_7d": training_load.sport_breakdown_7d,
            },
            recent_activities=recent_data,
            race_goal=race_goal,
            zones=zones,
        )

        # Load recent conversation for Claude (last 20 messages)
        history_result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.user_id == user.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(20)
        )
        history = list(reversed(history_result.scalars().all()))

        claude_messages = []
        for msg in history:
            claude_messages.append({"role": msg.role, "content": msg.content})

        # Call Claude
        claude = ClaudeService()
        reply = await claude.chat(claude_messages, context)

        # Save assistant reply
        assistant_msg = ChatMessage(user_id=user.id, role="assistant", content=reply)
        db.add(assistant_msg)
        await db.flush()

        # Return only assistant reply (user message already shown in JS)
        return templates.TemplateResponse(
            request,
            "partials/chat_messages.html",
            context={
                "new_messages": [assistant_msg],
                "user": user,
            },
        )

    except Exception:
        logger.exception("Coach chat error")
        error_msg = ChatMessage(
            user_id=user.id, role="assistant",
            content="Désolé, une erreur est survenue. Réessaie dans quelques instants."
        )
        db.add(error_msg)
        await db.flush()

        return templates.TemplateResponse(
            request,
            "partials/chat_messages.html",
            context={
                "new_messages": [error_msg],
                "user": user,
            },
        )


@router.delete("/partials/coach/clear", response_class=HTMLResponse)
async def clear_chat(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import delete
    await db.execute(
        delete(ChatMessage).where(ChatMessage.user_id == user.id)
    )
    await db.flush()
    return HTMLResponse("")
