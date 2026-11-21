"""Coaching: what the plan last read of its race (the race page's objective and estimate, kept for the day; or why the
plan went on without it once the race was deleted or behind).

Revision ID: n5c6d7e8f9a0
Revises: m4b5c6d7e8f9 (Coaching)
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "n5c6d7e8f9a0"
down_revision = "m4b5c6d7e8f9"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("coaching_plans", sa.Column("goal_state", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("coaching_plans", "goal_state")
