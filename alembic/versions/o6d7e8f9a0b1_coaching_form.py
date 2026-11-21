"""Coaching: « Adapter mes séances à ma forme » — when the athlete turned the option on, the mornings Santé's index
read low (dates only), and the sessions lightened or moved on such a morning.

Revision ID: o6d7e8f9a0b1
Revises: n5c6d7e8f9a0 (Coaching: the race's times)
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "o6d7e8f9a0b1"
down_revision = "n5c6d7e8f9a0"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("coaching_plans", sa.Column("form_on_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("coaching_plans", sa.Column("form_state", JSON, nullable=True))
    op.add_column("coaching_sessions", sa.Column("form", sa.String(length=12), nullable=True))


def downgrade() -> None:
    op.drop_column("coaching_sessions", "form")
    op.drop_column("coaching_plans", "form_state")
    op.drop_column("coaching_plans", "form_on_at")
