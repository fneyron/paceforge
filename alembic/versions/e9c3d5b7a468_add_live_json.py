"""add live_json (race-day passage anchor) to routes

Revision ID: e9c3d5b7a468
Revises: d8b2f4a6c357
Create Date: 2026-09-07 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e9c3d5b7a468'
down_revision: Union[str, None] = 'd8b2f4a6c357'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('routes', sa.Column('live_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('routes', 'live_json')
