import json
import logging

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.route import Route, RouteCheckpoint
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
    from app.services.gpx import build_course_profile, parse_gpx
    from app.services.race_simulator import build_athlete_gradient_profile, predict_course

    try:
        content = await gpx_file.read()
        points, gpx_waypoints = parse_gpx(content)
        course = build_course_profile(points, name=gpx_file.filename or "Course")

        # Snap GPX waypoints to route
        from app.services.gpx import snap_waypoints_to_route
        snapped_wpts = snap_waypoints_to_route(gpx_waypoints, points)

        # Build athlete profile and predict
        profile = await build_athlete_gradient_profile(db, user.id)
        course = predict_course(course, profile)

        # Build GeoJSON for leaflet-elevation
        geojson = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[p.lon, p.lat, p.elevation] for p in points[::max(1, len(points)//800)]],
            },
            "properties": {"name": course.name},
        }

        return templates.TemplateResponse(
            request,
            "partials/gpx_result.html",
            context={
                "course": course,
                "profile": profile,
                "course_json": course.model_dump_json(),
                "gpx_waypoints": json.dumps(snapped_wpts),
                "geojson": json.dumps(geojson),
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


@router.post("/partials/simulator/passage-times", response_class=HTMLResponse)
async def passage_times(
    request: Request,
    course_json: str = Form(...),
    checkpoints_json: str = Form(default="[]"),
    target_time_s: int | None = Form(default=None),
    heat_factor: float = Form(default=1.0),
    start_hour: int = Form(default=6),
    start_minute: int = Form(default=0),
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

        # Re-predict so the night penalty reflects the chosen start time.
        # Heat is applied once, in compute_passage_times.
        profile = await build_athlete_gradient_profile(db, user.id)
        course = predict_course(
            course, profile, start_hour=start_hour, start_minute=start_minute
        )

        sections = compute_passage_times(
            course, checkpoints, target_time_s, heat_factor, start_hour, start_minute
        )

        return templates.TemplateResponse(
            request,
            "partials/passage_times.html",
            context={
                "sections": sections,
                "has_target": target_time_s is not None,
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
):
    try:
        course_data = json.loads(course_json)
        cps = json.loads(checkpoints_json)

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


@router.get("/api/simulator/routes/{route_id}", response_class=HTMLResponse)
async def load_route(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.race_simulator import predict_course, build_athlete_gradient_profile
    from app.schemas.simulator import CourseProfile

    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    route = result.scalar_one_or_none()
    if not route or not route.course_json:
        return JSONResponse({"error": "Parcours non trouvé"}, status_code=404)

    course = CourseProfile(**route.course_json)
    profile = await build_athlete_gradient_profile(db, user.id)
    course = predict_course(course, profile)

    # Load checkpoints
    cp_result = await db.execute(
        select(RouteCheckpoint)
        .where(RouteCheckpoint.route_id == route_id)
        .order_by(RouteCheckpoint.distance_km)
    )
    cps = [{"name": cp.name, "distance_km": cp.distance_km, "elevation": cp.elevation}
           for cp in cp_result.scalars().all()]

    # Rebuild GeoJSON from route_coords
    coords = course.route_coords or []
    geojson = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[c[1], c[0], c[3] if len(c) > 3 else 0] for c in coords],
        },
        "properties": {"name": course.name},
    }

    return templates.TemplateResponse(
        request,
        "partials/gpx_result.html",
        context={
            "course": course,
            "profile": profile,
            "course_json": course.model_dump_json(),
            "gpx_waypoints": json.dumps(cps),
            "geojson": json.dumps(geojson),
            "saved_route_id": route_id,
            "saved_route_name": route.name,
            "saved_target_time_s": route.target_time_s,
            "saved_race_date": route.race_date,
            "saved_start_hour": route.start_hour,
            "saved_start_minute": route.start_minute,
        },
    )


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

    sections = compute_passage_times(
        course, cps, route.target_time_s, 1.0, start_hour, start_minute
    )

    return templates.TemplateResponse(
        request,
        "simulator_print.html",
        context={
            "route": route,
            "course": course,
            "sections": sections,
            "has_target": route.target_time_s is not None,
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
