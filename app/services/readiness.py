import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.schemas.readiness import RaceReadiness

logger = logging.getLogger(__name__)

# Recommended weekly volume as % of race distance (simplified model)
VOLUME_MULTIPLIERS = {
    5: 4.0,       # 5k → ~20 km/week
    10: 3.5,      # 10k → ~35 km/week
    21.1: 2.5,    # Half → ~53 km/week
    42.2: 2.0,    # Marathon → ~84 km/week
    50: 1.5,      # Ultra 50k → ~75 km/week
    80: 1.2,      # Ultra 80k
    100: 1.0,     # Ultra 100k
    160: 0.7,     # Ultra 100mi
}


def _get_recommended_weekly_km(race_distance_km: float) -> float:
    """Estimate recommended weekly volume based on race distance."""
    # Find the closest reference distance
    sorted_dists = sorted(VOLUME_MULTIPLIERS.keys())
    for i, d in enumerate(sorted_dists):
        if race_distance_km <= d:
            multiplier = VOLUME_MULTIPLIERS[d]
            return race_distance_km * multiplier
        if i == len(sorted_dists) - 1:
            return race_distance_km * VOLUME_MULTIPLIERS[d]
    return race_distance_km * 2.0


async def calculate_race_readiness(
    db: AsyncSession,
    user_id: int,
    race_date: datetime,
    race_distance_km: float,
) -> RaceReadiness:
    """Calculate a race readiness score (0-100)."""
    now = datetime.now(timezone.utc)
    days_remaining = max(0, (race_date - now).days)
    weeks_remaining = max(0, days_remaining // 7)

    # Calculate weekly averages over last 4 weeks
    date_4w = now - timedelta(weeks=4)
    result_4w = await db.execute(
        select(
            func.coalesce(func.sum(Activity.distance), 0).label("total_distance"),
            func.coalesce(func.sum(Activity.moving_time), 0).label("total_time"),
            func.count(Activity.id).label("count"),
        )
        .where(
            Activity.user_id == user_id,
            Activity.start_date >= date_4w,
        )
    )
    row_4w = result_4w.one()
    weekly_avg_km = round(row_4w.total_distance / 1000 / 4, 1)
    weekly_avg_sessions = row_4w.count / 4

    # Longest single activity in last 4 weeks
    result_longest = await db.execute(
        select(func.max(Activity.distance))
        .where(
            Activity.user_id == user_id,
            Activity.start_date >= date_4w,
        )
    )
    longest_m = result_longest.scalar() or 0
    longest_recent_km = round(longest_m / 1000, 1)

    # Consistency: how many of the last 4 weeks had 3+ sessions
    consistency_weeks = 0
    for week_offset in range(4):
        week_start = now - timedelta(weeks=week_offset + 1)
        week_end = now - timedelta(weeks=week_offset)
        result_week = await db.execute(
            select(func.count(Activity.id))
            .where(
                Activity.user_id == user_id,
                Activity.start_date >= week_start,
                Activity.start_date < week_end,
            )
        )
        if result_week.scalar() >= 3:
            consistency_weeks += 1
    consistency_pct = round(consistency_weeks / 4 * 100)

    # Calculate score components
    target_weekly_km = _get_recommended_weekly_km(race_distance_km)

    # Volume score (0-35): how close is weekly average to target
    volume_ratio = min(weekly_avg_km / target_weekly_km, 1.5) if target_weekly_km > 0 else 0
    volume_score = min(35, int(volume_ratio * 35))

    # Long run score (0-25): longest run vs race distance
    long_run_target = min(race_distance_km * 0.75, race_distance_km - 5)
    long_run_ratio = min(longest_recent_km / long_run_target, 1.0) if long_run_target > 0 else 0
    long_run_score = int(long_run_ratio * 25)

    # Consistency score (0-25)
    consistency_score = int(consistency_pct * 0.25)

    # Time score (0-15): enough weeks to prepare
    ideal_weeks = max(8, int(race_distance_km / 5))
    time_ratio = min(weeks_remaining / ideal_weeks, 1.0) if ideal_weeks > 0 else 1.0
    # Both too little and too much time is fine, penalize only if race is very soon
    if weeks_remaining < 2:
        time_score = 5
    elif weeks_remaining < 4:
        time_score = 10
    else:
        time_score = 15

    total_score = min(100, volume_score + long_run_score + consistency_score + time_score)

    # Label and color
    if total_score >= 80:
        label = "Excellent"
        color = "text-green-600"
    elif total_score >= 60:
        label = "Bonne"
        color = "text-blue-600"
    elif total_score >= 40:
        label = "En progression"
        color = "text-amber-600"
    else:
        label = "Insuffisante"
        color = "text-red-600"

    # Recommendations
    recommendations = []
    if volume_ratio < 0.7:
        recommendations.append(
            f"Augmentez progressivement votre volume hebdomadaire vers {target_weekly_km:.0f} km/sem"
        )
    if long_run_ratio < 0.6:
        recommendations.append(
            f"Visez une sortie longue de {long_run_target:.0f} km dans les prochaines semaines"
        )
    if consistency_pct < 75:
        recommendations.append("Améliorez la régularité : visez au moins 3 séances par semaine")
    if weeks_remaining < 3 and total_score < 60:
        recommendations.append("Concentrez-vous sur la récupération et la confiance avant la course")
    if not recommendations:
        recommendations.append("Continuez sur cette lancée, votre préparation est en bonne voie !")

    return RaceReadiness(
        score=total_score,
        label=label,
        color=color,
        weeks_remaining=weeks_remaining,
        days_remaining=days_remaining,
        longest_recent_km=longest_recent_km,
        weekly_avg_km=weekly_avg_km,
        weekly_target_km=round(target_weekly_km, 1),
        consistency_pct=consistency_pct,
        recommendations=recommendations,
    )
