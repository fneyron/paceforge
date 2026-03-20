from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.activity import JSONType


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    total_distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    total_elevation_gain: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    total_elevation_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    course_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    target_time_s: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    checkpoints = relationship("RouteCheckpoint", back_populates="route", cascade="all, delete-orphan", order_by="RouteCheckpoint.distance_km")
    simulations = relationship("Simulation", back_populates="route", cascade="all, delete-orphan")


class RouteCheckpoint(Base):
    __tablename__ = "route_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    route_id: Mapped[int] = mapped_column(Integer, ForeignKey("routes.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    elevation: Mapped[float | None] = mapped_column(Float, nullable=True)

    route = relationship("Route", back_populates="checkpoints")


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    route_id: Mapped[int] = mapped_column(Integer, ForeignKey("routes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_time_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    results_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    weather_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    route = relationship("Route", back_populates="simulations")
