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
_DEFAULT_GRADIENT_FACTORS = {
    -20: 1.4,
    -15: 1.1,
    -10: 0.85,
    -8: 0.78,
    -6: 0.75,
    -5: 0.77,
    -4: 0.80,
    -3: 0.85,
    -2: 0.90,
    -1: 0.95,
    0: 1.0,
    1: 1.06,
    2: 1.13,
    3: 1.21,
    4: 1.30,
    5: 1.40,
    6: 1.52,
    7: 1.65,
    8: 1.80,
    10: 2.10,
    12: 2.45,
    15: 3.00,
    20: 4.00,
    25: 5.20,
    30: 6.50,
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


def predict_course(
    course: CourseProfile,
    profile: AthleteGradientProfile,
    fatigue_factor: bool = True,
) -> CourseProfile:
    """Apply gradient-adjusted pace prediction to all segments."""
    cumulative_time = 0.0
    total_distance = course.total_distance_km

    for segment in course.segments:
        factor = _get_factor(profile, segment.avg_gradient_pct)

        # Fatigue factor for long races (>30km)
        if fatigue_factor and total_distance > 30:
            progress = segment.cumulative_distance_km / total_distance if total_distance > 0 else 0
            if progress > 0.8:
                factor *= 1.10
            elif progress > 0.6:
                factor *= 1.05

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


def compute_passage_times(
    course: CourseProfile,
    checkpoints: list[dict],
    target_time_s: int | None = None,
) -> list[dict]:
    """Compute passage times between checkpoints.

    Checkpoints are [{name, distance_km}]. Start (0km) and finish are added
    automatically. Returns a list of PassageTimeSection dicts.
    """
    from app.schemas.simulator import PassageTimeSection

    # Build full checkpoint list with start and finish
    all_cps = [{"name": "Depart", "distance_km": 0.0}]
    for cp in sorted(checkpoints, key=lambda c: c["distance_km"]):
        if cp["distance_km"] > 0 and cp["distance_km"] < course.total_distance_km:
            all_cps.append(cp)
    all_cps.append({"name": "Arrivee", "distance_km": course.total_distance_km})

    # Scale factor for target time
    scale = 1.0
    if target_time_s and course.predicted_total_time_s > 0:
        scale = target_time_s / course.predicted_total_time_s

    sections = []
    cumulative = 0.0
    adj_cumulative = 0.0

    for i in range(len(all_cps) - 1):
        start_km = all_cps[i]["distance_km"]
        end_km = all_cps[i + 1]["distance_km"]
        section_dist = end_km - start_km

        # Aggregate segments that fall within this section
        section_time = 0.0
        section_gain = 0.0
        section_loss = 0.0

        for seg in course.segments:
            # Skip segments entirely outside this section
            if seg.end_km <= start_km or seg.start_km >= end_km:
                continue

            # Compute overlap fraction
            overlap_start = max(seg.start_km, start_km)
            overlap_end = min(seg.end_km, end_km)
            seg_length = seg.end_km - seg.start_km
            if seg_length <= 0:
                continue
            fraction = (overlap_end - overlap_start) / seg_length

            section_time += seg.predicted_time_s * fraction
            section_gain += seg.elevation_gain * fraction
            section_loss += seg.elevation_loss * fraction

        cumulative += section_time
        pace = section_time / section_dist if section_dist > 0 else 0

        adjusted_time = section_time * scale
        adj_cumulative += adjusted_time

        sections.append(PassageTimeSection(
            start_name=all_cps[i]["name"],
            end_name=all_cps[i + 1]["name"],
            start_km=round(start_km, 1),
            end_km=round(end_km, 1),
            distance_km=round(section_dist, 1),
            elevation_gain=round(section_gain, 0),
            elevation_loss=round(section_loss, 0),
            predicted_time_s=round(section_time, 0),
            cumulative_time_s=round(cumulative, 0),
            predicted_pace_s_per_km=round(pace, 0),
            adjusted_time_s=round(adjusted_time, 0) if target_time_s else None,
            adjusted_cumulative_time_s=round(adj_cumulative, 0) if target_time_s else None,
        ).model_dump())

    return sections
