from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Use JSONB on PostgreSQL, JSON on other backends (SQLite for tests)
JSONType = JSON().with_variant(JSONB, "postgresql")

from app.database import Base


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_user_start_date", "user_id", "start_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strava_activity_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sport_type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Core metrics
    distance: Mapped[float] = mapped_column(Float, nullable=False, doc="Distance in meters")
    moving_time: Mapped[int] = mapped_column(Integer, nullable=False, doc="Moving time in seconds")
    elapsed_time: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="Elapsed time in seconds"
    )
    total_elevation_gain: Mapped[float] = mapped_column(Float, default=0.0)

    # Speed
    average_speed: Mapped[float | None] = mapped_column(Float, nullable=True, doc="m/s")
    max_speed: Mapped[float | None] = mapped_column(Float, nullable=True, doc="m/s")

    # Heart rate
    average_heartrate: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_heartrate: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Cadence
    average_cadence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Power (cycling)
    average_watts: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_watts: Mapped[float | None] = mapped_column(Float, nullable=True)
    weighted_average_watts: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Other
    suffer_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calories: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Structured data
    laps: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    splits_metric: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    best_efforts: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSONType, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user = relationship("User", back_populates="activities")
    analysis = relationship(
        "Analysis", back_populates="activity", uselist=False, lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Activity {self.id} strava={self.strava_activity_id} {self.sport_type}>"
