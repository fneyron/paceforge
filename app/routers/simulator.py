import json
import logging

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.nutrition import NutritionProduct
from app.models.route import Route, RouteCheckpoint
from app.models.user import User

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

# Expose weather-code → icon-category / label helpers to all simulator templates.
from app.services.weather import WMO_LABELS, wmo_category  # noqa: E402

templates.env.globals["wmo_category"] = wmo_category
templates.env.globals["wmo_label"] = lambda code: WMO_LABELS.get(wmo_category(code), "")

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

    # Saved routes
    result = await db.execute(
        select(Route)
        .where(Route.user_id == user.id)
        .order_by(Route.created_at.desc())
        .limit(20)
    )
    saved_routes = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "simulator.html",
        context={
            "user": user,
            "ftp": ftp,
            "rider_weight": user.weight_kg or 75,
            "saved_routes": saved_routes,
        },
    )


@router.post("/partials/simulator/gpx-upload", response_class=HTMLResponse)
async def gpx_upload(
    request: Request,
    gpx_file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Parse an uploaded GPX, persist it as a new Route, then redirect (via
    HX-Redirect) to its own detail page — so refresh/share/back work natively."""
    from app.services.gpx import (
        build_course_profile,
        parse_gpx,
        snap_waypoints_to_route,
    )
    from app.services.race_simulator import build_athlete_gradient_profile, predict_course

    try:
        content = await gpx_file.read()
        points, gpx_waypoints = parse_gpx(content)
        course = build_course_profile(points, name=gpx_file.filename or "Course")

        # Snap GPX waypoints to route
        snapped_wpts = snap_waypoints_to_route(gpx_waypoints, points)

        # Build athlete profile and predict
        profile = await build_athlete_gradient_profile(db, user.id)
        course = predict_course(course, profile)

        course_data = json.loads(course.model_dump_json())
        route = Route(
            user_id=user.id,
            name=course.name,
            total_distance_km=course.total_distance_km,
            total_elevation_gain=course.total_elevation_gain,
            total_elevation_loss=course.total_elevation_loss,
            course_json=course_data,
            sport_type="trail",
        )
        db.add(route)
        await db.flush()

        for wpt in snapped_wpts:
            db.add(RouteCheckpoint(
                route_id=route.id,
                name=wpt.get("name", ""),
                distance_km=wpt.get("distance_km", 0),
                elevation=wpt.get("elevation"),
            ))
        await db.flush()

        logger.info("Route %d created from GPX for user %d", route.id, user.id)
        # HTMX swaps nothing; it follows the redirect to the new detail page.
        return HTMLResponse(
            status_code=204,
            headers={"HX-Redirect": f"/simulator/routes/{route.id}"},
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

    from app.config import settings
    from app.services.claude import ClaudeService
    from app.services.race_simulator import build_athlete_gradient_profile
    from app.services.training_load import calculate_training_load

    if not settings.ANTHROPIC_API_KEY:
        return HTMLResponse(
            '<div class="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-700">'
            "Stratégie IA indisponible : la clé API Anthropic n'est pas configurée sur le serveur "
            "(<code>ANTHROPIC_API_KEY</code>)."
            "</div>"
        )

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
    except Exception as e:
        logger.exception("Race strategy generation failed")
        import html as _html
        detail = _html.escape(f"{type(e).__name__}: {e}"[:300])
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">'
            "Erreur lors de la génération de la stratégie."
            f'<br><span class="text-[11px] text-red-400 font-mono">{detail}</span>'
            "</div>"
        )


@router.post("/partials/simulator/passage-times", response_class=HTMLResponse)
async def passage_times(
    request: Request,
    course_json: str = Form(...),
    checkpoints_json: str = Form(default="[]"),
    target_time_s: int | None = Form(default=None),
    heat_factor: float = Form(default=1.0),
    start_hour: int = Form(default=6),
    start_minute: int = Form(default=0),
    hourly_json: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.schemas.simulator import CourseProfile
    from app.services.race_simulator import (
        _elevation_at_km,
        build_athlete_gradient_profile,
        compute_passage_times,
        predict_course,
    )

    try:
        course = CourseProfile(**json.loads(course_json))
        checkpoints = json.loads(checkpoints_json)
        hourly_weather = json.loads(hourly_json) if hourly_json else None

        # Re-predict so the night penalty reflects the chosen start time.
        # Heat is applied once, in compute_passage_times.
        profile = await build_athlete_gradient_profile(db, user.id)
        course = predict_course(
            course, profile, start_hour=start_hour, start_minute=start_minute
        )

        sections = compute_passage_times(
            course, checkpoints, target_time_s, heat_factor,
            start_hour, start_minute, hourly_weather,
        )
        has_weather = any(s.get("temperature_c") is not None for s in sections)

        return templates.TemplateResponse(
            request,
            "partials/passage_times.html",
            context={
                "sections": sections,
                "has_target": target_time_s is not None,
                "has_weather": has_weather,
                "predicted_total": course.predicted_total_time_s,
                "target_total": target_time_s,
                "start_offset_s": start_hour * 3600 + start_minute * 60,
                "start_elevation": _elevation_at_km(course, 0.0),
                "total_distance_km": course.total_distance_km,
            },
        )
    except Exception:
        logger.exception("Passage time calculation failed")
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">'
            "Erreur lors du calcul des temps de passage."
            "</div>"
        )


@router.post("/partials/simulator/bike-gpx-upload", response_class=HTMLResponse)
async def bike_gpx_upload(
    request: Request,
    gpx_file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.cycling_simulator import estimate_cda, predict_cycling_course
    from app.services.gpx import build_course_profile, parse_gpx
    from app.services.power_calculator import estimate_ftp

    try:
        content = await gpx_file.read()
        points, _ = parse_gpx(content)
        course = build_course_profile(points, name=gpx_file.filename or "Parcours velo")

        ftp = await estimate_ftp(db, user.id)
        rider_weight = user.weight_kg or 75
        cda_est = await estimate_cda(db, user.id, rider_weight)
        cda_default = cda_est.estimated_cda if cda_est else 0.32
        # Default target power: 75% of FTP (endurance) if known, else 200W
        default_target = round(ftp.estimated_ftp * 0.75) if ftp else 200

        cycling = predict_cycling_course(
            course,
            target_power_watts=default_target,
            rider_weight_kg=rider_weight,
            cda=cda_default,
            ftp_watts=ftp.estimated_ftp if ftp else None,
        )

        return templates.TemplateResponse(
            request,
            "partials/bike_gpx_result.html",
            context={
                "cycling": cycling,
                "ftp": ftp,
                "cda_est": cda_est,
                "cycling_json": cycling.model_dump_json(),
            },
        )
    except ValueError as e:
        return HTMLResponse(
            f'<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">{e}</div>'
        )
    except Exception:
        logger.exception("Bike GPX upload failed")
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">'
            "Erreur lors de l'analyse du fichier GPX velo."
            "</div>"
        )


@router.post("/partials/simulator/bike-predict", response_class=HTMLResponse)
async def bike_predict(
    request: Request,
    cycling_json: str = Form(...),
    target_power_watts: float = Form(...),
    rider_weight_kg: float = Form(...),
    bike_weight_kg: float = Form(9.0),
    cda: float = Form(0.32),
    crr: float = Form(0.005),
    wind_speed_kmh: float = Form(0.0),
    wind_direction_deg: float | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.schemas.simulator import CourseProfile, CourseSegment, CyclingProfile
    from app.services.cycling_simulator import estimate_cda, predict_cycling_course
    from app.services.power_calculator import estimate_ftp

    try:
        prev = CyclingProfile(**json.loads(cycling_json))
        # Reconstruct a CourseProfile from the cycling profile (segments share the
        # geometric fields predict_cycling_course needs).
        course = CourseProfile(
            name=prev.name,
            total_distance_km=prev.total_distance_km,
            total_elevation_gain=prev.total_elevation_gain,
            total_elevation_loss=prev.total_elevation_loss,
            segments=[
                CourseSegment(
                    index=s.index, start_km=s.start_km, end_km=s.end_km,
                    distance_m=s.distance_m, elevation_gain=s.elevation_gain,
                    elevation_loss=s.elevation_loss, avg_gradient_pct=s.avg_gradient_pct,
                    min_elevation=s.min_elevation, max_elevation=s.max_elevation,
                )
                for s in prev.segments
            ],
            elevation_points=prev.elevation_points,
            route_coords=prev.route_coords,
            km_markers=prev.km_markers,
        )

        ftp = await estimate_ftp(db, user.id)
        cda_est = await estimate_cda(db, user.id, rider_weight_kg, bike_weight_kg, crr=crr)
        cycling = predict_cycling_course(
            course,
            target_power_watts=target_power_watts,
            rider_weight_kg=rider_weight_kg,
            bike_weight_kg=bike_weight_kg,
            cda=cda,
            crr=crr,
            wind_speed_kmh=wind_speed_kmh,
            wind_direction_deg=wind_direction_deg,
            wind_source=prev.wind_source,
            ftp_watts=ftp.estimated_ftp if ftp else None,
        )

        return templates.TemplateResponse(
            request,
            "partials/bike_gpx_result.html",
            context={
                "cycling": cycling,
                "ftp": ftp,
                "cda_est": cda_est,
                "cycling_json": cycling.model_dump_json(),
            },
        )
    except Exception:
        logger.exception("Bike predict failed")
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">'
            "Erreur lors du calcul. Verifiez les valeurs saisies."
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


# ── Save / Load routes ──

@router.post("/api/simulator/routes")
async def save_route(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    course_json: str = Form(...),
    checkpoints_json: str = Form(default="[]"),
    name: str = Form(default=""),
    route_id: int | None = Form(default=None),
    target_time_s: int | None = Form(default=None),
    race_date: str | None = Form(default=None),
    start_hour: int | None = Form(default=None),
    start_minute: int | None = Form(default=None),
    sport_type: str = Form(default="trail"),
    weather_json: str | None = Form(default=None),
):
    try:
        course_data = json.loads(course_json)
        cps = json.loads(checkpoints_json)
        weather_data = json.loads(weather_json) if weather_json else None

        # Update existing route or create new one
        route = None
        if route_id:
            result = await db.execute(
                select(Route).where(Route.id == route_id, Route.user_id == user.id)
            )
            route = result.scalar_one_or_none()

        if route:
            # Update existing
            route.name = name or route.name
            route.course_json = course_data
            route.total_distance_km = course_data.get("total_distance_km", route.total_distance_km)
            route.total_elevation_gain = course_data.get("total_elevation_gain", route.total_elevation_gain)
            route.total_elevation_loss = course_data.get("total_elevation_loss", route.total_elevation_loss)
            route.target_time_s = target_time_s
            if race_date: route.race_date = race_date
            if start_hour is not None: route.start_hour = start_hour
            if start_minute is not None: route.start_minute = start_minute
            if sport_type: route.sport_type = sport_type
            if weather_data is not None: route.weather_json = weather_data

            # Delete old checkpoints and replace
            from sqlalchemy import delete
            await db.execute(
                delete(RouteCheckpoint).where(RouteCheckpoint.route_id == route.id)
            )
        else:
            # Create new
            route = Route(
                user_id=user.id,
                name=name or course_data.get("name", "Parcours"),
                total_distance_km=course_data.get("total_distance_km", 0),
                total_elevation_gain=course_data.get("total_elevation_gain", 0),
                total_elevation_loss=course_data.get("total_elevation_loss", 0),
                course_json=course_data,
                target_time_s=target_time_s,
                race_date=race_date,
                start_hour=start_hour,
                start_minute=start_minute,
                sport_type=sport_type or "trail",
                weather_json=weather_data,
            )
            db.add(route)

        await db.flush()

        for cp in cps:
            db.add(RouteCheckpoint(
                route_id=route.id,
                name=cp.get("name", ""),
                distance_km=cp.get("distance_km", 0),
                elevation=cp.get("elevation"),
            ))
        await db.flush()

        logger.info("Route %d saved for user %d: %s", route.id, user.id, route.name)
        return JSONResponse({"id": route.id, "name": route.name})
    except Exception:
        logger.exception("Failed to save route")
        return JSONResponse({"error": "Erreur lors de la sauvegarde"}, status_code=500)


@router.post("/api/simulator/routes/{route_id}/reimport", response_class=HTMLResponse)
async def reimport_route_gpx(
    route_id: int,
    request: Request,
    gpx_file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Refresh an existing route's GPS trace from a re-uploaded GPX, keeping its
    name, checkpoints, objective, conditions and nutrition plan untouched.

    Lets users fix routes imported before the full-resolution export fix without
    losing the checkpoints/aid-stations they already set up.
    """
    from app.services.gpx import build_course_profile, parse_gpx

    route = await _get_owned_route(route_id, user, db)
    if not route:
        return HTMLResponse("Parcours non trouvé", status_code=404)

    try:
        content = await gpx_file.read()
        points, _ = parse_gpx(content)
        course = build_course_profile(points, name=route.name)
        course_data = json.loads(course.model_dump_json())

        # Update only the geometry/elevation; leave everything else as-is.
        route.course_json = course_data
        route.total_distance_km = course.total_distance_km
        route.total_elevation_gain = course.total_elevation_gain
        route.total_elevation_loss = course.total_elevation_loss
        await db.flush()

        logger.info("Route %d trace re-imported for user %d", route.id, user.id)
        return HTMLResponse(status_code=204, headers={"HX-Redirect": f"/simulator/routes/{route.id}"})
    except ValueError as e:
        return HTMLResponse(
            f'<div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-600">{e}</div>'
        )
    except Exception:
        logger.exception("Route re-import failed")
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-600">'
            "Erreur lors de la mise à jour de la trace."
            "</div>"
        )


async def _build_route_context(route: Route, db: AsyncSession, user_id: int) -> dict:
    """Shared context for the route detail page and its partial."""
    from app.schemas.simulator import CourseProfile
    from app.services.race_simulator import build_athlete_gradient_profile, predict_course

    course = CourseProfile(**route.course_json)
    profile = await build_athlete_gradient_profile(db, user_id)
    course = predict_course(
        course, profile,
        start_hour=route.start_hour if route.start_hour is not None else 6,
        start_minute=route.start_minute or 0,
    )

    cp_result = await db.execute(
        select(RouteCheckpoint)
        .where(RouteCheckpoint.route_id == route.id)
        .order_by(RouteCheckpoint.distance_km)
    )
    cps = [{"name": cp.name, "distance_km": cp.distance_km, "elevation": cp.elevation}
           for cp in cp_result.scalars().all()]

    coords = course.route_coords or []
    geojson = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[c[1], c[0], c[3] if len(c) > 3 else 0] for c in coords],
        },
        "properties": {"name": course.name},
    }

    return {
        "course": course,
        "profile": profile,
        "course_json": course.model_dump_json(),
        "gpx_waypoints": json.dumps(cps),
        "geojson": json.dumps(geojson),
        "saved_route_id": route.id,
        "saved_route_name": route.name,
        "saved_target_time_s": route.target_time_s,
        "saved_race_date": route.race_date,
        "saved_start_hour": route.start_hour,
        "saved_start_minute": route.start_minute,
        "saved_sport_type": route.sport_type,
        "saved_weather_json": json.dumps(route.weather_json) if route.weather_json else None,
    }


@router.get("/simulator/routes/{route_id}", response_class=HTMLResponse)
async def route_detail_page(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full server-rendered detail page for a saved route (refresh-safe)."""
    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    route = result.scalar_one_or_none()
    if not route or not route.course_json:
        return HTMLResponse("Parcours non trouvé", status_code=404)

    ctx = await _build_route_context(route, db, user.id)
    ctx["user"] = user
    return templates.TemplateResponse(request, "simulator_route.html", context=ctx)


@router.get("/api/simulator/routes/{route_id}", response_class=HTMLResponse)
async def load_route(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    route = result.scalar_one_or_none()
    if not route or not route.course_json:
        return JSONResponse({"error": "Parcours non trouvé"}, status_code=404)

    ctx = await _build_route_context(route, db, user.id)
    return templates.TemplateResponse(request, "partials/gpx_result.html", context=ctx)


@router.patch("/api/simulator/routes/{route_id}")
async def rename_route(
    route_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
):
    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    route = result.scalar_one_or_none()
    if not route:
        return JSONResponse({"error": "Parcours non trouve"}, status_code=404)
    route.name = name.strip() or route.name
    await db.flush()
    return JSONResponse({"id": route.id, "name": route.name})


@router.get("/api/simulator/routes/{route_id}/gpx")
async def export_route_gpx(
    route_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export a saved route as a GPX file with checkpoints as waypoints."""
    import gpxpy
    import gpxpy.gpx

    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    route = result.scalar_one_or_none()
    if not route or not route.course_json:
        return JSONResponse({"error": "Parcours non trouvé"}, status_code=404)

    # route_coords entries are [lat, lon, cumulative_km, elevation]
    coords = route.course_json.get("route_coords", []) or []
    if not coords:
        return JSONResponse({"error": "Trace GPS indisponible"}, status_code=400)

    cp_result = await db.execute(
        select(RouteCheckpoint)
        .where(RouteCheckpoint.route_id == route_id)
        .order_by(RouteCheckpoint.distance_km)
    )
    checkpoints = cp_result.scalars().all()

    gpx = gpxpy.gpx.GPX()
    gpx.name = route.name
    gpx.creator = "PaceForge"

    track = gpxpy.gpx.GPXTrack(name=route.name)
    gpx.tracks.append(track)
    seg = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(seg)
    for c in coords:
        lat, lon = c[0], c[1]
        elev = c[3] if len(c) > 3 else None
        seg.points.append(gpxpy.gpx.GPXTrackPoint(lat, lon, elevation=elev))

    def _coord_at_km(km: float):
        best, best_d = coords[0], float("inf")
        for c in coords:
            d = abs((c[2] if len(c) > 2 else 0) - km)
            if d < best_d:
                best_d, best = d, c
        return best

    for i, cp in enumerate(checkpoints, start=1):
        c = _coord_at_km(cp.distance_km)
        gpx.waypoints.append(gpxpy.gpx.GPXWaypoint(
            latitude=c[0],
            longitude=c[1],
            elevation=cp.elevation if cp.elevation is not None else (c[3] if len(c) > 3 else None),
            name=f"CP{i} — {cp.name}" if cp.name else f"CP{i}",
            description=f"km {cp.distance_km}",
        ))

    safe_name = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in route.name).strip() or "parcours"
    return Response(
        content=gpx.to_xml(),
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.gpx"'},
    )


@router.get("/simulator/routes/{route_id}/print", response_class=HTMLResponse)
async def print_route_plan(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Printable race plan (passage timeline) for a saved route."""
    from app.schemas.simulator import CourseProfile
    from app.services.race_simulator import (
        _elevation_at_km,
        build_athlete_gradient_profile,
        compute_passage_times,
        predict_course,
    )

    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    route = result.scalar_one_or_none()
    if not route or not route.course_json:
        return HTMLResponse("Parcours non trouvé", status_code=404)

    start_hour = route.start_hour if route.start_hour is not None else 6
    start_minute = route.start_minute or 0

    course = CourseProfile(**route.course_json)
    profile = await build_athlete_gradient_profile(db, user.id)
    course = predict_course(
        course, profile, start_hour=start_hour, start_minute=start_minute
    )

    cp_result = await db.execute(
        select(RouteCheckpoint)
        .where(RouteCheckpoint.route_id == route_id)
        .order_by(RouteCheckpoint.distance_km)
    )
    cps = [{"name": cp.name, "distance_km": cp.distance_km, "elevation": cp.elevation}
           for cp in cp_result.scalars().all()]

    # Per-checkpoint temperatures for the printed plan. Prefer the weather saved
    # with the route (no re-fetch); fall back to a fresh forecast only if absent.
    hourly_weather = None
    if route.weather_json and route.weather_json.get("hourly"):
        hourly_weather = route.weather_json["hourly"]
    elif route.race_date and course.route_coords:
        from app.services.weather import get_weather_forecast

        weather = await get_weather_forecast(
            course.route_coords[0][0], course.route_coords[0][1], str(route.race_date)
        )
        if weather:
            hourly_weather = weather.get("hourly")

    sections = compute_passage_times(
        course, cps, route.target_time_s, 1.0, start_hour, start_minute, hourly_weather
    )
    has_weather = any(s.get("temperature_c") is not None for s in sections)

    return templates.TemplateResponse(
        request,
        "simulator_print.html",
        context={
            "route": route,
            "course": course,
            "sections": sections,
            "has_target": route.target_time_s is not None,
            "has_weather": has_weather,
            "start_hour": start_hour,
            "start_minute": start_minute,
            "start_offset_s": start_hour * 3600 + start_minute * 60,
            "start_elevation": _elevation_at_km(course, 0.0),
            "total_distance_km": course.total_distance_km,
            "print_mode": True,
        },
    )


@router.delete("/api/simulator/routes/{route_id}")
async def delete_route(
    route_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    route = result.scalar_one_or_none()
    if route:
        await db.delete(route)
        await db.flush()
    return JSONResponse({"ok": True})


# ── Weather ──

@router.post("/api/simulator/weather")
async def get_weather(
    lat: float = Form(...),
    lon: float = Form(...),
    date: str = Form(...),
):
    from app.services.weather import get_weather_forecast

    weather = await get_weather_forecast(lat, lon, date)
    if not weather:
        return JSONResponse({"error": "Impossible de récupérer la météo"}, status_code=500)
    return JSONResponse(weather)


# ── Nutrition ──

def _product_dict(p: NutritionProduct) -> dict:
    return {
        "id": p.id, "name": p.name, "kind": p.kind,
        "carbs_g": p.carbs_g, "sodium_mg": p.sodium_mg,
        "kcal": p.kcal, "caffeine_mg": p.caffeine_mg, "volume_ml": p.volume_ml,
    }


async def _nutrition_card_context(request: Request, route: Route, db: AsyncSession, user: User) -> dict:
    """Build everything the nutrition card needs: pantry, plan, schedule."""
    from app.schemas.simulator import CourseProfile
    from app.services.nutrition import compute_plan, default_targets
    from app.services.race_simulator import (
        build_athlete_gradient_profile,
        compute_passage_times,
        predict_course,
    )

    prod_result = await db.execute(
        select(NutritionProduct)
        .where(NutritionProduct.user_id == user.id)
        .order_by(NutritionProduct.created_at.desc())
    )
    products = [_product_dict(p) for p in prod_result.scalars().all()]
    products_by_id = {p["id"]: p for p in products}

    course = CourseProfile(**route.course_json)
    profile = await build_athlete_gradient_profile(db, user.id)
    start_hour = route.start_hour if route.start_hour is not None else 6
    start_minute = route.start_minute or 0
    course = predict_course(course, profile, start_hour=start_hour, start_minute=start_minute)

    # Duration used for totals: target if set, else the model prediction.
    duration_s = route.target_time_s or course.predicted_total_time_s or 0

    cp_result = await db.execute(
        select(RouteCheckpoint)
        .where(RouteCheckpoint.route_id == route.id)
        .order_by(RouteCheckpoint.distance_km)
    )
    cps = [{"name": cp.name, "distance_km": cp.distance_km} for cp in cp_result.scalars().all()]
    hourly_weather = route.weather_json.get("hourly") if route.weather_json else None
    sections = compute_passage_times(
        course, cps, route.target_time_s, 1.0, start_hour, start_minute, hourly_weather
    )

    mean_temp = route.weather_json.get("temperature_c") if route.weather_json else None
    nutrition = route.nutrition_json or {}
    targets = nutrition.get("targets") or default_targets(duration_s / 3600.0 if duration_s else 0, mean_temp)
    items = nutrition.get("items") or []
    flask_capacity_ml = nutrition.get("flask_capacity_ml") or 1000
    refills = set(nutrition.get("refills") or [])
    plan = compute_plan(
        duration_s, targets, items, products_by_id, sections,
        flask_capacity_ml=flask_capacity_ml, refill_kms=refills,
    )
    # rate per product for the form (product_id -> per_hour)
    rates = {it.get("product_id"): it.get("per_hour", 0) for it in items}

    def _interval_min(per_hour):
        return round(60.0 / per_hour) if per_hour and per_hour > 0 else None

    # The UI is interval-based ("1 every X min") — more natural than a fractional
    # rate. Convert per_hour <-> minutes for display; the math stays on per_hour.
    intervals = {pid: _interval_min(ph) for pid, ph in rates.items()}
    from app.services.nutrition import rate_for_target
    target_intervals = {
        p["id"]: _interval_min(rate_for_target(targets.get("carbs_g_per_h", 0), p["carbs_g"]))
        for p in products
    }
    # checkpoints the athlete can mark as refill points (all section ends but
    # the finish), with their current refill state.
    refill_points = [
        {"name": s.get("end_name", ""), "km": round(float(s.get("end_km") or 0), 1),
         "is_refill": round(float(s.get("end_km") or 0), 1) in {round(float(k), 1) for k in refills}}
        for s in sections[:-1]
    ] if sections else []

    return {
        "request": request,
        "route_id": route.id,
        "products": products,
        "targets": targets,
        "rates": rates,
        "intervals": intervals,
        "target_intervals": target_intervals,
        "flask_capacity_ml": flask_capacity_ml,
        "refill_points": refill_points,
        "plan": plan,
        "has_duration": duration_s > 0,
    }


async def _get_owned_route(route_id: int, user: User, db: AsyncSession) -> Route | None:
    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    return result.scalar_one_or_none()


@router.get("/partials/simulator/nutrition/{route_id}", response_class=HTMLResponse)
async def nutrition_card(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    ctx = await _nutrition_card_context(request, route, db, user)
    # htmx GETs can be heuristically cached by the browser; force a fresh card.
    return templates.TemplateResponse(
        request, "partials/nutrition_card.html", context=ctx,
        headers={"Cache-Control": "no-store"},
    )


# ── Pantry (reusable products, managed on their own page) ──

async def _pantry_context(request: Request, db: AsyncSession, user: User) -> dict:
    result = await db.execute(
        select(NutritionProduct)
        .where(NutritionProduct.user_id == user.id)
        .order_by(NutritionProduct.created_at.desc())
    )
    return {"request": request, "products": [_product_dict(p) for p in result.scalars().all()]}


@router.get("/nutrition", response_class=HTMLResponse)
async def nutrition_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ctx = await _pantry_context(request, db, user)
    ctx["user"] = user
    return templates.TemplateResponse(request, "nutrition.html", context=ctx)


_DEFAULT_PRODUCTS = [
    # name, kind, carbs_g, sodium_mg, volume_ml. Water isn't a product — it's
    # the fluid target + flasks/refills (hydration), not a dosed fuel.
    ("Boisson glucidique", "drink", 30, 300, 500),
    ("Gel énergétique", "gel", 22, 0, None),
    ("Pastille de sel", "salt", 0, 300, None),
]


@router.post("/api/nutrition/products/seed", response_class=HTMLResponse)
async def seed_products(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a starter pantry (water + common items) the athlete can tweak."""
    for name, kind, carbs, sodium, vol in _DEFAULT_PRODUCTS:
        db.add(NutritionProduct(
            user_id=user.id, name=name, kind=kind,
            carbs_g=carbs, sodium_mg=sodium, volume_ml=vol,
        ))
    await db.flush()
    ctx = await _pantry_context(request, db, user)
    return templates.TemplateResponse(request, "partials/pantry.html", context=ctx)


@router.post("/api/nutrition/products", response_class=HTMLResponse)
async def create_product(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    kind: str = Form(default="gel"),
    carbs_g: float = Form(default=0),
    sodium_mg: float = Form(default=0),
    kcal: float | None = Form(default=None),
    caffeine_mg: float | None = Form(default=None),
    volume_ml: float | None = Form(default=None),
):
    if name.strip():
        db.add(NutritionProduct(
            user_id=user.id, name=name.strip()[:100], kind=kind or "gel",
            carbs_g=carbs_g or 0, sodium_mg=sodium_mg or 0,
            kcal=kcal, caffeine_mg=caffeine_mg, volume_ml=volume_ml,
        ))
        await db.flush()
    ctx = await _pantry_context(request, db, user)
    return templates.TemplateResponse(request, "partials/pantry.html", context=ctx)


@router.post("/api/nutrition/products/{product_id}", response_class=HTMLResponse)
async def update_product(
    product_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    kind: str = Form(default="gel"),
    carbs_g: float = Form(default=0),
    sodium_mg: float = Form(default=0),
    kcal: float | None = Form(default=None),
    caffeine_mg: float | None = Form(default=None),
    volume_ml: float | None = Form(default=None),
):
    pr = await db.execute(
        select(NutritionProduct).where(
            NutritionProduct.id == product_id, NutritionProduct.user_id == user.id
        )
    )
    product = pr.scalar_one_or_none()
    if product and name.strip():
        product.name = name.strip()[:100]
        product.kind = kind or "gel"
        product.carbs_g = carbs_g or 0
        product.sodium_mg = sodium_mg or 0
        product.kcal = kcal
        product.caffeine_mg = caffeine_mg
        product.volume_ml = volume_ml
        await db.flush()
    ctx = await _pantry_context(request, db, user)
    return templates.TemplateResponse(request, "partials/pantry.html", context=ctx)


@router.post("/api/nutrition/products/{product_id}/delete", response_class=HTMLResponse)
async def delete_product(
    product_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    pr = await db.execute(
        select(NutritionProduct).where(
            NutritionProduct.id == product_id, NutritionProduct.user_id == user.id
        )
    )
    product = pr.scalar_one_or_none()
    if product:
        await db.delete(product)
        await db.flush()
    ctx = await _pantry_context(request, db, user)
    return templates.TemplateResponse(request, "partials/pantry.html", context=ctx)


def _to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@router.post("/partials/simulator/nutrition/{route_id}/plan", response_class=HTMLResponse)
async def save_nutrition_plan(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Persist targets + per-product intake rates (form fields), re-render card.

    Targets come as carbs_g_per_h / fluid_ml_per_h / sodium_mg_per_h; intake
    rates come as rate_<product_id> fields (units per hour).
    """
    route = await _get_owned_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)

    form = await request.form()
    targets = {
        "carbs_g_per_h": round(_to_float(form.get("carbs_g_per_h"))),
        "fluid_ml_per_h": round(_to_float(form.get("fluid_ml_per_h"))),
        "sodium_mg_per_h": round(_to_float(form.get("sodium_mg_per_h"))),
    }
    items = []
    refills = []
    for key, val in form.multi_items() if hasattr(form, "multi_items") else form.items():
        if key.startswith("interval_"):
            # "1 unit every N minutes" → units per hour
            minutes = _to_float(val)
            if minutes > 0:
                try:
                    items.append({"product_id": int(key[9:]), "per_hour": round(60.0 / minutes, 3)})
                except ValueError:
                    continue
        elif key.startswith("refill_"):
            try:
                refills.append(round(float(key[7:]), 1))
            except ValueError:
                continue
    flask_capacity_ml = round(_to_float(form.get("flask_capacity_ml"), 1000))
    route.nutrition_json = {
        "targets": targets, "items": items,
        "flask_capacity_ml": flask_capacity_ml, "refills": sorted(set(refills)),
    }
    await db.flush()
    ctx = await _nutrition_card_context(request, route, db, user)
    return templates.TemplateResponse(request, "partials/nutrition_card.html", context=ctx)


@router.post("/partials/simulator/nutrition/{route_id}/suggest", response_class=HTMLResponse)
async def suggest_nutrition_plan(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Auto-fill intake rates to roughly meet the carb target, then re-render."""
    from app.services.nutrition import suggest_rates

    route = await _get_owned_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)

    prod_result = await db.execute(
        select(NutritionProduct).where(NutritionProduct.user_id == user.id)
    )
    products = [_product_dict(p) for p in prod_result.scalars().all()]

    # Targets come from the current form (the button includes the plan form),
    # falling back to whatever was saved.
    form = await request.form()
    nutrition = route.nutrition_json or {}
    saved_targets = nutrition.get("targets") or {}
    targets = {
        "carbs_g_per_h": round(_to_float(form.get("carbs_g_per_h"), saved_targets.get("carbs_g_per_h", 0))),
        "fluid_ml_per_h": round(_to_float(form.get("fluid_ml_per_h"), saved_targets.get("fluid_ml_per_h", 0))),
        "sodium_mg_per_h": round(_to_float(form.get("sodium_mg_per_h"), saved_targets.get("sodium_mg_per_h", 0))),
    }
    rates = suggest_rates(targets.get("carbs_g_per_h", 0), products)
    items = [{"product_id": pid, "per_hour": rate} for pid, rate in rates.items() if rate > 0]
    route.nutrition_json = {**nutrition, "targets": targets, "items": items}
    await db.flush()
    ctx = await _nutrition_card_context(request, route, db, user)
    return templates.TemplateResponse(request, "partials/nutrition_card.html", context=ctx)
