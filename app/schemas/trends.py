from datetime import date, datetime

from pydantic import BaseModel


class WeeklyVolume(BaseModel):
    week_start: date
    distance_km: float
    duration_hours: float
    count: int
    sport_breakdown: dict[str, float]


class PaceTrend(BaseModel):
    week_start: date
    avg_pace_min_per_km: float
    sport_type: str


class PersonalRecord(BaseModel):
    distance_label: str
    elapsed_time: int
    pace_formatted: str
    activity_name: str
    activity_id: int
    date: datetime
