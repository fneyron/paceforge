"""add user feedback fields to analyses

Revision ID: b2c3d4e5f901
Revises: a1b2c3d4e890
Create Date: 2026-03-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f901'
down_revision: Union[str, None] = 'a1b2c3d4e890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('analyses', sa.Column('user_rpe', sa.Integer(), nullable=True))
    op.add_column('analyses', sa.Column('user_rating', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('analyses', 'user_rating')
    op.drop_column('analyses', 'user_rpe')
