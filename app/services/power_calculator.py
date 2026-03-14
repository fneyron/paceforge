"""Cycling power/speed physics calculator."""

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.schemas.simulator import FtpEstimate, PowerCalcInput, PowerCalcResult


def calculate_power(
    speed_ms: float,
    total_weight_kg: float,
    gradient_pct: float,
    cda: float = 0.35,
    crr: float = 0.005,
    rho: float = 1.225,
) -> float:
    """Calculate required power (watts) for a given speed on a gradient."""
    grade = gradient_pct / 100
    theta = math.atan(grade)

    p_gravity = total_weight_kg * 9.81 * math.sin(theta) * speed_ms
    p_rolling = crr * total_weight_kg * 9.81 * math.cos(theta) * speed_ms
    p_aero = 0.5 * cda * rho * speed_ms**3

    return max(0, p_gravity + p_rolling + p_aero)


def solve_speed_for_power(
    target_watts: float,
    total_weight_kg: float,
    gradient_pct: float,
    cda: float = 0.35,
    crr: float = 0.005,
    rho: float = 1.225,
) -> float:
    """Binary search for speed (m/s) that requires target_watts."""
    lo, hi = 0.1, 30.0  # m/s range

    for _ in range(50):  # Converges quickly
        mid = (lo + hi) / 2
        power = calculate_power(mid, total_weight_kg, gradient_pct, cda, crr, rho)
        if power < target_watts:
            lo = mid
        else:
            hi = mid

    return (lo + hi) / 2


def calculate_from_input(input: PowerCalcInput) -> PowerCalcResult:
    """Calculate power or time from input parameters."""
    total_weight = input.rider_weight_kg + input.bike_weight_kg
    length_m = input.length_km * 1000

    if input.target_time_s:
        # Given time, compute required power
        speed_ms = length_m / input.target_time_s
        watts = calculate_power(
            speed_ms, total_weight, input.gradient_pct,
            input.cda, input.crr, input.rho,
        )
        time_s = input.target_time_s
    elif input.target_watts:
        # Given power, compute time
        speed_ms = solve_speed_for_power(
            input.target_watts, total_weight, input.gradient_pct,
            input.cda, input.crr, input.rho,
        )
        watts = input.target_watts
        time_s = int(length_m / speed_ms) if speed_ms > 0 else 0
    else:
        raise ValueError("Spécifiez un temps cible ou une puissance cible.")

    speed_kmh = speed_ms * 3.6

    # VAM (vertical ascent meters per hour)
    elevation_gain = length_m * (input.gradient_pct / 100)
    vam = (elevation_gain / time_s * 3600) if time_s > 0 and elevation_gain > 0 else 0

    hours, remainder = divmod(time_s, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        time_fmt = f"{hours}h{minutes:02d}'{secs:02d}\""
    else:
        time_fmt = f"{minutes}'{secs:02d}\""

    return PowerCalcResult(
        gradient_pct=input.gradient_pct,
        length_km=input.length_km,
        total_weight_kg=total_weight,
        speed_kmh=round(speed_kmh, 1),
        power_watts=round(watts, 0),
        time_s=time_s,
        time_formatted=time_fmt,
        vam=round(vam, 0),
        watts_per_kg=round(watts / input.rider_weight_kg, 2) if input.rider_weight_kg > 0 else 0,
    )


async def estimate_ftp(db: AsyncSession, user_id: int) -> FtpEstimate | None:
    """Estimate FTP from Strava ride data with power."""
    result = await db.execute(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.sport_type.in_(["Ride", "VirtualRide"]),
            Activity.weighted_average_watts.is_not(None),
            Activity.weighted_average_watts > 0,
            Activity.moving_time >= 1200,  # At least 20 minutes
        )
        .order_by(Activity.weighted_average_watts.desc())
        .limit(10)
    )
    activities = result.scalars().all()

    if not activities:
        # Fallback: try average_watts
        result2 = await db.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.sport_type.in_(["Ride", "VirtualRide"]),
                Activity.average_watts.is_not(None),
                Activity.average_watts > 0,
                Activity.moving_time >= 1200,
            )
            .order_by(Activity.average_watts.desc())
            .limit(10)
        )
        activities = result2.scalars().all()
        if not activities:
            return None

    # Find best power effort
    best_activity = activities[0]
    best_power = best_activity.weighted_average_watts or best_activity.average_watts

    # Duration-based FTP estimation
    duration_min = best_activity.moving_time / 60

    if 18 <= duration_min <= 25:
        # Classic 20min test
        ftp = best_power * 0.95
        confidence = "Bonne"
    elif 40 <= duration_min <= 70:
        # ~1h effort is close to FTP
        ftp = best_power * 1.0
        confidence = "Bonne"
    elif duration_min > 70:
        # Long ride, power is below FTP
        ftp = best_power * 1.05
        confidence = "Estimée"
    else:
        # Short ride
        ftp = best_power * 0.90
        confidence = "Approximative"

    # Adjust confidence by number of rides
    if len(activities) < 3:
        confidence = "Approximative"

    return FtpEstimate(
        estimated_ftp=round(ftp, 0),
        best_20min_power=round(best_power, 0),
        activity_name=best_activity.name,
        activity_date=best_activity.start_date,
        confidence=confidence,
    )
