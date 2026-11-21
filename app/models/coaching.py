"""Coaching (beta, docs/coaching.md): an athlete's plan, its sessions, and who follows it as a coach.

- coaching_plans: one per athlete. The goal (a Route with a race date, or none: « Progresser »), the weekdays
  available, the long run's weekday, the starting weekly minutes, the Monday the progression counts from, what the
  ressenti changed week by week (`adaptations`: {Monday ISO: {"factor", "reason", …}}, kept so that a past week
  still says why it was lighter), what the plan last read of its race (`goal_state`: the race page's objective and
  estimate, kept for the day; or, once the race is deleted or behind, why the plan went on without it and from
  which Monday) and the calendar feed's token (encrypted, so its owner can read the link again; looked up by its
  SHA-256).
- coaching_sessions: one session on one local day. `auto_day` is the day the generator gave it (None for a session a
  person added): a moved session keeps it, so a regeneration never fills the day it left. `pinned`: a person edited
  it, a regeneration never rewrites it. `seq` counts its changes (the calendar feed's SEQUENCE).
- coaching_links: athlete → coach, made by a single-use code (coaching_invites, stored hashed, 7 days).
No pain, injury or illness field anywhere: the ressenti is how hard the session felt (1–5), nothing else.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.crypto import EncryptedString
from app.database import Base
from app.models.activity import JSONType


class CoachingPlan(Base):
    __tablename__ = "coaching_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                                         unique=True, index=True)
    # the race aimed at; None: « Progresser, sans course » (the race deleted: the same)
    route_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("routes.id", ondelete="SET NULL"), nullable=True)
    days: Mapped[str] = mapped_column(String(16), nullable=False)  # weekdays available, « 1,3,5,6 » (0 = Monday)
    long_day: Mapped[int] = mapped_column(Integer, nullable=False)
    base_minutes: Mapped[int] = mapped_column(Integer, nullable=False)  # the starting weekly minutes
    start_monday: Mapped[date] = mapped_column(Date, nullable=False)  # week 0 of the progression
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    adaptations: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # the race's times as its page reads them, kept for the day: {"route", "sig", "day", "objective_s", "estimate_s"};
    # or, the race deleted or behind: {"ended": "passee" | "supprimee", "name", "from": Monday ISO}
    goal_state: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    feed_token: Mapped[str | None] = mapped_column(EncryptedString(255), nullable=True)
    feed_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    @property
    def weekdays(self) -> tuple[int, ...]:
        return tuple(sorted({int(x) for x in (self.days or "").split(",") if x.strip().isdigit() and int(x) < 7}))


class CoachingSession(Base):
    __tablename__ = "coaching_sessions"
    __table_args__ = (Index("ix_coaching_sessions_user_day", "user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(Integer, ForeignKey("coaching_plans.id", ondelete="CASCADE"),
                                         nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)  # the athlete's local day
    auto_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    structure: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # planned | done | missed | cancelled (a coach removed a generated session: kept so that it is not made again)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="planned", server_default="planned")
    # the status was set by hand (« Fait », « Pas faite »): the matching never undoes it
    manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # the activity goes (a Strava delete): the link goes with it
    activity_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("activities.id", ondelete="SET NULL"),
                                                    nullable=True)
    done_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ressenti: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1 très facile … 5 très dur
    note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    coach_comment: Mapped[str | None] = mapped_column(String(280), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    origin: Mapped[str] = mapped_column(String(8), nullable=False, default="auto", server_default="auto")
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # the last change a calendar must see (its day, kind, duration or structure: services/coaching.touch), never a
    # status or a ressenti: the feed's LAST-MODIFIED, written by hand with `seq`
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CoachingLink(Base):
    __tablename__ = "coaching_links"
    __table_args__ = (UniqueConstraint("athlete_id", "coach_id", name="uq_coaching_links_athlete_coach"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    athlete_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                                            index=True)
    coach_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                                          index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CoachingInvite(Base):
    """The code an athlete gives a coach: short, shown once, used once, 7 days; only its SHA-256 is stored."""

    __tablename__ = "coaching_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    athlete_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                                            index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
