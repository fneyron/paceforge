from typing import Any

from pydantic import BaseModel, Field


class StravaWebhookEvent(BaseModel):
    object_type: str
    object_id: int
    aspect_type: str
    owner_id: int
    subscription_id: int
    event_time: int
    updates: dict[str, Any] = Field(default_factory=dict)


class StravaWebhookValidation(BaseModel):
    hub_mode: str = Field(alias="hub.mode")
    hub_challenge: str = Field(alias="hub.challenge")
    hub_verify_token: str = Field(alias="hub.verify_token")


class StravaTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: int
    token_type: str = "Bearer"


class StravaAthleteResponse(BaseModel):
    id: int
    firstname: str | None = None
    lastname: str | None = None
    profile: str | None = None
