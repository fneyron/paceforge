from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.activity import JSONType


class LactateTest(Base):
    __tablename__ = 'lactate_tests'
    __table_args__ = (Index('ix_lactate_tests_user_day', 'user_id', 'tested_on'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    tested_on: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)  # pace/speed = running, power = cycling
    device: Mapped[str] = mapped_column(String(80), nullable=False)
    protocol: Mapped[str] = mapped_column(String(160), nullable=False)
    notes: Mapped[str] = mapped_column(String(1000), nullable=False, default='')
    stages: Mapped[list] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
