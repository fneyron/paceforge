"""Add route_checkpoints.target_s: the passage time pinned at this point, in seconds after the start (null = the plan decides)

Revision ID: r2a3b4c5d6e7
Revises: q1f2a3b4c5d6
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'r2a3b4c5d6e7'
down_revision: Union[str, None] = 'q1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('route_checkpoints', sa.Column('target_s', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('route_checkpoints', 'target_s')
