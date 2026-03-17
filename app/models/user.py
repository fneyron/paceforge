from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.activity import JSONType


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Email/password auth
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    email_verify_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    password_reset_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    password_reset_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Strava account link
    strava_athlete_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, index=True, nullable=True
    )
    strava_access_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    strava_refresh_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    strava_token_expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Per-user Strava API app credentials
    strava_client_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    strava_client_secret_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    strava_webhook_subscription_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    strava_credentials_valid: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    last_activity_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    firstname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lastname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    profile_picture_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Sync
    initial_sync_done: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Settings
    auto_post_comments: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    preferred_sports: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    weekly_volume_target_km: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Physical
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Race goal
    race_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    race_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    race_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    activities = relationship("Activity", back_populates="user", lazy="noload")

    @property
    def strava_client_secret(self) -> str | None:
        if not self.strava_client_secret_encrypted:
            return None
        from app.crypto import decrypt_secret
        return decrypt_secret(self.strava_client_secret_encrypted)

    @property
    def has_own_strava_app(self) -> bool:
        return bool(self.strava_client_id and self.strava_client_secret_encrypted)

    @property
    def has_strava_linked(self) -> bool:
        return bool(self.strava_athlete_id and self.strava_access_token)

    def __repr__(self) -> str:
        return f"<User {self.id} email={self.email}>"
