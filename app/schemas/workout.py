from pydantic import BaseModel, Field


class WorkoutGenerationRequest(BaseModel):
    sport: str = Field(..., description="Sport type: running, trail, cycling, swimming")
    goal: str | None = Field(None, description="Optional specific goal")


class ClaudeWorkoutOutput(BaseModel):
    """Schema for the structured JSON response from Claude for workout generation."""

    session_title: str = Field(..., description="Title of the workout session")
    sport: str = Field(..., description="Sport type")
    goal: str = Field(..., description="Physiological goal explanation")
    estimated_duration: str = Field(..., description="Estimated duration")
    warmup: list[str] = Field(..., description="Warmup steps")
    main_set: list[str] = Field(..., description="Main workout blocks")
    cooldown: list[str] = Field(..., description="Cooldown steps")
    execution_tips: list[str] = Field(..., description="Execution tips")
    coach_note: str = Field(..., description="Motivational coach note")
