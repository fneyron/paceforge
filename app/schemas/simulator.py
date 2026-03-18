from datetime import datetime

from pydantic import BaseModel


class GpxPoint(BaseModel):
    lat: float
    lon: float
    elevation: float
    distance_from_start: float  # cumulative meters


class CourseSegment(BaseModel):
    index: int
    start_km: float
    end_km: float
    distance_m: float
    elevation_gain: float
    elevation_loss: float
    avg_gradient_pct: float
    min_elevation: float
    max_elevation: float
    predicted_pace_s_per_km: float = 0
    predicted_time_s: float = 0
    cumulative_time_s: float = 0
    cumulative_distance_km: float = 0


class CourseProfile(BaseModel):
    name: str = "Course"
    total_distance_km: float
    total_elevation_gain: float
    total_elevation_loss: float
    segments: list[CourseSegment]
    elevation_points: list[dict]  # {"distance_km": float, "elevation": float}
    route_coords: list[list[float]] = []  # [[lat, lon, distance_km], ...] for map + hover sync
    km_markers: list[dict] = []  # [{"km": int, "lat": float, "lon": float, "elevation": float}]
    predicted_total_time_s: int = 0
    predicted_total_time_formatted: str = ""


class AthleteGradientProfile(BaseModel):
    flat_pace_s_per_km: float
    gradient_factors: dict[int, float]  # gradient% -> pace multiplier
    data_points: int
    sport_types_used: list[str]


class PowerCalcInput(BaseModel):
    gradient_pct: float
    length_km: float
    rider_weight_kg: float
    bike_weight_kg: float = 9.0
    target_time_s: int | None = None
    target_watts: float | None = None
    cda: float = 0.35
    crr: float = 0.005
    rho: float = 1.225


class PowerCalcResult(BaseModel):
    gradient_pct: float
    length_km: float
    total_weight_kg: float
    speed_kmh: float
    power_watts: float
    time_s: int
    time_formatted: str
    vam: float  # vertical ascent m/h
    watts_per_kg: float


class FtpEstimate(BaseModel):
    estimated_ftp: float
    best_20min_power: float
    activity_name: str
    activity_date: datetime | None = None
    confidence: str


class ClaudeRaceStrategyOutput(BaseModel):
    race_summary: str
    key_challenges: list[str]
    pacing_strategy: list[str]
    nutrition_plan: list[str]
    mental_tips: list[str]
    coach_note: str


class CheckpointInput(BaseModel):
    name: str
    distance_km: float


class PassageTimeSection(BaseModel):
    start_name: str
    end_name: str
    start_km: float
    end_km: float
    distance_km: float
    elevation_gain: float
    elevation_loss: float
    predicted_time_s: float
    cumulative_time_s: float
    predicted_pace_s_per_km: float
    adjusted_time_s: float | None = None
    adjusted_cumulative_time_s: float | None = None
