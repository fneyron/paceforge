from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.activity import JSONType


class GeneratedPlan(Base):
    __tablename__ = "generated_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "workout" or "training_plan"
    sport: Mapped[str] = mapped_column(String(50), nullable=False)
    goal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
