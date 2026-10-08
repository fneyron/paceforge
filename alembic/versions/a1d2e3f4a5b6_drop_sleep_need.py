"""The sleep need is computed, never asked: users.sleep_need_min is dropped.

Owner, 2026-10-09: « Ne demande pas, ce doit être auto comme WHOOP ». The answer to « Combien d'heures de sommeil te
faut-il pour te sentir reposé ? » (z0c1d2e3f4a5, live for an hour) is no longer read since e3ea153: Santé counts 8 h,
more after a big effort and when sleep is owed (sante_sleep.sleep_need). Dropped one deploy later, so that the
container still running during a deploy never reads a missing column.

Revision ID: a1d2e3f4a5b6
Revises: z0c1d2e3f4a5
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1d2e3f4a5b6'
down_revision: Union[str, None] = 'z0c1d2e3f4a5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('sleep_need_min')


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('sleep_need_min', sa.Integer(), nullable=True))
