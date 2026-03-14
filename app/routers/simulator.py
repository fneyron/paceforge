import json
import logging

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.user import User

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["simulator"])


@router.get("/simulator", response_class=HTMLResponse)
async def simulator_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Estimate FTP for power tab
    from app.services.power_calculator import estimate_ftp

    ftp = await estimate_ftp(db, user.id)

    return templates.TemplateResponse(
        request,
        "simulator.html",
        context={
            "user": user,
            "ftp": ftp,
            "rider_weight": 75,  # Default, could be stored in user profile
        },
    )


@router.post("/partials/simulator/gpx-upload", response_class=HTMLResponse)
async def gpx_upload(
    request: Request,
    gpx_file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.gpx import build_course_profile, parse_gpx
    from app.services.race_simulator import build_athlete_gradient_profile, predict_course

    try:
        content = await gpx_file.read()
        points = parse_gpx(content)
        course = build_course_profile(points, name=gpx_file.filename or "Course")

        # Build athlete profile and predict
        profile = await build_athlete_gradient_profile(db, user.id)
        course = predict_course(course, profile)

        return templates.TemplateResponse(
            request,
            "partials/gpx_result.html",
            context={
                "course": course,
                "profile": profile,
                "course_json": course.model_dump_json(),
            },
        )
    except ValueError as e:
        return HTMLResponse(
            f'<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">{e}</div>'
        )
    except Exception:
        logger.exception("GPX upload failed")
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">'
            "Erreur lors de l'analyse du fichier GPX. Vérifiez le format du fichier."
            "</div>"
        )


@router.post("/partials/simulator/race-strategy", response_class=HTMLResponse)
async def race_strategy(
    request: Request,
    course_json: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone

    from app.services.claude import ClaudeService
    from app.services.race_simulator import build_athlete_gradient_profile
    from app.services.training_load import calculate_training_load

    try:
        course_data = json.loads(course_json)
        profile = await build_athlete_gradient_profile(db, user.id)

        now = datetime.now(timezone.utc)
        training_load = await calculate_training_load(db, user.id, now)
        tl_dict = {
            "volume_7d_km": training_load.volume_7d_km,
            "count_7d": training_load.count_7d,
            "volume_28d_km": training_load.volume_28d_km,
            "count_28d": training_load.count_28d,
        }

        claude = ClaudeService()
        strategy = await claude.generate_race_strategy(
            course_data=course_data,
            athlete_flat_pace=profile.flat_pace_s_per_km,
            data_points=profile.data_points,
            training_load=tl_dict,
            race_name=course_data.get("name"),
        )

        return templates.TemplateResponse(
            request,
            "partials/race_strategy_card.html",
            context={"strategy": strategy},
        )
    except Exception:
        logger.exception("Race strategy generation failed")
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">'
            "Erreur lors de la génération de la stratégie. Réessayez."
            "</div>"
        )


@router.post("/partials/simulator/power-calc", response_class=HTMLResponse)
async def power_calc(
    request: Request,
    gradient_pct: float = Form(...),
    length_km: float = Form(...),
    rider_weight_kg: float = Form(...),
    bike_weight_kg: float = Form(9.0),
    target_time_min: float | None = Form(None),
    target_watts: float | None = Form(None),
    user: User = Depends(get_current_user),
):
    from app.schemas.simulator import PowerCalcInput
    from app.services.power_calculator import calculate_from_input

    try:
        target_time_s = int(target_time_min * 60) if target_time_min else None

        calc_input = PowerCalcInput(
            gradient_pct=gradient_pct,
            length_km=length_km,
            rider_weight_kg=rider_weight_kg,
            bike_weight_kg=bike_weight_kg,
            target_time_s=target_time_s,
            target_watts=target_watts if not target_time_s else None,
        )

        result = calculate_from_input(calc_input)

        return templates.TemplateResponse(
            request,
            "partials/power_result.html",
            context={"result": result},
        )
    except ValueError as e:
        return HTMLResponse(
            f'<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">{e}</div>'
        )
    except Exception:
        logger.exception("Power calculation failed")
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">'
            "Erreur de calcul. Vérifiez les valeurs saisies."
            "</div>"
        )
