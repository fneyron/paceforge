"""Coaching: « Ma séance fixe » — one weekly session the athlete brings (a club's), its weekday, duration and name.

Revision ID: t5d6e7f8a9b0
Revises: s4c5d6e7f8a9 (Coaching: the level)
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t5d6e7f8a9b0"
down_revision = "s4c5d6e7f8a9"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("coaching_plans", sa.Column("fixed", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("coaching_plans", "fixed")
