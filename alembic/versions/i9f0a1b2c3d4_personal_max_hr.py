"""Personal maximum heart rate for running zones.

Revision ID: i9f0a1b2c3d4
Revises: h8e9f0a1b2c3
"""
from alembic import op
import sqlalchemy as sa

revision = "i9f0a1b2c3d4"
down_revision = "h8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("max_heart_rate", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("max_heart_rate")
