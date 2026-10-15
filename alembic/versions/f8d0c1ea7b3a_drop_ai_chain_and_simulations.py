"""Drop the tables of the removed AI chain and the unused simulations.

analyses, chat_messages, weekly_digests and generated_plans belonged to the coaching chain that never ran in
this product (its tasks were disabled from the start, its code is gone); simulations was written by nothing.
Their rows are machine output, never the athlete's own data. activities.computed_metrics and streams_data
stay one more release. The downgrade recreates the five tables as they were (schema only: no row comes back).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f8d0c1ea7b3a"
down_revision = "g7d8e9f0a1b2"
branch_labels = None
depends_on = None

TABLES = ("analyses", "chat_messages", "weekly_digests", "generated_plans", "simulations")


def _json():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _now():
    return sa.func.now()  # now() on PostgreSQL, CURRENT_TIMESTAMP on SQLite


def upgrade():
    present = set(sa.inspect(op.get_bind()).get_table_names())
    for name in TABLES:  # a database that already lacks one must not stop the start-up
        if name in present:
            op.drop_table(name)


def downgrade():
    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("strengths", _json(), nullable=False),
        sa.Column("improvements", _json(), nullable=False),
        sa.Column("next_workout_tip", sa.Text(), nullable=False),
        sa.Column("strava_comment", sa.Text(), nullable=False),
        sa.Column("fatigue_note", sa.Text(), nullable=True),
        sa.Column("training_load_7d_km", sa.Float(), nullable=True),
        sa.Column("training_load_7d_hours", sa.Float(), nullable=True),
        sa.Column("training_load_7d_count", sa.Integer(), nullable=True),
        sa.Column("training_load_28d_km", sa.Float(), nullable=True),
        sa.Column("training_load_28d_hours", sa.Float(), nullable=True),
        sa.Column("training_load_28d_count", sa.Integer(), nullable=True),
        sa.Column("model_used", sa.String(length=100), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("comment_posted", sa.Boolean(), nullable=False),
        sa.Column("comment_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_rpe", sa.Integer(), nullable=True),
        sa.Column("user_rating", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analyses_activity_id", "analyses", ["activity_id"], unique=True)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"], unique=False)

    op.create_table(
        "weekly_digests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("highlights", _json(), nullable=False),
        sa.Column("recommendations", _json(), nullable=False),
        sa.Column("volume_assessment", sa.Text(), nullable=True),
        sa.Column("training_load_summary", _json(), nullable=True),
        sa.Column("model_used", sa.String(length=100), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "week_start", name="uq_user_week"),
    )
    op.create_index("ix_weekly_digests_user_id", "weekly_digests", ["user_id"], unique=False)

    op.create_table(
        "generated_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_type", sa.String(length=20), nullable=False),
        sa.Column("sport", sa.String(length=50), nullable=False),
        sa.Column("goal", sa.String(length=500), nullable=True),
        sa.Column("content_json", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generated_plans_user_id", "generated_plans", ["user_id"], unique=False)

    op.create_table(
        "simulations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("target_time_s", sa.Integer(), nullable=True),
        sa.Column("config_json", _json(), nullable=True),
        sa.Column("results_json", _json(), nullable=True),
        sa.Column("weather_json", _json(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_simulations_route_id", "simulations", ["route_id"], unique=False)
    op.create_index("ix_simulations_user_id", "simulations", ["user_id"], unique=False)
