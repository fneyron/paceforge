from datetime import datetime

from pydantic import BaseModel, Field


class ClaudeCoachingOutput(BaseModel):
    """Schema for the structured JSON response from Claude."""

    summary: str = Field(..., description="2-3 sentence overview of the session")
    strengths: list[str] = Field(..., description="1-3 positive observations")
    improvements: list[str] = Field(..., description="1-3 areas for improvement")
    next_workout_tip: str = Field(..., description="Specific actionable suggestion")
    strava_comment: str = Field(..., max_length=500, description="Short Strava comment")
    fatigue_note: str | None = Field(
        None, description="Fatigue assessment based on training load"
    )


class TrainingLoadResult(BaseModel):
    volume_7d_km: float = 0.0
    volume_7d_hours: float = 0.0
    count_7d: int = 0
    volume_28d_km: float = 0.0
    volume_28d_hours: float = 0.0
    count_28d: int = 0
    sport_breakdown_7d: dict[str, float] = Field(default_factory=dict)


class AnalysisResponse(BaseModel):
    id: int
    activity_id: int
    summary: str
    strengths: list[str]
    improvements: list[str]
    next_workout_tip: str
    strava_comment: str
    fatigue_note: str | None = None
    training_load_7d_km: float | None = None
    training_load_7d_hours: float | None = None
    training_load_7d_count: int | None = None
    training_load_28d_km: float | None = None
    training_load_28d_hours: float | None = None
    training_load_28d_count: int | None = None
    model_used: str
    comment_posted: bool = False
    comment_posted_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
