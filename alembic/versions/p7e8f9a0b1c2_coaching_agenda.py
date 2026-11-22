"""Coaching: « Mon agenda » — the private iCalendar addresses an athlete pasted (encrypted), the busy time read from
them (a start, an end, nothing else), when they can run, and the time each session was given.

Revision ID: p7e8f9a0b1c2
Revises: o6d7e8f9a0b1 (Coaching: the form)
"""
import sqlalchemy as sa

from alembic import op

revision = "p7e8f9a0b1c2"
down_revision = "o6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coaching_calendars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=4096), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coaching_calendars_user_id", "coaching_calendars", ["user_id"])
    op.create_table(
        "coaching_busy",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("away", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["calendar_id"], ["coaching_calendars.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coaching_busy_calendar_id", "coaching_busy", ["calendar_id"])
    op.create_index("ix_coaching_busy_user_start", "coaching_busy", ["user_id", "start_at"])
    op.add_column("coaching_plans", sa.Column("moments", sa.String(length=24), nullable=True))
    op.add_column("coaching_sessions", sa.Column("slot_min", sa.Integer(), nullable=True))
    op.add_column("coaching_sessions", sa.Column("slot_chosen", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("coaching_sessions", sa.Column("slot_flag", sa.String(length=12), nullable=True))


def downgrade() -> None:
    op.drop_column("coaching_sessions", "slot_flag")
    op.drop_column("coaching_sessions", "slot_chosen")
    op.drop_column("coaching_sessions", "slot_min")
    op.drop_column("coaching_plans", "moments")
    op.drop_index("ix_coaching_busy_user_start", table_name="coaching_busy")
    op.drop_index("ix_coaching_busy_calendar_id", table_name="coaching_busy")
    op.drop_table("coaching_busy")
    op.drop_index("ix_coaching_calendars_user_id", table_name="coaching_calendars")
    op.drop_table("coaching_calendars")
