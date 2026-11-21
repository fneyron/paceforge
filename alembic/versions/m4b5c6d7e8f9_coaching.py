"""Coaching (beta, docs/coaching.md): an athlete's plan, its sessions, the athlete → coach links and their single-use
codes. Empty until an account makes a plan.

Revision ID: m4b5c6d7e8f9
Revises: l3a4b5c6d7e8 (the official Wahoo and Polar activities)
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "m4b5c6d7e8f9"
down_revision = "l3a4b5c6d7e8"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _stamps() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "coaching_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=True),
        sa.Column("days", sa.String(length=16), nullable=False),
        sa.Column("long_day", sa.Integer(), nullable=False),
        sa.Column("base_minutes", sa.Integer(), nullable=False),
        sa.Column("start_monday", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adaptations", JSON, nullable=True),
        sa.Column("feed_token", sa.String(length=255), nullable=True),
        sa.Column("feed_token_hash", sa.String(length=64), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coaching_plans_user_id", "coaching_plans", ["user_id"], unique=True)
    op.create_index("ix_coaching_plans_feed_token_hash", "coaching_plans", ["feed_token_hash"], unique=True)
    op.create_table(
        "coaching_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("auto_day", sa.Date(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("structure", JSON, nullable=True),
        sa.Column("status", sa.String(length=12), server_default="planned", nullable=False),
        sa.Column("manual", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=True),
        sa.Column("done_minutes", sa.Integer(), nullable=True),
        sa.Column("ressenti", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=280), nullable=True),
        sa.Column("coach_comment", sa.String(length=280), nullable=True),
        sa.Column("pinned", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("origin", sa.String(length=8), server_default="auto", nullable=False),
        sa.Column("seq", sa.Integer(), server_default="0", nullable=False),
        *_stamps(),
        sa.ForeignKeyConstraint(["plan_id"], ["coaching_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coaching_sessions_plan_id", "coaching_sessions", ["plan_id"])
    op.create_index("ix_coaching_sessions_user_day", "coaching_sessions", ["user_id", "day"])
    op.create_table(
        "coaching_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("coach_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["coach_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("athlete_id", "coach_id", name="uq_coaching_links_athlete_coach"),
    )
    op.create_index("ix_coaching_links_athlete_id", "coaching_links", ["athlete_id"])
    op.create_index("ix_coaching_links_coach_id", "coaching_links", ["coach_id"])
    op.create_table(
        "coaching_invites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coaching_invites_athlete_id", "coaching_invites", ["athlete_id"])
    op.create_index("ix_coaching_invites_code_hash", "coaching_invites", ["code_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("coaching_invites")
    op.drop_table("coaching_links")
    op.drop_table("coaching_sessions")
    op.drop_table("coaching_plans")
