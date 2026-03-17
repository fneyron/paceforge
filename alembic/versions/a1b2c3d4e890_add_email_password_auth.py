"""add email/password auth fields

Revision ID: a1b2c3d4e890
Revises: f1a2b3c4d567
Create Date: 2026-03-17 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e890'
down_revision: Union[str, None] = 'f1a2b3c4d567'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add email/password columns
    op.add_column('users', sa.Column('email', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('password_hash', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('users', sa.Column('email_verify_token', sa.String(100), nullable=True))
    op.add_column('users', sa.Column('password_reset_token', sa.String(100), nullable=True))
    op.add_column('users', sa.Column('password_reset_expires_at', sa.DateTime(timezone=True), nullable=True))

    # Create unique index on email
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    # Make strava fields nullable (auth is now via email, not strava)
    op.alter_column('users', 'strava_athlete_id', nullable=True)
    op.alter_column('users', 'strava_access_token', nullable=True)
    op.alter_column('users', 'strava_refresh_token', nullable=True)
    op.alter_column('users', 'strava_token_expires_at', nullable=True)


def downgrade() -> None:
    op.alter_column('users', 'strava_token_expires_at', nullable=False)
    op.alter_column('users', 'strava_refresh_token', nullable=False)
    op.alter_column('users', 'strava_access_token', nullable=False)
    op.alter_column('users', 'strava_athlete_id', nullable=False)
    op.drop_index('ix_users_email', table_name='users')
    op.drop_column('users', 'password_reset_expires_at')
    op.drop_column('users', 'password_reset_token')
    op.drop_column('users', 'email_verify_token')
    op.drop_column('users', 'email_verified')
    op.drop_column('users', 'password_hash')
    op.drop_column('users', 'email')
