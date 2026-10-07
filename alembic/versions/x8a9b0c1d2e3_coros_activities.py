"""COROS sessions: activities that COROS also has, or alone has (its labelId),
and when a link's sessions history was read in full

Revision ID: x8a9b0c1d2e3
Revises: w7f8a9b0c1d2
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'x8a9b0c1d2e3'
down_revision: Union[str, None] = 'w7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('activities') as batch:
        batch.add_column(sa.Column('coros_activity_id', sa.BigInteger(), nullable=True))
        batch.create_index('ix_activities_coros_activity_id', ['coros_activity_id'], unique=True)
    with op.batch_alter_table('coros_connections') as batch:
        batch.add_column(sa.Column('sessions_synced_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('coros_connections') as batch:
        batch.drop_column('sessions_synced_at')
    # the sessions that only COROS had go (Strava or Garmin rows keep theirs)
    op.execute("DELETE FROM activities WHERE strava_activity_id IS NULL AND garmin_activity_id IS NULL "
               "AND coros_activity_id IS NOT NULL")
    with op.batch_alter_table('activities') as batch:
        batch.drop_index('ix_activities_coros_activity_id')
        batch.drop_column('coros_activity_id')
