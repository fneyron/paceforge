"""Gradient-adjusted pace prediction from athlete Strava data."""

import logging
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.schemas.simulator import AthleteGradientProfile, CourseProfile

logger = logging.getLogger(__name__)


def actual_passage_times(
    splits_metric: list,
    checkpoints: list[dict],
    route_total_km: float,
) -> tuple[list[dict], int]:
    """Reconstruct real cumulative times at each checkpoint from a Strava
    activity's per-km splits. Returns (per-checkpoint [{name, km, time_s}],
    total_moving_s).

    Checkpoints are mapped onto the activity by proportion of total distance
    (the matched activity may differ slightly in length from the planned route).
    Uses moving time — so it's comparable to the model's moving prediction
    (both exclude aid-station stops).
    """
    cum_d = 0.0
    cum_t = 0.0
    pts = [(0.0, 0.0)]  # (cumulative_distance_m, cumulative_moving_s)
    for s in splits_metric or []:
        d = s.get("distance", 0) or 0
        mt = s.get("moving_time", 0) or 0
        if d <= 0 or mt <= 0:
            continue
        cum_d += d
        cum_t += mt
        pts.append((cum_d, cum_t))

    total_d = pts[-1][0]
    total_t = int(pts[-1][1])
    if total_d <= 0:
        return [], 0

    def _time_at(dist_m: float) -> float:
        if dist_m <= 0:
            return 0.0
        if dist_m >= total_d:
            return pts[-1][1]
        for i in range(1, len(pts)):
            if pts[i][0] >= dist_m:
                d0, t0 = pts[i - 1]
                d1, t1 = pts[i]
                span = d1 - d0
                frac = (dist_m - d0) / span if span > 0 else 0
                return t0 + frac * (t1 - t0)
        return pts[-1][1]

    out = []
    for cp in checkpoints:
        km = cp.get("distance_km", 0)
        # position in the activity = same proportion of total distance
        frac = (km / route_total_km) if route_total_km else 0
        out.append({
            "name": cp.get("name", ""),
            "km": km,
            "time_s": round(_time_at(frac * total_d)),
        })
    return out, total_t


# Minetti-based empirical model: gradient% -> pace multiplier relative to flat
# These are defaults when athlete has no data for a gradient bucket
# Generic gradient → pace multipliers (relative to flat), used when the athlete
# has no personal split data or to fill gaps. Calibrated to a trained trail
# runner's grade-adjusted pace (GAP): the previous table was markedly pessimistic
# — it slowed steep climbs ~2× too much and treated moderate descents as SLOWER
# than flat. Real strong runners climb more efficiently and gain time on
# moderate descents. (Personal Strava splits override these where available.)
_DEFAULT_GRADIENT_FACTORS = {
    -20: 1.10,
    -15: 0.92,
    -10: 0.82,
    -8: 0.80,
    -6: 0.80,
    -5: 0.82,
    -4: 0.85,
    -3: 0.88,
    -2: 0.92,
    -1: 0.96,
    0: 1.0,
    1: 1.05,
    2: 1.10,
    3: 1.16,
    4: 1.23,
    5: 1.31,
    6: 1.40,
    7: 1.50,
    8: 1.62,
    10: 1.85,
    12: 2.10,
    15: 2.45,
    20: 3.10,
    25: 3.90,
    30: 4.70,
}


async def build_athlete_gradient_profile(
    db: AsyncSession,
    user_id: int,
    months: int = 6,
) -> AthleteGradientProfile:
    """Build a gradient-to-pace profile from the athlete's trail/run splits."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)

    result = await db.execute(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.sport_type.in_(["Run", "TrailRun", "VirtualRun"]),
            Activity.start_date >= cutoff,
            Activity.splits_metric.is_not(None),
        )
        .order_by(Activity.start_date.desc())
    )
    activities = result.scalars().all()

    # Double recordings (watch + phone) would count the same outing twice.
    from app.services.activity_dedupe import find_duplicate_ids

    duplicate_ids = find_duplicate_ids(activities)

    # Extract gradient-pace data points from splits
    data_points: list[tuple[float, float]] = []
    sport_types_used = set()

    for activity in activities:
        if activity.id in duplicate_ids:
            continue
        splits = activity.splits_metric
        if not splits or not isinstance(splits, list):
            continue

        sport_types_used.add(activity.sport_type)

        for split in splits:
            distance = split.get("distance", 0)
            moving_time = split.get("moving_time", 0)
            elevation_diff = split.get("elevation_difference", 0)

            if not distance or distance < 500 or not moving_time:
                continue

            pace_s_per_km = moving_time / (distance / 1000)

            # Filter unrealistic paces
            if pace_s_per_km < 120 or pace_s_per_km > 1200:  # 2min/km to 20min/km
                continue

            gradient_pct = (elevation_diff / distance) * 100
            data_points.append((gradient_pct, pace_s_per_km))

    if not data_points:
        # No split data — use a reasonable default based on avg speed
        flat_pace = await _estimate_flat_pace_from_activities(db, user_id, cutoff)
        profile = AthleteGradientProfile(
            flat_pace_s_per_km=flat_pace,
            gradient_factors=_DEFAULT_GRADIENT_FACTORS,
            data_points=0,
            sport_types_used=list(sport_types_used),
        )
        profile.race_model = await _race_model(db, user_id)
        return profile

    # Group by gradient bucket (integer-rounded)
    buckets: dict[int, list[float]] = {}
    for grad, pace in data_points:
        bucket = round(grad)
        bucket = max(-25, min(30, bucket))  # Clamp
        if bucket not in buckets:
            buckets[bucket] = []
        buckets[bucket].append(pace)

    # Flat reference pace: median of -1% to +1% range
    flat_paces = []
    for b in range(-1, 2):
        if b in buckets:
            flat_paces.extend(buckets[b])

    if flat_paces:
        flat_pace = statistics.median(flat_paces)
    else:
        flat_pace = statistics.median([p for _, p in data_points])

    # Compute gradient factors
    gradient_factors: dict[int, float] = {}
    for bucket, paces in buckets.items():
        if len(paces) >= 2:
            median_pace = statistics.median(paces)
            factor = median_pace / flat_pace
            gradient_factors[bucket] = round(factor, 3)
        elif len(paces) == 1:
            # Blend with model: 50/50
            athlete_factor = paces[0] / flat_pace
            model_factor = _get_default_factor(bucket)
            gradient_factors[bucket] = round((athlete_factor + model_factor) / 2, 3)

    # Fill gaps with default model
    for grad in range(-20, 31):
        if grad not in gradient_factors:
            gradient_factors[grad] = _get_default_factor(grad)

    profile = AthleteGradientProfile(
        flat_pace_s_per_km=round(flat_pace, 1),
        gradient_factors=gradient_factors,
        data_points=len(data_points),
        sport_types_used=list(sport_types_used),
    )
    profile.fatigue_tilt = await _personal_fatigue_tilt(db, user_id)
    profile.race_model = await _race_model(db, user_id)
    return profile


async def _race_model(db: AsyncSession, user_id: int) -> dict | None:
    """Effort-km/h model fitted on the athlete's races (never raises)."""
    try:
        from app.services.race_calibration import build_race_effort_model

        return await build_race_effort_model(db, user_id)
    except Exception:
        logger.exception("race effort model failed")
        return None


async def _personal_fatigue_tilt(db: AsyncSession, user_id: int) -> float:
    """Personal fresh→fade tilt, calibrated when a matched race result exists.

    The tilt is computed once at match time (see save_route_result) and stored
    in Route.result_json; here we just read the most recent one. Clamped hard —
    one race must adjust, not dominate.
    """
    from app.models.route import Route

    try:
        result = await db.execute(
            select(Route.result_json)
            .where(Route.user_id == user_id, Route.result_json.is_not(None))
            .order_by(Route.id.desc())
            .limit(5)
        )
        for rj in result.scalars().all():
            tilt = (rj or {}).get("fatigue_tilt")
            if tilt is not None:
                return max(0.05, min(float(tilt), 0.40))
    except Exception:
        logger.exception("personal fatigue tilt lookup failed")
    return 0.15


async def _estimate_flat_pace_from_activities(
    db: AsyncSession, user_id: int, cutoff: datetime
) -> float:
    """Fallback: estimate flat pace from average speed of recent runs."""
    from sqlalchemy import func

    result = await db.execute(
        select(func.avg(Activity.average_speed))
        .where(
            Activity.user_id == user_id,
            Activity.sport_type.in_(["Run", "TrailRun"]),
            Activity.start_date >= cutoff,
            Activity.average_speed.is_not(None),
            Activity.average_speed > 0,
        )
    )
    avg_speed = result.scalar()
    if avg_speed and avg_speed > 0:
        return round(1000 / avg_speed, 1)
    return 330.0  # Default 5:30/km


def _get_default_factor(gradient: int) -> float:
    """Get the default pace factor for a gradient from the empirical model."""
    if gradient in _DEFAULT_GRADIENT_FACTORS:
        return _DEFAULT_GRADIENT_FACTORS[gradient]

    # Interpolate between nearest known values
    known = sorted(_DEFAULT_GRADIENT_FACTORS.keys())
    if gradient < known[0]:
        return _DEFAULT_GRADIENT_FACTORS[known[0]]
    if gradient > known[-1]:
        return _DEFAULT_GRADIENT_FACTORS[known[-1]]

    for i in range(len(known) - 1):
        if known[i] <= gradient <= known[i + 1]:
            t = (gradient - known[i]) / (known[i + 1] - known[i])
            return round(
                _DEFAULT_GRADIENT_FACTORS[known[i]] * (1 - t)
                + _DEFAULT_GRADIENT_FACTORS[known[i + 1]] * t,
                3,
            )

    return 1.0


def _get_factor(profile: AthleteGradientProfile, gradient_pct: float) -> float:
    """Get interpolated pace factor for a specific gradient."""
    g = round(gradient_pct)
    g = max(-20, min(30, g))

    if g in profile.gradient_factors:
        return profile.gradient_factors[g]

    # Interpolate
    keys = sorted(profile.gradient_factors.keys())
    for i in range(len(keys) - 1):
        if keys[i] <= g <= keys[i + 1]:
            t = (g - keys[i]) / (keys[i + 1] - keys[i])
            return (
                profile.gradient_factors[keys[i]] * (1 - t)
                + profile.gradient_factors[keys[i + 1]] * t
            )

    return 1.0


def _fatigue_factor(
    progress: float,
    total_distance_km: float,
    cumulative_gain: float,
    tilt: float = 0.15,
) -> float:
    """Exponential fatigue factor based on race progress and D+.

    Returns a multiplier >= 1.0 (higher = slower).
    """
    if total_distance_km < 20:
        return 1.0

    # Base growth with distance (original magnitudes — keeps the overall total).
    k = 0.12 * (total_distance_km / 42)
    base = 1.0 + k * (progress ** 2)

    # ~Total-neutral fresh→fade reshape: fresh legs run a touch faster than the
    # averaged gradient curve early, then decay late (validated on real race
    # splits). ``tilt`` is per-athlete once a matched race result exists —
    # a runner who blows up late gets a steeper tilt than the generic default.
    base += tilt * (progress - 0.45)

    # Glycogen depletion after ~30-35km
    glycogen_threshold = min(35 / total_distance_km, 0.7)
    if progress > glycogen_threshold:
        base += 0.04 * ((progress - glycogen_threshold) / (1 - glycogen_threshold)) ** 1.5

    # Elevation fatigue: more D+ = more fatigue (bites late on hilly ultras).
    if cumulative_gain > 0:
        base += (cumulative_gain / 8000) * 0.02

    return max(0.85, min(base, 1.55))


def _altitude_factor(avg_elevation: float) -> float:
    """Performance loss at altitude. VO2max drops ~6.3% per 1000m above 1500m.

    Returns multiplier >= 1.0 (higher = slower).
    """
    if avg_elevation <= 1500:
        return 1.0
    return 1.0 + 0.063 * ((avg_elevation - 1500) / 1000)


def _terrain_difficulty_factor(gradient_pct: float, elevation_gain: float, elevation_loss: float, distance_m: float) -> float:
    """Technical terrain penalty based on gradient steepness and elevation variance.

    Steeper and more variable terrain = more technical = slower.
    Returns multiplier >= 1.0.
    """
    if distance_m <= 0:
        return 1.0
    # Total elevation change per km (both up and down)
    vert_intensity = (elevation_gain + elevation_loss) / (distance_m / 1000)
    abs_gradient = abs(gradient_pct)

    factor = 1.0
    # Very steep terrain (>15%) gets a technical penalty
    if abs_gradient > 20:
        factor += 0.08
    elif abs_gradient > 15:
        factor += 0.04

    # High vert intensity (lots of up AND down per km) = technical
    if vert_intensity > 150:  # >150m of vert per km = very technical
        factor += 0.05
    elif vert_intensity > 100:
        factor += 0.02

    return factor


def _night_penalty(cumulative_time_s: float, start_hour: float = 6) -> float:
    """Penalty for running at night. Assumes race starts at start_hour.

    ``start_hour`` may be fractional (e.g. 6.5 for 06:30).
    Night = between 21:00 and 06:00. Returns multiplier >= 1.0.
    """
    elapsed_hours = cumulative_time_s / 3600
    current_hour = (start_hour + elapsed_hours) % 24

    if 21 <= current_hour or current_hour < 6:
        return 1.08  # 8% slower at night
    if 20 <= current_hour < 21 or 6 <= current_hour < 7:
        return 1.03  # 3% slower dusk/dawn
    return 1.0


def predict_course(
    course: CourseProfile,
    profile: AthleteGradientProfile,
    heat_factor: float = 1.0,
    start_hour: int = 6,
    start_minute: int = 0,
) -> CourseProfile:
    """Apply gradient-adjusted pace prediction with fatigue, heat, altitude, terrain, night."""
    start_hour_frac = start_hour + (start_minute or 0) / 60
    cumulative_time = 0.0
    cumulative_gain = 0.0
    total_distance = course.total_distance_km

    tilt = getattr(profile, "fatigue_tilt", 0.15) or 0.15

    for segment in course.segments:
        grade_factor = _get_factor(profile, segment.avg_gradient_pct)
        cumulative_gain += segment.elevation_gain

        # Altitude correction (>1500m)
        avg_elev = (segment.min_elevation + segment.max_elevation) / 2
        alt = _altitude_factor(avg_elev)

        # Terrain difficulty
        terrain = _terrain_difficulty_factor(
            segment.avg_gradient_pct, segment.elevation_gain,
            segment.elevation_loss, segment.distance_m,
        )


        # Progressive fatigue (personalised tilt). Progress from the segment's
        # own end_km: reading cumulative_distance_km here used the PREVIOUS
        # prediction's value (0 on a fresh course — flattening fatigue).
        progress = segment.end_km / total_distance if total_distance > 0 else 0
        factor = grade_factor * alt * terrain
        factor *= _fatigue_factor(progress, total_distance, cumulative_gain, tilt)

        # Heat factor
        factor *= heat_factor

        # Night penalty
        factor *= _night_penalty(cumulative_time, start_hour_frac)

        predicted_pace = profile.flat_pace_s_per_km * factor
        predicted_time = predicted_pace * (segment.distance_m / 1000)
        # The basis used to split a TARGET into passage times: terrain, fatigue
        # and night, not heat (applied per section with the forecast). A plan
        # on terrain alone asked for the last 30 km faster than the start —
        # faster than the 2025 Transjeju winner ran them.
        segment.base_time_s = round(predicted_time / (heat_factor or 1.0), 1)

        segment.predicted_pace_s_per_km = round(predicted_pace, 1)
        segment.predicted_time_s = round(predicted_time, 1)
        cumulative_time += predicted_time
        segment.cumulative_time_s = round(cumulative_time, 1)
        segment.cumulative_distance_km = segment.end_km

    course.predicted_total_time_s = int(cumulative_time)
    course.predicted_total_time_formatted = format_time(int(cumulative_time))

    # Races, not training, set the level of the prediction (see race_calibration).
    if getattr(profile, "race_model", None):
        from app.services.race_calibration import apply_race_calibration

        profile.race_calibration = apply_race_calibration(course, profile.race_model)

    return course


def format_time(seconds: int) -> str:
    """Format seconds into Xh XX' XX\" format."""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}'{secs:02d}\""
    return f"{minutes}'{secs:02d}\""


def _elevation_at_km(course: CourseProfile, km: float) -> float | None:
    """Interpolate elevation (m) at a given distance from the elevation profile."""
    pts = course.elevation_points or []
    if not pts:
        return None
    if km <= pts[0]["distance_km"]:
        return round(pts[0]["elevation"])
    for i in range(1, len(pts)):
        if pts[i]["distance_km"] >= km:
            p, n = pts[i - 1], pts[i]
            span = n["distance_km"] - p["distance_km"]
            t = (km - p["distance_km"]) / span if span > 0 else 0
            return round(p["elevation"] + t * (n["elevation"] - p["elevation"]))
    return round(pts[-1]["elevation"])


# Standard atmospheric lapse rate: temperature drops ~6.5°C per 1000m of altitude.
_LAPSE_RATE_C_PER_M = 0.0065


def compute_passage_times(
    course: CourseProfile,
    checkpoints: list[dict],
    target_time_s: int | None = None,
    heat_factor: float = 1.0,
    start_hour: int = 6,
    start_minute: int = 0,
    hourly_weather: dict | None = None,
    stop_s_per_aid: int = 0,
    aid_kms: set | None = None,
    aid_stops: dict | None = None,
) -> list[dict]:
    """Compute passage times between checkpoints.

    ``aid_stops`` maps a checkpoint km to its stop in seconds (one value per
    station); without it every km of ``aid_kms`` gets ``stop_s_per_aid``.

    Checkpoints are [{name, distance_km}]. Start (0km) and finish are added
    automatically. Clock passage times are derived from the race start time of
    day (``start_hour``:``start_minute``). Returns PassageTimeSection dicts.

    When ``hourly_weather`` ({"temps": [24], "humidity": [24]}) is provided, a
    per-section heat factor is computed from each section's estimated time of day
    (which hour it is run) and its mean altitude (lapse-rate corrected from the
    start). This overrides the scalar ``heat_factor``.

    ``stop_s_per_aid`` adds a stop at each checkpoint whose km is in ``aid_kms``
    (aid stations). Stops shift the CLOCK times (and the target distribution's
    moving budget) — predictions themselves stay moving-time.

    The target plan ("adjusted_*") is distributed by segment.base_time_s:
    terrain, fatigue and night (heat excluded, applied per section here). A
    plan on terrain alone asked for the second half faster than the first,
    which nobody runs on a 100-miler.
    """
    from app.schemas.simulator import PassageTimeSection
    from app.services.weather import compute_heat_factor

    start_offset_s = start_hour * 3600 + (start_minute or 0) * 60

    # Build full checkpoint list with start and finish. Track the original index
    # of each checkpoint so the UI can map a row back to its checkpoint.
    all_cps = [{"name": "Depart", "distance_km": 0.0, "cp_index": None}]
    sorted_cps = sorted(
        enumerate(checkpoints), key=lambda pair: pair[1]["distance_km"]
    )
    for orig_idx, cp in sorted_cps:
        if cp["distance_km"] > 0 and cp["distance_km"] < course.total_distance_km:
            all_cps.append({**cp, "cp_index": orig_idx})
    all_cps.append(
        {"name": "Arrivée", "distance_km": course.total_distance_km, "cp_index": None}
    )

    stops = _stops_by_km(stop_s_per_aid, aid_kms, aid_stops)
    # Aid stops apply at intermediate checkpoints only (not the finish).
    total_stops_s = sum(stops.get(round(float(cp["distance_km"]), 1), 0) for cp in all_cps[1:-1])

    use_hourly = bool(hourly_weather and hourly_weather.get("temps"))
    base_elev = _elevation_at_km(course, 0.0) or 0.0

    # Pass 1: aggregate baseline section times (no heat) so we can estimate the
    # clock time at which each section is run before applying weather.
    raw = []
    baseline_cum = 0.0
    for i in range(len(all_cps) - 1):
        start_km = all_cps[i]["distance_km"]
        end_km = all_cps[i + 1]["distance_km"]
        section_dist = end_km - start_km

        section_time = 0.0
        section_base = 0.0
        section_gain = 0.0
        section_loss = 0.0
        for seg in course.segments:
            if seg.end_km <= start_km or seg.start_km >= end_km:
                continue
            overlap_start = max(seg.start_km, start_km)
            overlap_end = min(seg.end_km, end_km)
            seg_length = seg.end_km - seg.start_km
            if seg_length <= 0:
                continue
            fraction = (overlap_end - overlap_start) / seg_length
            section_time += seg.predicted_time_s * fraction
            # plan basis: terrain + fatigue + night (falls back to predicted for old cached courses)
            section_base += (seg.base_time_s or seg.predicted_time_s) * fraction
            section_gain += seg.elevation_gain * fraction
            section_loss += seg.elevation_loss * fraction

        mid_cum = baseline_cum + section_time / 2
        baseline_cum += section_time
        raw.append({
            "start_km": start_km, "end_km": end_km, "dist": section_dist,
            "time": section_time, "base": section_base,
            "gain": section_gain, "loss": section_loss,
            "mid_cum": mid_cum, "cum": baseline_cum, "cp_index": all_cps[i + 1]["cp_index"],
            "start_name": all_cps[i]["name"], "end_name": all_cps[i + 1]["name"],
        })

    # Target distribution: the plan's moving budget is the target minus the
    # planned aid-station stops, split in proportion to each section's basis
    # (terrain, fatigue, night): the finish is slower than the start.
    total_base = sum(r["base"] for r in raw) or 1.0
    moving_target_s = None
    if target_time_s:
        moving_target_s = max(target_time_s - total_stops_s, 1)

    # Pass 2: apply per-section (or global) heat and accumulate final times.
    sections = []
    cumulative = 0.0
    adj_cumulative = 0.0
    stops_acc = 0
    for r in raw:
        temperature_c = None
        weather_code = None
        wind_kmh = None
        humidity_pct = None
        if use_hourly:
            from app.services.weather import hourly_at

            hour = int((start_offset_s + r["mid_cum"] + stops_acc) // 3600)
            mid_km = (r["start_km"] + r["end_km"]) / 2
            w = hourly_at(hourly_weather, hour, mid_km)
            base_temp = w["temp"] if w["temp"] is not None else 15.0
            humidity = w["humidity"] if w["humidity"] is not None else 60
            if w["code"] is not None:
                weather_code = int(w["code"])
            wind_kmh = round(float(w["wind"]), 1) if w["wind"] is not None else None
            humidity_pct = round(float(humidity))
            # The forecast point has its own elevation (model grid): correct the
            # section's temperature from THAT elevation, not from the start's.
            mid_elev = _elevation_at_km(course, mid_km) or base_elev
            ref_elev = float(w["elevation"]) if w["elevation"] is not None else base_elev
            temperature_c = round(base_temp - _LAPSE_RATE_C_PER_M * (mid_elev - ref_elev), 1)
            sec_heat = compute_heat_factor(temperature_c, humidity)
            # What the table shows is the forecast AT the arrival point, at the
            # arrival hour (the mid-leg sample above only drives the heat factor).
            end_hour = int((start_offset_s + r["cum"] + stops_acc) // 3600)
            w_end = hourly_at(hourly_weather, end_hour, r["end_km"])
            if w_end["temp"] is not None:
                end_elev = _elevation_at_km(course, r["end_km"]) or base_elev
                end_ref = float(w_end["elevation"]) if w_end["elevation"] is not None else base_elev
                temperature_c = round(float(w_end["temp"]) - _LAPSE_RATE_C_PER_M * (end_elev - end_ref), 1)
                if w_end["code"] is not None:
                    weather_code = int(w_end["code"])
                wind_kmh = round(float(w_end["wind"]), 1) if w_end["wind"] is not None else wind_kmh
                humidity_pct = round(float(w_end["humidity"])) if w_end["humidity"] is not None else humidity_pct
        else:
            sec_heat = heat_factor

        section_time = r["time"] * sec_heat
        cumulative += section_time
        pace = section_time / r["dist"] if r["dist"] > 0 else 0

        # Plan (target): the moving budget split by the basis share.
        adjusted_time = (moving_target_s * (r["base"] / total_base)) if moving_target_s else 0.0
        adj_cumulative += adjusted_time

        # Stop at this arrival checkpoint? (intermediate aid stations only)
        arrival_stop = stops.get(round(float(r["end_km"]), 1), 0) if r["cp_index"] is not None else 0

        sections.append(PassageTimeSection(
            start_name=r["start_name"],
            end_name=r["end_name"],
            start_km=round(r["start_km"], 1),
            end_km=round(r["end_km"], 1),
            distance_km=round(r["dist"], 1),
            elevation_gain=round(r["gain"], 0),
            elevation_loss=round(r["loss"], 0),
            predicted_time_s=round(section_time, 0),
            cumulative_time_s=round(cumulative, 0),
            predicted_pace_s_per_km=round(pace, 0),
            adjusted_time_s=round(adjusted_time, 0) if target_time_s else None,
            adjusted_cumulative_time_s=round(adj_cumulative, 0) if target_time_s else None,
            end_elevation=_elevation_at_km(course, r["end_km"]),
            clock_time_s=int(start_offset_s + cumulative + stops_acc),
            adjusted_clock_time_s=int(start_offset_s + adj_cumulative + stops_acc) if target_time_s else None,
            end_checkpoint_index=r["cp_index"],
            temperature_c=temperature_c,
            heat_factor=round(sec_heat, 3) if use_hourly else None,
            weather_code=weather_code,
            wind_kmh=wind_kmh,
            humidity_pct=humidity_pct,
            stop_s=int(arrival_stop),
        ).model_dump())

        stops_acc += arrival_stop

    return sections


def _stops_by_km(stop_s_per_aid: int, aid_kms: set | None, aid_stops: dict | None) -> dict:
    """{km: stop seconds}: the per-station map when given, else one value for every aid km."""
    if aid_stops:
        return {round(float(k), 1): int(v) for k, v in aid_stops.items() if v}
    if stop_s_per_aid:
        return {round(float(k), 1): int(stop_s_per_aid) for k in (aid_kms or set())}
    return {}


def replan_from_passage(
    sections: list[dict],
    start_offset_s: int,
    target_time_s: int | None,
    anchor_km: float,
    anchor_clock_s: int,
    stop_s_per_aid: int = 0,
    aid_kms: set | None = None,
    min_feasible_ratio: float = 0.85,
    aid_stops: dict | None = None,
) -> tuple[list[dict], dict | None]:
    """Race-day re-plan: "I'm at <checkpoint> at <clock>, now what?"

    Given the plan's sections and the REAL arrival clock at one checkpoint,
    rewrite the remaining sections at even effort so the plan stays usable:

    - if the objective is still within reach (the remainder needs at most
      ``1 - min_feasible_ratio`` faster than predicted), the remaining time
      budget is (objective - elapsed - remaining stops), distributed by the
      same effort shares as the plan;
    - otherwise the plan falls back to the athlete's ACTUAL rhythm so far
      (elapsed / predicted at the anchor) and announces the projected finish.

    Rows up to the anchor are flagged ``passed``; the anchor row shows the
    real clock. Returns (new_sections, replan_summary) or (sections, None)
    when the anchor doesn't match a checkpoint.
    """
    aid_set = {round(float(k), 1) for k in (aid_kms or set())}
    idx = next(
        (i for i, s in enumerate(sections)
         if s.get("end_checkpoint_index") is not None and abs(float(s["end_km"]) - float(anchor_km)) < 0.15),
        None,
    )
    if idx is None:
        return sections, None

    a = sections[idx]
    use_plan = bool(target_time_s) and a.get("adjusted_clock_time_s") is not None
    # The plan's clock floored to the minute, as the table prints it, so the
    # delta the athlete reads matches the two clocks he compares.
    plan_clock = int(a["adjusted_clock_time_s"] if use_plan else a["clock_time_s"]) // 60 * 60
    plan_elapsed = plan_clock - int(start_offset_s)
    anchor_elapsed = int(anchor_clock_s) - int(start_offset_s)
    while anchor_elapsed < 0:  # passage after midnight
        anchor_elapsed += 86400
    # A clock alone does not say which day: take the occurrence closest to the
    # plan (a 100-miler crosses midnight twice).
    while abs(anchor_elapsed + 86400 - plan_elapsed) < abs(anchor_elapsed - plan_elapsed):
        anchor_elapsed += 86400
    delta_s = anchor_elapsed - plan_elapsed
    # Rhythm is measured against the PLAN (even effort is deliberately slower
    # than the prediction early on): 1.05 = 5 % slower than planned so far.
    rhythm = (anchor_elapsed / plan_elapsed) if plan_elapsed > 0 else 1.0

    stops = _stops_by_km(stop_s_per_aid, aid_kms, aid_stops)

    def stop_of(s: dict) -> int:
        return stops.get(round(float(s["end_km"]), 1), 0) if s.get("end_checkpoint_index") is not None else 0

    remaining = sections[idx + 1:]
    anchor_stop = stop_of(a)
    stops_remaining = anchor_stop + sum(stop_of(s) for s in remaining)
    shares = [
        float(s["adjusted_time_s"]) if (target_time_s and s.get("adjusted_time_s")) else float(s["predicted_time_s"])
        for s in remaining
    ]
    total_share = sum(shares) or 1.0  # = the plan's remaining moving time

    projected_moving = total_share * rhythm
    projected_finish = start_offset_s + anchor_elapsed + projected_moving + stops_remaining

    mode, feasible, required_ratio, budget = "rhythm", None, None, projected_moving
    if target_time_s and total_share > 0:
        budget_target = target_time_s - anchor_elapsed - stops_remaining
        required_ratio = budget_target / total_share  # vs what the plan intended
        feasible = budget_target > 0 and required_ratio >= min_feasible_ratio
        if feasible:
            mode, budget = "target", budget_target

    out = [dict(s) for s in sections]
    for i in range(idx + 1):
        out[i]["passed"] = True
    out[idx]["is_anchor"] = True
    # stored with its day so day separators, cutoffs and the profile agree
    out[idx]["adjusted_clock_time_s"] = int(start_offset_s + anchor_elapsed)
    out[idx]["adjusted_cumulative_time_s"] = int(anchor_elapsed)

    cum = 0.0
    stops_acc = anchor_stop
    for j, s in enumerate(remaining):
        t = budget * shares[j] / total_share
        cum += t
        o = out[idx + 1 + j]
        o["adjusted_time_s"] = round(t)
        o["adjusted_cumulative_time_s"] = round(anchor_elapsed + cum)
        o["adjusted_clock_time_s"] = int(start_offset_s + anchor_elapsed + cum + stops_acc)
        if stop_of(s) and j < len(remaining) - 1:
            stops_acc += stop_of(s)

    replan = {
        "anchor_name": a["end_name"],
        "anchor_km": a["end_km"],
        "anchor_clock_s": int(anchor_clock_s),
        "delta_s": int(delta_s),
        "mode": mode,
        "feasible": feasible,
        "required_ratio": round(required_ratio, 3) if required_ratio is not None else None,
        "rhythm": round(rhythm, 3),
        "projected_finish_clock_s": int(projected_finish),
        "finish_clock_s": out[-1]["adjusted_clock_time_s"] if out else None,
        "target_time_s": target_time_s,
    }
    return out, replan


# ── Three scenarios with an explicit switch rule ──

def build_scenarios(
    sections: list[dict],
    start_offset_s: int,
    target_time_s: int | None,
    fast_pct: float = 5.0,
    safe_pct: float = 10.0,
    switch_km: float | None = None,
    total_distance_km: float | None = None,
) -> dict | None:
    """Target / optimistic / safety columns for every checkpoint, plus the
    rule that decides which column you are running.

    The three plans share the SAME shape (even effort) and the same aid stops;
    only the moving budget changes: cible = the plan (or the prediction when no
    target is set), optimiste = cible × (1 − fast_pct), sécurité = cible ×
    (1 + safe_pct). The switch checkpoint defaults to the one closest to 60 %
    of the distance — past the point where a fast start can still be paid for.
    """
    if not sections:
        return None
    use_target = bool(target_time_s) and sections[0].get("adjusted_clock_time_s") is not None
    f_fast = max(0.0, fast_pct) / 100.0
    f_safe = max(0.0, safe_pct) / 100.0
    rows = []
    for s in sections:
        cum = float(s["adjusted_cumulative_time_s"] if use_target else s["cumulative_time_s"])
        clock = int(s["adjusted_clock_time_s"] if use_target else s["clock_time_s"])
        stops = clock - int(start_offset_s) - cum  # aid stops accumulated so far
        rows.append({
            "name": s["end_name"], "km": s["end_km"], "cp_index": s.get("end_checkpoint_index"),
            "target_s": int(round(start_offset_s + cum + stops)),
            "fast_s": int(round(start_offset_s + cum * (1 - f_fast) + stops)),
            "safe_s": int(round(start_offset_s + cum * (1 + f_safe) + stops)),
            "cutoff_elapsed_s": s.get("cutoff_elapsed_s"),
            "kind": s.get("kind"),
        })
        cut = s.get("cutoff_elapsed_s")
        if cut is not None:
            cut_clock = int(start_offset_s) + int(cut)
            rows[-1]["safe_ok"] = rows[-1]["safe_s"] <= cut_clock
            rows[-1]["target_ok"] = rows[-1]["target_s"] <= cut_clock
            rows[-1]["cutoff_clock_s"] = cut_clock

    total_km = float(total_distance_km or sections[-1]["end_km"] or 1)
    want = float(switch_km) if switch_km else total_km * 0.6
    cps = [r for r in rows if r["cp_index"] is not None]
    switch = min(cps, key=lambda r: abs(float(r["km"]) - want)) if cps else None

    total_target = rows[-1]["target_s"] - start_offset_s
    total_fast = rows[-1]["fast_s"] - start_offset_s
    total_safe = rows[-1]["safe_s"] - start_offset_s
    return {
        "rows": rows,
        "switch": switch,
        "switch_late_s": 15 * 60,  # ≥ 15 min late at the switch point → safety column
        "fast_pct": fast_pct, "safe_pct": safe_pct,
        "totals": {"target_s": int(total_target), "fast_s": int(total_fast), "safe_s": int(total_safe)},
        "basis": "target" if use_target else "prediction",
        "cutoff_breach": [r for r in rows if r.get("safe_ok") is False],
    }
