from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OAuthAttempt(Base):
    """Short-lived, single-use account link, with credentials kept server-side."""

    __tablename__ = "oauth_attempts"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credentials: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
