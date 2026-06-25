"""Gradient-adjusted pace prediction from athlete Strava data."""

import logging
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.schemas.simulator import AthleteGradientProfile, CourseProfile, CourseSegment

logger = logging.getLogger(__name__)

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

    # Extract gradient-pace data points from splits
    data_points: list[tuple[float, float]] = []
    sport_types_used = set()

    for activity in activities:
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
        return AthleteGradientProfile(
            flat_pace_s_per_km=flat_pace,
            gradient_factors=_DEFAULT_GRADIENT_FACTORS,
            data_points=0,
            sport_types_used=list(sport_types_used),
        )

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

    return AthleteGradientProfile(
        flat_pace_s_per_km=round(flat_pace, 1),
        gradient_factors=gradient_factors,
        data_points=len(data_points),
        sport_types_used=list(sport_types_used),
    )


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
) -> float:
    """Exponential fatigue factor based on race progress and D+.

    Returns a multiplier >= 1.0 (higher = slower).
    """
    if total_distance_km < 20:
        return 1.0

    # Base exponential: grows with distance
    k = 0.12 * (total_distance_km / 42)  # Scaled to marathon
    base = 1.0 + k * (progress ** 2)

    # Glycogen depletion after ~2.5h of running (~30-35km)
    glycogen_threshold = min(35 / total_distance_km, 0.7)
    if progress > glycogen_threshold:
        glycogen_penalty = 0.04 * ((progress - glycogen_threshold) / (1 - glycogen_threshold)) ** 1.5
        base += glycogen_penalty

    # Elevation fatigue: more D+ = more fatigue
    if cumulative_gain > 0:
        base += (cumulative_gain / 8000) * 0.02

    return min(base, 1.5)


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

    for segment in course.segments:
        factor = _get_factor(profile, segment.avg_gradient_pct)
        cumulative_gain += segment.elevation_gain

        # Progressive fatigue
        progress = segment.cumulative_distance_km / total_distance if total_distance > 0 else 0
        factor *= _fatigue_factor(progress, total_distance, cumulative_gain)

        # Heat factor
        factor *= heat_factor

        # Altitude correction (>1500m)
        avg_elev = (segment.min_elevation + segment.max_elevation) / 2
        factor *= _altitude_factor(avg_elev)

        # Terrain difficulty
        factor *= _terrain_difficulty_factor(
            segment.avg_gradient_pct, segment.elevation_gain,
            segment.elevation_loss, segment.distance_m,
        )

        # Night penalty
        factor *= _night_penalty(cumulative_time, start_hour_frac)

        predicted_pace = profile.flat_pace_s_per_km * factor
        predicted_time = predicted_pace * (segment.distance_m / 1000)

        segment.predicted_pace_s_per_km = round(predicted_pace, 1)
        segment.predicted_time_s = round(predicted_time, 1)
        cumulative_time += predicted_time
        segment.cumulative_time_s = round(cumulative_time, 1)
        segment.cumulative_distance_km = segment.end_km

    course.predicted_total_time_s = int(cumulative_time)
    course.predicted_total_time_formatted = format_time(int(cumulative_time))

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
) -> list[dict]:
    """Compute passage times between checkpoints.

    Checkpoints are [{name, distance_km}]. Start (0km) and finish are added
    automatically. Clock passage times are derived from the race start time of
    day (``start_hour``:``start_minute``). Returns PassageTimeSection dicts.

    When ``hourly_weather`` ({"temps": [24], "humidity": [24]}) is provided, a
    per-section heat factor is computed from each section's estimated time of day
    (which hour it is run) and its mean altitude (lapse-rate corrected from the
    start). This overrides the scalar ``heat_factor``.
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
        {"name": "Arrivee", "distance_km": course.total_distance_km, "cp_index": None}
    )

    # Scale factor for target time
    scale = 1.0
    if target_time_s and course.predicted_total_time_s > 0:
        scale = target_time_s / course.predicted_total_time_s

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
            section_gain += seg.elevation_gain * fraction
            section_loss += seg.elevation_loss * fraction

        mid_cum = baseline_cum + section_time / 2
        baseline_cum += section_time
        raw.append({
            "start_km": start_km, "end_km": end_km, "dist": section_dist,
            "time": section_time, "gain": section_gain, "loss": section_loss,
            "mid_cum": mid_cum, "cp_index": all_cps[i + 1]["cp_index"],
            "start_name": all_cps[i]["name"], "end_name": all_cps[i + 1]["name"],
        })

    # Pass 2: apply per-section (or global) heat and accumulate final times.
    sections = []
    cumulative = 0.0
    adj_cumulative = 0.0
    for r in raw:
        temperature_c = None
        weather_code = None
        if use_hourly:
            hour = int((start_offset_s + r["mid_cum"]) // 3600) % 24
            temps = hourly_weather["temps"]
            hums = hourly_weather.get("humidity") or []
            codes = hourly_weather.get("codes") or []
            base_temp = temps[hour] if hour < len(temps) else temps[-1]
            humidity = hums[hour] if hour < len(hums) else (hums[-1] if hums else 60)
            if hour < len(codes) and codes[hour] is not None:
                weather_code = int(codes[hour])
            mid_elev = _elevation_at_km(course, (r["start_km"] + r["end_km"]) / 2) or base_elev
            temperature_c = round(base_temp - _LAPSE_RATE_C_PER_M * (mid_elev - base_elev), 1)
            sec_heat = compute_heat_factor(temperature_c, humidity)
        else:
            sec_heat = heat_factor

        section_time = r["time"] * sec_heat
        cumulative += section_time
        pace = section_time / r["dist"] if r["dist"] > 0 else 0

        adjusted_time = section_time * scale
        adj_cumulative += adjusted_time

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
            clock_time_s=int(start_offset_s + cumulative),
            adjusted_clock_time_s=int(start_offset_s + adj_cumulative) if target_time_s else None,
            end_checkpoint_index=r["cp_index"],
            temperature_c=temperature_c,
            heat_factor=round(sec_heat, 3) if use_hourly else None,
            weather_code=weather_code,
        ).model_dump())

    return sections
