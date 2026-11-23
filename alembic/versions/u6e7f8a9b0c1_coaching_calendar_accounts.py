"""Coaching › « Mon agenda »: Google Agenda and Outlook connected by OAuth beside the pasted addresses — a source's
provider, its encrypted tokens and when the access token lapses; the address becomes optional (an account has none).

Revision ID: u6e7f8a9b0c1
Revises: t5d6e7f8a9b0 (Coaching: « Ma séance fixe »)
"""
import sqlalchemy as sa

from alembic import op

revision = "u6e7f8a9b0c1"
down_revision = "t5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("coaching_calendars", sa.Column("provider", sa.String(length=12), server_default="ical",
                                                  nullable=False))
    op.add_column("coaching_calendars", sa.Column("access_token", sa.String(length=8192), nullable=True))
    op.add_column("coaching_calendars", sa.Column("refresh_token", sa.String(length=8192), nullable=True))
    op.add_column("coaching_calendars", sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("coaching_calendars", "url", existing_type=sa.String(length=4096), nullable=True)


def downgrade() -> None:
    # the connected accounts have no address: they go (their busy time with them, ON DELETE CASCADE)
    op.execute("DELETE FROM coaching_calendars WHERE provider <> 'ical'")
    op.alter_column("coaching_calendars", "url", existing_type=sa.String(length=4096), nullable=False)
    op.drop_column("coaching_calendars", "token_expires_at")
    op.drop_column("coaching_calendars", "refresh_token")
    op.drop_column("coaching_calendars", "access_token")
    op.drop_column("coaching_calendars", "provider")
