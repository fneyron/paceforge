"""add stop_minutes (planned aid-station stop) to routes

Revision ID: d8b2f4a6c357
Revises: c7a1e3b9d245
Create Date: 2026-07-29 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd8b2f4a6c357'
down_revision: Union[str, None] = 'c7a1e3b9d245'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('routes', sa.Column('stop_minutes', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('routes', 'stop_minutes')
