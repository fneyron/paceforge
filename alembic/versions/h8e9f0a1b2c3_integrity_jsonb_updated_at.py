"""Integrity after the audit: every row hangs on its owner, JSON columns are jsonb holding SQL NULLs for
« nothing », the last write of a race, a route or a checkpoint is dated.

In this order, safe on live data: the rows of accounts deleted before their table had a foreign key go, and the
links to an activity or a phone gone are cleared (counts in the migration log); the missing foreign keys come
(routes and nutrition_products to users ON DELETE CASCADE; routes.result_activity_id to activities and
mobile_daily.device_id to mobile_devices ON DELETE SET NULL); the seven json columns become
jsonb; a JSON null stored for « nothing » becomes a SQL NULL (the models write SQL NULLs from now on: JSONType
none_as_null); users.strava_token_expires_at grows to BIGINT; activities, routes and route_checkpoints get
updated_at; four indexes a wider one covers go; the big activity columns compress with lz4 from now on (the old
rows stay as they are); mobile_devices.prev_token_hash is unique by constraint, as the model says. PostgreSQL-only
statements are skipped on another dialect (the tests build their schema from the models). The tables of the
features switched off (analyses, chat_messages, weekly_digests, generated_plans, simulations) are left as they
are: unmapped at the next release, dropped after.

The downgrade restores the schema; the orphans deleted and the JSON nulls are not brought back.
"""

import logging

import sqlalchemy as sa

from alembic import op

revision = "h8e9f0a1b2c3"
down_revision = "g7d8e9f0a1b2"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# tables whose user_id had no foreign key: their rows of accounts gone are orphans
OWNED = ("routes", "nutrition_products")
# links to a row that may be gone: cleared before the key SET NULLs them from now on
LINKS = (("routes", "result_activity_id", "activities"), ("mobile_daily", "device_id", "mobile_devices"))
# (table, column, referred table, ON DELETE), named as PostgreSQL names the earlier keys
FOREIGN_KEYS = (
    ("routes", "user_id", "users", "CASCADE"),
    ("nutrition_products", "user_id", "users", "CASCADE"),
    ("routes", "result_activity_id", "activities", "SET NULL"),
    ("mobile_daily", "device_id", "mobile_devices", "SET NULL"),
)
JSON_TO_JSONB = tuple(("routes", c) for c in ("live_json", "params_json", "weather_json", "nutrition_json",
                                               "result_json", "reference_json")) + (("mobile_daily", "sources"),)
# the nullable JSON columns: a JSON null there meant « nothing »
NULLABLE_JSON = (
    ("activities", ("laps", "splits_metric", "best_efforts", "streams_data", "computed_metrics")),
    ("routes", ("course_json", "live_json", "params_json", "weather_json", "nutrition_json", "result_json",
                "reference_json")),
    ("health_metrics", ("details",)),
    ("users", ("preferred_sports",)),
)
DATED = ("activities", "routes", "route_checkpoints")
# (index, table, columns): covered by ix_activities_user_start_date and uq_mobile_daily (user_id first), or read
# by nothing (ix_activities_start_date: every read is per athlete)
REDUNDANT_INDEXES = (
    ("ix_activities_user_id", "activities", ["user_id"]),
    ("ix_activities_start_date", "activities", ["start_date"]),
    ("ix_mobile_daily_user_id", "mobile_daily", ["user_id"]),
)
COMPRESSED = ("raw_data", "laps", "splits_metric", "streams_data")


def _fk_name(table: str, column: str) -> str:
    return f"{table}_{column}_fkey"


def _set_compression(bind, method: str) -> None:
    """lz4 reads back 2,5× faster than pglz (measured on the audit's dataset); a server built without lz4 keeps
    pglz: each statement runs in a savepoint, the first refusal ends the step."""
    for column in COMPRESSED:
        try:
            with bind.begin_nested():
                bind.execute(sa.text(f"ALTER TABLE activities ALTER COLUMN {column} SET COMPRESSION {method}"))
        except Exception as exc:  # noqa: BLE001 — nothing else of the migration depends on it
            log.warning("activities.%s keeps its compression: %s", column, str(exc).splitlines()[0])
            return


def upgrade():
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"

    # 1. orphans and dangling links (user_id is NOT NULL everywhere: NOT IN is safe)
    for table in OWNED:
        n = bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE user_id NOT IN (SELECT id FROM users)")).scalar()
        log.info("%s: %d orphan rows of deleted accounts", table, n)
        if n:
            bind.execute(sa.text(f"DELETE FROM {table} WHERE user_id NOT IN (SELECT id FROM users)"))
    for table, column, ref in LINKS:
        n = bind.execute(sa.text(
            f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL AND {column} NOT IN (SELECT id FROM {ref})"
        )).rowcount
        log.info("%s.%s: %d links to a %s row gone cleared", table, column, n, ref)

    # 2. the keys
    for table, column, ref, rule in FOREIGN_KEYS:
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(_fk_name(table, column), ref, [column], ["id"], ondelete=rule)

    if pg:
        # 3. json → jsonb (json has no equality and no operators, and is parsed at every read)
        for table, column in JSON_TO_JSONB:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE jsonb USING {column}::jsonb")
        # 4. a JSON null stored for « nothing » is a SQL NULL: IS NOT NULL means « has a value » again
        for table, columns in NULLABLE_JSON:
            for column in columns:
                n = bind.execute(sa.text(f"UPDATE {table} SET {column} = NULL WHERE jsonb_typeof({column}) = 'null'")).rowcount
                if n:
                    log.info("%s.%s: %d JSON nulls are SQL NULLs now", table, column, n)

    # 5. an epoch past 2038
    with op.batch_alter_table("users") as batch:
        batch.alter_column("strava_token_expires_at", type_=sa.BigInteger(), existing_type=sa.Integer(),
                           existing_nullable=True)

    # 6. when a row was last written (the existing rows: now)
    for table in DATED:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                                       nullable=False))

    # 7. the indexes a wider one covers
    for name, table, _columns in REDUNDANT_INDEXES:
        op.drop_index(name, table_name=table)

    # 8. lz4 for the big columns written from now on
    if pg:
        _set_compression(bind, "lz4")

    # 9. prev_token_hash: unique by constraint, as the model (g7d8e9f0a1b2 made a unique index)
    op.drop_index("ix_mobile_devices_prev_token_hash", table_name="mobile_devices")
    with op.batch_alter_table("mobile_devices") as batch:
        batch.create_unique_constraint("uq_mobile_devices_prev_token_hash", ["prev_token_hash"])


def downgrade():
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"

    with op.batch_alter_table("mobile_devices") as batch:
        batch.drop_constraint("uq_mobile_devices_prev_token_hash", type_="unique")
    op.create_index("ix_mobile_devices_prev_token_hash", "mobile_devices", ["prev_token_hash"], unique=True)
    if pg:
        _set_compression(bind, "pglz")
    for name, table, columns in REDUNDANT_INDEXES:
        op.create_index(name, table, columns)
    for table in reversed(DATED):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("updated_at")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("strava_token_expires_at", type_=sa.Integer(), existing_type=sa.BigInteger(),
                           existing_nullable=True)
    if pg:
        for table, column in JSON_TO_JSONB:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE json USING {column}::json")
    for table, column, _ref, _rule in reversed(FOREIGN_KEYS):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(_fk_name(table, column), type_="foreignkey")
