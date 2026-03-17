"""add per-user Strava API credentials

Revision ID: f1a2b3c4d567
Revises: e8a3b5c7d912
Create Date: 2026-03-17 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d567'
down_revision: Union[str, None] = 'e8a3b5c7d912'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('strava_client_id', sa.String(20), nullable=True))
    op.add_column('users', sa.Column('strava_client_secret_encrypted', sa.LargeBinary(), nullable=True))
    op.add_column('users', sa.Column('strava_webhook_subscription_id', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('strava_credentials_valid', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('users', sa.Column('last_activity_poll_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'last_activity_poll_at')
    op.drop_column('users', 'strava_credentials_valid')
    op.drop_column('users', 'strava_webhook_subscription_id')
    op.drop_column('users', 'strava_client_secret_encrypted')
    op.drop_column('users', 'strava_client_id')
