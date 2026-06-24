"""add nutrition products table and route nutrition_json

Revision ID: b2e5d7f9a013
Revises: a1f4c9d2e567
Create Date: 2026-06-24 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b2e5d7f9a013'
down_revision: Union[str, None] = 'a1f4c9d2e567'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'nutrition_products',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False, server_default='gel'),
        sa.Column('carbs_g', sa.Float(), nullable=False, server_default='0'),
        sa.Column('sodium_mg', sa.Float(), nullable=False, server_default='0'),
        sa.Column('kcal', sa.Float(), nullable=True),
        sa.Column('caffeine_mg', sa.Float(), nullable=True),
        sa.Column('volume_ml', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_nutrition_products_user_id', 'nutrition_products', ['user_id'])
    op.add_column('routes', sa.Column('nutrition_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('routes', 'nutrition_json')
    op.drop_index('ix_nutrition_products_user_id', table_name='nutrition_products')
    op.drop_table('nutrition_products')
