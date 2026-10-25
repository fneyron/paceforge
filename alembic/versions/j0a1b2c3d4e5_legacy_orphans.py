"""The rows deleted accounts left in the legacy tables: removed (data only, no table dropped).

The tables of the features switched off (analyses, chat_messages, weekly_digests, generated_plans, simulations) are
unmapped and kept until a release drops them. Their keys do not all cascade from users: generated_plans.user_id has
no foreign key at all and simulations.user_id neither (its route_id cascades from routes, which cascades from users
only since h8e9f0a1b2c3), so an account deleted before left rows there. Account deletion now deletes them by name
(app.routers.settings._delete_legacy_rows); this removes the ones left before: rows whose user, activity or route is
gone. Each table only where it exists (a database built from the models has none). Counts in the migration log.

Downgrade: nothing to do (the rows were nobody's).
"""

import logging

import sqlalchemy as sa

from alembic import op

revision = "j0a1b2c3d4e5"
down_revision = "i9f0a1b2c3d4"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# (table, column, owner table): a row whose owner is gone is an orphan (every column here is NOT NULL)
ORPHANS = (
    ("generated_plans", "user_id", "users"),
    ("simulations", "user_id", "users"),
    ("simulations", "route_id", "routes"),
    ("chat_messages", "user_id", "users"),
    ("weekly_digests", "user_id", "users"),
    ("analyses", "activity_id", "activities"),
)


def upgrade():
    bind = op.get_bind()
    present = set(sa.inspect(bind).get_table_names())
    for table, column, owner in ORPHANS:
        if table not in present or owner not in present:
            continue
        n = bind.execute(sa.text(f"DELETE FROM {table} WHERE {column} NOT IN (SELECT id FROM {owner})")).rowcount
        log.info("%s: %d orphan rows deleted (%s not in %s)", table, n, column, owner)


def downgrade():
    pass
