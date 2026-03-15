from pydantic import BaseModel, Field


class PlanSession(BaseModel):
    day: str
    title: str
    description: str
    type: str = "endurance"


class PlanWeek(BaseModel):
    week_number: int
    theme: str
    volume_target_km: float = 0
    intensity: str = "modérée"
    sessions: list[PlanSession]
    coach_note: str = ""


class ClaudeTrainingPlanOutput(BaseModel):
    plan_title: str
    duration_weeks: int
    goal_summary: str
    philosophy: str = ""
    weeks: list[PlanWeek]
    key_sessions_explanation: str = ""
    coach_advice: str = ""
