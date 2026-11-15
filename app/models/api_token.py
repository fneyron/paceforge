"""Personal API keys (Réglages › Accès API): read-only access to the athlete's own data, in JSON (/api/v1).

A key is ``pf_`` + 43 random URL-safe characters, shown once at its creation; only its SHA-256 is stored (a lookup
hashes what it receives), with its first 8 characters to tell keys apart. ``scopes``: « read » (plans, passage
times, activities), « read health » once the athlete also opened the health data to it (only with the consent to
health data, checked again at every call). Revoked keys stay a while (refused with their own message), then the
hourly upkeep deletes them (app.tasks.oauth_cleanup); the account's deletion takes them with it (cascade).
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    prefix: Mapped[str] = mapped_column(String(8))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[str] = mapped_column(String(40), default="read", server_default="read")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # written at most every few minutes (api_v1.SEEN_EVERY), never on each call
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def scope_list(self) -> list[str]:
        return (self.scopes or "read").split()

    @property
    def health(self) -> bool:
        return "health" in self.scope_list
