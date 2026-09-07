"""add params_json (sport-specific plan parameters) to routes

Revision ID: f0d4e6c8b579
Revises: e9c3d5b7a468
Create Date: 2026-09-07 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f0d4e6c8b579'
down_revision: Union[str, None] = 'e9c3d5b7a468'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('routes', sa.Column('params_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('routes', 'params_json')
