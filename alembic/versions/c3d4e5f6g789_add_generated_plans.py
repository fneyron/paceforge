"""add generated_plans table

Revision ID: c3d4e5f6g789
Revises: b2c3d4e5f678
Create Date: 2026-03-19 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'c3d4e5f6g789'
down_revision: Union[str, None] = 'b2c3d4e5f678'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('generated_plans',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('plan_type', sa.String(20), nullable=False),
        sa.Column('sport', sa.String(50), nullable=False),
        sa.Column('goal', sa.String(500), nullable=True),
        sa.Column('content_json', JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_generated_plans_user_id', 'generated_plans', ['user_id'])


def downgrade() -> None:
    op.drop_table('generated_plans')
