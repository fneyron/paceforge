"""Preserve daily health measurements per source and allow a preferred watch."""

import sqlalchemy as sa

from alembic import op

revision = "e5b6c7d8e9f0"
down_revision = "d4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE health_metrics SET source = '' WHERE source IS NULL")
    with op.batch_alter_table("health_metrics") as batch:
        batch.drop_constraint("uq_health_metrics_day", type_="unique")
        batch.alter_column(
            "source", existing_type=sa.String(100), nullable=False, server_default=""
        )
        batch.create_unique_constraint(
            "uq_health_metrics_day_source", ["user_id", "date", "metric", "source"]
        )
    op.add_column("users", sa.Column("health_source", sa.String(20), nullable=True))


def downgrade():
    # The old schema cannot hold both watches. Refuse data loss on rollback.
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM health_metrics GROUP BY user_id, date, metric HAVING count(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicates:
        raise RuntimeError(
            "Cannot downgrade health sources without losing measurements; export and resolve duplicates first."
        )
    with op.batch_alter_table("health_metrics") as batch:
        batch.drop_constraint("uq_health_metrics_day_source", type_="unique")
        batch.alter_column(
            "source", existing_type=sa.String(100), nullable=True, server_default=None
        )
        batch.create_unique_constraint("uq_health_metrics_day", ["user_id", "date", "metric"])
    op.drop_column("users", "health_source")
