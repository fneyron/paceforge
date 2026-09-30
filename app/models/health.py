from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.activity import JSONType


class HealthSample(Base):
    """One raw Apple Health sample (HRV reading, sleep stage interval, weigh-in…).

    Kept so a later push or an export import can re-aggregate a day without
    double counting. Times are the athlete's LOCAL wall-clock time (naive):
    days and nights are cut on the clock the athlete lives by.
    """

    __tablename__ = "health_samples"
    __table_args__ = (
        # same sample sent twice (daily push overlap, export re-import) = one row
        UniqueConstraint("user_id", "metric", "start_at", "source", "kind", name="uq_health_samples_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(12), nullable=False)  # hrv, rhr, sleep, weight, vo2max
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="")  # sleep stage, else ""
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)  # sleep: minutes
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<HealthSample {self.metric}/{self.kind} {self.start_at} = {self.value}>"


class HealthMetric(Base):
    """One value per athlete, day and metric — what the fitness card reads."""

    __tablename__ = "health_metrics"
    __table_args__ = (
        # also the per-user date-range index
        UniqueConstraint("user_id", "date", "metric", name="uq_health_metrics_day"),
        Index("ix_health_metrics_user_metric_date", "user_id", "metric", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    metric: Mapped[str] = mapped_column(String(12), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # sleep: {"core": min, "deep": min, "rem": min, "awake": min, "in_bed": min, "bedtime": "23:10", "wake": "06:40"}
    details: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    n_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<HealthMetric {self.user_id} {self.date} {self.metric}={self.value}>"
