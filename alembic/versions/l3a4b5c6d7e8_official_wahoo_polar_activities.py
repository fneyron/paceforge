"""Official Wahoo and Polar connectors: the sessions Wahoo (its workout id) or Polar (its exercise id, a string) also
has, or alone has (app.connectors, docs/official-connectors.md). Empty until those connectors are switched on.
official_connections.provider is a plain string (no check constraint): "wahoo" and "polar" need no change there.

Revision ID: l3a4b5c6d7e8
Revises: k2f3a4b5c6d7 (the official connections)
"""
import sqlalchemy as sa

from alembic import op

revision = "l3a4b5c6d7e8"
down_revision = "k2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("activities") as batch:
        batch.add_column(sa.Column("wahoo_activity_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("polar_activity_id", sa.String(length=64), nullable=True))
        batch.create_index("ix_activities_wahoo_activity_id", ["wahoo_activity_id"], unique=True)
        batch.create_index("ix_activities_polar_activity_id", ["polar_activity_id"], unique=True)


def downgrade() -> None:
    # the sessions only Wahoo or Polar had go (Strava, Garmin or COROS rows keep theirs)
    op.execute("DELETE FROM activities WHERE strava_activity_id IS NULL AND garmin_activity_id IS NULL "
               "AND coros_activity_id IS NULL AND (wahoo_activity_id IS NOT NULL OR polar_activity_id IS NOT NULL)")
    op.execute("DELETE FROM official_connections WHERE provider IN ('wahoo', 'polar')")
    with op.batch_alter_table("activities") as batch:
        batch.drop_index("ix_activities_polar_activity_id")
        batch.drop_index("ix_activities_wahoo_activity_id")
        batch.drop_column("polar_activity_id")
        batch.drop_column("wahoo_activity_id")
