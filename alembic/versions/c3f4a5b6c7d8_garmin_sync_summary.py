"""Keep Garmin sync coverage and outcome independently of imported values."""
import sqlalchemy as sa

from alembic import op

revision = "c3f4a5b6c7d8"
down_revision = "b2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("garmin_connections", sa.Column("sync_summary", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("garmin_connections", "sync_summary")
