"""Audit foundation: the athlete's time zone, when the weight was typed, session revocation,
and the phone link's sliding expiry, previous token and per-device daily rows.

One migration for the whole October audit, so the fixes that follow add no head of their own.
"""

import sqlalchemy as sa

from alembic import op

revision = "g7d8e9f0a1b2"
down_revision = "f6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("timezone", sa.String(64), nullable=True))
        batch.add_column(sa.Column("weight_set_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("mobile_devices") as batch:
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("prev_token_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("prev_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_mobile_devices_prev_token_hash", ["prev_token_hash"], unique=True)
    with op.batch_alter_table("mobile_daily") as batch:
        batch.add_column(sa.Column("device_id", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("mobile_daily") as batch:
        batch.drop_column("device_id")
    with op.batch_alter_table("mobile_devices") as batch:
        batch.drop_index("ix_mobile_devices_prev_token_hash")
        batch.drop_column("prev_expires_at")
        batch.drop_column("prev_token_hash")
        batch.drop_column("last_seen_at")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("session_version")
        batch.drop_column("weight_set_at")
        batch.drop_column("timezone")
