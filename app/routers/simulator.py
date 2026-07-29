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
    route_id: int | None = Form(default=None),
    stop_minutes: int | None = Form(default=None),
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

        # Aid-station stops: refills come from the saved route's nutrition plan;
        # the stop duration comes from the live input (fallback: saved value).
        aid_kms: set = set()
        stop_min = stop_minutes or 0
        if route_id:
            r_res = await db.execute(
                select(Route).where(Route.id == route_id, Route.user_id == user.id)
            )
            route_obj = r_res.scalar_one_or_none()
            if route_obj:
                if route_obj.nutrition_json:
                    aid_kms = set(route_obj.nutrition_json.get("refills") or [])
                if stop_minutes is None:
                    stop_min = route_obj.stop_minutes or 0

        sections = compute_passage_times(
            course, checkpoints, target_time_s, heat_factor,
            start_hour, start_minute, hourly_weather,
            stop_s_per_aid=stop_min * 60, aid_kms=aid_kms,
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

        # Persist like trail uploads: bike routes get their own detail page.
        course_data = json.loads(course.model_dump_json())
        route = Route(
            user_id=user.id,
            name=course.name,
            total_distance_km=course.total_distance_km,
            total_elevation_gain=course.total_elevation_gain,
            total_elevation_loss=course.total_elevation_loss,
            course_json=course_data,
            sport_type="bike",
        )
        db.add(route)
        await db.flush()
        logger.info("Bike route %d created from GPX for user %d", route.id, user.id)
        return HTMLResponse(
            status_code=204,
            headers={"HX-Redirect": f"/simulator/routes/{route.id}"},
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
    stop_minutes: int | None = Form(default=None),
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
            if stop_minutes is not None: route.stop_minutes = stop_minutes

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
                stop_minutes=stop_minutes,
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
        "saved_stop_minutes": route.stop_minutes,
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

    if route.sport_type == "bike":
        from app.schemas.simulator import CourseProfile
        from app.services.cycling_simulator import estimate_cda, predict_cycling_course
        from app.services.power_calculator import estimate_ftp

        course = CourseProfile(**route.course_json)
        ftp = await estimate_ftp(db, user.id)
        rider_weight = user.weight_kg or 75
        cda_est = await estimate_cda(db, user.id, rider_weight)
        cda_default = cda_est.estimated_cda if cda_est else 0.32
        default_target = round(ftp.estimated_ftp * 0.75) if ftp else 200
        cycling = predict_cycling_course(
            course,
            target_power_watts=default_target,
            rider_weight_kg=rider_weight,
            cda=cda_default,
            ftp_watts=ftp.estimated_ftp if ftp else None,
        )
        return templates.TemplateResponse(
            request, "simulator_route_bike.html",
            context={
                "user": user,
                "route": route,
                "cycling": cycling,
                "ftp": ftp,
                "cda_est": cda_est,
                "cycling_json": cycling.model_dump_json(),
            },
            headers={"Cache-Control": "no-store"},
        )

    ctx = await _build_route_context(route, db, user.id)
    ctx["user"] = user
    # Don't let the browser serve a stale page (kept hiding UI updates).
    return templates.TemplateResponse(
        request, "simulator_route.html", context=ctx,
        headers={"Cache-Control": "no-store"},
    )


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

    aid_kms = set((route.nutrition_json or {}).get("refills") or [])
    stop_min = route.stop_minutes or 0
    sections = compute_passage_times(
        course, cps, route.target_time_s, 1.0, start_hour, start_minute, hourly_weather,
        stop_s_per_aid=stop_min * 60, aid_kms=aid_kms,
    )
    has_weather = any(s.get("temperature_c") is not None for s in sections)

    # Race pack: include the nutrition per-leg plan on the same printout.
    nutrition_schedule = []
    nutrition_lines = []
    nut = route.nutrition_json or {}
    if nut.get("items"):
        from app.services.nutrition import compute_plan, default_targets

        prod_result = await db.execute(
            select(NutritionProduct).where(NutritionProduct.user_id == user.id)
        )
        products_by_id = {p.id: _product_dict(p) for p in prod_result.scalars().all()}
        duration_s = route.target_time_s or course.predicted_total_time_s or 0
        mean_temp = route.weather_json.get("temperature_c") if route.weather_json else None
        targets = nut.get("targets") or default_targets(duration_s / 3600.0 if duration_s else 0, mean_temp)
        nplan = compute_plan(
            duration_s, targets, nut["items"], products_by_id, sections,
            flask_capacity_ml=nut.get("flask_capacity_ml") or 1000, refill_kms=aid_kms,
        )
        nutrition_schedule = nplan["schedule"]
        nutrition_lines = [ln for ln in nplan["lines"] if ln.get("total_units") and not ln.get("is_water")]

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
            "stop_minutes": stop_min,
            "n_aid": len(aid_kms),
            "nutrition_schedule": nutrition_schedule,
            "nutrition_lines": nutrition_lines,
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


# ── Simulated vs actual (predicted-vs-actual calibration) ──

_RUN_TYPES = ["Run", "TrailRun", "VirtualRun"]


async def _result_compare_context(request: Request, route: Route, db: AsyncSession, user: User) -> dict:
    from app.models.activity import Activity
    from app.schemas.simulator import CourseProfile
    from app.services.race_simulator import (
        build_athlete_gradient_profile,
        compute_passage_times,
        predict_course,
    )

    start_hour = route.start_hour if route.start_hour is not None else 6
    start_minute = route.start_minute or 0
    course = CourseProfile(**route.course_json)
    profile = await build_athlete_gradient_profile(db, user.id)
    course = predict_course(course, profile, start_hour=start_hour, start_minute=start_minute)

    cp_result = await db.execute(
        select(RouteCheckpoint)
        .where(RouteCheckpoint.route_id == route.id)
        .order_by(RouteCheckpoint.distance_km)
    )
    cps = [{"name": cp.name, "distance_km": cp.distance_km} for cp in cp_result.scalars().all()]
    sections = compute_passage_times(course, cps, None, 1.0, start_hour, start_minute, None)
    predicted = {s["end_name"]: s["cumulative_time_s"] for s in sections}
    predicted_total = course.predicted_total_time_s

    rows = []
    result = route.result_json
    if result and result.get("actual"):
        actual = {a["name"]: a["time_s"] for a in result["actual"]}
        for cp in cps:
            n = cp["name"]
            rows.append({
                "name": n, "km": cp["distance_km"],
                "predicted_s": predicted.get(n), "actual_s": actual.get(n),
            })
        rows.append({
            "name": "Arrivée", "km": route.total_distance_km,
            "predicted_s": predicted_total, "actual_s": result.get("total_actual_s"),
        })

    candidates = []
    total_activities = 0
    if not result:
        route_m = (route.total_distance_km or 0) * 1000
        # Broad net: a race may be recorded with odd GPS distance; don't hide it
        # behind a tight band. Show the longest recent runs (races are long),
        # closest-distance first, and let the user pick.
        cand_q = await db.execute(
            select(Activity)
            .where(Activity.user_id == user.id, Activity.sport_type.in_(_RUN_TYPES))
            .order_by(Activity.start_date.desc())
            .limit(200)
        )
        acts = cand_q.scalars().all()
        total_activities = len(acts)
        # rank by distance proximity to the route, keep the 40 closest
        acts = sorted(acts, key=lambda a: abs((a.distance or 0) - route_m))[:40]
        acts = sorted(acts, key=lambda a: a.start_date or 0, reverse=True)
        for a in acts:
            candidates.append({
                "id": a.id, "name": a.name,
                "date": a.start_date.strftime("%d/%m/%Y") if a.start_date else "",
                "distance_km": round((a.distance or 0) / 1000, 1),
                "dplus": round(a.total_elevation_gain or 0),
                "has_splits": bool(a.splits_metric),
            })

    return {
        "request": request,
        "route_id": route.id,
        "rows": rows,
        "predicted_total_s": predicted_total,
        "result": result,
        "candidates": candidates,
        "total_activities": total_activities,
    }


@router.get("/api/simulator/routes/{route_id}/result", response_class=HTMLResponse)
async def result_compare_card(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    ctx = await _result_compare_context(request, route, db, user)
    return templates.TemplateResponse(
        request, "partials/result_compare.html", context=ctx,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/simulator/routes/{route_id}/result", response_class=HTMLResponse)
async def save_route_result(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    activity_id: int = Form(...),
):
    """Match a Strava activity to this route and store the real per-checkpoint
    times for predicted-vs-actual comparison + the global calibration dataset."""
    from app.models.activity import Activity
    from app.services.race_simulator import actual_passage_times

    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)

    act_res = await db.execute(
        select(Activity).where(Activity.id == activity_id, Activity.user_id == user.id)
    )
    activity = act_res.scalar_one_or_none()
    if not activity:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-600">Activité introuvable.</div>'
        )

    cp_result = await db.execute(
        select(RouteCheckpoint)
        .where(RouteCheckpoint.route_id == route.id)
        .order_by(RouteCheckpoint.distance_km)
    )
    cps = [{"name": cp.name, "distance_km": cp.distance_km} for cp in cp_result.scalars().all()]
    if activity.splits_metric:
        # full per-checkpoint comparison
        actual, total_actual_s = actual_passage_times(activity.splits_metric, cps, route.total_distance_km)
    else:
        # no splits → compare the total only (still useful)
        actual, total_actual_s = [], int(activity.moving_time or activity.elapsed_time or 0)

    # One-shot personal fatigue calibration: compare early residual vs final
    # residual against the DEFAULT-tilt prediction. An athlete who is faster
    # than predicted early but fades to (or past) the prediction late gets a
    # steeper fresh→fade tilt. Hard-clamped: one race adjusts, never dominates.
    fatigue_tilt = None
    if actual and total_actual_s:
        try:
            from app.schemas.simulator import CourseProfile
            from app.services.race_simulator import (
                build_athlete_gradient_profile,
                compute_passage_times,
                predict_course,
            )

            profile = await build_athlete_gradient_profile(db, user.id)
            base_profile = profile.model_copy(update={"fatigue_tilt": 0.15})
            course = CourseProfile(**route.course_json)
            sh = route.start_hour if route.start_hour is not None else 6
            sm = route.start_minute or 0
            course = predict_course(course, base_profile, start_hour=sh, start_minute=sm)
            secs = compute_passage_times(course, cps, None, 1.0, sh, sm, None)
            pred = {s["end_name"]: s["cumulative_time_s"] for s in secs}
            pred_total = course.predicted_total_time_s
            first = next((a for a in actual if pred.get(a["name"])), None)
            if first and pred_total:
                early = (first["time_s"] - pred[first["name"]]) / pred[first["name"]]
                late = (total_actual_s - pred_total) / pred_total
                fatigue_tilt = round(max(0.05, min(0.15 + 0.5 * (late - early), 0.40)), 3)
        except Exception:
            logger.exception("fatigue tilt calibration failed")

    route.result_activity_id = activity.id
    route.result_json = {
        "activity_id": activity.id,
        "activity_name": activity.name,
        "activity_date": activity.start_date.strftime("%d/%m/%Y") if activity.start_date else "",
        "total_actual_s": total_actual_s,
        "actual": actual,
        **({"fatigue_tilt": fatigue_tilt} if fatigue_tilt is not None else {}),
    }
    await db.flush()
    ctx = await _result_compare_context(request, route, db, user)
    return templates.TemplateResponse(request, "partials/result_compare.html", context=ctx)


@router.post("/api/simulator/routes/{route_id}/result/clear", response_class=HTMLResponse)
async def clear_route_result(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    route = await _get_owned_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    route.result_activity_id = None
    route.result_json = None
    await db.flush()
    ctx = await _result_compare_context(request, route, db, user)
    return templates.TemplateResponse(request, "partials/result_compare.html", context=ctx)


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
    # Works out of the box: with an empty pantry, plan on generic products.
    using_generic = not products
    products_for_plan = products if products else _GENERIC_PLAN_PRODUCTS
    products_by_id = {p["id"]: p for p in products_for_plan}

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
    # No auto-fill: the plan shows ONLY what the athlete sets (a frequency per
    # product). Empty = no plan yet, with per-product "pour la cible" hints to
    # guide them. Never silently pick a product.
    items = nutrition.get("items") or []
    flask_capacity_ml = nutrition.get("flask_capacity_ml") or 1000
    refills = set(nutrition.get("refills") or [])
    plan = compute_plan(
        duration_s, targets, items, products_by_id, sections,
        flask_capacity_ml=flask_capacity_ml, refill_kms=refills,
    )
    # rate per product for the form (product_id -> per_hour) — manual items only
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
        "using_generic": using_generic,
        "carb_presets": [50, 60, 75, 90],
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


# Generic products used to auto-build a plan when the pantry is empty (display
# only — never saved). Gel is the primary carb source so the auto cadence reads
# naturally ("1 gel toutes les X min").
_GENERIC_PLAN_PRODUCTS = [
    {"id": -1, "name": "Gel", "kind": "gel", "carbs_g": 25, "sodium_mg": 0, "kcal": 100, "caffeine_mg": None, "volume_ml": None},
    {"id": -2, "name": "Boisson glucidique", "kind": "drink", "carbs_g": 22, "sodium_mg": 300, "kcal": 90, "caffeine_mg": None, "volume_ml": 500},
    {"id": -3, "name": "Pastille de sel", "kind": "salt", "carbs_g": 0, "sodium_mg": 300, "kcal": None, "caffeine_mg": None, "volume_ml": None},
]


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


