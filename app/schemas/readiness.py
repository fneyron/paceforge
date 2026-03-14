from pydantic import BaseModel


class RaceReadiness(BaseModel):
    score: int  # 0-100
    label: str  # "Excellent", "Bonne", "En progression", "Insuffisante"
    color: str  # CSS color class
    weeks_remaining: int
    days_remaining: int
    longest_recent_km: float
    weekly_avg_km: float
    weekly_target_km: float
    consistency_pct: float  # % weeks with 3+ sessions
    recommendations: list[str]
