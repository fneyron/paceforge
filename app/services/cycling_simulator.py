"""Cycling course prediction with physics + wind, BestBikeSplit / myWindsock style."""

import logging
import math
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.schemas.simulator import (
    CdaEstimate,
    CourseProfile,
    CyclingProfile,
    CyclingSegment,
)

logger = logging.getLogger(__name__)


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, in degrees [0, 360)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _segment_bearing(route_coords: list[list[float]], start_km: float, end_km: float) -> float:
    """Average bearing across a segment by sampling consecutive coords inside [start_km, end_km].

    route_coords entries are [lat, lon, distance_km, elevation].
    """
    if not route_coords:
        return 0.0

    pts = [c for c in route_coords if start_km <= c[2] <= end_km]
    if len(pts) < 2:
        # Fall back to first/last coords bracketing the segment
        a = next((c for c in route_coords if c[2] >= start_km), route_coords[0])
        b = next((c for c in reversed(route_coords) if c[2] <= end_km), route_coords[-1])
        if a is b:
            return 0.0
        return _bearing_deg(a[0], a[1], b[0], b[1])

    # Vector mean of consecutive bearings (handles 350°/10° wrap)
    sin_sum = 0.0
    cos_sum = 0.0
    for i in range(1, len(pts)):
        b = _bearing_deg(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])
        sin_sum += math.sin(math.radians(b))
        cos_sum += math.cos(math.radians(b))
    return (math.degrees(math.atan2(sin_sum, cos_sum)) + 360) % 360


def headwind_component(wind_speed_ms: float, wind_from_deg: float, rider_bearing_deg: float) -> float:
    """Project meteorological wind onto rider's heading.

    `wind_from_deg` follows the meteo convention (direction the wind comes FROM).
    Returns the headwind component in m/s: +ve = headwind, -ve = tailwind.
    """
    delta = math.radians(wind_from_deg - rider_bearing_deg)
    return wind_speed_ms * math.cos(delta)


def cycling_power(
    speed_ms: float,
    total_weight_kg: float,
    gradient_pct: float,
    headwind_ms: float = 0.0,
    cda: float = 0.32,
    crr: float = 0.005,
    rho: float = 1.225,
) -> float:
    """Power (W) the rider must produce to hold ground speed `speed_ms`.

    Includes gravity (signed by gradient), rolling resistance, and aero drag in
    a wind-aware form. Power can be negative on a descent + tailwind (rider
    would be accelerated by gravity/wind beyond the target speed).
    """
    grade = gradient_pct / 100
    theta = math.atan(grade)

    p_gravity = total_weight_kg * 9.81 * math.sin(theta) * speed_ms
    p_rolling = crr * total_weight_kg * 9.81 * math.cos(theta) * speed_ms
    v_air = speed_ms + headwind_ms
    p_aero = 0.5 * cda * rho * v_air * abs(v_air) * speed_ms
    return p_gravity + p_rolling + p_aero


def solve_cycling_speed(
    target_watts: float,
    total_weight_kg: float,
    gradient_pct: float,
    headwind_ms: float = 0.0,
    cda: float = 0.32,
    crr: float = 0.005,
    rho: float = 1.225,
) -> float:
    """Binary search for ground speed (m/s) that requires `target_watts`."""
    lo, hi = 0.5, 30.0  # m/s (≈1.8 to 108 km/h)
    p_lo = cycling_power(lo, total_weight_kg, gradient_pct, headwind_ms, cda, crr, rho)
    p_hi = cycling_power(hi, total_weight_kg, gradient_pct, headwind_ms, cda, crr, rho)

    # Target above terminal at v_max → cap (effectively unattainable without more power)
    if p_hi < target_watts:
        return hi
    # Target below what's needed even at the slowest v (very steep climb at low watts) → cap
    if p_lo > target_watts:
        return lo

    for _ in range(60):
        mid = (lo + hi) / 2
        p = cycling_power(mid, total_weight_kg, gradient_pct, headwind_ms, cda, crr, rho)
        if p < target_watts:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def predict_cycling_course(
    course: CourseProfile,
    target_power_watts: float,
    rider_weight_kg: float,
    bike_weight_kg: float = 9.0,
    cda: float = 0.32,
    crr: float = 0.005,
    rho: float = 1.225,
    wind_speed_kmh: float = 0.0,
    wind_direction_deg: float | None = None,
    wind_source: str | None = None,
    ftp_watts: float | None = None,
) -> CyclingProfile:
    """Predict per-segment power/speed/time for a bike course at constant target power.

    Wind is integrated per segment by projecting onto the rider's bearing.
    `wind_direction_deg` is the direction the wind comes FROM (meteo convention);
    pass `None` to disable wind effects.
    """
    total_weight = rider_weight_kg + bike_weight_kg
    wind_speed_ms = wind_speed_kmh / 3.6
    apply_wind = wind_direction_deg is not None and wind_speed_ms > 0

    cycling_segments: list[CyclingSegment] = []
    cumulative_time = 0.0
    work_j = 0.0
    fourth_power_time_sum = 0.0  # Σ p_i^4 * t_i

    for seg in course.segments:
        bearing = _segment_bearing(course.route_coords, seg.start_km, seg.end_km)

        if apply_wind:
            headwind_ms = headwind_component(wind_speed_ms, wind_direction_deg, bearing)
        else:
            headwind_ms = 0.0

        speed_ms = solve_cycling_speed(
            target_power_watts, total_weight, seg.avg_gradient_pct,
            headwind_ms, cda, crr, rho,
        )
        # Power actually produced at that speed (clamped to >= 0 for display: rider
        # can't pedal negative, but we still report the target-driven solution).
        seg_power = max(0.0, cycling_power(
            speed_ms, total_weight, seg.avg_gradient_pct,
            headwind_ms, cda, crr, rho,
        ))
        seg_time_s = seg.distance_m / speed_ms if speed_ms > 0 else 0
        cumulative_time += seg_time_s
        work_j += seg_power * seg_time_s
        fourth_power_time_sum += (seg_power ** 4) * seg_time_s

        cycling_segments.append(CyclingSegment(
            index=seg.index,
            start_km=seg.start_km,
            end_km=seg.end_km,
            distance_m=seg.distance_m,
            elevation_gain=seg.elevation_gain,
            elevation_loss=seg.elevation_loss,
            avg_gradient_pct=seg.avg_gradient_pct,
            min_elevation=seg.min_elevation,
            max_elevation=seg.max_elevation,
            bearing_deg=round(bearing, 1),
            headwind_ms=round(headwind_ms, 2),
            predicted_power_watts=round(seg_power, 0),
            predicted_speed_kmh=round(speed_ms * 3.6, 1),
            predicted_time_s=round(seg_time_s, 1),
            cumulative_time_s=round(cumulative_time, 1),
            cumulative_distance_km=seg.end_km,
        ))

    total_time_s = max(int(cumulative_time), 1)
    avg_power = work_j / total_time_s if total_time_s > 0 else 0
    np_watts = (fourth_power_time_sum / total_time_s) ** 0.25 if total_time_s > 0 else 0
    avg_speed_kmh = course.total_distance_km / (total_time_s / 3600) if total_time_s > 0 else 0
    work_kj = work_j / 1000

    intensity_factor = (np_watts / ftp_watts) if ftp_watts and ftp_watts > 0 else None
    tss = None
    if intensity_factor is not None:
        tss = (total_time_s / 3600) * (intensity_factor ** 2) * 100

    return CyclingProfile(
        name=course.name,
        total_distance_km=course.total_distance_km,
        total_elevation_gain=course.total_elevation_gain,
        total_elevation_loss=course.total_elevation_loss,
        segments=cycling_segments,
        elevation_points=course.elevation_points,
        route_coords=course.route_coords,
        km_markers=course.km_markers,
        target_power_watts=target_power_watts,
        rider_weight_kg=rider_weight_kg,
        bike_weight_kg=bike_weight_kg,
        cda=cda,
        crr=crr,
        rho=rho,
        wind_speed_kmh=wind_speed_kmh if apply_wind else 0,
        wind_direction_deg=wind_direction_deg if apply_wind else None,
        wind_source=wind_source if apply_wind else None,
        predicted_total_time_s=total_time_s,
        predicted_total_time_formatted=format_time(total_time_s),
        avg_power_watts=round(avg_power, 0),
        normalized_power_watts=round(np_watts, 0),
        avg_speed_kmh=round(avg_speed_kmh, 1),
        intensity_factor=round(intensity_factor, 2) if intensity_factor is not None else None,
        work_kj=round(work_kj, 0),
        tss=round(tss, 0) if tss is not None else None,
    )


def format_time(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}'{secs:02d}\""
    return f"{minutes}'{secs:02d}\""


# ── CdA estimation from Strava streams ──────────────────────────────────────


def _air_density_at_altitude(alt_m: float) -> float:
    """Approximate ISA air density (kg/m³) at the given altitude in metres."""
    return 1.225 * math.exp(-alt_m / 8500)


def estimate_cda_from_streams(
    streams: dict,
    total_weight_kg: float,
    crr: float = 0.005,
    rho: float | None = None,
) -> tuple[float, float] | None:
    """Estimate CdA for one ride via aggregate energy balance.

    Solves: Σ P·dt = m·g·Δh + Crr·m·g·Σ V·dt + 0.5·ρ·CdA·Σ V³·dt
    Assumptions: ride starts and ends at rest (ΔKE ≈ 0), no drafting, mild wind
    averaging out, Crr fixed. Returns `(cda, mean_speed_ms)` or None if the
    streams don't yield a physically plausible value.
    """
    watts = streams.get("watts") or []
    velocity = streams.get("velocity_smooth") or []
    altitude = streams.get("altitude") or []
    time_s = streams.get("time") or []

    n = min(len(watts), len(velocity), len(altitude), len(time_s))
    if n < 600:  # Need at least ~10 minutes of data
        return None

    # Build per-step dt from the time stream (Strava streams are time-indexed in seconds)
    work_j = 0.0
    rolling_int = 0.0  # Σ V·dt
    aero_int = 0.0  # Σ V³·dt
    moving_dt = 0.0
    nonzero_v = []

    for i in range(1, n):
        dt = time_s[i] - time_s[i - 1]
        if dt <= 0 or dt > 30:  # Skip pauses or weird gaps
            continue
        v = velocity[i] or 0
        p = watts[i] or 0
        if v < 1.0:  # Effectively stopped — drop, contributes no aero info
            continue

        work_j += p * dt
        rolling_int += v * dt
        aero_int += (v ** 3) * dt
        moving_dt += dt
        nonzero_v.append(v)

    if moving_dt < 600 or aero_int <= 0 or not nonzero_v:
        return None

    delta_h = (altitude[n - 1] or 0) - (altitude[0] or 0)
    if rho is None:
        avg_alt = sum(a or 0 for a in altitude[:n]) / n
        rho = _air_density_at_altitude(avg_alt)

    g = 9.81
    pe_j = total_weight_kg * g * delta_h
    rolling_j = crr * total_weight_kg * g * rolling_int
    aero_j = work_j - pe_j - rolling_j
    if aero_j <= 0:
        return None

    cda = aero_j / (0.5 * rho * aero_int)
    if not (0.15 < cda < 0.6):
        return None

    return cda, statistics.median(nonzero_v)


async def estimate_cda(
    db: AsyncSession,
    user_id: int,
    rider_weight_kg: float,
    bike_weight_kg: float = 9.0,
    crr: float = 0.005,
    months: int = 6,
    max_rides: int = 30,
) -> CdaEstimate | None:
    """Aggregate CdA estimate across the user's recent outdoor rides with streams."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    result = await db.execute(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.sport_type == "Ride",  # Outdoor only — VirtualRide drops aero
            Activity.streams_data.is_not(None),
            Activity.start_date >= cutoff,
            Activity.moving_time >= 1800,  # ≥ 30 min
            Activity.average_watts.is_not(None),
            Activity.average_watts > 0,
        )
        .order_by(Activity.start_date.desc())
        .limit(max_rides)
    )
    activities = result.scalars().all()

    total_weight = rider_weight_kg + bike_weight_kg
    samples: list[float] = []
    speeds: list[float] = []
    used_count = 0
    for activity in activities:
        try:
            estimate = estimate_cda_from_streams(activity.streams_data, total_weight, crr=crr)
        except Exception:
            logger.warning("CdA estimation failed for activity %d", activity.id, exc_info=True)
            continue
        if estimate is None:
            continue
        cda, median_v = estimate
        samples.append(cda)
        speeds.append(median_v)
        used_count += 1

    if not samples:
        return None

    median_cda = statistics.median(samples)
    median_speed = statistics.median(speeds) if speeds else 0

    spread = statistics.stdev(samples) if len(samples) > 1 else 1.0
    if used_count >= 8 and spread < 0.04:
        confidence = "Bonne"
    elif used_count >= 4:
        confidence = "Correcte"
    else:
        confidence = "Approximative"

    return CdaEstimate(
        estimated_cda=round(median_cda, 3),
        samples=[round(s, 3) for s in samples],
        activity_count=used_count,
        confidence=confidence,
        crr_assumed=crr,
        rho_assumed=1.225,
        median_speed_kmh=round(median_speed * 3.6, 1),
    )
