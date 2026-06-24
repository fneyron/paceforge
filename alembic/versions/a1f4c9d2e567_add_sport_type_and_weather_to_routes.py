"""add sport_type and weather_json to routes

Revision ID: a1f4c9d2e567
Revises: f7a9c1d2e345
Create Date: 2026-06-24 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1f4c9d2e567'
down_revision: Union[str, None] = 'f7a9c1d2e345'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'routes',
        sa.Column('sport_type', sa.String(length=20), nullable=False, server_default='trail'),
    )
    op.add_column('routes', sa.Column('weather_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('routes', 'weather_json')
    op.drop_column('routes', 'sport_type')
