"""Activity hygiene: sport grouping, duplicate detection, false starts.

Double recordings (watch + phone both uploaded to Strava) are common. They
clutter the history AND double-weight the athlete's gradient/pace profile used
by the race predictor, so the same rule must apply in both places.
"""

from __future__ import annotations

from collections.abc import Iterable

SPORT_GROUPS: dict[str, list[str]] = {
    "run": ["Run", "VirtualRun"],
    "trail": ["TrailRun"],
    "bike": ["Ride", "VirtualRide", "EBikeRide", "GravelRide", "MountainBikeRide"],
}
GROUP_LABELS = {"run": "Course", "trail": "Trail", "bike": "Vélo", "other": "Autre"}

# Two recordings of the same outing: starts within this window and distances
# that agree within this tolerance (GPS on a phone vs a watch differ a bit).
_DUP_WINDOW_S = 180
_DUP_DISTANCE_TOL = 0.15

# Tiny recordings (pressed start, stopped right away).
_FALSE_START_MAX_M = 1000
_FALSE_START_MAX_S = 300


def sport_group(sport_type: str | None) -> str:
    for group, types in SPORT_GROUPS.items():
        if sport_type in types:
            return group
    return "other"


def _same_family(a: str, b: str) -> bool:
    """Run and trail are the same family — a watch may tag one as the other."""
    ga, gb = sport_group(a), sport_group(b)
    foot = {"run", "trail"}
    return ga == gb or (ga in foot and gb in foot)


def is_false_start(activity) -> bool:
    return (activity.distance or 0) < _FALSE_START_MAX_M and (activity.moving_time or 0) < _FALSE_START_MAX_S


def _richness(activity) -> tuple:
    """Which of two duplicates to keep: the one with splits, then the longer one."""
    has_splits = 1 if getattr(activity, "splits_metric", None) else 0
    return (has_splits, activity.moving_time or 0, -activity.id)


def find_duplicate_ids(activities: Iterable) -> set[int]:
    """Return ids of activities that duplicate a richer one (same outing)."""
    acts = sorted(
        (a for a in activities if a.start_date is not None),
        key=lambda a: a.start_date,
    )
    duplicates: set[int] = set()
    for i, a in enumerate(acts):
        if a.id in duplicates:
            continue
        for b in acts[i + 1:]:
            gap = (b.start_date - a.start_date).total_seconds()
            if gap > _DUP_WINDOW_S:
                break
            if b.id in duplicates or not _same_family(a.sport_type, b.sport_type):
                continue
            da, db_ = a.distance or 0, b.distance or 0
            if max(da, db_) <= 0:
                continue
            if abs(da - db_) / max(da, db_) > _DUP_DISTANCE_TOL:
                continue
            loser = a if _richness(a) < _richness(b) else b
            duplicates.add(loser.id)
            if loser is a:
                break
    return duplicates
