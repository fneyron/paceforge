"""Coaching: a race without a course (« Ajouter une course », no GPX file) — its name, day, distance and, if given,
the time aimed at, kept on the plan.

Revision ID: q8f9a0b1c2d3
Revises: p7e8f9a0b1c2 (Coaching: « Mon agenda »)
"""
import sqlalchemy as sa

from alembic import op

revision = "q8f9a0b1c2d3"
down_revision = "p7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("coaching_plans", sa.Column("race_name", sa.String(length=80), nullable=True))
    op.add_column("coaching_plans", sa.Column("race_date", sa.Date(), nullable=True))
    op.add_column("coaching_plans", sa.Column("race_km", sa.Float(), nullable=True))
    op.add_column("coaching_plans", sa.Column("race_target_s", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("coaching_plans", "race_target_s")
    op.drop_column("coaching_plans", "race_km")
    op.drop_column("coaching_plans", "race_date")
    op.drop_column("coaching_plans", "race_name")
