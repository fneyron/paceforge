"""Garmin: the athlete's Garmin Connect link (encrypted DI tokens, sync state),
and activities that may come from Garmin only (no Strava id)

Revision ID: w7f8a9b0c1d2
Revises: v6e7f8a9b0c1
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'w7f8a9b0c1d2'
down_revision: Union[str, None] = 'v6e7f8a9b0c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'garmin_connections',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('domain', sa.String(length=32), nullable=False),
        sa.Column('di_client_id', sa.String(length=255), nullable=False),
        sa.Column('access_token_encrypted', sa.LargeBinary(), nullable=False),
        sa.Column('refresh_token_encrypted', sa.LargeBinary(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('display_name', sa.String(length=255), nullable=True),
        sa.Column('connected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sync_claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('needs_reauth', sa.Boolean(), server_default='false', nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_garmin_connections_user'),
    )
    with op.batch_alter_table('activities') as batch:
        batch.alter_column('strava_activity_id', existing_type=sa.BigInteger(), nullable=True)
        batch.add_column(sa.Column('garmin_activity_id', sa.BigInteger(), nullable=True))
        batch.create_index('ix_activities_garmin_activity_id', ['garmin_activity_id'], unique=True)


def downgrade() -> None:
    # the sessions that only Garmin had cannot keep a NULL Strava id
    op.execute("DELETE FROM activities WHERE strava_activity_id IS NULL")
    with op.batch_alter_table('activities') as batch:
        batch.drop_index('ix_activities_garmin_activity_id')
        batch.drop_column('garmin_activity_id')
        batch.alter_column('strava_activity_id', existing_type=sa.BigInteger(), nullable=False)
    op.drop_table('garmin_connections')
