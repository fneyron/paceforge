"""add start_minute to routes

Revision ID: f7a9c1d2e345
Revises: e5f6g7h8i901
Create Date: 2026-06-23 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f7a9c1d2e345'
down_revision: Union[str, None] = 'e5f6g7h8i901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('routes', sa.Column('start_minute', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('routes', 'start_minute')
