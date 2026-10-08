import json
import math
import re
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.features import cycling_enabled, hidden_sports, require_cycling, sport_hidden
from app.models.nutrition import NutritionProduct
from app.models.route import Route, RouteCheckpoint
from app.models.user import User

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

# Expose weather-code → icon-category / label helpers to all simulator templates.
from app.services.weather import WMO_LABELS, wmo_category  # noqa: E402

templates.env.globals["wmo_category"] = wmo_category
templates.env.globals["wmo_label"] = lambda code: WMO_LABELS.get(wmo_category(code), "")
templates.env.globals["cycling_enabled"] = cycling_enabled

router = APIRouter(tags=["simulator"])


@router.get("/simulator", response_class=HTMLResponse)
async def simulator_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Saved routes (bike / triathlon ones stay out while cycling is off)
    query = select(Route).where(Route.user_id == user.id)
    if hidden_sports():
        query = query.where(Route.sport_type.notin_(hidden_sports()))
    result = await db.execute(query.order_by(Route.created_at.desc()).limit(40))
    saved_routes = list(result.scalars().all())
    # upcoming races first (soonest), then past ones (most recent first), then undated
    from datetime import date as _date

    today = _date.today()
    for rt in saved_routes:
        try:
            rt.days_to = (_date.fromisoformat(str(rt.race_date)[:10]) - today).days if rt.race_date else None
        except ValueError:
            rt.days_to = None
    saved_routes.sort(key=lambda rt: (0, rt.days_to) if rt.days_to is not None and rt.days_to >= 0 else (1, -rt.days_to) if rt.days_to is not None else (2, 0))
    for rt in saved_routes:
        rt.card = _route_card(rt)

    return templates.TemplateResponse(
        request,
        "simulator.html",
        context={"user": user, "saved_routes": saved_routes},
    )


_DAYS_FR = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
_MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def _hm(s: int) -> str:
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


def _route_card(rt: Route) -> dict:
    """What « Mes courses » shows for a race: the date in words, the objective, the real
    time against the plan once run, and a mini profile."""
    from datetime import date as _date

    out: dict = {"date": None, "when": None, "objective": None, "real": None, "diff": None, "good": None, "profile": None}
    d = None
    try:
        d = _date.fromisoformat(str(rt.race_date)[:10]) if rt.race_date else None
    except ValueError:
        d = None
    year = f" {d.year}" if d and (d.year != _date.today().year or d < _date.today()) else ""  # past races carry their year
    if d:
        out["date"] = f"{_DAYS_FR[d.weekday()]} {d.day} {_MONTHS_FR[d.month - 1]}{year}"
    days = getattr(rt, "days_to", None)
    if days is not None and days >= 0:
        out["when"] = ("aujourd'hui" if days == 0 else "demain" if days == 1 else f"J-{days}" if days <= 14
                       else f"dans {round(days / 7)} sem." if days < 63 else f"dans {round(days / 30.4)} mois")
    if rt.target_time_s:
        out["objective"] = _hm(rt.target_time_s)
    res = rt.result_json or {}
    if res.get("total_actual_s"):
        real = int(res["total_actual_s"])
        out["real"] = _hm(real)
        if rt.target_time_s:
            diff = round((real - rt.target_time_s) / 60)
            out["diff"] = ("−" if diff < 0 else "+") + (f"{abs(diff)} min" if abs(diff) < 60 else _hm(abs(diff) * 60))
            out["good"] = diff <= 0
    # mini profile: 80 points, 400 × 70 viewBox
    pts = (rt.course_json or {}).get("elevation_points") or []
    if len(pts) > 2:
        step = max(1, len(pts) // 80)
        sample = pts[::step] + ([pts[-1]] if (len(pts) - 1) % step else [])
        total = float(sample[-1]["distance_km"]) or 1.0
        lo = min(p["elevation"] for p in sample)
        hi = max(p["elevation"] for p in sample)
        span = (hi - lo) or 1.0
        coords = [(round(p["distance_km"] / total * 400, 1), round(66 - (p["elevation"] - lo) / span * 60, 1)) for p in sample]
        line = "M" + " L".join(f"{x} {y}" for x, y in coords)
        out["profile"] = {"line": line, "area": line + " L400 70 L0 70 Z"}
    return out


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
        course = build_course_profile(points, name=_gpx_title(content, gpx_file.filename, "Course"))

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


def _script_json(obj) -> str:
    """JSON for a <script> block: '</' would end the block (a checkpoint named
    '</script>…' must not run)."""
    return json.dumps(obj).replace("</", "<\\/")


def _clock_to_s(clock: str) -> int | None:
    """'HH:MM' → seconds since midnight, None if malformed."""
    try:
        hh, mm = clock.strip().split(":")[:2]
        return int(hh) * 3600 + int(mm) * 60
    except (ValueError, AttributeError):
        return None


async def _pantry(db: AsyncSession, user_id: int) -> dict:
    """« Tes produits », in the order he added them: {id: product dict}."""
    res = await db.execute(
        select(NutritionProduct).where(NutritionProduct.user_id == user_id)
        .order_by(NutritionProduct.created_at, NutritionProduct.id)
    )
    return {p.id: _product_dict(p) for p in res.scalars().all()}


def _until(name: str | None) -> str:
    """'Gasiri', 'l'arrivée'."""
    return "l'arrivée" if (name or "") == "Arrivée" else (name or "")


def _time_at_km(sections: list[dict], start_offset_s: int):
    """km → seconds after the start at the point that ends there (within 0.5 km), else None."""
    from app.services.nutrition_plan import arrival_clock

    ends = [(round(float(s.get("end_km") or 0), 1), arrival_clock(s, start_offset_s) - start_offset_s) for s in sections]

    def at(km: float) -> float | None:
        best = min(ends, key=lambda e: abs(e[0] - km), default=None)
        return best[1] if best is not None and abs(best[0] - km) <= 0.5 else None
    return at


async def _nutrition_plan(db: AsyncSession, user_id: int, route: Route | None, sections: list[dict], start_offset_s: int) -> tuple[dict, dict, dict]:
    """(plan, products, pantry) of a race: its lines read from whatever is
    stored (an older plan mapped in memory: reading never writes)."""
    from app.services import nutrition as N
    from app.services.nutrition_plan import race_end_s, read_plan

    pantry = await _pantry(db, user_id)
    products = N.with_generics(pantry)
    plan = read_plan(route.nutrition_json if route is not None else None, products,
                     time_at_km=_time_at_km(sections, start_offset_s), race_s=race_end_s(sections, start_offset_s))
    return plan, products, pantry


def _nutrition_count(plan: dict, products: dict, sections: list[dict], checkpoints: list[dict], start_offset_s: int) -> dict | None:
    """The plan counted on this race's clocks: the intakes, one row per refill
    point, the list. None without a simulation (no clocks to count on)."""
    from app.services import nutrition_plan as NP

    end = NP.race_end_s(sections, start_offset_s)
    if not end:
        return None
    lines = plan["rhythms"]
    il = NP.intakes(lines, end)
    order = list(dict.fromkeys(r["product_id"] for r in lines))
    spare = NP.main_product(lines, il, products) if plan["spare"] else None
    rows = NP.ravito_rows(NP.refill_points(sections, checkpoints, start_offset_s), end, il, products, order, spare)
    shop = NP.shopping(rows)
    return {"end_s": end, "intakes": il, "rows": rows, "order": order, "spare_pid": spare,
            "finish_clock_s": NP.arrival_clock(sections[-1], start_offset_s),
            "shop": {p: shop[p] for p in sorted(shop, key=lambda p: order.index(p) if p in order else len(order))}}


async def _leg_details(
    db: AsyncSession, user_id: int, route_obj: Route | None, guide: dict, sections: list[dict],
    checkpoints: list[dict], start_offset_s: int,
) -> list[dict]:
    """What a passage row says once opened, one entry per section (the leg that ENDS
    at that point): how to run it (heart-rate ceiling of its terrain, computed at
    the leg's own midpoint), the steep bits; where the bag is refilled, once the
    race has a nutrition plan, a pointer to that point's row in Nutrition (never
    its contents: the card is the one place for them). Never fails the table: on
    any error the rows open without these lines."""
    try:
        from markupsafe import Markup

        from app.services.nutrition_plan import refill_points
        from app.services.pacing_guide import effort_sentence, leg_instructions, steep_on_leg, steep_sentence

        li = leg_instructions(guide, sections)
        walk = float((guide.get("caps") or {}).get("walk_grade") or 18)
        refill: dict = {}
        if route_obj is not None and (await _nutrition_plan(db, user_id, route_obj, sections, start_offset_s))[0]["rhythms"]:
            pts = refill_points(sections, checkpoints, start_offset_s)
            for a, b in zip(pts[1:], pts[2:] + [None], strict=False):
                refill[a["key"]] = {"key": a["key"], "to": _until(b["name"] if b else "Arrivée")}
        out = []
        for i, sec in enumerate(sections):
            ins = li[i] if i < len(li) else {}
            cls, hr = ins.get("cls"), ins.get("hr_cap")
            steep = steep_on_leg(ins.get("steep"), float(sec.get("start_km") or 0), float(sec.get("end_km") or 0))
            km = round(float(sec.get("end_km") or 0), 1)
            effort = effort_sentence(cls, hr, walk)
            out.append({
                "hr_cap": hr, "terrain": (ins.get("block") or {}).get("label"), "cls": cls,
                "effort": Markup(effort) if effort else None,
                "steep": steep, "steep_text": steep_sentence(steep) if steep and cls != "stairs" else None,
                "ravito": None if i == len(sections) - 1 else refill.get(km),
            })
        return out
    except Exception:
        logger.exception("leg details failed")
        return []


async def _passage_table_context(
    db: AsyncSession, user_id: int, course, checkpoints: list[dict], target_time_s: int | None, heat_factor: float,
    start_hour: int, start_minute: int, hourly_weather: dict | None, route_obj: Route | None, stop_minutes: int | None,
    anchor_km: float | None, anchor_clock: str | None, profile=None, route_id: int | None = None,
) -> dict:
    """Everything partials/passage_times.html needs: the (re)planned sections, scenarios, plan data.
    Used by the recalculation endpoint and by the page itself, so the first paint already has the table."""
    from app.services.checkpoints import annotate_cutoffs, autonomy_legs
    from app.services.race_simulator import (
        _elevation_at_km,
        build_athlete_gradient_profile,
        compute_passage_times,
        objective_moving_s,
        predict_course,
    )

    # Aid-station stops: refills come from the saved route's nutrition plan;
    # the stop duration comes from the live input (fallback: saved value).
    aid_kms: set = _aid_kms_from_checkpoints(checkpoints)
    refills: list = []
    params: dict = {}
    if route_obj:
        if route_obj.nutrition_json:
            refills = list(route_obj.nutrition_json.get("refills") or [])
            aid_kms |= set(refills)
        if stop_minutes is None:
            stop_minutes = route_obj.stop_minutes
        params = route_obj.params_json or {}
    aid_stops = _aid_stops(checkpoints, refills, stop_minutes)
    stop_min = stop_minutes or 0

    # Re-predict so the night penalty reflects the chosen start time, and the
    # plan's fatigue and night the objective's clock. Heat is applied once, in
    # compute_passage_times.
    profile = profile or await build_athlete_gradient_profile(db, user_id)
    course = predict_course(
        course, profile, start_hour=start_hour, start_minute=start_minute,
        plan_moving_s=objective_moving_s(
            target_time_s, course.total_distance_km, checkpoints, stop_min * 60, aid_kms, aid_stops,
        ),
    )

    sections = compute_passage_times(
        course, checkpoints, target_time_s, heat_factor,
        start_hour, start_minute, hourly_weather,
        stop_s_per_aid=stop_min * 60, aid_kms=aid_kms, aid_stops=aid_stops,
    )
    has_weather = any(s.get("temperature_c") is not None for s in sections)
    start_offset_s = start_hour * 3600 + start_minute * 60

    # Race day: "I'm at <checkpoint> at <clock>" → re-plan the remainder.
    replan = None
    if anchor_km is not None and anchor_clock:
        from app.services.race_simulator import replan_from_passage

        anchor_clock_s = _clock_to_s(anchor_clock)
        if anchor_clock_s is not None:
            sections, replan = replan_from_passage(
                sections, start_hour * 3600 + start_minute * 60, target_time_s,
                anchor_km, anchor_clock_s, stop_s_per_aid=stop_min * 60, aid_kms=aid_kms, aid_stops=aid_stops,
            )
    # Cutoff margins and autonomy legs read the (re)planned clocks, so they
    # come after the replan: on race day the margin is THE number he checks.
    sections = annotate_cutoffs(sections, checkpoints, start_offset_s)
    autonomy = autonomy_legs(checkpoints, course.total_distance_km, sections)

    from app.services.checkpoints import KINDS
    from app.services.race_simulator import build_scenarios, is_pinned, plan_total_s

    # pinned checkpoints make a plan even without an objective
    pinned = is_pinned(sections)
    scenarios = None if replan else build_scenarios(
        sections, start_offset_s, target_time_s,
        fast_pct=float(params.get("scenario_fast_pct") or 5.0),
        safe_pct=float(params.get("scenario_safe_pct") or 10.0),
        switch_km=params.get("switch_km"),
        total_distance_km=course.total_distance_km,
    )
    # What the profile draws: night, terrain bands, clocks, cutoffs.
    from app.services.pacing_guide import build_pacing_guide, resolve_hr_caps
    from app.services.plan_view import build_plan_data
    from app.services.training_zones import estimate_training_zones

    zones = await estimate_training_zones(db, user_id)
    rc = resolve_hr_caps(params, zones.get("max_hr"))
    plan_s = plan_total_s(sections, start_offset_s, target_time_s) or int(course.predicted_total_time_s)
    guide = build_pacing_guide(
        course, plan_s, hr_cap_climb=rc["climb"], hr_cap_flat=rc["flat"], hr_release_descent=rc["descent"],
        walk_grade=rc["walk_grade"], plan_sections=sections,
    )
    plan_data = build_plan_data(sections, start_offset_s, bool(target_time_s) or replan is not None or pinned, course.total_distance_km, guide["blocks"], scenarios, autonomy=autonomy)
    legs = await _leg_details(db, user_id, route_obj, guide, sections, checkpoints, start_offset_s)
    return {
        "legs": legs,
        "plan_data": _script_json(plan_data),
        "sections": sections,
        "has_target": (target_time_s is not None) or replan is not None or pinned,
        "replan": replan,
        "has_weather": has_weather,
        "predicted_total": course.predicted_total_time_s,
        "target_total": target_time_s,
        "start_offset_s": start_offset_s,
        "start_elevation": _elevation_at_km(course, 0.0),
        "total_distance_km": course.total_distance_km,
        "autonomy": autonomy,
        "scenarios": scenarios,
        "kinds": KINDS,
        "route_id": route_id,
        "has_coords": bool(course.route_coords),  # « Voir sur la carte » only when there is a map
    }


@router.post("/partials/simulator/passage-times", response_class=HTMLResponse)
async def passage_times(
    request: Request,
    course_json: str = Form(default=""),
    checkpoints_json: str = Form(default="[]"),
    target_time_s: int | None = Form(default=None),
    heat_factor: float = Form(default=1.0),
    start_hour: int = Form(default=6),
    start_minute: int = Form(default=0),
    hourly_json: str = Form(default=""),
    route_id: int | None = Form(default=None),
    stop_minutes: int | None = Form(default=None),
    anchor_km: float | None = Form(default=None),
    anchor_clock: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.schemas.simulator import CourseProfile
    from app.services.checkpoints import normalize_checkpoint

    try:
        # The course is 175 KB+ of GPX points: once the route is saved the
        # client sends only its id and the server reads the course from the db.
        route_obj = None
        if route_id:
            r_res = await db.execute(
                select(Route).where(Route.id == route_id, Route.user_id == user.id)
            )
            route_obj = _visible(r_res.scalar_one_or_none())
        if course_json:
            course = CourseProfile(**json.loads(course_json))
        elif route_obj and route_obj.course_json:
            course = CourseProfile(**route_obj.course_json)
        else:
            return HTMLResponse('<div class="text-sm text-red-600">Parcours introuvable.</div>', status_code=404)
        checkpoints = [normalize_checkpoint(c) for c in json.loads(checkpoints_json)]
        hourly_weather = json.loads(hourly_json) if hourly_json else None
        if hourly_weather is None and route_obj and route_obj.weather_json:
            hourly_weather = (route_obj.weather_json or {}).get("hourly")
            if heat_factor == 1.0:
                heat_factor = float((route_obj.weather_json or {}).get("heat_factor") or 1.0)

        ctx = await _passage_table_context(
            db, user.id, course, checkpoints, target_time_s, heat_factor, start_hour, start_minute, hourly_weather,
            route_obj, stop_minutes, anchor_km, anchor_clock, route_id=route_id,
        )
        return templates.TemplateResponse(request, "partials/passage_times.html", context=ctx)
    except Exception:
        logger.exception("Passage time calculation failed")
        return HTMLResponse("", status_code=500)  # the page keeps its last table and offers « réessayer »


@router.post("/partials/simulator/bike-gpx-upload", response_class=HTMLResponse, dependencies=[Depends(require_cycling)])
async def bike_gpx_upload(
    request: Request,
    gpx_file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.cycling_simulator import estimate_cda, predict_cycling_course
    from app.services.gpx import build_course_profile, parse_gpx
    from app.services.power_calculator import ftp_for_user

    try:
        from app.services.gpx import snap_waypoints_to_route

        content = await gpx_file.read()
        points, gpx_waypoints = parse_gpx(content)
        course = build_course_profile(points, name=_gpx_title(content, gpx_file.filename, "Parcours vélo"))
        snapped_wpts = snap_waypoints_to_route(gpx_waypoints, points)

        ftp = await ftp_for_user(db, user)
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
        for wpt in snapped_wpts:
            db.add(RouteCheckpoint(route_id=route.id, name=wpt.get("name", ""), distance_km=wpt.get("distance_km", 0), elevation=wpt.get("elevation")))
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


# ── Save / Load routes ──

@router.post("/api/simulator/routes")
async def save_route(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    course_json: str = Form(default=""),
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
    if sport_hidden(sport_type):
        sport_type = "trail"  # no new bike / triathlon routes while cycling is off
    try:
        course_data = json.loads(course_json) if course_json else None
        cps = json.loads(checkpoints_json)
        weather_data = json.loads(weather_json) if weather_json else None

        # Update existing route or create new one
        route = None
        if route_id:
            result = await db.execute(
                select(Route).where(Route.id == route_id, Route.user_id == user.id)
            )
            route = _visible(result.scalar_one_or_none())

        if route:
            # Update existing (the course itself is re-sent only for a fresh import)
            route.name = name or route.name
            if course_data:
                route.course_json = course_data
                route.total_distance_km = course_data.get("total_distance_km", route.total_distance_km)
                route.total_elevation_gain = course_data.get("total_elevation_gain", route.total_elevation_gain)
                route.total_elevation_loss = course_data.get("total_elevation_loss", route.total_elevation_loss)
            route.target_time_s = target_time_s
            if race_date: route.race_date = _iso_date(race_date)
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
            if not course_data:
                return JSONResponse({"error": "Parcours manquant"}, status_code=400)
            route = Route(
                user_id=user.id,
                name=name or course_data.get("name", "Parcours"),
                total_distance_km=course_data.get("total_distance_km", 0),
                total_elevation_gain=course_data.get("total_elevation_gain", 0),
                total_elevation_loss=course_data.get("total_elevation_loss", 0),
                course_json=course_data,
                target_time_s=target_time_s,
                race_date=_iso_date(race_date),
                start_hour=start_hour,
                start_minute=start_minute,
                sport_type=sport_type or "trail",
                weather_json=weather_data,
                stop_minutes=stop_minutes,
            )
            db.add(route)

        await db.flush()

        from app.services.checkpoints import normalize_checkpoint

        for raw_cp in cps:
            cp = normalize_checkpoint(raw_cp)
            db.add(RouteCheckpoint(
                route_id=route.id,
                name=cp["name"],
                distance_km=cp["distance_km"],
                elevation=cp["elevation"],
                kind=cp["kind"], crew=cp["crew"], drop_bag=cp["drop_bag"],
                cutoff_clock=cp["cutoff_clock"], stop_s=cp.get("stop_s"), target_s=cp.get("target_s"),
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


_FR_WEEKDAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
_FR_MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def fr_date_label(iso: str | None, today=None) -> str:
    """« sam. 17 oct. » (the year only when it is not this year), whatever the browser's locale.
    The plan page's script writes the same text after an edit."""
    from datetime import date as _date

    if not iso:
        return ""
    try:
        d = _date.fromisoformat(str(iso)[:10])
    except ValueError:
        return ""
    today = today or _date.today()
    label = f"{_FR_WEEKDAYS[d.weekday()]} {d.day} {_FR_MONTHS[d.month - 1]}"
    return label if d.year == today.year else f"{label} {d.year}"


def debrief_mode(race_date: str | None, has_result: bool, today=None) -> str | None:
    """Where the debrief lives on the plan page: "primary" once the race is run (the main
    button), "menu" when the race has no date or is today, None before a dated race."""
    from datetime import date as _date

    if has_result:
        return "primary"
    if not race_date:
        return "menu"
    try:
        d = _date.fromisoformat(str(race_date)[:10])
    except ValueError:
        return "menu"
    today = today or _date.today()
    if d < today:
        return "primary"
    if d == today:
        return "menu"
    return None


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
    cps = [cp.as_dict() for cp in cp_result.scalars().all()]

    coords = course.route_coords or []
    geojson = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[c[1], c[0], c[3] if len(c) > 3 else 0] for c in coords],
        },
        "properties": {"name": course.name},
    }

    # The table itself, rendered now: no blank band and no round trip before the plan shows.
    initial_table_html = None
    try:
        from app.services.checkpoints import normalize_checkpoint

        w = route.weather_json or {}
        live = route.live_json or {}
        # No objective: the plan runs on the estimate, to the minute, exactly as the page's own recalculations
        # do (they send the objective field, which then holds the estimate). The first render and the next
        # ones agree, and the hero's « Estimation » plus the start gives the arrival shown under it.
        table_target = route.target_time_s or (int((course.predicted_total_time_s + 30) // 60) * 60 if course.predicted_total_time_s else None)
        tctx = await _passage_table_context(
            db, user_id, CourseProfile(**route.course_json), [normalize_checkpoint(c) for c in cps], table_target,
            float(w.get("heat_factor") or 1.0), route.start_hour if route.start_hour is not None else 6, route.start_minute or 0,
            w.get("hourly"), route, route.stop_minutes, live.get("anchor_km"), live.get("anchor_clock"), profile=profile, route_id=route.id,
        )
        initial_table_html = templates.get_template("partials/passage_times.html").render(tctx)
    except Exception:
        logger.exception("Initial passage table failed; the page will ask for it")

    p = route.params_json or {}
    mode = debrief_mode(route.race_date, bool(route.result_json))
    from app.services.training_zones import estimate_training_zones

    max_hr = (await estimate_training_zones(db, user_id)).get("max_hr")
    try:
        own_cap = int(float(p.get("hr_cap_climb"))) if p.get("hr_cap_climb") not in (None, "") else None
    except (TypeError, ValueError):
        own_cap = None
    return {
        "course": course,
        "profile": profile,
        "initial_table_html": initial_table_html,
        # the debrief is reachable (primary button or menu) in every mode but "before a dated race"
        "debrief_mode": mode,
        "show_debrief": mode is not None,
        "race_date_label": fr_date_label(route.race_date),
        "start_label": "%02d:%02d" % (route.start_hour if route.start_hour is not None else 6, route.start_minute or 0),
        "scenario": {"fast_pct": float(p.get("scenario_fast_pct") or 5.0), "safe_pct": float(p.get("scenario_safe_pct") or 10.0), "switch_km": p.get("switch_km")},
        "course_json": course.model_dump_json(),
        "gpx_waypoints": _script_json(cps),
        "geojson": json.dumps(geojson),
        "saved_route_id": route.id,
        "saved_route_name": route.name,
        "saved_target_time_s": route.target_time_s,
        "saved_race_date": route.race_date,
        "saved_start_hour": route.start_hour,
        "saved_start_minute": route.start_minute,
        "saved_sport_type": route.sport_type,
        "saved_weather_json": _script_json(route.weather_json) if route.weather_json else None,
        "saved_stop_minutes": route.stop_minutes,
        "saved_live_json": _script_json(route.live_json) if route.live_json else None,
        "race_calibration": getattr(profile, "race_calibration", None),
        "params": route.params_json or {},
        # Réglages du plan › « Plafond cardio en montée » (empty = 75 % of the max HR seen)
        "hr_cap_climb": own_cap,
        "hr_cap_default": int(round(max_hr * 0.75)) if max_hr and max_hr >= 120 else None,
        "max_hr": max_hr,
    }


@router.get("/simulator/routes/{route_id}", response_class=HTMLResponse)
async def route_detail_page(
    route_id: int,
    request: Request,
    compare: int | None = None,
    vue: str | None = None,
    open_: str | None = Query(None, alias="open"),
    confirm: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full server-rendered detail page for a saved route (refresh-safe).

    ``compare`` = an activity id coming from "Comparer à un parcours": the page
    opens on the debrief tab with that activity preselected. ``vue=nutrition``
    opens the Nutrition card, rendered here (where its forms land without JS).
    """
    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    route = _visible(result.scalar_one_or_none())
    if not route:
        raise HTTPException(status_code=404)  # full page: the app's 404 page

    if route.sport_type == "triathlon":
        ctx = await _tri_plan_context(request, route, db, user)
        ctx["compare_activity_id"] = compare
        return templates.TemplateResponse(
            request, "simulator_route_tri.html", context=ctx,
            headers={"Cache-Control": "no-store"},
        )

    if not route.course_json:
        raise HTTPException(status_code=404)

    if route.sport_type == "bike":
        ctx = await _bike_plan_context(request, route, db, user)
        ctx["nutrition_html"] = await _nutrition_html(request, route, db, user, vue, open_, confirm)
        return templates.TemplateResponse(
            request, "simulator_route_bike.html", context=ctx,
            headers={"Cache-Control": "no-store"},
        )

    # road / track / trail from OpenStreetMap, once per trace, in the background
    from app.services.surface import schedule_surface

    schedule_surface(route.id, route.course_json)

    ctx = await _build_route_context(route, db, user.id)
    ctx["nutrition_html"] = await _nutrition_html(request, route, db, user, vue, open_, confirm)
    ctx["user"] = user
    ctx["compare_activity_id"] = compare
    from app.services.race_prep import prep_context

    ctx["prep"] = await prep_context(db, user, route)  # « Préparation » (#prep): taper, nights, race week
    if compare:  # arrived from an activity: the debrief is the main action
        ctx["debrief_mode"] = "primary"
        ctx["show_debrief"] = True
    # Don't let the browser serve a stale page (kept hiding UI updates).
    return templates.TemplateResponse(
        request, "simulator_route.html", context=ctx,
        headers={"Cache-Control": "no-store"},
    )


async def _nutrition_html(request: Request, route: Route, db: AsyncSession, user: User, vue: str | None, open_: str | None,
                          confirm: str | None):
    """The Nutrition card for the page itself when it opens on it (``vue=nutrition``), else None (htmx loads it)."""
    if vue != "nutrition":
        return None
    from markupsafe import Markup

    ctx = await _nutrition_card_context(request, route, db, user, open_=open_, confirm=_id_or_none(confirm))
    return Markup(templates.get_template("partials/nutrition_card.html").render(ctx))


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
    route = _visible(result.scalar_one_or_none())
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
    route = _visible(result.scalar_one_or_none())
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
    route = _visible(result.scalar_one_or_none())
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


@router.post("/api/simulator/routes/{route_id}/live")
async def set_live_passage(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    anchor_km: float | None = Form(default=None),
    anchor_clock: str | None = Form(default=None),
    anchor_name: str = Form(default=""),
):
    """Persist (or clear) the race-day passage anchor so a reload keeps it."""
    route = await _get_owned_route(route_id, user, db)
    if not route:
        return JSONResponse({"ok": False}, status_code=404)
    if anchor_km is None or not anchor_clock or _clock_to_s(anchor_clock) is None:
        route.live_json = None
    else:
        route.live_json = {
            "anchor_km": float(anchor_km),
            "anchor_clock": anchor_clock.strip()[:5],
            "anchor_name": (anchor_name or "")[:100],
        }
    await db.flush()
    return JSONResponse({"ok": True, "live": route.live_json})


# ── Bike plan (road book with wind at time of passage) ──

def _bike_leg_stats(cycling, a: float, b: float) -> dict:
    """Time-weighted wind and power over a leg of the course, plus its mean speed."""
    from app.services.cycling_simulator import wind_label

    parts = [sg for sg in cycling.segments if sg.end_km > a and sg.start_km < b]
    t = sum(sg.predicted_time_s for sg in parts) or 1.0
    power = sum(sg.predicted_power_watts * sg.predicted_time_s for sg in parts) / t
    hw = sum(sg.headwind_ms * sg.predicted_time_s for sg in parts) / t * 3.6
    wk = sum(sg.wind_kmh * sg.predicted_time_s for sg in parts) / t
    speed = (b - a) / (t / 3600) if parts and t > 0 else 0.0
    return {"power_watts": round(power), "headwind_kmh": round(hw, 1), "wind_kmh": round(wk), "wind_label": wind_label(hw, wk), "speed_kmh": round(speed, 1)}


async def _bike_plan_context(request: Request, route: Route, db: AsyncSession, user: User, start_offset_override: int | None = None) -> dict:
    """Everything the bike plan page needs: prediction at the saved parameters,
    wind taken from the saved forecast at each segment's time of passage, and
    the road-book sections."""
    from app.schemas.simulator import CourseProfile
    from app.services.cycling_simulator import (
        build_bike_sections,
        estimate_cda,
        predict_cycling_course,
    )
    from app.services.power_calculator import ftp_for_user

    course = CourseProfile(**route.course_json)
    ftp = await ftp_for_user(db, user)
    p = route.params_json or {}
    rider_weight = float(p.get("rider_weight_kg") or user.weight_kg or 75)
    bike_weight = float(p.get("bike_weight_kg") or 9.0)
    crr = float(p.get("crr") or 0.005)
    cda_est = await estimate_cda(db, user.id, rider_weight, bike_weight, crr=crr)
    cda = float(p.get("cda") or (cda_est.estimated_cda if cda_est else 0.32))
    target = float(p.get("target_power_watts") or (round(ftp.estimated_ftp * 0.75) if ftp else 200))
    weather = route.weather_json or {}
    wind_mode = p.get("wind_mode") or ("auto" if weather else "none")
    start_hour = route.start_hour if route.start_hour is not None else 8
    start_minute = route.start_minute or 0
    start_offset = start_hour * 3600 + start_minute * 60
    if start_offset_override is not None:  # e.g. triathlon: bike starts after swim + T1
        start_offset = int(start_offset_override) % 86400
        start_hour, start_minute = start_offset // 3600, (start_offset % 3600) // 60

    hourly_wind = None
    wind_speed, wind_dir, wind_source = 0.0, None, None
    if wind_mode == "auto" and weather:
        hw = weather.get("hourly") or {}
        if hw.get("wind"):
            hourly_wind = {"speed": hw["wind"], "dir": hw.get("wind_dir") or [], "points": hw.get("points") or []}
        else:
            wind_speed = float(weather.get("wind_speed_kmh") or 0)
            wind_dir = weather.get("wind_direction_deg")
        wind_source = weather.get("source")
    elif wind_mode == "manual":
        wind_speed = float(p.get("wind_speed_kmh") or 0)
        wind_dir = p.get("wind_direction_deg")

    def _predict(watts: float):
        return predict_cycling_course(
            course, target_power_watts=watts, rider_weight_kg=rider_weight, bike_weight_kg=bike_weight,
            cda=cda, crr=crr, wind_speed_kmh=wind_speed, wind_direction_deg=wind_dir, wind_source=wind_source,
            ftp_watts=ftp.estimated_ftp if ftp else None, hourly_wind=hourly_wind, start_offset_s=start_offset,
        )

    cycling = _predict(target)
    sections = build_bike_sections(cycling, start_offset)

    # Objective → the constant power it takes (the bike's "even effort").
    required_power = None
    if route.target_time_s and start_offset_override is None:
        from app.services.cycling_simulator import solve_power_for_time

        stop_total = (route.stop_minutes or 0) * 60 * len(_aid_kms_from_checkpoints(
            [cp.as_dict() for cp in (await db.execute(
                select(RouteCheckpoint).where(RouteCheckpoint.route_id == route.id))).scalars().all()]))
        required_power = solve_power_for_time(_predict, max(1, route.target_time_s - stop_total))

    # Checkpoints (aid stations) with passage times, cutoffs and autonomy — the
    # same table as the trail plan, fed by the power model.
    cps: list[dict] = []
    cp_ids: list[int] = []
    passages: list[dict] = []
    autonomy: list[dict] = []
    if start_offset_override is None:
        from app.services.checkpoints import annotate_cutoffs, autonomy_legs
        from app.services.cycling_simulator import build_bike_passage_sections

        cp_res = await db.execute(select(RouteCheckpoint).where(RouteCheckpoint.route_id == route.id).order_by(RouteCheckpoint.distance_km))
        cp_rows = cp_res.scalars().all()
        cps = [cp.as_dict() for cp in cp_rows]
        cp_ids = [cp.id for cp in cp_rows]
        aid = set((route.nutrition_json or {}).get("refills") or []) | _aid_kms_from_checkpoints(cps)
        passages = build_bike_passage_sections(
            cycling, cps, start_offset, route.target_time_s, stop_s_per_aid=(route.stop_minutes or 0) * 60, aid_kms=aid,
        )
        passages = annotate_cutoffs(passages, cps, start_offset)
        autonomy = autonomy_legs(cps, cycling.total_distance_km, passages)
        for ps in passages:
            ps.update(_bike_leg_stats(cycling, float(ps.get("start_km") or 0), float(ps.get("end_km") or 0)))
    return {
        "request": request, "user": user, "route": route,
        "cycling": cycling, "cycling_json": cycling.model_dump_json(), "sections": sections,
        "checkpoints": cps, "cp_ids": cp_ids, "passages": passages, "autonomy": autonomy, "required_power": required_power,
        "kinds": __import__("app.services.checkpoints", fromlist=["KINDS"]).KINDS,
        "ftp": ftp, "cda_est": cda_est,
        "params": {
            "target_power_watts": int(round(target)), "rider_weight_kg": rider_weight, "bike_weight_kg": bike_weight,
            "cda": cda, "crr": crr, "wind_mode": wind_mode,
            "wind_speed_kmh": float(p.get("wind_speed_kmh") or weather.get("wind_speed_kmh") or 0),
            "wind_direction_deg": p.get("wind_direction_deg") if p.get("wind_direction_deg") is not None else weather.get("wind_direction_deg"),
        },
        "weather": weather, "start_hour": start_hour, "start_minute": start_minute, "start_offset_s": start_offset,
    }


@router.post("/api/simulator/routes/{route_id}/bike", response_class=HTMLResponse, dependencies=[Depends(require_cycling)])
async def save_bike_plan(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    target_power_watts: float = Form(...),
    rider_weight_kg: float = Form(...),
    bike_weight_kg: float = Form(default=9.0),
    cda: float = Form(default=0.32),
    crr: float = Form(default=0.005),
    race_date: str = Form(default=""),
    start_time: str = Form(default="08:00"),
    wind_mode: str = Form(default="auto"),
    wind_speed_kmh: float = Form(default=0.0),
    wind_direction_deg: float | None = Form(default=None),
    refresh_weather: str = Form(default=""),
    target_h: str = Form(default=""),
    target_m: str = Form(default=""),
    stop_minutes: str = Form(default=""),
):
    """Save the bike plan parameters (+ date/start/objective), refresh the
    forecast when needed, and re-render the plan."""
    route = await _get_owned_route(route_id, user, db)
    if not route or route.sport_type != "bike":
        return HTMLResponse("", status_code=404)
    try:
        th, tm = int(target_h or 0), int(target_m or 0)
        route.target_time_s = th * 3600 + tm * 60 if (th or tm) else None
    except ValueError:
        pass
    try:
        route.stop_minutes = int(stop_minutes) if stop_minutes.strip() else None
    except ValueError:
        pass

    route.params_json = {
        "target_power_watts": max(30.0, target_power_watts), "rider_weight_kg": rider_weight_kg,
        "bike_weight_kg": bike_weight_kg, "cda": cda, "crr": crr,
        "wind_mode": wind_mode if wind_mode in ("auto", "manual", "none") else "auto",
        "wind_speed_kmh": wind_speed_kmh, "wind_direction_deg": wind_direction_deg,
    }
    route.race_date = _iso_date(race_date)
    st = _clock_to_s(start_time)
    if st is not None:
        route.start_hour, route.start_minute = st // 3600, (st % 3600) // 60

    saved = route.weather_json or {}
    if route.race_date and (refresh_weather or not saved or saved.get("date") != route.race_date):
        coords = (route.course_json or {}).get("route_coords") or []
        if coords:
            from app.services.weather import get_weather_forecast

            cp_res = await db.execute(select(RouteCheckpoint).where(RouteCheckpoint.route_id == route.id).order_by(RouteCheckpoint.distance_km))
            w = await get_weather_forecast(coords[0][0], coords[0][1], route.race_date, points=_route_sample_points(route, [cp.as_dict() for cp in cp_res.scalars().all()]))
            if w:
                w["date"] = route.race_date
                route.weather_json = w
    await db.flush()

    ctx = await _bike_plan_context(request, route, db, user)
    return templates.TemplateResponse(
        request, "partials/bike_plan.html", context=ctx, headers={"Cache-Control": "no-store"},
    )


# ── Triathlon: swim + T1 + bike + T2 + run, one timeline, one fuel plan ──

async def _create_run_route_from_gpx(content: bytes, filename: str, user: User, db: AsyncSession) -> Route:
    from app.services.gpx import build_course_profile, parse_gpx, snap_waypoints_to_route
    from app.services.race_simulator import build_athlete_gradient_profile, predict_course

    points, gpx_waypoints = parse_gpx(content)
    course = build_course_profile(points, name=filename or "Course")
    snapped = snap_waypoints_to_route(gpx_waypoints, points)
    profile = await build_athlete_gradient_profile(db, user.id)
    course = predict_course(course, profile)
    route = Route(
        user_id=user.id, name=course.name, total_distance_km=course.total_distance_km,
        total_elevation_gain=course.total_elevation_gain, total_elevation_loss=course.total_elevation_loss,
        course_json=json.loads(course.model_dump_json()), sport_type="trail",
    )
    db.add(route)
    await db.flush()
    for wpt in snapped:
        db.add(RouteCheckpoint(route_id=route.id, name=wpt.get("name", ""), distance_km=wpt.get("distance_km", 0), elevation=wpt.get("elevation")))
    await db.flush()
    return route


async def _create_bike_route_from_gpx(content: bytes, filename: str, user: User, db: AsyncSession) -> Route:
    from app.services.gpx import build_course_profile, parse_gpx

    points, _ = parse_gpx(content)
    course = build_course_profile(points, name=filename or "Parcours vélo")
    route = Route(
        user_id=user.id, name=course.name, total_distance_km=course.total_distance_km,
        total_elevation_gain=course.total_elevation_gain, total_elevation_loss=course.total_elevation_loss,
        course_json=json.loads(course.model_dump_json()), sport_type="bike",
    )
    db.add(route)
    await db.flush()
    return route


_TRI_IF_BY_FORMAT = {"S": (0.90, 1.00), "M": (0.80, 0.90), "half": (0.75, 0.85), "full": (0.65, 0.75)}
_POWER_ZONES = [("Z1 Récupération", 0.0, 0.55), ("Z2 Endurance", 0.56, 0.75), ("Z3 Tempo", 0.76, 0.90),
                ("Z4 Seuil", 0.91, 1.05), ("Z5 VO2max", 1.06, 1.20), ("Z6 Anaérobie", 1.21, 1.50)]


@router.post("/api/simulator/triathlon", response_class=HTMLResponse, dependencies=[Depends(require_cycling)])
async def create_triathlon(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    name: str = Form(default="Triathlon"),
    tri_format: str = Form(default="half"),
    swim_distance_m: float = Form(default=1500),
    swim_pace: str = Form(default="2:00"),
    t1_min: float = Form(default=3),
    t2_min: float = Form(default=2),
    race_date: str = Form(default=""),
    start_time: str = Form(default="07:00"),
    bike_route_id: str = Form(default=""),
    run_route_id: str = Form(default=""),
    bike_gpx: UploadFile | None = File(default=None),
    run_gpx: UploadFile | None = File(default=None),
):
    """Create a triathlon from two legs (existing routes or fresh GPX uploads)."""
    from app.services.triathlon import DEFAULT_T1_S, DEFAULT_T2_S, parse_pace_100m

    def err(msg: str) -> HTMLResponse:
        return HTMLResponse(f'<div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-600">{msg}</div>')

    try:
        bike = run = None
        if bike_gpx is not None and bike_gpx.filename:
            bike = await _create_bike_route_from_gpx(await bike_gpx.read(), bike_gpx.filename, user, db)
        elif bike_route_id.strip():
            bike = await _get_owned_route(int(bike_route_id), user, db)
        if run_gpx is not None and run_gpx.filename:
            run = await _create_run_route_from_gpx(await run_gpx.read(), run_gpx.filename, user, db)
        elif run_route_id.strip():
            run = await _get_owned_route(int(run_route_id), user, db)
        if not bike or bike.sport_type != "bike":
            return err("Il manque le parcours vélo : choisis-en un ou importe son GPX.")
        if not run or run.sport_type == "bike":
            return err("Il manque le parcours à pied : choisis-en un ou importe son GPX.")

        pace_s = parse_pace_100m(swim_pace) or 120.0
        st = _clock_to_s(start_time)
        tri = Route(
            user_id=user.id, name=(name or "Triathlon").strip()[:255],
            total_distance_km=round(swim_distance_m / 1000 + bike.total_distance_km + run.total_distance_km, 2),
            total_elevation_gain=(bike.total_elevation_gain or 0) + (run.total_elevation_gain or 0),
            total_elevation_loss=(bike.total_elevation_loss or 0) + (run.total_elevation_loss or 0),
            course_json=None, sport_type="triathlon",
            race_date=race_date.strip() or None,
            start_hour=(st // 3600) if st is not None else 7,
            start_minute=((st % 3600) // 60) if st is not None else 0,
            params_json={
                "format": tri_format if tri_format in _TRI_IF_BY_FORMAT else "half",
                "swim_distance_m": float(swim_distance_m), "swim_pace_s_100m": float(pace_s),
                "t1_s": int(t1_min * 60) if t1_min is not None else DEFAULT_T1_S,
                "t2_s": int(t2_min * 60) if t2_min is not None else DEFAULT_T2_S,
                "bike_route_id": bike.id, "run_route_id": run.id,
            },
        )
        db.add(tri)
        await db.flush()
        logger.info("Triathlon %d created for user %d (bike %d, run %d)", tri.id, user.id, bike.id, run.id)
        return HTMLResponse(status_code=204, headers={"HX-Redirect": f"/simulator/routes/{tri.id}"})
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("Triathlon creation failed")
        return err("Erreur lors de la création du triathlon. Vérifie les fichiers GPX.")


async def _tri_plan_context(request: Request, route: Route, db: AsyncSession, user: User) -> dict:
    from app.schemas.simulator import CourseProfile
    from app.services.power_calculator import ftp_for_user
    from app.services.race_simulator import build_athlete_gradient_profile, predict_course
    from app.services.triathlon import (
        DEFAULT_T1_S,
        DEFAULT_T2_S,
        FORMATS,
        build_timeline,
        fmt_pace_100m,
        triathlon_nutrition,
    )

    p = route.params_json or {}
    bike = await _get_owned_route(int(p["bike_route_id"]), user, db) if p.get("bike_route_id") else None
    run = await _get_owned_route(int(p["run_route_id"]), user, db) if p.get("run_route_id") else None
    if bike and (bike.sport_type != "bike" or not bike.course_json):
        bike = None
    if run and (run.sport_type == "bike" or not run.course_json):
        run = None

    swim_m = float(p.get("swim_distance_m") or 1500)
    pace_s = float(p.get("swim_pace_s_100m") or 120)
    swim_s = int(round(swim_m / 100.0 * pace_s))
    t1_s = int(p.get("t1_s") if p.get("t1_s") is not None else DEFAULT_T1_S)
    t2_s = int(p.get("t2_s") if p.get("t2_s") is not None else DEFAULT_T2_S)
    start_hour = route.start_hour if route.start_hour is not None else 7
    start_minute = route.start_minute or 0
    start_offset = start_hour * 3600 + start_minute * 60

    bike_s, bike_ctx = 0, None
    if bike:
        bike_ctx = await _bike_plan_context(request, bike, db, user, start_offset_override=start_offset + swim_s + t1_s)
        bike_s = int(bike_ctx["cycling"].predicted_total_time_s)

    run_s, run_pred_s = 0, None
    if run:
        profile = await build_athlete_gradient_profile(db, user.id)
        run_start = start_offset + swim_s + t1_s + bike_s + t2_s
        course = predict_course(
            CourseProfile(**run.course_json), profile,
            start_hour=int(run_start // 3600) % 24, start_minute=int((run_start % 3600) // 60),
        )
        run_pred_s = int(course.predicted_total_time_s)
        run_s = int(run.target_time_s or run_pred_s)

    timeline = build_timeline(start_offset, swim_s, t1_s, bike_s, t2_s, run_s)
    total_s = swim_s + t1_s + bike_s + t2_s + run_s
    nutrition = triathlon_nutrition(bike_s / 3600, run_s / 3600, user.weight_kg, p.get("carbs_g_per_h"))

    ftp = await ftp_for_user(db, user)
    fmt = p.get("format") if p.get("format") in _TRI_IF_BY_FORMAT else "half"
    power = None
    if ftp:
        lo, hi = _TRI_IF_BY_FORMAT[fmt]
        power = {
            "ftp": int(round(ftp.estimated_ftp)), "if_lo": lo, "if_hi": hi,
            "lo": int(round(ftp.estimated_ftp * lo)), "hi": int(round(ftp.estimated_ftp * hi)),
            "zones": [{"name": n, "lo": int(round(ftp.estimated_ftp * a)), "hi": int(round(ftp.estimated_ftp * b))} for n, a, b in _POWER_ZONES],
            "bike_target": bike_ctx["params"]["target_power_watts"] if bike_ctx else None,
            "bike_if": round(bike_ctx["params"]["target_power_watts"] / ftp.estimated_ftp, 2) if bike_ctx and ftp.estimated_ftp else None,
        }

    return {
        "request": request, "user": user, "route": route, "params": p, "fmt": fmt, "formats": FORMATS,
        "bike": bike, "run": run, "bike_ctx": bike_ctx, "run_pred_s": run_pred_s,
        "swim_m": swim_m, "swim_pace": fmt_pace_100m(pace_s), "swim_s": swim_s, "t1_s": t1_s, "t2_s": t2_s,
        "bike_s": bike_s, "run_s": run_s, "total_s": total_s, "timeline": timeline,
        "nutrition": nutrition, "power": power, "ftp": ftp,
        "start_hour": start_hour, "start_minute": start_minute, "start_offset_s": start_offset,
    }


@router.post("/api/simulator/routes/{route_id}/tri", response_class=HTMLResponse, dependencies=[Depends(require_cycling)])
async def save_tri_plan(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tri_format: str = Form(default="half"),
    swim_distance_m: float = Form(default=1500),
    swim_pace: str = Form(default="2:00"),
    t1_min: float = Form(default=3),
    t2_min: float = Form(default=2),
    race_date: str = Form(default=""),
    start_time: str = Form(default="07:00"),
    carbs_g_per_h: str = Form(default=""),
):
    from app.services.triathlon import parse_pace_100m

    route = await _get_owned_route(route_id, user, db)
    if not route or route.sport_type != "triathlon":
        return HTMLResponse("", status_code=404)
    p = dict(route.params_json or {})
    p.update({
        "format": tri_format if tri_format in _TRI_IF_BY_FORMAT else p.get("format", "half"),
        "swim_distance_m": float(swim_distance_m), "swim_pace_s_100m": float(parse_pace_100m(swim_pace) or p.get("swim_pace_s_100m") or 120),
        "t1_s": int(max(0.0, t1_min) * 60), "t2_s": int(max(0.0, t2_min) * 60),
    })
    try:
        p["carbs_g_per_h"] = float(carbs_g_per_h.replace(",", ".")) if carbs_g_per_h.strip() else None
    except ValueError:
        p["carbs_g_per_h"] = None
    route.params_json = p
    route.race_date = _iso_date(race_date)
    st = _clock_to_s(start_time)
    if st is not None:
        route.start_hour, route.start_minute = st // 3600, (st % 3600) // 60
    await db.flush()
    ctx = await _tri_plan_context(request, route, db, user)
    return templates.TemplateResponse(request, "partials/tri_plan.html", context=ctx, headers={"Cache-Control": "no-store"})


@router.get("/simulator/routes/{route_id}/print", response_class=HTMLResponse)
async def print_route_plan(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Printable race plan (passage timeline + nutrition) for a saved route."""
    from app.services.race_simulator import build_scenarios, replan_from_passage

    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    route = _visible(result.scalar_one_or_none())
    if not route or not route.course_json:
        raise HTTPException(status_code=404)

    b = await _plan_bundle(route, db, user)
    course, sections, cps = b["course"], b["sections"], b["checkpoints"]
    start_offset_s = b["start_offset_s"]
    aid_kms, stop_min = b["aid_kms"], b["stop_min"]
    live = route.live_json or {}
    live_applied = False
    if live.get("anchor_km") is not None and live.get("anchor_clock"):
        clock_s = _clock_to_s(live["anchor_clock"])
        if clock_s is not None:
            sections, rp = replan_from_passage(
                sections, start_offset_s, route.target_time_s,
                float(live["anchor_km"]), clock_s, stop_s_per_aid=stop_min * 60, aid_kms=aid_kms, aid_stops=b.get("aid_stops"),
            )
            live_applied = rp is not None
            if live_applied:  # margins against the recalculated clocks
                from app.services.checkpoints import annotate_cutoffs

                sections = annotate_cutoffs(sections, cps, start_offset_s)
    pp = route.params_json or {}
    scenarios = None if live_applied else build_scenarios(
        sections, start_offset_s, route.target_time_s,
        fast_pct=float(pp.get("scenario_fast_pct") or 5.0), safe_pct=float(pp.get("scenario_safe_pct") or 10.0),
        switch_km=pp.get("switch_km"), total_distance_km=course.total_distance_km,
    )

    # Race pack: the Nutrition card's rows on the same printout (what to take
    # when leaving each refill point, on these clocks) under the plan's lines.
    from app.services import nutrition_plan as NP

    nutrition_rows: list = []
    nutrition_lines: list = []
    plan, products, _ = await _nutrition_plan(db, user.id, route, sections, start_offset_s)
    count = _nutrition_count(plan, products, sections, cps, start_offset_s) if plan["rhythms"] else None
    if count:
        nutrition_lines = [NP.line_words(r, products) for r in plan["rhythms"]]
        nutrition_rows = [{"clock": NP.clock_hm(r["clock_s"]), "name": r["name"], "take": NP.take_text(r["items"], products)} for r in count["rows"]]
        nutrition_rows.append({"clock": NP.clock_hm(count["finish_clock_s"]), "name": "Arrivée", "take": None})

    guide = (await _pacing_guide_for(route, db, user)) if b["sport"] != "bike" else None
    legs_guide = None
    if guide:
        from app.services.pacing_guide import leg_instructions

        legs_guide = leg_instructions(guide, sections)
    total_fmt = f"{b['predicted_total_s'] // 3600}h{(b['predicted_total_s'] % 3600) // 60:02d}"
    # « Consignes »: per leg, the passage row's own words (cardio ceiling + what to do);
    # the pace is the cell's, so no second one here. Consecutive legs with the same
    # instruction print as one row (« → A, B, C »).
    from app.services.pacing_guide import effort_sentence

    walk_grade = int(round(float(((guide or {}).get("caps") or {}).get("walk_grade") or 18)))
    legs_rows: list[dict] = []
    for lg in legs_guide or []:
        bl = lg.get("block") or {}
        # the leg's own ceiling (the one the row and the watch show): legs merge only when it is equal
        key = (bl.get("cls"), lg.get("hr_cap"), bl.get("hr_free"), bool(lg.get("steep")))
        if legs_rows and legs_rows[-1]["key"] == key and not lg.get("steep"):
            legs_rows[-1]["to_names"].append(lg["to_name"])
        else:
            legs_rows.append({"key": key, "to_names": [lg["to_name"]], "block": lg.get("block"), "steep": lg.get("steep"), "hr_cap": lg.get("hr_cap"),
                              "effort": effort_sentence(bl.get("cls"), None, walk_grade) if bl else None})
    return templates.TemplateResponse(
        request,
        "simulator_print.html",
        context={
            "day_labels": _day_labels(route.race_date),
            "legs_rows": legs_rows,
            "route": route,
            "course": course,
            "predicted_total_formatted": total_fmt,
            "sections": sections,
            "has_target": route.target_time_s is not None or live_applied or b["pinned"],
            "pinned": b["pinned"],
            "plan_total_s": b["plan_total_s"],
            "start_offset_s": start_offset_s,
            "nutrition_rows": nutrition_rows,
            "nutrition_lines": nutrition_lines,
            "scenarios": scenarios,
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
    route = _visible(result.scalar_one_or_none())
    if route:
        await db.delete(route)
        await db.flush()
    return JSONResponse({"ok": True})


def _day_labels(race_date: str | None, n: int = 5) -> list[str]:
    """Weekday names for day 0..n-1 of the race (« ven. », « sam. »…), or « jour 2 »… without a date."""
    from datetime import date as _d, timedelta

    names = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
    try:
        d0 = _d.fromisoformat((race_date or "")[:10])
        return [names[(d0 + timedelta(days=i)).weekday()] for i in range(n)]
    except ValueError:
        return [f"jour {i + 1}" for i in range(n)]


def _iso_date(value: str | None) -> str | None:
    """YYYY-MM-DD or None: a date the browser could not parse must not reach the db."""
    from datetime import date as _d

    try:
        return _d.fromisoformat((value or "").strip()[:10]).isoformat() if value and value.strip() else None
    except ValueError:
        return None


def _gpx_title(content: bytes, filename: str | None, fallback: str) -> str:
    """The GPX's own name (<metadata><name> or <trk><name>), else the file name without .gpx."""
    try:
        import gpxpy

        g = gpxpy.parse(content.decode("utf-8", "ignore"))
        for cand in [g.name] + [t.name for t in g.tracks]:
            if cand and cand.strip():
                return cand.strip()[:120]
    except Exception:
        pass
    base = (filename or "").rsplit("/", 1)[-1]
    return (re.sub(r"\.gpx$", "", base, flags=re.I).replace("_", " ").replace("-", " ").strip() or fallback)[:120]


# ── Weather ──

def _route_sample_points(route: Route, cps: list[dict]) -> list[list[float]]:
    """[[lat, lon, km]] at every checkpoint and at the finish — where the
    forecast is fetched so each section gets the weather of its own place."""
    coords = (route.course_json or {}).get("route_coords") or []
    if not coords:
        return []

    def at_km(km: float):
        return min(coords, key=lambda c: abs(float(c[2]) - km))

    pts = [[float(at_km(float(cp["distance_km"]))[0]), float(at_km(float(cp["distance_km"]))[1]), float(cp["distance_km"])] for cp in cps]
    last = coords[-1]
    pts.append([float(last[0]), float(last[1]), float(last[2])])
    return pts


@router.post("/api/simulator/weather")
async def get_weather(
    lat: float = Form(...),
    lon: float = Form(...),
    date: str = Form(...),
    points_json: str = Form(default=""),
):
    from app.services.weather import get_weather_forecast

    points = None
    if points_json:
        try:
            points = [p for p in json.loads(points_json) if isinstance(p, list | tuple) and len(p) >= 3][:24]
        except (ValueError, TypeError):
            points = None
    try:
        weather = await get_weather_forecast(lat, lon, date, points=points)
    except Exception:  # the service already logs; the page shows a retry chip either way
        weather = None
    if not weather:
        # 503, not 500: the upstream is unreachable, nothing is broken here — the client reads the error field
        return JSONResponse({"error": "météo indisponible"}, status_code=503)
    return JSONResponse(weather)


# ── Simulated vs actual (predicted-vs-actual calibration) ──

_RUN_TYPES = ["Run", "TrailRun", "VirtualRun"]


_BIKE_TYPES = ["Ride", "VirtualRide", "GravelRide", "EBikeRide", "MountainBikeRide"]


def _km_key(km) -> float | None:
    """A checkpoint's key when matching plan and result: its km (names repeat on loops)."""
    return round(float(km), 1) if km is not None else None


async def _result_compare_context(request: Request, route: Route, db: AsyncSession, user: User) -> dict:
    from app.models.activity import Activity

    b = await _plan_bundle(route, db, user)
    start_hour, start_minute = b["start_hour"], b["start_minute"]
    cps, plan_sections = b["checkpoints"], b["sections"]
    predicted = {_km_key(s["end_km"]): s["cumulative_time_s"] for s in plan_sections}
    predicted_total = b["predicted_total_s"]
    sport_types = _BIKE_TYPES if route.sport_type == "bike" else _RUN_TYPES

    # Leg-by-leg debrief against the PLAN the athlete actually ran with (target
    # + aid stops) — or the prediction when no target was set.
    debrief = None
    result = route.result_json
    if result and result.get("activity_id"):
        from app.models.activity import Activity as _Act
        from app.services.debrief import leg_debrief

        act = (await db.execute(select(_Act).where(_Act.id == result["activity_id"], _Act.user_id == user.id))).scalar_one_or_none()
        if act and act.splits_metric:
            stop_min = b["stop_min"]
            hr_cap = (route.params_json or {}).get("hr_cap_climb")
            debrief = leg_debrief(
                act.splits_metric, plan_sections, route.total_distance_km,
                use_target=b["use_target"], stop_s_per_aid=stop_min * 60,
                hr_cap=int(hr_cap) if hr_cap else None,
            )
            debrief["basis"] = "plan" if b["use_target"] else "prediction"

    rows = []
    if result and result.get("actual"):
        actual = {_km_key(a.get("km")): a["time_s"] for a in result["actual"]}
        for cp in cps:
            k = _km_key(cp["distance_km"])
            rows.append({
                "name": cp["name"], "km": cp["distance_km"],
                "predicted_s": predicted.get(k), "actual_s": actual.get(k),
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
            .where(Activity.user_id == user.id, Activity.sport_type.in_(sport_types))
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
        "debrief": debrief,
    }


@router.get("/api/simulator/routes/{route_id}/result", response_class=HTMLResponse)
async def result_compare_card(
    route_id: int,
    request: Request,
    preselect: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    ctx = await _result_compare_context(request, route, db, user)
    ctx["preselect"] = preselect
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

    act_km = float(activity.distance or 0) / 1000
    route_km = float(route.total_distance_km or 0)
    if route_km and act_km and abs(act_km - route_km) / route_km > 0.15:
        return HTMLResponse(
            f'<div class="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">Cette activité fait {act_km:.0f} km, la course {route_km:.0f} km : ce n\'est pas le même parcours, elle n\'est pas associée.</div>'
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

    # One-shot personal fatigue calibration, on every matched checkpoint: the
    # fresh→fade tilt whose plan, run on the runner's own finish time (as the
    # curve was fitted), best reproduces his cumulative passage times. Shrunk
    # toward the neutral tilt with few checkpoints and hard-clamped: one race
    # adjusts, never dominates. Stored with the curve it was measured against.
    # (The first checkpoint alone read +4 % at km 7 of the 2026 Transjeju and
    # missed the +31 min at km 58 that the runner then made up.)
    fatigue_tilt = fatigue_tilt_n = None
    if actual and total_actual_s and route.sport_type != "bike":
        try:
            from app.services.race_calibration import measure_fatigue_tilt
            from app.services.race_simulator import FATIGUE_MODEL, build_athlete_gradient_profile

            profile = await build_athlete_gradient_profile(db, user.id)
            sh = route.start_hour if route.start_hour is not None else 6
            sm = route.start_minute or 0
            measured = measure_fatigue_tilt(route.course_json, profile, cps, actual, total_actual_s, sh, sm)
            if measured:
                fatigue_tilt, fatigue_tilt_n = measured["tilt"], measured["n"]
        except Exception:
            logger.exception("fatigue tilt calibration failed")

    route.result_activity_id = activity.id
    route.result_json = {
        "activity_id": activity.id,
        "activity_name": activity.name,
        "activity_date": activity.start_date.strftime("%d/%m/%Y") if activity.start_date else "",
        "total_actual_s": total_actual_s,
        "actual": actual,
        **({"fatigue_tilt": fatigue_tilt, "fatigue_model": FATIGUE_MODEL, "fatigue_tilt_cps": fatigue_tilt_n}
           if fatigue_tilt is not None else {}),
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
        "servings": getattr(p, "servings", None) or 1,
    }


def _hour_options(current: int | None, hours: int, *, lo: int | None = None, hi: int | None = None, end: bool = False) -> list[tuple]:
    """(value in minutes, label, selected) for a « de » (``end`` False: 0 h …) or
    « à » (1 h … then « la fin », value "") select. A value off the whole hours
    (an older plan mapped at a ravito's time) is kept as its own option."""
    from app.services.nutrition_plan import hours_words

    mins = list(range(60 if end else 0, hours * 60, 60))
    if current is not None and current not in mins:
        mins = sorted(mins + [current])
    mins = [m for m in mins if (lo is None or m > lo) and (hi is None or m < hi)]
    opts = [(str(m), hours_words(m), m == current) for m in mins]
    if end:
        opts.append(("", "la fin", current is None))
    return opts


def _num_in(x) -> str:
    """A label value as a form shows it: 25.0 → '25', None → ''."""
    return f"{float(x):g}" if x not in (None, "") else ""


def _carbs_target(end_s: float | None) -> float:
    """The carbs per hour « Plan type » aims at: the guideline for the race's duration (50 g/h on an ultra)."""
    from app.services.nutrition import starter_carbs

    return float(starter_carbs((end_s or 0) / 3600.0))


async def _product_uses(db: AsyncSession, user: User, route: Route, pid: int, plan: dict) -> dict:
    """Where a product of « Tes produits » is eaten: this race's plan, and how
    many of his other races' plans."""
    here = any(r["product_id"] == pid for r in plan["rhythms"])
    others = []
    rows = (await db.execute(select(Route.id, Route.nutrition_json).where(Route.user_id == user.id, Route.id != route.id))).all()
    for rid, nj in rows:
        lines = nj.get("rhythms") if isinstance(nj, dict) and nj.get("v") == 2 else None
        if isinstance(lines, list) and any(isinstance(r, dict) and r.get("product_id") == pid for r in lines):
            others.append(rid)
    return {"here": here, "others": others}


def _delete_question(uses: dict, name: str) -> str:
    """What deleting a product also does, in one active sentence (v4.4 copy pass): « Supprimer Maurten Gel 100 le
    retire aussi de ton plan et de celui d'une autre course. »"""
    n = len(uses["others"])
    if uses["here"] and n:
        where = ("de ton plan et de celui d'une autre course" if n == 1
                 else f"de ton plan et de ceux de {n} autres courses")
    elif uses["here"]:
        where = "de ton plan"
    else:
        where = "du plan d'une autre course" if n == 1 else f"des plans de {n} autres courses"
    return f"Supprimer {name} le retire aussi {where}."


async def _nutrition_card_context(
    request: Request, route: Route, db: AsyncSession, user: User, *, b: dict | None = None, open_: str | None = None,
    confirm: int | None = None, own_form: dict | None = None, focus: str | None = None,
) -> dict:
    """Everything the Nutrition card shows: « Ton plan » (one line per rhythm and
    the totals against the evidence, the sodium too when the race is hot),
    « À chaque ravito » (what to take when leaving each refill point and the
    water to carry to the next one), « Ta liste », « Tes produits »."""
    from app.services import nutrition as N
    from app.services import nutrition_plan as NP

    b = b or await _plan_bundle(route, db, user)
    sections, cps, start = b["sections"], b["checkpoints"], b["start_offset_s"]
    plan, products, pantry = await _nutrition_plan(db, user.id, route, sections, start)
    lines = plan["rhythms"]
    count = _nutrition_count(plan, products, sections, cps, start)
    end = count["end_s"] if count else None
    hours = max(1, min(7 * 24, math.ceil(end / 3600))) if end else 30

    # « Ton plan »: one line per rhythm, the product among « Tes produits »
    choices = [(pid, N.short_label(p)) for pid, p in pantry.items()]
    view_lines = []
    for i, r in enumerate(lines):
        p = products[r["product_id"]]
        opts = choices if r["product_id"] in pantry else [(r["product_id"], N.short_label(p))] + choices
        view_lines.append({
            "i": i, "label": N.short_label(p), "multi": N.servings_of(p) > 1,
            "products": [(pid, lbl, pid == r["product_id"]) for pid, lbl in opts],
            "every": [(m, m == r["every_min"]) for m in NP.INTERVALS],
            "from": _hour_options(r["from_min"], hours, hi=r["to_min"]),
            "to": _hour_options(r["to_min"], hours, lo=r["from_min"], end=True),
            # one product all race long: « toute la course », no « de … à … » (they come back with a second line)
            "whole": len(lines) == 1 and not r["from_min"] and r["to_min"] is None,
        })

    carbs = caf = sodium = water_note = None
    rows: list[dict] = []
    shop: list[dict] = []
    finish = None
    copy_text = ""
    if count and lines:
        # a hot race (H: 25 °C or more on average over its predicted hours, the temperatures the simulator read in
        # its saved forecast; none saved: not hot): its water at the top of ISSN 2019's range, and its sodium
        hot = NP.is_hot(sections, start)
        g = NP._round(NP.carbs_per_hour(count["intakes"], products, end))
        note, tone = NP.carbs_note(g, end / 3600)
        carbs = {"g": g, "note": note, "tone": tone}
        if hot:  # the sodium per hour only when it is hot (ISSN 2019: 300 to 600 mg/h in the heat)
            na = NP._round(NP.sodium_per_hour(count["intakes"], products, end))
            na_note, na_tone = NP.sodium_note(na)
            sodium = {"mg": na, "note": na_note, "tone": na_tone}
        water_note = NP.water_note(hot)
        if NP.holds_caffeine(lines, products):
            mg, cap = NP.caffeine_24h(count["intakes"], products), N.caffeine_cap_mg(user.weight_kg)
            caf = {"mg": mg, "cap": cap, "over": mg > cap, "no_weight": not user.weight_kg}
        for r in count["rows"]:
            tag = "base vie" if r["base"] else ("assistance" if r["crew"] else ("drop bag" if r["drop"] else None))
            text = NP.take_text(r["items"], products)
            # after what to take, the water to carry to the next refill point: « 1 Maurten 100 · 1 L »
            water = NP.litres(NP.water_ml(r["t_s"], r["end_s"], hot))
            # the opened pouch's prises: on a phone only when the row (its water too) stays one line, always on a
            # wider screen
            room = NP.ROW_CHARS - len(r["name"]) - (len(tag) + 1 if tag else 0)
            rows.append({"id": f"s-{r['key']}", "clock": NP.clock_hm(r["clock_s"]), "name": r["name"], "tag": tag,
                         "take": text, "water": water, "parts": NP.take_parts(r["items"], products),
                         "fits": len(f"{text} · {water}") <= room})
        finish = NP.clock_hm(count["finish_clock_s"])
        # a product of « Tes produits » by its full name (what to buy); a generic one in its own words (« 14 gels »)
        shop = [{"n": n, "name": (products[pid]["one"] if n == 1 else products[pid]["many"]) if products[pid].get("one")
                 else (products[pid].get("name") or N.short_label(products[pid]))} for pid, n in count["shop"].items()]
        copy_text = "\n".join(
            [f"{route.name} : nutrition", "", "Ta liste"] + [f"{s['n']} {s['name']}" for s in shop]
            + ["", "À chaque ravito"] + [f"{r['clock']} {r['name']} : {r['take']} · {r['water']}" for r in rows]
            + [f"{finish} Arrivée"])

    # « Tes produits »: the same for every race
    used = {r["product_id"] for r in lines}
    question = None
    if confirm is not None and confirm in pantry:
        uses = await _product_uses(db, user, route, confirm, plan)
        question = _delete_question(uses, pantry[confirm].get("name") or "") if (uses["here"] or uses["others"]) else None
    my_products = []
    for pid, p in pantry.items():
        bits = [f"{N._fr(float(p.get('carbs_g') or 0))} g de glucides"]
        if p.get("sodium_mg"):
            bits.append(f"{N._fr(float(p['sodium_mg']))} mg de sodium")
        if p.get("caffeine_mg"):
            bits.append(f"{N._fr(float(p['caffeine_mg']))} mg de caféine")
        if N.servings_of(p) > 1:
            bits.append(f"en {N.servings_of(p)} prises")
        my_products.append({"id": pid, "name": p.get("name") or "", "detail": " · ".join(bits), "used": pid in used,
                            "carbs_in": _num_in(p.get("carbs_g")), "sodium_in": _num_in(p.get("sodium_mg")),
                            "caffeine_in": _num_in(p.get("caffeine_mg")) if p.get("caffeine_mg") else "",
                            "servings_in": str(N.servings_of(p)), "question": question if pid == confirm else None})
    have = {(p.get("name") or "").strip().lower() for p in pantry.values()}
    catalog = [(label, [c for c in N.PRODUCT_CATALOG if N.role(c) == role and c["name"].lower() not in have])
               for label, role in (("Gels", "gel"), ("Avec caféine", "caf"), ("Boissons", "drink"), ("Sel", "salt"))]
    return {
        "request": request, "route_id": route.id, "page_url": f"/simulator/routes/{route.id}?vue=nutrition",
        "has_times": count is not None, "old_notice": plan["old"], "lines": view_lines, "can_add": len(lines) < NP.MAX_LINES,
        "carbs": carbs, "caf": caf, "sodium": sodium, "water_note": water_note,
        "rows": rows, "finish": finish, "spare": plan["spare"], "shop": shop, "copy_text": copy_text,
        "products": my_products, "catalog": [(label, cs) for label, cs in catalog if cs], "own_form": own_form,
        "open_products": open_ == "produits" or not lines or question is not None or own_form is not None,
        "focus": focus,
    }


def _visible(route: Route | None) -> Route | None:
    """The route, or None when its sport is hidden (bike / triathlon while cycling is off)."""
    return None if route is None or sport_hidden(route.sport_type) else route


async def _get_owned_route(route_id: int, user: User, db: AsyncSession) -> Route | None:
    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    return _visible(result.scalar_one_or_none())


def _aid_kms_from_checkpoints(cps: list[dict]) -> set:
    """Checkpoints typed as an aid station (water / full / base) are stops."""
    return {round(float(cp.get("distance_km") or 0), 1) for cp in cps if (cp.get("kind") or "none") != "none"}


STOP_DEFAULT_S = {"water": 120, "full": 300, "base": 900}  # eau 2′ · ravito 5′ · base vie 15′


def _aid_stops(cps: list[dict], refills=None, override_min: int | None = None) -> dict:
    """{km: stop seconds} for every aid station: by kind, or one value everywhere when set (> 0)."""
    forced = int(override_min) * 60 if override_min else None
    out: dict = {}
    for cp in cps:
        kind = cp.get("kind") or "none"
        own = cp.get("stop_s")
        if own is not None:  # set on the point itself: wins, even 0
            if int(own) > 0:
                out[round(float(cp.get("distance_km") or 0), 1)] = int(own)
            continue
        if kind == "none":
            continue
        out[round(float(cp.get("distance_km") or 0), 1)] = forced if forced is not None else STOP_DEFAULT_S.get(kind, 300)
    for k in refills or []:
        out.setdefault(round(float(k), 1), forced if forced is not None else STOP_DEFAULT_S["full"])
    return out


async def _plan_bundle(route: Route, db: AsyncSession, user: User) -> dict:
    """Everything derived from a saved trail route, computed ONE way for every
    consumer (pacing guide, exports, debrief): the predicted course,
    the checkpoints with their metadata, the plan sections (target + stops +
    weather), cutoffs, start offset."""
    from app.schemas.simulator import CourseProfile
    from app.services.checkpoints import annotate_cutoffs
    from app.services.race_simulator import (
        build_athlete_gradient_profile,
        compute_passage_times,
        objective_moving_s,
        predict_course,
    )

    cp_result = await db.execute(
        select(RouteCheckpoint).where(RouteCheckpoint.route_id == route.id).order_by(RouteCheckpoint.distance_km)
    )
    cps = [cp.as_dict() for cp in cp_result.scalars().all()]
    refills = list((route.nutrition_json or {}).get("refills") or [])
    aid = set(refills) | _aid_kms_from_checkpoints(cps)
    stop_min = route.stop_minutes or 0
    aid_stops = _aid_stops(cps, refills, route.stop_minutes)

    if route.sport_type == "bike":
        from app.services.cycling_simulator import build_bike_passage_sections

        bctx = await _bike_plan_context(None, route, db, user)
        cycling = bctx["cycling"]
        start_offset_s = bctx["start_offset_s"]
        sections = build_bike_passage_sections(
            cycling, cps, start_offset_s, route.target_time_s, stop_s_per_aid=stop_min * 60, aid_kms=aid,
        )
        sections = annotate_cutoffs(sections, cps, start_offset_s)
        return {
            "course": CourseProfile(**route.course_json), "profile": None, "cycling": cycling, "bike_ctx": bctx,
            "checkpoints": cps, "sections": sections, "predicted_total_s": int(cycling.predicted_total_time_s),
            "start_hour": bctx["start_hour"], "start_minute": bctx["start_minute"], "start_offset_s": start_offset_s,
            "stop_min": stop_min, "aid_kms": aid, "use_target": bool(route.target_time_s),
            "pinned": False, "plan_total_s": route.target_time_s,
            "params": route.params_json or {}, "sport": "bike",
        }

    start_hour = route.start_hour if route.start_hour is not None else 6
    start_minute = route.start_minute or 0
    start_offset_s = start_hour * 3600 + start_minute * 60
    course = CourseProfile(**route.course_json)
    profile = await build_athlete_gradient_profile(db, user.id)
    course = predict_course(
        course, profile, start_hour=start_hour, start_minute=start_minute,
        plan_moving_s=objective_moving_s(
            route.target_time_s, course.total_distance_km, cps, stop_min * 60, aid, aid_stops,
        ),
    )
    sections = compute_passage_times(
        course, cps, route.target_time_s, 1.0, start_hour, start_minute,
        route.weather_json.get("hourly") if route.weather_json else None,
        stop_s_per_aid=stop_min * 60, aid_kms=aid, aid_stops=aid_stops,
    )
    sections = annotate_cutoffs(sections, cps, start_offset_s)
    from app.services.race_simulator import is_pinned, plan_total_s

    return {
        "course": course, "profile": profile, "checkpoints": cps, "sections": sections, "aid_stops": aid_stops,
        "predicted_total_s": int(course.predicted_total_time_s),
        "start_hour": start_hour, "start_minute": start_minute, "start_offset_s": start_offset_s,
        # pinned checkpoints make a plan (adjusted_*) even without an objective
        "stop_min": stop_min, "aid_kms": aid, "use_target": bool(route.target_time_s) or is_pinned(sections),
        "pinned": is_pinned(sections),
        # the plan's finish (stops included): the objective, or where the pins lead
        "plan_total_s": plan_total_s(sections, start_offset_s, route.target_time_s),
        "params": route.params_json or {}, "sport": "trail",
    }


# ── Pacing guide (terrain-typed instructions) ──

async def _pacing_guide_for(route: Route, db: AsyncSession, user: User) -> dict:
    """The terrain guide with the athlete's ceilings (used by the Pilotage tab,
    the watch export and the printed band)."""
    ctx = await _pacing_context(None, route, db, user)
    return ctx["guide"]


async def _pacing_context(request: Request | None, route: Route, db: AsyncSession, user: User) -> dict:
    from app.services.pacing_guide import build_pacing_guide, default_hr_caps, resolve_hr_caps
    from app.services.training_zones import estimate_training_zones

    b = await _plan_bundle(route, db, user)
    p = b["params"]
    zones = await estimate_training_zones(db, user.id)
    defaults = default_hr_caps(zones.get("max_hr"))
    rc = resolve_hr_caps(p, zones.get("max_hr"))  # the same ceilings as the passage rows
    caps = {"hr_cap_climb": rc["climb"], "hr_cap_flat": rc["flat"], "hr_release_descent": rc["descent"]}
    walk_grade = rc["walk_grade"]
    guide = build_pacing_guide(b["course"], b["plan_total_s"] or int(b["course"].predicted_total_time_s), walk_grade=walk_grade, plan_sections=b["sections"], **caps)
    return {
        "request": request, "route": route, "route_id": route.id, "guide": guide, "caps": caps,
        "walk_grade": walk_grade, "max_hr": zones.get("max_hr"), "defaults": defaults,
        "has_target": b["use_target"], "total_km": b["course"].total_distance_km,
        "scenario": {
            "fast_pct": float(p.get("scenario_fast_pct") or 5.0), "safe_pct": float(p.get("scenario_safe_pct") or 10.0),
            "switch_km": p.get("switch_km"),
        },
        "checkpoints": b["checkpoints"],
    }


@router.get("/partials/simulator/pacing/{route_id}", response_class=HTMLResponse)
async def pacing_card(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    ctx = await _pacing_context(request, route, db, user)
    return templates.TemplateResponse(
        request, "partials/pacing_guide.html", context=ctx, headers={"Cache-Control": "no-store"},
    )


@router.post("/api/simulator/routes/{route_id}/params", response_class=HTMLResponse)
async def save_route_params(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Heart-rate ceilings (and old scenario fields): only the keys the form
    carries change. « Plafond cardio en montée » alone (Réglages du plan) →
    204, flat and descent derive from it again; the old Pilotage form (it
    posts walk_grade) still gets its card back until it is removed."""
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    p = dict(route.params_json or {})
    if "hr_cap_climb" in form:
        p["hr_cap_climb"] = _form_num(form, "hr_cap_climb", 80, 220, int)
        if p["hr_cap_climb"] is None:
            p.pop("hr_cap_climb", None)  # empty = automatic (75 % of the max HR seen)
        if "hr_cap_flat" not in form and "hr_release_descent" not in form:
            p.pop("hr_cap_flat", None)
            p.pop("hr_release_descent", None)
    for key in ("hr_cap_flat", "hr_release_descent"):
        if key in form:
            p[key] = _form_num(form, key, 80, 220, int)
    if "walk_grade" in form:
        p["walk_grade"] = _form_num(form, "walk_grade", 8, 40) or 18.0
    if "scenario_fast_pct" in form or "scenario_safe_pct" in form or "switch_km" in form:  # older clients
        _apply_scenarios(form, p, route)
    route.params_json = p
    await db.flush()
    if "walk_grade" not in form:
        return HTMLResponse(status_code=204)
    ctx = await _pacing_context(request, route, db, user)
    return templates.TemplateResponse(request, "partials/pacing_guide.html", context=ctx, headers={"Cache-Control": "no-store"})


def _form_num(form, key, lo, hi, cast=float):
    raw = form.get(key)
    if raw in (None, ""):
        return None
    try:
        v = cast(float(str(raw).replace(",", ".")))
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, v))


def _apply_scenarios(form, p: dict, route: Route) -> None:
    fast, safe = _form_num(form, "scenario_fast_pct", 0, 30), _form_num(form, "scenario_safe_pct", 0, 60)
    p["scenario_fast_pct"] = fast if fast is not None else 5.0
    p["scenario_safe_pct"] = safe if safe is not None else 10.0
    p["switch_km"] = _form_num(form, "switch_km", 0.1, float(route.total_distance_km or 9999))


@router.post("/api/simulator/routes/{route_id}/scenarios")
async def save_route_scenarios(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Optimiste / sécurité margins and the switch point, from the plan page; the page then recalculates."""
    route = await _get_owned_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    p = dict(route.params_json or {})
    _apply_scenarios(await request.form(), p, route)
    route.params_json = p
    await db.flush()
    return HTMLResponse(status_code=204)


# ── Pace strategy export (COROS / Garmin) ──

@router.get("/api/simulator/routes/{route_id}/pace-export")
async def export_pace_strategy(
    route_id: int,
    fmt: str = Query("gpx", alias="format"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Target passage times per waypoint, for the watch: timed GPX, TCX course
    (Garmin Virtual Partner) or CSV (COROS pace strategy / spreadsheet)."""
    from app.services.pace_export import build_pace_csv, build_pace_gpx, build_pace_tcx

    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return JSONResponse({"error": "Parcours non trouvé"}, status_code=404)
    coords = route.course_json.get("route_coords") or []
    if not coords:
        return JSONResponse({"error": "Trace GPS indisponible"}, status_code=400)
    b = await _plan_bundle(route, db, user)
    segs = [g.model_dump() for g in (b["cycling"].segments if b["sport"] == "bike" else b["course"].segments)]
    leg_codes: list[str] = []
    if b["sport"] != "bike":
        from app.services.pacing_guide import leg_instructions

        leg_codes = [li["code"] for li in leg_instructions(await _pacing_guide_for(route, db, user), b["sections"])]
    # « Prends 2 Maurten 100 » on each point where the bag is refilled (the Nutrition card's row), so the watch shows it
    notes: dict = {}
    if b["sections"]:
        try:
            from app.services.nutrition_plan import take_text

            plan, products, _ = await _nutrition_plan(db, user.id, route, b["sections"], b["start_offset_s"])
            count = _nutrition_count(plan, products, b["sections"], b["checkpoints"], b["start_offset_s"]) if plan["rhythms"] else None
            for r in (count or {}).get("rows", [])[1:]:
                if any(it["units"] for it in r["items"]):
                    notes[r["key"]] = "Prends " + take_text(r["items"], products)
        except Exception:
            logger.exception("nutrition notes for the export failed")
    safe_name = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in route.name).strip() or "parcours"
    fmt = (fmt or "gpx").lower()
    if fmt == "csv":
        body = build_pace_csv(route.name, b["sections"], b["use_target"], b["start_offset_s"], leg_codes=leg_codes)
        return Response(content=body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{safe_name}-plan.csv"'})
    if fmt == "tcx":
        body = build_pace_tcx(route.name, coords, segs, b["sections"], b["use_target"], route.race_date, b["start_offset_s"], leg_codes=leg_codes, notes_by_km=notes)
        return Response(content=body, media_type="application/vnd.garmin.tcx+xml",
                        headers={"Content-Disposition": f'attachment; filename="{safe_name}-plan.tcx"'})
    body = build_pace_gpx(route.name, coords, segs, b["sections"], b["use_target"], route.race_date, b["start_offset_s"], leg_codes=leg_codes, notes_by_km=notes)
    return Response(content=body, media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}-plan.gpx"'})


_NU = "/partials/simulator/nutrition/{route_id}"


@router.get(_NU, response_class=HTMLResponse)
async def nutrition_card(
    route_id: int,
    request: Request,
    open_: str | None = Query(None, alias="open"),
    confirm: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The Nutrition card. Reading it never writes: an older plan is read in
    memory, stored as v2 on the first change. ``open=produits`` opens « Tes
    produits »; ``confirm`` asks before deleting a product a plan uses."""
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    ctx = await _nutrition_card_context(request, route, db, user, open_=open_, confirm=_id_or_none(confirm))
    # htmx GETs can be heuristically cached by the browser; force a fresh card.
    return templates.TemplateResponse(request, "partials/nutrition_card.html", context=ctx, headers={"Cache-Control": "no-store"})


@router.get("/nutrition")
async def nutrition_page(user: User = Depends(get_current_user)):
    """The products now live in each race's Nutrition card."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/simulator", status_code=303)


def _product_fields(form) -> dict | None:
    """Label values from « Un produit à toi » (or its edit form); None without
    a name. A new product is a gel unless the form says otherwise; an edit
    keeps the kind it has."""
    name = (form.get("name") or "").strip()
    if not name:
        return None

    def _f(key):
        raw = form.get(key)
        if raw in (None, ""):
            return None
        try:
            x = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            return None
        return max(0.0, x) if math.isfinite(x) else None  # « inf », « 1e999 », « nan »: not a number for a label

    out = {"name": name[:100], "carbs_g": _f("carbs_g") or 0, "sodium_mg": _f("sodium_mg") or 0, "caffeine_mg": _f("caffeine_mg") or None}
    if "kind" in form:
        kind = (form.get("kind") or "").strip()
        out["kind"] = kind if kind in ("gel", "bar", "drink", "salt", "solid") else "gel"
    if "servings" in form:  # prises in one unit (a PF 90 pouch: 3)
        out["servings"] = max(1, min(12, int(_f("servings") or 1)))
    else:  # an older client: the catalogue's prises for a product named like it (a PF 90 pouch)
        from app.services.nutrition import servings_of

        out["servings"] = servings_of({"name": out["name"], "carbs_g": out["carbs_g"]})
    return out


async def _add_from_catalog(key: str, db: AsyncSession, user: User) -> int | None:
    """The catalogue product in « Tes produits » (added once): its id."""
    from app.services.nutrition import CATALOG_BY_KEY

    c = CATALOG_BY_KEY.get(key)
    if not c:
        return None
    res = await db.execute(select(NutritionProduct).where(NutritionProduct.user_id == user.id))
    for p in res.scalars().all():
        if (p.name or "").strip().lower() == c["name"].lower():
            return p.id
    prod = NutritionProduct(
        user_id=user.id, name=c["name"], kind=c["kind"], carbs_g=c["carbs_g"], sodium_mg=c["sodium_mg"],
        kcal=c.get("kcal"), caffeine_mg=c.get("caffeine_mg"), volume_ml=c.get("volume_ml"), servings=int(c.get("servings") or 1),
    )
    db.add(prod)
    await db.flush()
    return prod.id


def _id_or_none(raw: str | None) -> int | None:
    """A product id from the address (« ?confirm=12 »); anything else is no id."""
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _nu_page(route_id: int, anchor: str, **params) -> str:
    """The race page with its Nutrition card open (rendered on the server: no JS needed), at ``anchor``."""
    q = "".join(f"&{k}={v}" for k, v in params.items() if v not in (None, ""))
    return f"/simulator/routes/{route_id}?vue=nutrition{q}#{anchor}"


async def _nu_reply(request: Request, route: Route, db: AsyncSession, user: User, *, anchor: str, b: dict | None = None,
                    open_: str | None = None, confirm: int | None = None, own_form: dict | None = None, focus: str | None = None):
    """After a change: htmx gets the card back (it swaps it in place); a plain
    form post (no JS) is sent back to the race page, the card open at its block."""
    if request.headers.get("HX-Request"):
        ctx = await _nutrition_card_context(request, route, db, user, b=b, open_=open_, confirm=confirm, own_form=own_form, focus=focus)
        return templates.TemplateResponse(request, "partials/nutrition_card.html", context=ctx, headers={"Cache-Control": "no-store"})
    from fastapi.responses import RedirectResponse

    return RedirectResponse(_nu_page(route.id, anchor, open=open_, confirm=confirm), status_code=303)


async def _nu_state(route: Route, db: AsyncSession, user: User) -> tuple[dict, dict, dict, dict]:
    """(plan, products, pantry, bundle): the plan as the card shows it, before a change."""
    b = await _plan_bundle(route, db, user)
    plan, products, pantry = await _nutrition_plan(db, user.id, route, b["sections"], b["start_offset_s"])
    return plan, products, pantry, b


async def _nu_store(route: Route, db: AsyncSession, rhythms: list[dict], spare: bool) -> None:
    """The plan, stored as v2 (an older plan's extra stops carried: the passage table reads them)."""
    from app.services.nutrition_plan import plan_json

    old = route.nutrition_json if isinstance(route.nutrition_json, dict) else {}
    refills = [x for x in old.get("refills") or [] if isinstance(x, int | float)] if isinstance(old.get("refills"), list) else None
    route.nutrition_json = plan_json(rhythms, spare, refills)
    await db.flush()


async def _nu_route(route_id: int, user: User, db: AsyncSession) -> Route | None:
    route = await _get_owned_route(route_id, user, db)
    return route if route and route.course_json else None


@router.post(_NU + "/rhythms", response_class=HTMLResponse)
async def nutrition_add_line(route_id: int, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """« + Ajouter un produit au plan »: a line for the next product of « Tes
    produits » not in the plan yet, every 60 min, from the start to the finish."""
    from app.services import nutrition as N
    from app.services import nutrition_plan as NP

    route = await _nu_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    plan, products, pantry, b = await _nu_state(route, db, user)
    lines = list(plan["rhythms"])
    focus = None
    if len(lines) < NP.MAX_LINES:
        in_plan = {r["product_id"] for r in lines}
        pid = next((p for p in pantry if p not in in_plan), next(iter(pantry), N.GENERIC_GEL))
        every = 60 if lines else NP.starter_interval(products[pid], _carbs_target(NP.race_end_s(b["sections"], b["start_offset_s"])))
        lines.append({"product_id": pid, "every_min": every, "from_min": 0, "to_min": None})
        await _nu_store(route, db, lines, plan["spare"])
        focus = f"nu-l{len(lines) - 1}-p"
    return await _nu_reply(request, route, db, user, anchor="nu-plan", b=b, focus=focus)


@router.post(_NU + "/rhythms/{i}", response_class=HTMLResponse)
async def nutrition_update_line(route_id: int, i: int, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """A line's product, interval, « de » and « à » (minutes; « à » empty = the
    finish). A field the form does not carry keeps its value; a line that no
    longer exists (a stale card) changes nothing."""
    from app.services.nutrition_plan import clean_lines

    route = await _nu_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    plan, products, _pantry_, b = await _nu_state(route, db, user)
    lines = list(plan["rhythms"])
    if 0 <= i < len(lines):
        line = dict(lines[i])
        for key in ("product_id", "every_min", "from_min"):
            if form.get(key) not in (None, ""):
                line[key] = form.get(key)
        if "to_min" in form:
            line["to_min"] = form.get("to_min") or None
        cleaned = clean_lines([line], products)
        if cleaned and cleaned[0] != lines[i]:
            lines[i] = cleaned[0]
            await _nu_store(route, db, lines, plan["spare"])
    return await _nu_reply(request, route, db, user, anchor="nu-plan", b=b)


@router.post(_NU + "/rhythms/{i}/delete", response_class=HTMLResponse)
async def nutrition_delete_line(route_id: int, i: int, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    route = await _nu_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    plan, _products, _pantry_, b = await _nu_state(route, db, user)
    lines = list(plan["rhythms"])
    if 0 <= i < len(lines):
        lines.pop(i)
        await _nu_store(route, db, lines, plan["spare"])
    return await _nu_reply(request, route, db, user, anchor="nu-plan", b=b, focus="nu-add" if lines else "nu-starter")


@router.post(_NU + "/starter", response_class=HTMLResponse)
async def nutrition_starter(route_id: int, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """« Plan type »: the first gel of « Tes produits » (else a generic gel),
    one every N min, N giving about the guideline's carbs per hour for the
    race's duration. An empty plan only."""
    from app.services import nutrition as N
    from app.services import nutrition_plan as NP

    route = await _nu_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    plan, products, pantry, b = await _nu_state(route, db, user)
    if not plan["rhythms"]:
        pid = next((p for p, prod in pantry.items() if N.role(prod) == "gel" and N.per_prise(prod, "carbs_g") > 0), N.GENERIC_GEL)
        every = NP.starter_interval(products[pid], _carbs_target(NP.race_end_s(b["sections"], b["start_offset_s"])))
        await _nu_store(route, db, [{"product_id": pid, "every_min": every, "from_min": 0, "to_min": None}], plan["spare"])
    return await _nu_reply(request, route, db, user, anchor="nu-plan", b=b, focus="nu-l0-p")


@router.post(_NU + "/spare", response_class=HTMLResponse)
async def nutrition_spare(route_id: int, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """« +1 de secours à chaque ravito »: ``spare`` = 1 / 0 sets it (a stale card cannot flip it back), absent toggles."""
    route = await _nu_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    plan, _products, _pantry_, b = await _nu_state(route, db, user)
    raw = form.get("spare")
    spare = (str(raw) in ("1", "true", "on")) if raw not in (None, "") else not plan["spare"]
    if spare != plan["spare"]:
        await _nu_store(route, db, plan["rhythms"], spare)
    return await _nu_reply(request, route, db, user, anchor="nu-ravitos", b=b)


@router.post(_NU + "/products", response_class=HTMLResponse)
@router.post(_NU + "/catalog/{key}", response_class=HTMLResponse)
async def nutrition_add_product(route_id: int, request: Request, key: str | None = None,
                                user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """« Tes produits »: one from the catalogue (``key``) or « Un produit à
    toi ». It joins the pantry, not the plan: a line says when it is eaten."""
    route = await _nu_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    key = key or (form.get("key") or "").strip() or None
    own_form = None
    focus = None
    if key:
        pid = await _add_from_catalog(key, db, user)
        focus = f"nu-prod-{pid}" if pid else "nu-cat"
    else:
        f = _product_fields(form)
        if f:
            prod = NutritionProduct(user_id=user.id, **{"kind": "gel", **f})
            db.add(prod)
            await db.flush()
            focus = f"nu-prod-{prod.id}"
        else:  # no name: the form stays open with what was typed
            own_form = {"error": "Donne-lui un nom.", **{k: str(form.get(k) or "")[:12] for k in ("carbs_g", "sodium_mg", "caffeine_mg", "servings")}}
            focus = "nu-own-name"
    return await _nu_reply(request, route, db, user, anchor="nu-produits", open_="produits", own_form=own_form, focus=focus)


@router.post(_NU + "/products/{product_id}", response_class=HTMLResponse)
async def nutrition_edit_product(route_id: int, product_id: int, request: Request,
                                 user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    route = await _nu_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    product = (await db.execute(select(NutritionProduct).where(NutritionProduct.id == product_id, NutritionProduct.user_id == user.id))).scalar_one_or_none()
    f = _product_fields(form) if product else None
    if f:
        for k, v in f.items():
            setattr(product, k, v)
        await db.flush()
    return await _nu_reply(request, route, db, user, anchor="nu-produits", open_="produits", focus=f"nu-prod-{product_id}")


@router.post(_NU + "/products/{product_id}/delete", response_class=HTMLResponse)
async def nutrition_delete_product(route_id: int, product_id: int, request: Request,
                                   user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Out of « Tes produits ». A product a plan eats asks first (``confirm``
    = 1 deletes it and takes it off those plans' lines)."""
    route = await _nu_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    product = (await db.execute(select(NutritionProduct).where(NutritionProduct.id == product_id, NutritionProduct.user_id == user.id))).scalar_one_or_none()
    if not product:
        return await _nu_reply(request, route, db, user, anchor="nu-produits", open_="produits")
    plan, _products, pantry, b = await _nu_state(route, db, user)
    uses = await _product_uses(db, user, route, product_id, plan)
    if (uses["here"] or uses["others"]) and str(form.get("confirm")) != "1":
        return await _nu_reply(request, route, db, user, anchor="nu-produits", b=b, open_="produits", confirm=product_id,
                               focus=f"nu-yes-{product_id}")
    await db.delete(product)
    await db.flush()
    if uses["here"]:
        await _nu_store(route, db, [r for r in plan["rhythms"] if r["product_id"] != product_id], plan["spare"])
    if uses["others"]:
        for other in (await db.execute(select(Route).where(Route.id.in_(uses["others"]), Route.user_id == user.id))).scalars().all():
            nj = dict(other.nutrition_json)
            nj["rhythms"] = [r for r in nj.get("rhythms") or [] if not (isinstance(r, dict) and r.get("product_id") == product_id)]
            other.nutrition_json = nj
        await db.flush()
    # the focus lands on the list, or on the fold when it was his last product
    return await _nu_reply(request, route, db, user, anchor="nu-produits", b=b, open_="produits", focus="nu-prods" if len(pantry) > 1 else "nu-produits")


@router.post(_NU + "/plan", response_class=HTMLResponse)
async def nutrition_stale_card(route_id: int, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """A tap from a card left open from before Nutrition v2: nothing is
    changed, the new card comes back in its place."""
    route = await _nu_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    return await _nu_reply(request, route, db, user, anchor="nutrition")


# ── Bike checkpoints (aid stations): server-side editing, plan re-rendered ──

async def _render_bike_plan(request: Request, route: Route, db: AsyncSession, user: User) -> HTMLResponse:
    ctx = await _bike_plan_context(request, route, db, user)
    return templates.TemplateResponse(request, "partials/bike_plan.html", context=ctx, headers={"Cache-Control": "no-store"})


@router.post("/api/simulator/routes/{route_id}/checkpoints", response_class=HTMLResponse, dependencies=[Depends(require_cycling)])
async def add_checkpoint(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    name: str = Form(default=""),
    distance_km: float = Form(...),
):
    from app.services.race_simulator import _elevation_at_km

    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    if name.strip() and 0 < distance_km < (route.total_distance_km or 0):
        from app.schemas.simulator import CourseProfile

        elev = _elevation_at_km(CourseProfile(**route.course_json), float(distance_km))
        db.add(RouteCheckpoint(route_id=route.id, name=name.strip()[:100], distance_km=round(float(distance_km), 1), elevation=elev))
        await db.flush()
    return await _render_bike_plan(request, route, db, user)


@router.post("/api/simulator/routes/{route_id}/checkpoints/{cp_id}", response_class=HTMLResponse, dependencies=[Depends(require_cycling)])
async def update_checkpoint(
    route_id: int,
    cp_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.checkpoints import normalize_checkpoint

    route = await _get_owned_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    cp = (await db.execute(select(RouteCheckpoint).where(RouteCheckpoint.id == cp_id, RouteCheckpoint.route_id == route.id))).scalar_one_or_none()
    if cp:
        form = await request.form()
        data = cp.as_dict()
        for key in ("name", "distance_km", "kind", "cutoff_clock", "target_s"):
            if key in form:
                data[key] = form.get(key)
        for key in ("crew", "drop_bag"):
            if key in form:
                data[key] = str(form.get(key)).lower() in ("1", "true", "on")
        n = normalize_checkpoint(data)
        if n["name"] and 0 < n["distance_km"] < (route.total_distance_km or 0):
            cp.name, cp.distance_km = n["name"], n["distance_km"]
        cp.kind, cp.crew, cp.drop_bag, cp.cutoff_clock = n["kind"], n["crew"], n["drop_bag"], n["cutoff_clock"]
        cp.target_s = n["target_s"]
        await db.flush()
    return await _render_bike_plan(request, route, db, user)


@router.post("/api/simulator/routes/{route_id}/checkpoints/{cp_id}/delete", response_class=HTMLResponse, dependencies=[Depends(require_cycling)])
async def delete_checkpoint(
    route_id: int,
    cp_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    route = await _get_owned_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    cp = (await db.execute(select(RouteCheckpoint).where(RouteCheckpoint.id == cp_id, RouteCheckpoint.route_id == route.id))).scalar_one_or_none()
    if cp:
        await db.delete(cp)
        await db.flush()
    return await _render_bike_plan(request, route, db, user)
