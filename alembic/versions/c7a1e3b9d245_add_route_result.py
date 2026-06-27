"""add actual race result fields to routes (predicted-vs-actual calibration)

Revision ID: c7a1e3b9d245
Revises: b2e5d7f9a013
Create Date: 2026-06-26 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c7a1e3b9d245'
down_revision: Union[str, None] = 'b2e5d7f9a013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('routes', sa.Column('result_activity_id', sa.Integer(), nullable=True))
    op.add_column('routes', sa.Column('result_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('routes', 'result_json')
    op.drop_column('routes', 'result_activity_id')
