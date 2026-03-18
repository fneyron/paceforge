from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

JSONType = JSON().with_variant(JSONB, "postgresql")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("activities.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )

    # Coaching output
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    strengths: Mapped[list] = mapped_column(JSONType, nullable=False)
    improvements: Mapped[list] = mapped_column(JSONType, nullable=False)
    next_workout_tip: Mapped[str] = mapped_column(Text, nullable=False)
    strava_comment: Mapped[str] = mapped_column(Text, nullable=False)
    fatigue_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Training load snapshot at analysis time
    training_load_7d_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    training_load_7d_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    training_load_7d_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    training_load_28d_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    training_load_28d_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    training_load_28d_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Claude metadata
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)

    # Comment posting
    comment_posted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    comment_posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # User feedback
    user_rpe: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="Rate of Perceived Exertion 1-10"
    )
    user_rating: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="Analysis quality rating: -1, 0, 1"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    activity = relationship("Activity", back_populates="analysis")

    def __repr__(self) -> str:
        return f"<Analysis {self.id} activity={self.activity_id}>"
