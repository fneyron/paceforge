"""checkpoint metadata (kind, crew, drop bag, cutoff) + reference finisher on routes

Revision ID: g1b2c3d4e5f6
Revises: f0d4e6c8b579
Create Date: 2026-09-20 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'g1b2c3d4e5f6'
down_revision: Union[str, None] = 'f0d4e6c8b579'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Aid-station metadata: what kind of post it is, whether crew / a drop bag
    # is allowed there, and the official cutoff (clock "HH:MM").
    op.add_column('route_checkpoints', sa.Column('kind', sa.String(length=10), nullable=False, server_default='none'))
    op.add_column('route_checkpoints', sa.Column('crew', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('route_checkpoints', sa.Column('drop_bag', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('route_checkpoints', sa.Column('cutoff_clock', sa.String(length=5), nullable=True))
    # Reference finisher (splits pasted from Strava / a live-tracking page).
    op.add_column('routes', sa.Column('reference_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('routes', 'reference_json')
    op.drop_column('route_checkpoints', 'cutoff_clock')
    op.drop_column('route_checkpoints', 'drop_bag')
    op.drop_column('route_checkpoints', 'crew')
    op.drop_column('route_checkpoints', 'kind')
