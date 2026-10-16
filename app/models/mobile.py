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
    # sliding expiry: renewed on use; a rotated token stays valid a few days as prev_token_hash
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prev_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    prev_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MobileDaily(Base):
    __tablename__ = "mobile_daily"
    # uq_mobile_daily (user_id first) is also the per-athlete index
    __table_args__ = (UniqueConstraint("user_id", "platform", "date", "metric", name="uq_mobile_daily"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(8))
    date: Mapped[date] = mapped_column(Date)
    metric: Mapped[str] = mapped_column(String(12))
    value: Mapped[float] = mapped_column(Float)
    sources: Mapped[list] = mapped_column(JSONType)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # the phone that sent the row: the same phone may correct its own day (a weigh-in deleted in Santé); the row
    # outlives the phone link (unlinked: nobody's phone, the day stays)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("mobile_devices.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
