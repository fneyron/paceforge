import json
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
    """What « Mes courses » shows for a race: the date in words, the objective and the
    arrival clock, the real time against the plan once run, and a mini profile."""
    from datetime import date as _date

    out: dict = {"date": None, "when": None, "objective": None, "arrival": None, "real": None, "diff": None, "good": None, "profile": None}
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
    sh = rt.start_hour if rt.start_hour is not None else 6
    sm = rt.start_minute or 0
    out["start"] = f"{sh:02d}:{sm:02d}"
    if rt.target_time_s:
        out["objective"] = _hm(rt.target_time_s)
        fin = sh * 3600 + sm * 60 + rt.target_time_s
        n = fin // 86400
        clock = f"{(fin % 86400) // 3600:02d}:{(fin % 3600) // 60:02d}"
        if d and n:
            from datetime import timedelta as _td

            out["arrival"] = f"{_DAYS_FR[(d + _td(days=n)).weekday()]} {clock}"
        else:
            out["arrival"] = (f"J+{n} " if n else "") + clock
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


def _hm_s(seconds: float) -> str:
    """7260 → '2h01', 2700 → '45 min'."""
    s = int(round(float(seconds or 0) / 60)) * 60
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h{m:02d}" if h else f"{m} min"


def _l1(ml: float) -> str:
    """Litres with one decimal, French: 1100 → '1,1'."""
    return f"{float(ml or 0) / 1000:.1f}".replace(".", ",")


async def _pantry(db: AsyncSession, user_id: int) -> dict:
    """The athlete's products, newest first: {id: product dict}."""
    res = await db.execute(
        select(NutritionProduct).where(NutritionProduct.user_id == user_id)
        .order_by(NutritionProduct.created_at.desc(), NutritionProduct.id.desc())
    )
    return {p.id: _product_dict(p) for p in res.scalars().all()}


async def _default_source(db: AsyncSession, user_id: int, exclude_id: int | None, products: dict) -> dict | None:
    """The newest other race of the athlete whose plan has products: a new race starts from it."""
    from app.services.nutrition import read_state

    q = select(Route.id, Route.name, Route.nutrition_json).where(Route.user_id == user_id)
    if exclude_id:
        q = q.where(Route.id != exclude_id)
    if hidden_sports():
        q = q.where(Route.sport_type.notin_(hidden_sports()))
    for _rid, name, nj in (await db.execute(q.order_by(Route.id.desc()).limit(40))).all():
        if nj and any(p in products for p in read_state(nj)["picks"]):
            return {"name": name, "nutrition_json": nj}
    return None


def _moving_s(sections: list[dict] | None) -> float | None:
    """The plan's moving time (its stops left out): the last section's cumulative time."""
    if not sections:
        return None
    last = sections[-1]
    v = last.get("adjusted_cumulative_time_s")
    return float(v if v is not None else (last.get("cumulative_time_s") or 0)) or None


async def _nutrition_inputs(
    db: AsyncSession, user_id: int, route: Route | None, duration_s: float, weight_kg: float | None,
    moving_s: float | None = None, start_offset_s: int | None = None, mean_temp_c: float | None = None,
) -> dict:
    """ONE resolver for every surface (card, passage rows, print band): the
    stored choices (or the virtual default) → compute_plan's arguments.
    ``mean_temp_c``: a forecast the page has but has not saved yet (else the saved one)."""
    from app.services.nutrition import read_state, resolve_inputs, with_generics

    pantry = await _pantry(db, user_id)
    nj = route.nutrition_json if route else None
    st = read_state(nj)
    products = with_generics(pantry)
    default_from = None
    if not any(p in products for p in st["picks"]) and not (st.get("explicit") and not st["picks"]):
        default_from = await _default_source(db, user_id, route.id if route else None, products)
    mean_temp = (route.weather_json or {}).get("temperature_c") if route and route.weather_json else None
    if mean_temp_c is not None:
        mean_temp = mean_temp_c
    inp = resolve_inputs(nj, pantry, duration_s, mean_temp, weight_kg, default_from, moving_s=moving_s, start_offset_s=start_offset_s)
    inp["pantry"], inp["mean_temp"] = pantry, mean_temp
    inp["moving_s"], inp["start_offset_s"] = moving_s, start_offset_s
    return inp


def _resupply_points(checkpoints: list[dict]) -> list[dict]:
    return [{"km": cp["distance_km"], "name": cp["name"]} for cp in checkpoints if cp.get("drop_bag") or cp.get("crew")]


async def _nutrition_plan(
    db: AsyncSession, user_id: int, weight_kg: float | None, route: Route | None, sections: list[dict],
    checkpoints: list[dict], aid_kms: set, start_offset_s: int, duration_s: float, mean_temp_c: float | None = None,
) -> tuple[dict, dict, list]:
    """(inputs, plan, shopping list): the same numbers everywhere."""
    from app.services.nutrition import compute_plan, main_product_id, shopping_list

    inp = await _nutrition_inputs(db, user_id, route, duration_s, weight_kg, _moving_s(sections), start_offset_s, mean_temp_c)
    plan = compute_plan(
        duration_s, inp["targets"], inp["items"], inp["products_by_id"], sections,
        flask_capacity_ml=inp["flask_capacity_ml"], refill_kms=aid_kms, resupply_points=_resupply_points(checkpoints),
        caffeine=inp["caffeine"], start_offset_s=start_offset_s, weight_kg=weight_kg,
    )
    shop = shopping_list(plan, main_product_id(plan), inp["products_by_id"])
    return inp, plan, shop


def _until(name: str | None) -> str:
    """'jusqu'à Gasiri', 'jusqu'à l'arrivée'."""
    return "l'arrivée" if (name or "") == "Arrivée" else (name or "")


def _bag_title(g: dict, cps_by_km: dict, sep: str = " · ") -> str:
    if g.get("is_start"):
        return "Sac au départ"
    cp = cps_by_km.get(round(float(g.get("km") or 0), 1)) or {}
    return f"{'Assistance' if cp.get('crew') and not cp.get('drop_bag') else 'Drop bag'}{sep}{g['at']}"


def _water_notes(plan: dict, carry_ml: int | None) -> dict:
    """{km of a refill point: (sentence, alert)} where a long dry stretch starts."""
    from app.services.nutrition import _fr, ceil_half_l, liters

    hy = plan.get("hydration") or {}
    out: dict = {}
    if not hy.get("has_refills"):
        return out
    for s in hy.get("segments") or []:
        if s["from_km"] <= 0:
            continue  # from the start: the « Sac au départ » card says it
        need, key = s["need_ml"], round(float(s["from_km"]), 1)
        if carry_ml:
            if need > carry_ml + 1:
                out[key] = (f"Remplis tout ici : il faut {_fr(ceil_half_l(need))} L jusqu'à {s['to_name']} ({_hm_s(s['time_s'])}), tu portes {liters(carry_ml)} L.", True)
        elif need >= 1000:
            out[key] = (f"Remplis {_fr(ceil_half_l(need))} L ici : pas d'eau avant {s['to_name']} ({_hm_s(s['time_s'])}).", False)
    return out


async def _leg_details(
    db: AsyncSession, user_id: int, route_obj: Route | None, guide: dict, sections: list[dict],
    checkpoints: list[dict], aid_kms: set, start_offset_s: int, duration_s: float | None = None,
    mean_temp_c: float | None = None,
) -> list[dict]:
    """What a passage row says once opened, one entry per section (the leg that ENDS
    at that point): what to do on it (heart-rate ceiling of its terrain, computed at
    the leg's own midpoint), the steep bits, what to eat, and at that point the water
    to take for the next dry stretch and what to pick up from a drop bag. Same numbers
    as the Ravitaillement card. Never fails the table: on any error the rows open
    without these lines."""
    try:
        from markupsafe import Markup

        from app.services.nutrition import leg_labels
        from app.services.pacing_guide import effort_sentence, leg_instructions, steep_on_leg, steep_sentence

        li = leg_instructions(guide, sections)
        walk = float((guide.get("caps") or {}).get("walk_grade") or 18)
        weight = (await db.execute(select(User.weight_kg).where(User.id == user_id))).scalar()
        if duration_s is None:
            last = sections[-1] if sections else {}
            duration_s = last.get("adjusted_cumulative_time_s") or last.get("cumulative_time_s") or 0
        inp, plan, _shop = await _nutrition_plan(db, user_id, weight, route_obj, sections, checkpoints, aid_kms, start_offset_s, duration_s, mean_temp_c)
        products = inp["products_by_id"]
        bags = {round(float(g["km"]), 1): g for g in plan.get("bags") or [] if not g.get("is_start")}
        cps_by_km = {round(float(cp["distance_km"]), 1): cp for cp in checkpoints}
        notes = _water_notes(plan, inp["carry_ml"])
        out = []
        for i, leg in enumerate(plan["schedule"]):
            ins = li[i] if i < len(li) else {}
            sec = sections[i] if i < len(sections) else {}
            cls, hr = ins.get("cls"), ins.get("hr_cap")
            steep = steep_on_leg(ins.get("steep"), float(sec.get("start_km") or 0), float(sec.get("end_km") or 0))
            km = round(float(leg["km"] or 0), 1)
            bag, cp, note = bags.get(km), cps_by_km.get(km) or {}, notes.get(km)
            effort = effort_sentence(cls, hr, walk)
            units = leg_labels(leg, products)
            # the row is the leg that ARRIVES here (its leg line already says from where);
            # the « ici » lines (water, bag) are about leaving
            out.append({
                "hr_cap": hr, "terrain": (ins.get("block") or {}).get("label"), "cls": cls,
                "effort": Markup(effort) if effort else None,
                "steep": steep, "steep_text": steep_sentence(steep) if steep and cls != "stairs" else None,
                "carbs_g": leg["carbs_real_g"], "carbs_planned": True,
                "fluid_ml": leg["fluid_ml"], "water_ml": leg.get("water_ml", leg["fluid_ml"]),
                "units": units,
                "units_text": f"À prendre en route : {', '.join(units)}." if units else None,
                "water_note": note[0] if note else None, "water_alert": bool(note and note[1]),
                "bag": ({"head": "Assistance ici" if cp.get("crew") and not cp.get("drop_bag") else "Drop bag ici",
                         "until": _until(bag.get("until")), "units": bag.get("labels") or [], "carbs_g": bag.get("carbs_g") or 0}
                        if bag else None),
            })
        return out
    except Exception:
        logger.exception("leg details failed")
        return []


async def _passage_table_context(
    db: AsyncSession, user_id: int, course, checkpoints: list[dict], target_time_s: int | None, heat_factor: float,
    start_hour: int, start_minute: int, hourly_weather: dict | None, route_obj: Route | None, stop_minutes: int | None,
    anchor_km: float | None, anchor_clock: str | None, profile=None, route_id: int | None = None,
    mean_temp_c: float | None = None,
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
    legs = await _leg_details(db, user_id, route_obj, guide, sections, checkpoints, aid_kms, start_offset_s, duration_s=plan_s, mean_temp_c=mean_temp_c)
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
    weather_temp_c: float | None = Form(default=None),
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
            # the forecast the page just fetched, not saved yet: the rows' water and salt use it as the card will
            mean_temp_c=weather_temp_c,
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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full server-rendered detail page for a saved route (refresh-safe).

    ``compare`` = an activity id coming from "Comparer à un parcours": the page
    opens on the debrief tab with that activity preselected.
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
        return templates.TemplateResponse(
            request, "simulator_route_bike.html", context=ctx,
            headers={"Cache-Control": "no-store"},
        )

    # road / track / trail from OpenStreetMap, once per trace, in the background
    from app.services.surface import schedule_surface

    schedule_surface(route.id, route.course_json)

    ctx = await _build_route_context(route, db, user.id)
    ctx["user"] = user
    ctx["compare_activity_id"] = compare
    if compare:  # arrived from an activity: the debrief is the main action
        ctx["debrief_mode"] = "primary"
        ctx["show_debrief"] = True
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
    route.race_date = race_date.strip() or None
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
    from app.services.race_simulator import _elevation_at_km, build_scenarios, replan_from_passage

    result = await db.execute(
        select(Route).where(Route.id == route_id, Route.user_id == user.id)
    )
    route = _visible(result.scalar_one_or_none())
    if not route or not route.course_json:
        raise HTTPException(status_code=404)

    b = await _plan_bundle(route, db, user)
    course, sections, cps = b["course"], b["sections"], b["checkpoints"]
    start_hour, start_minute, start_offset_s = b["start_hour"], b["start_minute"], b["start_offset_s"]
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
    has_weather = any(s.get("temperature_c") is not None for s in sections)
    pp = route.params_json or {}
    scenarios = None if live_applied else build_scenarios(
        sections, start_offset_s, route.target_time_s,
        fast_pct=float(pp.get("scenario_fast_pct") or 5.0), safe_pct=float(pp.get("scenario_safe_pct") or 10.0),
        switch_km=pp.get("switch_km"), total_distance_km=course.total_distance_km,
    )

    # Race pack: the Ravitaillement plan on the same printout (the virtual
    # default too): the same units as the card and the passage rows, and the
    # shopping totals (spares included) as the pack list.
    nutrition_schedule: list = []
    nutrition_lines: list = []
    duration_s = b["plan_total_s"] or b["predicted_total_s"] or 0
    if duration_s and sections:
        from app.services.nutrition import leg_labels

        ninp, nplan, nshop = await _nutrition_plan(db, user.id, user.weight_kg, route, sections, cps, aid_kms, start_offset_s, duration_s)
        # the leg time written as in the rows and Ravitaillement (rounded: « 1h15 », « 38 min »)
        nutrition_schedule = [{**leg, "labels": leg_labels(leg, ninp["products_by_id"]), "leg_hm": _hm_s(leg["leg_time_s"])} for leg in nplan["schedule"]]
        nutrition_lines = nshop

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
            "has_weather": has_weather,
            "start_hour": start_hour,
            "start_minute": start_minute,
            "start_offset_s": start_offset_s,
            "start_elevation": _elevation_at_km(course, 0.0),
            "total_distance_km": course.total_distance_km,
            "print_mode": True,
            "stop_minutes": stop_min,
            "n_aid": len(aid_kms),
            "nutrition_schedule": nutrition_schedule,
            "nutrition_lines": nutrition_lines,
            "scenarios": scenarios,
            "sport": b["sport"],
            "guide": guide,
            "legs_guide": legs_guide,
            "walk_grade": walk_grade,
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
    }


async def _nutrition_card_context(
    request: Request, route: Route, db: AsyncSession, user: User, open_: str | None = None, bundle: dict | None = None,
    own_form: dict | None = None,
) -> dict:
    """Everything the Ravitaillement card shows: the rule, the stomach, the
    products, the bags, the shopping list, the legs, and the rare settings."""
    from app.services import nutrition as N

    b = bundle or await _plan_bundle(route, db, user)
    duration_s = b["plan_total_s"] or b["predicted_total_s"] or 0
    ctx: dict = {"request": request, "route_id": route.id, "has_duration": duration_s > 0}
    if not duration_s:
        return ctx
    cps, sections = b["checkpoints"], b["sections"]
    inp, plan, shop = await _nutrition_plan(db, user.id, user.weight_kg, route, sections, cps, b["aid_kms"], b["start_offset_s"], duration_s)
    products, picks, pantry = inp["products_by_id"], inp["picks"], inp["pantry"]
    has_refills = bool(b["aid_kms"])
    temp = inp["mean_temp"]

    # chips in a FIXED order, each with its pressed state (a tapped chip stays
    # under the finger): the quick picks (one hidden when a picked product
    # already plays its role), then his own products, newest first
    roles = {N.role(products[p]) for p in picks}
    chips = [{"id": g, "label": N.GENERIC_BY_ID[g]["label"], "title": N.GENERIC_BY_ID[g]["name"], "on": g in picks}
             for g in N.GENERIC_ORDER if g in picks or N.role(N.GENERIC_BY_ID[g]) not in roles]
    # his products: the picked ones and the 4 newest others on the row, in the order
    # he added them (a new one lands at the end, next to « + Marque »); the rest under « + Marque »
    shown = set(picks) | set([pid for pid in pantry if pid not in picks][:4])
    for pid in reversed(list(pantry)):
        if pid in shown:
            p = pantry[pid]
            chips.append({"id": pid, "label": N.short_label(p), "title": p.get("name") or "", "on": pid in picks})
    others = [{"id": pid, "label": N.short_label(p), "title": p.get("name") or "", "on": False} for pid, p in pantry.items() if pid not in shown]
    have = {(p.get("name") or "").strip().lower() for p in pantry.values()}
    catalog = [c for c in N.PRODUCT_CATALOG if c["name"].lower() not in have]
    brand_groups = [(label, [c for c in catalog if N.role(c) == r]) for label, r in
                    (("Gels", "gel"), ("Caféinés", "caf"), ("Boissons", "drink"), ("Barres", "bar"), ("Sel", "salt"))]

    # drop bags: one chip per full / base point (or one already flagged), the finish excluded.
    # No ravito typed yet (points imported from a GPX are « passage »): every
    # point can take a bag, and the water points are one tap away too.
    bag_chips, water_chips = [], []
    untyped = not any((cp.get("kind") or "none") == "full" for cp in cps)
    if b.get("sport") != "bike":
        for s in sections:
            ci = s.get("end_checkpoint_index")
            if ci is None or ci >= len(cps):
                continue
            cp = cps[ci]
            kind = cp.get("kind") or "none"
            base = {"ci": ci, "km": s["end_km"], "name": cp["name"], "kmr": round(float(s["end_km"])), "kind": kind,
                    "label": f"{cp['name']} · km {round(float(s['end_km']))}"}
            if untyped or kind in ("full", "base") or cp.get("drop_bag"):
                bag_chips.append({**base, "on": bool(cp.get("drop_bag"))})
            if untyped and kind in ("none", "water"):
                water_chips.append({**base, "on": kind == "water"})

    # one card per carry segment (bag), spare included
    cps_by_km = {round(float(cp["distance_km"]), 1): cp for cp in cps}
    carry = inp["carry_ml"]
    cards, titles = [], []
    for g in plan.get("bags") or []:
        titles.append(_bag_title(g, cps_by_km, sep=" "))
        water = None
        ds = g.get("dry_stretch")
        if has_refills and ds:
            need = ds["need_ml"]
            if carry:
                if g.get("short"):
                    water = {"alert": True, "text": f"Eau : il te faudrait {N._fr(N.ceil_half_l(need))} L entre {ds['from']} et {ds['to']} ({_hm_s(ds['time_s'])}), tu portes {N.liters(carry)} L. "
                                                    "Prends une flasque de plus, ou change « Eau que tu portes » dans Ajuster."}
                else:
                    water = {"alert": False, "text": f"Eau : ce que tu portes suffit ({N.liters(carry)} L)"}
            elif need >= 1000:
                water = {"alert": False, "text": f"Eau : prévois {N._fr(N.ceil_half_l(need))} L (le plus long sans eau : {ds['from']} → {ds['to']}, {_hm_s(ds['time_s'])})"
                         + (" · le plus long de la course" if g.get("is_longest") else "")}
            else:
                water = {"alert": False, "text": "Eau : 1 L suffit"}
        cards.append({
            "title": _bag_title(g, cps_by_km), "is_start": g.get("is_start"),
            # clock to clock, stops included: the same durations as the rows and the header
            "sub": f"km {round(float(g['km'] or 0))} → {round(float(g['until_km'] or 0))} · jusqu'à {_until(g['until'])} · "
                   + _hm_s((g["end_clock_s"] - g["start_clock_s"]) if g.get("end_clock_s") is not None and g.get("start_clock_s") is not None else (g.get("time_s") or 0)),
            "chips": g.get("labels") or [], "water": water,
        })
    main = N.main_product_id(plan)
    spare_noun = N.rule_noun(products[main]) if main in products else None

    # tronçon par tronçon: the same units as the passage rows, plus the water to drink
    legs = []
    for leg in plan.get("schedule") or []:
        parts = N.leg_labels(leg, products)
        if leg.get("water_ml", 0) >= 200:
            parts.append(f"{_l1(leg['water_ml'])} L d'eau")
        legs.append({"clock": f"{(leg['clock_s'] % 86400) // 3600:02d}:{(leg['clock_s'] % 3600) // 60:02d}",
                     "to": leg["to_name"], "time": _hm_s(leg["leg_time_s"]), "text": " · ".join(parts)})

    # Ajuster: sweat, water carried, quantities, caffeine, products
    t = inp["targets"]
    rates, manual, custom = inp["rates"], inp["manual"], inp["custom"]
    qty_rows = []
    for pid in picks:
        p = products[pid]
        if N.role(p) == "caf":
            qty_rows.append({"id": pid, "label": N.short_label(p), "caf": True})
        else:
            r = rates.get(pid, 0.0)
            qty_rows.append({"id": pid, "label": N.short_label(p), "caf": False, "rate": N._fr(r) if r else "0", "manual": pid in manual})
    caf_line = None
    if inp["has_caf"]:
        cf = inp["caffeine"]
        every = N._hm_words(float(cf.get("every_h") or 2.5) * 60)
        if custom.get("caffeine"):
            caf_line = f"Ton réglage : {int(round(float(cf.get('dose_mg') or 0)))} mg toutes les {every} dès {N._fr(float(cf.get('from_h') or 0))} h."
        else:
            cp_ = next(products[p] for p in picks if N.role(products[p]) == "caf")
            d2 = next((d for d in (plan.get("caffeine") or {}).get("doses") or [] if (d.get("units") or 1) > 1), None)
            dawn = f", {N.unit_label(d2['units'], cp_)} vers {N._clock(d2['clock_s'])} (l'aube)" if d2 else ""
            caf_line = f"{N.rule_noun(cp_)} toutes les {every} de course dès 3 h{dawn}, {inp['cap_mg']} mg au plus pour toi."
    sweat = inp["sweat"]
    carry_opts = [("auto", "Auto", carry is None)] + [(str(c), f"{N.liters(c)} L", carry == c) for c in N.CARRY_CHOICES]
    if carry and carry not in N.CARRY_CHOICES:
        carry_opts.append((str(carry), f"{N.liters(carry)} L", True))
    summary = [
        {"peu": "peu de transpiration", "normal": "transpiration normale", "beaucoup": "beaucoup de transpiration"}.get(sweat or "", "transpiration : ton réglage"),
        "eau auto" if not carry else f"{N.liters(carry)} L portés",
        "quantités à la main" if manual else "quantités auto",
    ]
    def _num(x) -> str:
        """An input's value: 25 not 25.0."""
        return f"{float(x):g}" if x not in (None, "") else ""
    my_products = []
    for pid, p in pantry.items():
        bits = [f"{N._fr(float(p.get('carbs_g') or 0))} g", f"{N._fr(float(p.get('sodium_mg') or 0))} mg sodium"]
        if p.get("caffeine_mg"):
            bits.append(f"{N._fr(float(p['caffeine_mg']))} mg caféine")
        my_products.append({**p, "label": N.short_label(p), "detail": " · ".join(bits), "role": N.role(p),
                            "carbs_in": _num(p.get("carbs_g")), "sodium_in": _num(p.get("sodium_mg")),
                            "caffeine_in": _num(p.get("caffeine_mg")) if p.get("caffeine_mg") else ""})

    # all the caffeine the plan has you take (an old plan may take a caffeinated gel per hour)
    caf_total, cap = N.plan_caffeine_mg(plan), inp["cap_mg"]
    caf_over = None
    if caf_total > cap + 1:
        caf_over = {"total_mg": caf_total, "max_mg": cap,
                    "fix": "Touche « Revenir au calcul auto »." if inp["hand_set"] else "Retire une prise."}
    warnings = N.rule_warnings(plan, picks, products, has_refills,
                               water_hint="Touche les points d'eau dans « Tes sacs » pour savoir combien d'eau porter." if water_chips else None)
    if caf_over:
        warnings.insert(0, {"tone": "alert", "text": f"Trop de caféine : {caf_total} mg en tout, au-dessus des {cap} mg conseillés pour toi. {caf_over['fix']}"})

    hours = duration_s / 3600.0
    return {
        **ctx,
        "context_line": f"{_hm_s(duration_s)} de course · départ {b['start_hour']:02d}:{b['start_minute']:02d}" + (f" · {round(temp)} °C prévus" if temp is not None else ""),
        "rules": N.rule_lines(plan, picks, products, has_refills),
        "warnings": warnings,
        "hand_set": inp["hand_set"],
        "level": inp["level"], "custom_carbs": t["carbs_g_per_h"],
        "chips": chips, "pantry_more": others,
        "brand_groups": [(lbl, items) for lbl, items in brand_groups if items],
        "source": inp["source"],
        "bag_chips": bag_chips, "water_chips": water_chips, "no_points": not any(s.get("end_checkpoint_index") is not None for s in sections),
        "is_bike": b.get("sport") == "bike",
        "cards": cards, "spare_noun": spare_noun,
        "shop": shop, "caf_over": caf_over,
        "own_form": own_form,
        "copy_text": N.copy_text(route.name, shop, plan.get("bags") or [], titles),
        "legs": legs,
        "sweat": sweat, "sweat_info": f"{N.liters(t['fluid_ml_per_h'])} L d'eau et {t['sodium_mg_per_h']} mg de sodium par heure, "
                                      + (f"d'après {round(temp)} °C prévus." if temp is not None else "pour une météo douce."),
        "custom_sweat": ("fluid_ml_per_h" in custom or "sodium_mg_per_h" in custom),
        "carry_opts": carry_opts, "carry": carry,
        "qty_rows": qty_rows, "caf_line": caf_line,
        "summary": " · ".join(summary), "my_products": my_products,
        "open": open_ if open_ in ("adjust", "brands") else None,
        "hours": hours,
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
    consumer (pacing guide, exports, reference, debrief): the predicted course,
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
    safe_name = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in route.name).strip() or "parcours"
    fmt = (fmt or "gpx").lower()
    if fmt == "csv":
        body = build_pace_csv(route.name, b["sections"], b["use_target"], b["start_offset_s"], leg_codes=leg_codes)
        return Response(content=body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{safe_name}-plan.csv"'})
    if fmt == "tcx":
        body = build_pace_tcx(route.name, coords, segs, b["sections"], b["use_target"], route.race_date, b["start_offset_s"], leg_codes=leg_codes)
        return Response(content=body, media_type="application/vnd.garmin.tcx+xml",
                        headers={"Content-Disposition": f'attachment; filename="{safe_name}-plan.tcx"'})
    body = build_pace_gpx(route.name, coords, segs, b["sections"], b["use_target"], route.race_date, b["start_offset_s"], leg_codes=leg_codes)
    return Response(content=body, media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}-plan.gpx"'})


# ── Reference finisher ──

async def _reference_context(request: Request, route: Route, db: AsyncSession, user: User, error: str | None = None) -> dict:
    from app.services.reference import align_reference, compare_to_plan

    b = await _plan_bundle(route, db, user)
    ref = route.reference_json
    comparison = None
    if ref and ref.get("points"):
        aligned = align_reference(ref["points"], b["checkpoints"], route.total_distance_km, ref.get("total_km"))
        plan_total = None
        if b["sections"]:
            last = b["sections"][-1]
            plan_total = last["adjusted_cumulative_time_s"] if (b["use_target"] and last.get("adjusted_cumulative_time_s") is not None) else last["cumulative_time_s"]
            # the plan total including planned stops = clock − start
            clock = last["adjusted_clock_time_s"] if (b["use_target"] and last.get("adjusted_clock_time_s") is not None) else last["clock_time_s"]
            plan_total = int(clock) - b["start_offset_s"] if clock is not None else plan_total
        comparison = compare_to_plan(aligned, b["sections"], b["use_target"], ref.get("total_s"), int(plan_total) if plan_total else None)
    return {
        "request": request, "route_id": route.id, "reference": ref, "comparison": comparison,
        "error": error, "basis": "plan" if b["use_target"] else "prediction", "has_checkpoints": bool(b["checkpoints"]),
    }


@router.get("/api/simulator/routes/{route_id}/reference", response_class=HTMLResponse)
async def reference_card(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    ctx = await _reference_context(request, route, db, user)
    return templates.TemplateResponse(request, "partials/reference_card.html", context=ctx, headers={"Cache-Control": "no-store"})


@router.post("/api/simulator/routes/{route_id}/reference", response_class=HTMLResponse)
async def save_reference(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    source: str = Form(default=""),
    label: str = Form(default=""),
    total_time: str = Form(default=""),
):
    """``source`` is a Strava activity URL (fetched via the API when public) or
    a pasted passage table (name / km / race time per line)."""
    from app.services.reference import (
        parse_pasted_splits,
        parse_strava_activity_id,
        points_from_splits_metric,
    )

    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    error = None
    points: list[dict] = []
    total_km = None
    total_s = None
    strava_id = parse_strava_activity_id(source)
    if strava_id:
        try:
            from app.services.strava import StravaService

            data = await StravaService.for_user(db, user).get_activity(user, strava_id)
            points = points_from_splits_metric(data.get("splits_metric") or [])
            total_km = round((data.get("distance") or 0) / 1000, 2) or None
            total_s = int(data.get("elapsed_time") or 0) or None
            label = label.strip() or f"{data.get('name', 'Activité Strava')} — {(data.get('athlete') or {}).get('firstname', '')}".strip(" —")
            if not points:
                error = "Cette activité n'expose pas ses splits (activité privée ou d'un autre athlète) : colle plutôt ses temps de passage."
        except Exception:
            logger.exception("reference strava fetch failed")
            error = "Impossible de lire cette activité Strava (privée, ou d'un autre athlète). Colle ses temps de passage à la place."
    else:
        points = parse_pasted_splits(source)
        if not points:
            error = "Aucune ligne avec un temps (HH:MM ou HH:MM:SS) trouvée."
    if total_time.strip():
        parsed = parse_pasted_splits("total " + total_time.strip())
        if parsed:
            total_s = parsed[0]["time_s"]
    if points and not error:
        # the last point often IS the finish
        if total_s is None and points[-1].get("km") is not None and abs(points[-1]["km"] - (total_km or route.total_distance_km)) < 1.5:
            total_s = points[-1]["time_s"]
        route.reference_json = {
            "label": (label.strip() or "Finisher de référence")[:120],
            "source": "strava" if strava_id else "paste",
            "source_text": source.strip()[:4000] if not strava_id else source.strip()[:200],
            "points": points[:400], "total_km": total_km, "total_s": total_s,
        }
        await db.flush()
    ctx = await _reference_context(request, route, db, user, error=error)
    return templates.TemplateResponse(request, "partials/reference_card.html", context=ctx)


@router.post("/api/simulator/routes/{route_id}/reference/clear", response_class=HTMLResponse)
async def clear_reference(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    route = await _get_owned_route(route_id, user, db)
    if not route:
        return HTMLResponse("", status_code=404)
    route.reference_json = None
    await db.flush()
    ctx = await _reference_context(request, route, db, user)
    return templates.TemplateResponse(request, "partials/reference_card.html", context=ctx)


@router.get("/partials/simulator/nutrition/{route_id}", response_class=HTMLResponse)
async def nutrition_card(
    route_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The Ravitaillement view. Reading it never writes: a race without a plan
    shows the virtual default, saved on the first change."""
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    ctx = await _nutrition_card_context(request, route, db, user)
    # htmx GETs can be heuristically cached by the browser; force a fresh card.
    return templates.TemplateResponse(
        request, "partials/nutrition_card.html", context=ctx,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/nutrition")
async def nutrition_page(user: User = Depends(get_current_user)):
    """The products now live in each race's Ravitaillement view."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/simulator", status_code=303)


_PRODUCT_KINDS = ("gel", "bar", "drink", "salt", "solid")


def _product_fields(form) -> dict | None:
    """Label values from the « un produit à toi » form; None when there is no name.
    Volume and kcal are never asked: a drink is a 500 ml flask."""
    name = (form.get("name") or "").strip()
    if not name:
        return None

    def _f(key):
        raw = form.get(key)
        if raw in (None, ""):
            return None
        try:
            return max(0.0, float(str(raw).replace(",", ".")))
        except (TypeError, ValueError):
            return None

    kind = (form.get("kind") or "gel").strip()
    out = {
        "name": name[:100], "kind": kind if kind in _PRODUCT_KINDS else "gel",
        "carbs_g": _f("carbs_g") or 0, "sodium_mg": _f("sodium_mg") or 0, "caffeine_mg": _f("caffeine_mg") or None,
    }
    for key in ("kcal", "volume_ml"):  # older clients may still send them
        if key in form:
            out[key] = _f(key)
    return out


async def _write_nutrition(route: Route, db: AsyncSession, user: User, op: str, value=None) -> dict:
    """Apply one tap to the route's plan and store it (v2 shape + echo). The
    first write from a virtual default or an old plan starts from what was shown."""
    from app.services import nutrition as N

    b = await _plan_bundle(route, db, user)
    duration_s = b["plan_total_s"] or b["predicted_total_s"] or 0
    moving_s = _moving_s(b["sections"])
    inp = await _nutrition_inputs(db, user.id, route, duration_s, user.weight_kg, moving_s, b["start_offset_s"])
    if op == "keep" and not inp["is_virtual"]:
        return b  # « keep » only freezes a plan still taken from the default
    st = N.apply_op(inp["state"], op, value, inp["products_by_id"], inp["rates"])
    nj = N.state_json(st)
    route.nutrition_json = N.with_echo(nj, N.resolve_inputs(nj, inp["pantry"], duration_s, inp["mean_temp"], user.weight_kg,
                                                          moving_s=moving_s, start_offset_s=b["start_offset_s"]))
    await db.flush()
    return b


async def _add_from_catalog(key: str, db: AsyncSession, user: User) -> int | None:
    """The catalogue product in the athlete's pantry (added once): its id."""
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
        kcal=c.get("kcal"), caffeine_mg=c.get("caffeine_mg"), volume_ml=c.get("volume_ml"),
    )
    db.add(prod)
    await db.flush()
    return prod.id


@router.post("/partials/simulator/nutrition/{route_id}/products", response_class=HTMLResponse)
@router.post("/partials/simulator/nutrition/{route_id}/products/{product_id}", response_class=HTMLResponse)
@router.post("/partials/simulator/nutrition/{route_id}/products/{product_id}/delete", response_class=HTMLResponse)
async def pantry_in_plan(
    route_id: int,
    request: Request,
    product_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """« Un produit à toi » (added and picked, in place of the quick pick of its
    role), edited or deleted (and unpicked) from the Ravitaillement view."""
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    b = None
    own_form = None
    if product_id is None:
        f = _product_fields(form)
        if f:
            if f["kind"] == "drink" and not f.get("volume_ml"):
                f["volume_ml"] = 500
            prod = NutritionProduct(user_id=user.id, **f)
            db.add(prod)
            await db.flush()
            b = await _write_nutrition(route, db, user, "pick", prod.id)
        else:  # no name: the form stays open with what was typed
            kind = (form.get("kind") or "gel").strip()
            own_form = {"error": "Donne-lui un nom.", "kind": kind if kind in _PRODUCT_KINDS else "gel",
                        **{k: str(form.get(k) or "")[:12] for k in ("carbs_g", "sodium_mg", "caffeine_mg")}}
    else:
        pr = await db.execute(select(NutritionProduct).where(NutritionProduct.id == product_id, NutritionProduct.user_id == user.id))
        product = pr.scalar_one_or_none()
        if product and request.url.path.endswith("/delete"):
            await db.delete(product)
            await db.flush()
            if product_id in ((route.nutrition_json or {}).get("picks") or [p.get("product_id") for p in (route.nutrition_json or {}).get("items") or []]):
                b = await _write_nutrition(route, db, user, "unpick", product_id)
        elif product:
            f = _product_fields(form)
            if f:
                for k, v in f.items():
                    setattr(product, k, v)
                if product.kind == "drink" and not product.volume_ml:
                    product.volume_ml = 500
                await db.flush()
    open_ = form.get("open") or ("adjust" if product_id is not None else ("brands" if own_form else None))
    ctx = await _nutrition_card_context(request, route, db, user, open_=open_, bundle=b, own_form=own_form)
    return templates.TemplateResponse(request, "partials/nutrition_card.html", context=ctx)


@router.post("/partials/simulator/nutrition/{route_id}/catalog/{key}", response_class=HTMLResponse)
async def add_catalog_product_to_plan(
    route_id: int,
    key: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """« + Marque » › a brand chip: in the pantry once, picked, in place of the
    quick pick of the same role; the panel closes."""
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    pid = await _add_from_catalog(key, db, user)
    b = await _write_nutrition(route, db, user, "pick", pid) if pid else None
    ctx = await _nutrition_card_context(request, route, db, user, bundle=b)
    return templates.TemplateResponse(request, "partials/nutrition_card.html", context=ctx)


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
    """One tap of the Ravitaillement view (``op`` = level | toggle | sweat |
    carry | step | reset | keep), or the old form (targets, use_* / qty_*,
    caffeine_*, flask) from an older client, stored as a plan with every
    quantity by hand. ``keep`` (sent after a drop-bag chip) stores the plan
    still taken from another race as this race's own, so a later change
    elsewhere never rewrites it."""
    route = await _get_owned_route(route_id, user, db)
    if not route or not route.course_json:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    op = (form.get("op") or "").strip()
    v = form.get("v")
    b = None
    if op:
        value = v
        try:
            if op == "toggle":
                if str(v or "").startswith("cat:"):
                    value, op = await _add_from_catalog(str(v)[4:], db, user), "pick"
                else:
                    value = int(v)
            elif op == "step":
                value = (int(form.get("pid")), max(-2.0, min(2.0, float(form.get("step") or 0))))
        except (TypeError, ValueError):
            op = ""
        if op in ("level", "toggle", "pick", "sweat", "carry", "step", "reset", "keep") and (value is not None or op in ("reset", "keep")):
            b = await _write_nutrition(route, db, user, op, value)
    else:
        await _legacy_nutrition_post(route, db, user, form)
    ctx = await _nutrition_card_context(request, route, db, user, open_=form.get("open"), bundle=b)
    return templates.TemplateResponse(request, "partials/nutrition_card.html", context=ctx)


async def _legacy_nutrition_post(route: Route, db: AsyncSession, user: User, form) -> None:
    """The form of the previous Nutrition view: typed targets and per-product
    quantities, kept working; stored as a v2 plan (every quantity by hand,
    the targets and caffeine settings as « ton réglage »)."""
    from app.services import nutrition as N

    targets = {
        "carbs_g_per_h": round(_to_float(form.get("carbs_g_per_h"))),
        "fluid_ml_per_h": round(_to_float(form.get("fluid_ml_per_h"))),
        "sodium_mg_per_h": round(_to_float(form.get("sodium_mg_per_h"))),
    }
    prev = route.nutrition_json or {}
    prev_rates = {it.get("product_id"): float(it.get("per_hour") or 0) for it in (prev.get("items") or [])}
    used: list[int] = []
    qty: dict[int, float] = {}
    for key, val in form.multi_items() if hasattr(form, "multi_items") else form.items():
        try:
            if key.startswith("use_"):
                used.append(int(key[4:]))
            elif key.startswith("qty_"):
                qty[int(key[4:])] = _to_float(str(val).replace(",", "."))
        except ValueError:
            continue
    pantry = await _pantry(db, user.id)
    by_id = N.with_generics(pantry)
    selected = [by_id[i] for i in dict.fromkeys(used) if i in by_id]
    suggested = N.auto_rates(targets["carbs_g_per_h"], targets["sodium_mg_per_h"], selected) if selected else {}
    plain_carb = any((p.get("carbs_g") or 0) > 0 and not (p.get("caffeine_mg") or 0) for p in selected)
    st = {"picks": [], "manual": {}, "level": None, "sweat": None, "custom": {}, "refills": list(prev.get("refills") or [])}
    for p in selected:
        pid = p["id"]
        r = qty.get(pid, 0.0)
        if form.get("auto") or (r <= 0 and prev_rates.get(pid, 0) <= 0):
            r = suggested.get(pid, 0.0)
        elif r <= 0:
            r = prev_rates.get(pid, 0.0)
        if r <= 0 and (p.get("caffeine_mg") or 0) > 0 and plain_carb:
            r = 1.0
        if r > 0:
            st["picks"].append(pid)
            st["manual"][pid] = round(r, 2)
    N._legacy_targets_into(st, {"targets": targets})
    st["carry_ml"] = N._carry(form.get("flask_capacity_ml")) or 1000
    new_caf = plain_carb and any((p.get("caffeine_mg") or 0) > 0 and p["id"] not in prev_rates for p in selected)
    if bool(form.get("caffeine_enabled")) or new_caf:  # switched off = no setting of his (auto when a caffeinated gel is picked)
        st["custom"]["caffeine"] = {
            "enabled": True,
            "from_h": max(0.0, _to_float(form.get("caffeine_from_h"), 3.0)),
            "every_h": max(0.5, _to_float(form.get("caffeine_every_h"), 2.5)),
            "dose_mg": max(0.0, _to_float(form.get("caffeine_dose_mg"), 50)),
            "boost_dawn": bool(form.get("caffeine_boost_dawn")),
        }
    b = await _plan_bundle(route, db, user)
    duration_s = b["plan_total_s"] or b["predicted_total_s"] or 0
    mean_temp = (route.weather_json or {}).get("temperature_c") if route.weather_json else None
    nj = N.state_json(st)
    route.nutrition_json = N.with_echo(nj, N.resolve_inputs(nj, pantry, duration_s, mean_temp, user.weight_kg,
                                                          moving_s=_moving_s(b["sections"]), start_offset_s=b["start_offset_s"]))
    await db.flush()




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
