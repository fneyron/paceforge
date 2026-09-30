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


class OAuthClient(Base):
    """A client PaceForge registered itself with on an authorization server
    (dynamic registration), one per server and redirect URI — shared by every
    athlete, so connecting never registers a client per person."""

    __tablename__ = "oauth_clients"
    __table_args__ = (UniqueConstraint("issuer", "redirect_uri", name="uq_oauth_clients_issuer_redirect"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<OAuthClient {self.issuer} {self.client_id}>"


class CorosConnection(Base):
    """An athlete's COROS link: OAuth tokens (Fernet-encrypted) for the COROS
    MCP server, where it lives (the region is discovered at connect time) and
    how the last sync went."""

    __tablename__ = "coros_connections"
    __table_args__ = (UniqueConstraint("user_id", name="uq_coros_connections_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resource_url: Mapped[str] = mapped_column(String(500), nullable=False)  # also the MCP endpoint
    token_endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    revocation_endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
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
        return f"<CorosConnection user={self.user_id} reauth={self.needs_reauth}>"
