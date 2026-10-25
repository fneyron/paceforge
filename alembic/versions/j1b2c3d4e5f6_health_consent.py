"""Explicit consent to health data (GDPR art. 9.2.a): when it was given, and to which text.

Both columns start empty: no athlete is assumed to have consented. Santé asks the ones with health data or a
health source; a new source asks before it starts.

Revision ID: j1b2c3d4e5f6
Revises: j0a1b2c3d4e5
"""
from alembic import op
import sqlalchemy as sa

revision = "j1b2c3d4e5f6"
down_revision = "j0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("health_consent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("health_consent_version", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("health_consent_version")
        batch.drop_column("health_consent_at")
