import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.database import Base
from app.models import Activity, Analysis, User  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override sqlalchemy.url with our async DATABASE_URL
# Convert asyncpg URL to psycopg2 for sync operations if needed
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


# Two containers of a deploy may start together, each running `alembic upgrade head`: the second waits on this
# lock until the first's connection closes (its migrations committed), then finds nothing left to run. A
# session-level lock: it covers the whole run whatever transaction_per_migration says, and goes with the
# connection. The wait has no lock_timeout or statement_timeout, by design (a big migration may take minutes):
# a holder whose process died half-open keeps the next container waiting until TCP keepalive ends its
# session. Any fixed bigint.
MIGRATION_LOCK_KEY = 7_364_720_914


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        if connection.dialect.name == "postgresql":
            await connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
            await connection.commit()  # the lock outlives this transaction; the migrations open their own
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
