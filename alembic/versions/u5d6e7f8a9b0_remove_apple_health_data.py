"""remove the Apple Health data (the Apple Health link is gone; COROS stays)

Revision ID: u5d6e7f8a9b0
Revises: t4c5d6e7f8a9
Create Date: 2026-09-30 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'u5d6e7f8a9b0'
down_revision: Union[str, None] = 't4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # every COROS-derived row carries source "COROS"; the rest came from Apple
    op.execute("DELETE FROM health_samples WHERE source <> 'COROS'")
    op.execute("DELETE FROM health_metrics WHERE source IS NULL OR source <> 'COROS'")
    op.execute("UPDATE users SET health_key_hash = NULL, health_key_prefix = NULL, health_key_created_at = NULL")


def downgrade() -> None:
    pass  # deleted data does not come back
