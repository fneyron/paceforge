"""Personal API keys (Réglages › Accès API): their name, first characters, SHA-256 (never the key), scopes, dates.

Revision ID: k2a3b4c5d6e7
Revises: j1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa

revision = "k2a3b4c5d6e7"
down_revision = "j1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("prefix", sa.String(8), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", sa.String(40), nullable=False, server_default="read"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_table("api_tokens")
