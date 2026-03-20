"""add target_time_s to routes

Revision ID: d4e5f6g7h890
Revises: c3d4e5f6g789
Create Date: 2026-03-20 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6g7h890'
down_revision: Union[str, None] = 'c3d4e5f6g789'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('routes', sa.Column('target_time_s', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('routes', 'target_time_s')
