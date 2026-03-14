from datetime import datetime
from typing import Any

from pydantic import BaseModel, computed_field


class ActivitySummary(BaseModel):
    id: int
    strava_activity_id: int
    sport_type: str
    name: str
    start_date: datetime
    distance: float
    moving_time: int
    average_speed: float | None = None
    average_heartrate: float | None = None
    total_elevation_gain: float = 0.0
    has_analysis: bool = False

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def distance_km(self) -> float:
        return round(self.distance / 1000, 2)

    @computed_field
    @property
    def duration_formatted(self) -> str:
        hours, remainder = divmod(self.moving_time, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h{minutes:02d}'{seconds:02d}\""
        return f"{minutes}'{seconds:02d}\""

    @computed_field
    @property
    def pace_formatted(self) -> str | None:
        if not self.average_speed or self.average_speed == 0:
            return None
        if self.sport_type in ("Ride", "VirtualRide", "EBikeRide"):
            return f"{self.average_speed * 3.6:.1f} km/h"
        if self.sport_type in ("Swim",):
            pace_per_100m = 100 / self.average_speed
            mins = int(pace_per_100m // 60)
            secs = int(pace_per_100m % 60)
            return f"{mins}:{secs:02d}/100m"
        pace_per_km = 1000 / self.average_speed
        mins = int(pace_per_km // 60)
        secs = int(pace_per_km % 60)
        return f"{mins}:{secs:02d}/km"


class ActivityDetail(ActivitySummary):
    elapsed_time: int
    max_speed: float | None = None
    max_heartrate: float | None = None
    average_cadence: float | None = None
    average_watts: float | None = None
    max_watts: float | None = None
    weighted_average_watts: float | None = None
    suffer_score: int | None = None
    calories: float | None = None
    laps: list[dict[str, Any]] | None = None
    splits_metric: list[dict[str, Any]] | None = None
    best_efforts: list[dict[str, Any]] | None = None
