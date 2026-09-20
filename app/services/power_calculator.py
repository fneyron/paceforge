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


def mean_max_power(streams: dict, window_s: int) -> float | None:
    """Best average power over any ``window_s`` seconds of a ride's streams.

    Strava streams are time-indexed (seconds since start) with gaps at pauses;
    the ride is resampled to 1 s on a prefix sum so windows are exact.
    """
    watts = streams.get("watts") or []
    times = streams.get("time") or []
    n = min(len(watts), len(times))
    if n < 2 or not times[n - 1] or times[n - 1] < window_s:
        return None
    total = int(times[n - 1]) + 1
    if total > 48 * 3600:
        return None
    series = [0.0] * total
    for i in range(1, n):
        t0, t1 = int(times[i - 1]), int(times[i])
        if t1 <= t0 or t1 - t0 > 30:  # pause: no power produced
            continue
        w = float(watts[i] or 0)
        for t in range(t0, min(t1, total)):
            series[t] = w
    prefix = [0.0]
    for v in series:
        prefix.append(prefix[-1] + v)
    best = 0.0
    for t in range(window_s, total + 1):
        avg = (prefix[t] - prefix[t - window_s]) / window_s
        if avg > best:
            best = avg
    return round(best) if best > 0 else None


def ftp_from_mean_max(p5: float | None, p20: float | None, p60: float | None) -> tuple[float | None, str]:
    """FTP candidates from a ride's mean-max curve; returns (ftp, method).

    - 60 min: FTP by definition;
    - 20 min: the classic 95 % (Coggan);
    - Monod critical power from the 5 and 20 min points — CP is a good FTP
      proxy and is robust when the rider never did a clean 20 min effort.
    The estimate keeps the highest of the three: FTP is what you CAN hold,
    and a sub-maximal 60 min ride must not drag it down.
    """
    cands = []
    if p60:
        cands.append((p60, "60 min"))
    if p20:
        cands.append((p20 * 0.95, "20 min × 0,95"))
    if p5 and p20 and p5 > p20:
        cp = (p20 * 1200 - p5 * 300) / (1200 - 300)
        if cp > 0:
            cands.append((cp, "puissance critique 5–20 min"))
    if not cands:
        return None, ""
    return max(cands, key=lambda c: c[0])


async def estimate_ftp(db: AsyncSession, user_id: int) -> FtpEstimate | None:
    """Estimate FTP from Strava rides with power.

    Two tiers:
    1. Rides with second-by-second streams → mean-max 5 / 20 / 60 min, FTP as
       the best of 60 min, 95 % of 20 min and the 5–20 min critical power
       (intervals.icu-style eFTP), over the last 120 days.
    2. Otherwise (summary data only) → the best normalized power of the last
       12 months scaled by ride duration, averaged over the top 3 rides so a
       single freak value does not set the number.
    Falls back to all-time data and flags the estimate as stale when the
    reference ride is older than 6 months.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    def _age_days(a: Activity) -> int:
        sd = a.start_date
        if sd is not None and sd.tzinfo is None:
            sd = sd.replace(tzinfo=timezone.utc)
        return (now - sd).days if sd else 0

    # ── tier 1: streams ──
    q = (
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.sport_type.in_(["Ride", "VirtualRide", "GravelRide"]),
            Activity.streams_data.is_not(None),
            Activity.start_date >= now - timedelta(days=120),
            Activity.moving_time >= 1200,
        )
        .order_by(Activity.start_date.desc())
        .limit(60)
    )
    rides = (await db.execute(q)).scalars().all()
    best = None
    n_with_power = 0
    for a in rides:
        st = a.streams_data or {}
        if not st.get("watts"):
            continue
        n_with_power += 1
        p5, p20, p60 = mean_max_power(st, 300), mean_max_power(st, 1200), mean_max_power(st, 3600)
        ftp, method = ftp_from_mean_max(p5, p20, p60)
        if ftp and (best is None or ftp > best[0]):
            best = (ftp, method, a, p20 or p60 or p5)
    if best:
        ftp, method, a, ref_power = best
        confidence = "Bonne" if n_with_power >= 3 else "Moyenne"
        return FtpEstimate(
            estimated_ftp=round(ftp, 0), best_20min_power=round(ref_power or ftp, 0),
            activity_name=a.name, activity_date=a.start_date, confidence=confidence,
            is_stale=False, age_months=max(0, _age_days(a) // 30), method=method, rides_used=n_with_power,
        )

    # ── tier 2: summary data (NP / average power by duration) ──
    activities = []
    for since in (now - timedelta(days=365), None):
        for col in (Activity.weighted_average_watts, Activity.average_watts):
            q = (
                select(Activity)
                .where(
                    Activity.user_id == user_id,
                    Activity.sport_type.in_(["Ride", "VirtualRide", "GravelRide"]),
                    col.is_not(None),
                    col > 0,
                    Activity.moving_time >= 1200,  # At least 20 minutes
                )
                .order_by(col.desc())
                .limit(10)
            )
            if since is not None:
                q = q.where(Activity.start_date >= since)
            activities = (await db.execute(q)).scalars().all()
            if activities:
                break
        if activities:
            break
    if not activities:
        return None

    def _scaled(a: Activity) -> float:
        power = a.weighted_average_watts or a.average_watts or 0
        minutes = a.moving_time / 60
        if 18 <= minutes <= 25:
            return power * 0.95
        if 40 <= minutes <= 70:
            return power * 1.0
        if minutes > 70:
            return power * 1.05
        return power * 0.90

    top = sorted((_scaled(a) for a in activities), reverse=True)[:3]
    ftp = sum(top) / len(top)
    best_activity = activities[0]
    age_days = _age_days(best_activity)
    confidence = "Moyenne" if len(activities) >= 3 else "Faible"
    return FtpEstimate(
        estimated_ftp=round(ftp, 0),
        best_20min_power=round(best_activity.weighted_average_watts or best_activity.average_watts or 0, 0),
        activity_name=best_activity.name,
        activity_date=best_activity.start_date,
        confidence=confidence,
        is_stale=age_days > 180,
        age_months=max(0, age_days // 30),
        method="puissance normalisée des meilleures sorties",
        rides_used=len(activities),
    )
