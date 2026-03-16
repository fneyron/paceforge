"""add_streams_and_computed_metrics

Revision ID: e8a3b5c7d912
Revises: d7f3a1b2c456
Create Date: 2026-03-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'e8a3b5c7d912'
down_revision: Union[str, None] = 'd7f3a1b2c456'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('activities', sa.Column('streams_data', JSONB(), nullable=True))
    op.add_column('activities', sa.Column('computed_metrics', JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('activities', 'computed_metrics')
    op.drop_column('activities', 'streams_data')
