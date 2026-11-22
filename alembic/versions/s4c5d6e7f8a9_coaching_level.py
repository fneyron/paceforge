"""Coaching: the level read from the athlete's history when the plan is made (débutant, régulier, entraîné; never
asked) and what it read.

Revision ID: s4c5d6e7f8a9
Revises: r3b4c5d6e7f8 (Coaching: the intent)
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "s4c5d6e7f8a9"
down_revision = "r3b4c5d6e7f8"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("coaching_plans", sa.Column("level", sa.String(length=12), nullable=True))
    op.add_column("coaching_plans", sa.Column("level_read", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("coaching_plans", "level_read")
    op.drop_column("coaching_plans", "level")
