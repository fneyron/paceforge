from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OfficialConnection(Base):
    """An athlete's link through a provider's official program (app.connectors): the COROS Partner API or the
    Garmin Connect Developer Program. OAuth tokens Fernet-encrypted (app.crypto), the athlete's id at the provider
    (COROS openId, Garmin API user id: what its pushes name), the permissions granted, and how the last push or pull
    went. One link per athlete and provider, one PaceForge account per provider account."""

    __tablename__ = "official_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_official_connections_user_provider"),
        UniqueConstraint("provider", "provider_user_id", name="uq_official_connections_provider_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)  # "coros" | "garmin"
    provider_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)  # space-separated, as the provider says them
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # a pull (COROS)
    # set by the worker that takes a pull, so parallel workers don't all run it
    sync_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # plain French, shown as is

    def __repr__(self) -> str:
        return f"<OfficialConnection {self.provider} user={self.user_id} {self.status}>"
