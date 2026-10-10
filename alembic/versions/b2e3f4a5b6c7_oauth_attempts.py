"""Keep pending Strava credentials and single-use OAuth states server-side."""
import sqlalchemy as sa

from alembic import op

revision = "b2e3f4a5b6c7"
down_revision = "a1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "oauth_attempts",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("credentials", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_attempts_user_id", "oauth_attempts", ["user_id"])
    op.create_index("ix_oauth_attempts_expires_at", "oauth_attempts", ["expires_at"])


def downgrade():
    op.drop_table("oauth_attempts")
