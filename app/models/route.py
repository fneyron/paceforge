from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
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
    race_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-23
    start_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-59
    sport_type: Mapped[str] = mapped_column(String(20), nullable=False, default="trail", server_default="trail")
    # Planned stop per aid station (minutes) — shifts clock passage times.
    stop_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Race-day state: the last real passage the athlete entered
    # ({"anchor_km", "anchor_clock": "HH:MM", "anchor_name"}); the plan is
    # re-planned from there. Persisted so a phone reload mid-race keeps it.
    live_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Sport-specific plan parameters (bike: target power, weights, CdA, Crr,
    # wind mode/override). Trail uses dedicated columns.
    params_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Cached weather payload (see services/weather.get_weather_forecast) so a saved
    # route restores its conditions without re-fetching on every page view.
    weather_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Per-race nutrition plan: {"targets": {...}, "items": [{"product_id", "per_hour"}]}.
    nutrition_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Actual race result matched to this route (Strava activity) for predicted-vs-
    # actual calibration: {"activity_id", "activity_name", "activity_date",
    # "total_actual_s", "actual": [{"name", "km", "time_s"}]}.
    result_activity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Reference finisher aligned on this route: {"label", "source", "total_s",
    # "points": [{"km", "time_s"}]} — their passage time next to yours at each CP.
    reference_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

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
    # Aid-station metadata (see services/checkpoints.py): none | water | full | base
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="none", server_default="none")
    crew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    drop_bag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Official cutoff as a clock time "HH:MM" (day rollover inferred from the start).
    cutoff_clock: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # Planned stop at this station in seconds; null = the default for its kind.
    stop_s: Mapped[int | None] = mapped_column(Integer, nullable=True)

    route = relationship("Route", back_populates="checkpoints")

    def as_dict(self) -> dict:
        return {
            "name": self.name, "distance_km": self.distance_km, "elevation": self.elevation,
            "kind": self.kind or "none", "crew": bool(self.crew), "drop_bag": bool(self.drop_bag),
            "cutoff_clock": self.cutoff_clock, "stop_s": self.stop_s,
        }


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
