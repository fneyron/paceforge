"""add routes, checkpoints, simulations tables

Revision ID: b2c3d4e5f678
Revises: a1b2c3d4e890
Create Date: 2026-03-18 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'b2c3d4e5f678'
down_revision: Union[str, None] = 'a1b2c3d4e890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('routes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('total_distance_km', sa.Float(), nullable=False),
        sa.Column('total_elevation_gain', sa.Float(), nullable=False, server_default='0'),
        sa.Column('total_elevation_loss', sa.Float(), nullable=False, server_default='0'),
        sa.Column('course_json', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_routes_user_id', 'routes', ['user_id'])

    op.create_table('route_checkpoints',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('route_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('distance_km', sa.Float(), nullable=False),
        sa.Column('elevation', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['route_id'], ['routes.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_route_checkpoints_route_id', 'route_checkpoints', ['route_id'])

    op.create_table('simulations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('route_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('target_time_s', sa.Integer(), nullable=True),
        sa.Column('config_json', JSONB(), nullable=True),
        sa.Column('results_json', JSONB(), nullable=True),
        sa.Column('weather_json', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['route_id'], ['routes.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_simulations_route_id', 'simulations', ['route_id'])
    op.create_index('ix_simulations_user_id', 'simulations', ['user_id'])


def downgrade() -> None:
    op.drop_table('simulations')
    op.drop_table('route_checkpoints')
    op.drop_table('routes')
