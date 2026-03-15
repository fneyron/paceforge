from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.activity import JSONType


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strava_athlete_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    strava_access_token: Mapped[str] = mapped_column(String(512), nullable=False)
    strava_refresh_token: Mapped[str] = mapped_column(String(512), nullable=False)
    strava_token_expires_at: Mapped[int] = mapped_column(Integer, nullable=False)

    firstname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lastname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    profile_picture_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Sync
    initial_sync_done: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Settings
    auto_post_comments: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    preferred_sports: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    weekly_volume_target_km: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Physical
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Race goal
    race_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    race_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    race_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    activities = relationship("Activity", back_populates="user", lazy="noload")

    def __repr__(self) -> str:
        return f"<User {self.id} strava={self.strava_athlete_id}>"
