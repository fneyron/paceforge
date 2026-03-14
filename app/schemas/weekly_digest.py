from datetime import date, datetime

from pydantic import BaseModel, Field


class ClaudeWeeklyDigestOutput(BaseModel):
    """Schema for the structured JSON response from Claude for weekly digest."""

    summary: str = Field(..., description="3-4 sentence overview of the week")
    highlights: list[str] = Field(..., description="2-4 notable achievements")
    recommendations: list[str] = Field(..., description="2-3 suggestions for next week")
    volume_assessment: str = Field(
        ..., description="Assessment of volume trend (increased/decreased/stable)"
    )


class WeeklyDigestResponse(BaseModel):
    id: int
    week_start: date
    week_end: date
    summary: str
    highlights: list[str]
    recommendations: list[str]
    volume_assessment: str | None = None
    training_load_summary: dict | None = None
    model_used: str
    created_at: datetime

    model_config = {"from_attributes": True}
