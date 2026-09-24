"""Add route_checkpoints.stop_s: the planned stop at this station, in seconds (null = by kind)

Revision ID: l6a7b8c9d0e1
Revises: k5f6a7b8c9d0
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'l6a7b8c9d0e1'
down_revision: Union[str, None] = 'k5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('route_checkpoints', sa.Column('stop_s', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('route_checkpoints', 'stop_s')
