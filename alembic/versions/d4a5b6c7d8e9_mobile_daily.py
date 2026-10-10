"""Scoped phone credentials and independent daily health imports."""
import sqlalchemy as sa
from alembic import op

revision = "d4a5b6c7d8e9"
down_revision = "c3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mobile_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(8), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True),
        sa.Column("code_hash", sa.String(64), unique=True),
        sa.Column("challenge", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_mobile_devices_user_id", "mobile_devices", ["user_id"])
    op.create_table("mobile_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(8), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(12), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "platform", "date", "metric", name="uq_mobile_daily"))
    op.create_index("ix_mobile_daily_user_id", "mobile_daily", ["user_id"])


def downgrade():
    op.drop_table("mobile_daily")
    op.drop_table("mobile_devices")
