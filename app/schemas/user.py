from datetime import datetime

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: int
    strava_athlete_id: int
    firstname: str | None = None
    lastname: str | None = None
    profile_picture_url: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
