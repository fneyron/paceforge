"""Coaching: the intent chosen on the start screen (« Garder la forme », « Progresser », « Préparer une course »);
None for a plan made before, which keeps its rules.

Revision ID: r3b4c5d6e7f8
Revises: q8f9a0b1c2d3 (Coaching: a race without a course)
"""
import sqlalchemy as sa

from alembic import op

revision = "r3b4c5d6e7f8"
down_revision = "q8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("coaching_plans", sa.Column("intent", sa.String(length=12), nullable=True))


def downgrade() -> None:
    op.drop_column("coaching_plans", "intent")
