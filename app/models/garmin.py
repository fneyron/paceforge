from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GarminConnection(Base):
    """An athlete's Garmin Connect link: the DI OAuth tokens (Fernet-encrypted)
    the login gave, which Garmin server (garmin.com or garmin.cn) and how the
    last sync went. The athlete's password is never stored."""

    __tablename__ = "garmin_connections"
    __table_args__ = (UniqueConstraint("user_id", name="uq_garmin_connections_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(32), nullable=False, default="garmin.com")
    di_client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # the profile name some endpoints take in their path (read on the first sync)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # set by the worker that takes a sync, so parallel workers don't all run it
    sync_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # plain French, shown as is
    needs_reauth: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    def __repr__(self) -> str:
        return f"<GarminConnection user={self.user_id} reauth={self.needs_reauth}>"
