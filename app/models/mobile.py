"""Phone access is restricted to complementary daily measurements."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.activity import JSONType


class MobileDevice(Base):
    __tablename__ = "mobile_devices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(8))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    challenge: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MobileDaily(Base):
    __tablename__ = "mobile_daily"
    __table_args__ = (UniqueConstraint("user_id", "platform", "date", "metric", name="uq_mobile_daily"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(8))
    date: Mapped[date] = mapped_column(Date)
    metric: Mapped[str] = mapped_column(String(12))
    value: Mapped[float] = mapped_column(Float)
    sources: Mapped[list] = mapped_column(JSONType)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
