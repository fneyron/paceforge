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
    max_speed_ms: float = 70 / 3.6,
) -> float:
    """Binary search for ground speed (m/s) that requires `target_watts`.

    ``max_speed_ms`` caps descents: a pure power model never brakes, a rider
    does (corners, safety) — 70 km/h is a realistic road ceiling.
    """
    lo, hi = 0.5, max_speed_ms
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
    hourly_wind: dict | None = None,
    start_offset_s: int = 0,
    wind_height_factor: float = 0.75,
    max_speed_kmh: float = 70.0,
) -> CyclingProfile:
    """Predict per-segment power/speed/time for a bike course at constant target power.

    Wind is integrated per segment by projecting onto the rider's bearing.
    `wind_direction_deg` is the direction the wind comes FROM (meteo convention);
    pass `None` to disable wind effects.

    With ``hourly_wind`` ({"speed": [24 km/h], "dir": [24 deg]}) the wind is
    taken at each segment's estimated time of passage (``start_offset_s`` +
    cumulative time). Forecast wind is reported at 10 m; ``wind_height_factor``
    scales it to rider height (~1.5 m, log wind profile ≈ 0.75).
    """
    total_weight = rider_weight_kg + bike_weight_kg
    wind_speed_ms = wind_speed_kmh / 3.6
    apply_hourly = bool(hourly_wind and hourly_wind.get("speed"))
    apply_wind = apply_hourly or (wind_direction_deg is not None and wind_speed_ms > 0)

    cycling_segments: list[CyclingSegment] = []
    cumulative_time = 0.0
    work_j = 0.0
    fourth_power_time_sum = 0.0  # Σ p_i^4 * t_i

    for seg in course.segments:
        bearing = _segment_bearing(course.route_coords, seg.start_km, seg.end_km)

        seg_wind_kmh, seg_wind_from = 0.0, None
        if apply_hourly:
            hour = int((start_offset_s + cumulative_time) // 3600) % 24
            speeds, dirs = hourly_wind["speed"], hourly_wind.get("dir") or []
            ws_kmh = (speeds[hour] if hour < len(speeds) else speeds[-1]) or 0.0
            wd = dirs[hour] if hour < len(dirs) else (dirs[-1] if dirs else None)
            seg_wind_kmh = ws_kmh * wind_height_factor
            seg_wind_from = wd
            headwind_ms = headwind_component(seg_wind_kmh / 3.6, wd, bearing) if wd is not None else 0.0
        elif apply_wind:
            seg_wind_kmh, seg_wind_from = wind_speed_kmh, wind_direction_deg
            headwind_ms = headwind_component(wind_speed_ms, wind_direction_deg, bearing)
        else:
            headwind_ms = 0.0

        speed_ms = solve_cycling_speed(
            target_power_watts, total_weight, seg.avg_gradient_pct,
            headwind_ms, cda, crr, rho, max_speed_ms=max_speed_kmh / 3.6,
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
            wind_kmh=round(seg_wind_kmh, 1),
            wind_from_deg=round(seg_wind_from, 0) if seg_wind_from is not None else None,
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
        wind_mode="auto" if apply_hourly else ("manual" if apply_wind else "none"),
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


# ── Road book: readable sections (climbs / descents / flats) ─────────────────

def _regime(grade_pct: float) -> str:
    if grade_pct >= 2.5:
        return "climb"
    if grade_pct <= -2.5:
        return "descent"
    return "flat"


def wind_label(headwind_kmh: float, wind_kmh: float) -> str:
    """Rider-facing wind word: face / dos / travers / calme."""
    if wind_kmh < 6:
        return "calme"
    if headwind_kmh >= 6:
        return "face"
    if headwind_kmh <= -6:
        return "dos"
    return "travers"


def build_bike_sections(cycling: CyclingProfile, start_offset_s: int = 0, min_len_km: float | None = None) -> list[dict]:
    """Merge fine segments into a road book: one row per climb / descent / flat
    stretch (short stretches absorbed into their neighbour), with the numbers a
    rider actually uses — distance, D+, grade, wind at time of passage, target
    power, speed, time, clock. This is the Best Bike Split "cheat sheet" with
    myWindsock's per-segment wind.
    """
    segs = list(cycling.segments)
    if not segs:
        return []
    min_len = min_len_km if min_len_km is not None else max(1.5, cycling.total_distance_km * 0.04)

    groups: list[list] = []  # [regime, [segments]]
    for sg in segs:
        r = _regime(sg.avg_gradient_pct)
        if groups and groups[-1][0] == r:
            groups[-1][1].append(sg)
        else:
            groups.append([r, [sg]])

    def km_len(g) -> float:
        return sum(x.distance_m for x in g[1]) / 1000

    merged: list[list] = []
    for g in groups:
        if merged and km_len(g) < min_len:
            merged[-1][1].extend(g[1])
        else:
            merged.append(g)
    if len(merged) > 1 and km_len(merged[0]) < min_len:
        merged[1][1] = merged[0][1] + merged[1][1]
        merged.pop(0)

    rows: list[dict] = []
    n_climb = n_desc = 0
    for g in merged:
        parts = g[1]
        dist_km = sum(x.distance_m for x in parts) / 1000
        gain = sum(x.elevation_gain for x in parts)
        loss = sum(x.elevation_loss for x in parts)
        time_s = sum(x.predicted_time_s for x in parts)
        net_grade = ((gain - loss) / (dist_km * 1000) * 100) if dist_km > 0 else 0.0
        if net_grade >= 2:
            n_climb += 1
            label, kind = f"Montée {n_climb}", "climb"
        elif net_grade <= -2:
            n_desc += 1
            label, kind = f"Descente {n_desc}", "descent"
        else:
            label = "Vallonné" if dist_km > 0 and (gain + loss) / dist_km > 30 else "Plat"
            kind = "flat"
        w_t = time_s or 1.0
        power = sum(x.predicted_power_watts * x.predicted_time_s for x in parts) / w_t
        headwind_ms = sum(x.headwind_ms * x.predicted_time_s for x in parts) / w_t
        wind_kmh = sum(x.wind_kmh * x.predicted_time_s for x in parts) / w_t
        speed_kmh = dist_km / (time_s / 3600) if time_s > 0 else 0.0
        cumulative = parts[-1].cumulative_time_s
        rows.append({
            "label": label,
            "kind": kind,
            "start_km": round(parts[0].start_km, 1),
            "end_km": round(parts[-1].end_km, 1),
            "distance_km": round(dist_km, 1),
            "elevation_gain": round(gain),
            "elevation_loss": round(loss),
            "grade_pct": round(net_grade, 1),
            "headwind_kmh": round(headwind_ms * 3.6, 1),
            "wind_kmh": round(wind_kmh, 1),
            "wind_label": wind_label(headwind_ms * 3.6, wind_kmh),
            "power_watts": round(power),
            "speed_kmh": round(speed_kmh, 1),
            "time_s": round(time_s),
            "cumulative_s": round(cumulative),
            "clock_s": int(start_offset_s + cumulative),
        })
    return rows
