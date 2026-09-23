"""Add users.ftp_watts: a manual FTP that overrides the Strava estimate

Revision ID: i3d4e5f6a7b8
Revises: h2c3d4e5f6a7
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'i3d4e5f6a7b8'
down_revision: Union[str, None] = 'h2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('ftp_watts', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'ftp_watts')
