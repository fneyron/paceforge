from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.activity import JSONType


class WeeklyDigest(Base):
    __tablename__ = "weekly_digests"
    __table_args__ = (
        UniqueConstraint("user_id", "week_start", name="uq_user_week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    highlights: Mapped[list] = mapped_column(JSONType, nullable=False)
    recommendations: Mapped[list] = mapped_column(JSONType, nullable=False)
    volume_assessment: Mapped[str | None] = mapped_column(Text, nullable=True)
    training_load_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user = relationship("User", lazy="selectin")

    def __repr__(self) -> str:
        return f"<WeeklyDigest {self.id} user={self.user_id} week={self.week_start}>"
