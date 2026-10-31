"""Tables of the features switched off (the AI analyses, chat, weekly digests, plan generator, old simulations): no
model maps them, the database still has them (alembic h8e9f0a1b2c3) until a release drops them (the owner's call:
irreversible). An account's rows there are deleted with the account (settings.delete_account) and exported with its
data (data_export), by name, in each table that exists.

Their keys do not all cascade from users (generated_plans.user_id has no foreign key, simulations.user_id neither:
only its route_id cascades), so the rows are found by user_id; analyses has no user_id: its rows are found by the
account's activities. Alembic j0a1b2c3d4e5 removed the orphans the older deletions left.
"""
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

USER_TABLES = ("generated_plans", "simulations", "chat_messages", "weekly_digests")
TABLES = ("analyses",) + USER_TABLES


async def present(db: AsyncSession) -> list[str]:
    """The legacy tables this database still has, in TABLES' order."""
    names = set(await db.run_sync(lambda session: inspect(session.connection()).get_table_names()))
    return [table for table in TABLES if table in names]


def of_account(table: str) -> str:
    """The SQL condition on `table` (one of TABLES, never a name from a request) for the rows of the account :uid."""
    if table not in TABLES:
        raise ValueError(table)
    if table == "analyses":
        return "activity_id IN (SELECT id FROM activities WHERE user_id = :uid)"
    return "user_id = :uid"
