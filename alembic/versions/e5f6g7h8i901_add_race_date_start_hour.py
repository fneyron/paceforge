"""add race_date and start_hour to routes

Revision ID: e5f6g7h8i901
Revises: d4e5f6g7h890
Create Date: 2026-03-20 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e5f6g7h8i901'
down_revision: Union[str, None] = 'd4e5f6g7h890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('routes', sa.Column('race_date', sa.String(10), nullable=True))
    op.add_column('routes', sa.Column('start_hour', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('routes', 'start_hour')
    op.drop_column('routes', 'race_date')
