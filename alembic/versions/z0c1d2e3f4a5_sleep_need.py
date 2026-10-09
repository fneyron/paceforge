"""The athlete's sleep need: their answer to « Combien d'heures de sommeil te faut-il pour te sentir reposé ? »

Adds users.sleep_need_min (minutes, NULL until answered: Santé then counts 8 h). Owner, 2026-10-09: « Le besoin
diffère en fonction des personnes ».

Revision ID: z0c1d2e3f4a5
Revises: y9b0c1d2e3f4
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'z0c1d2e3f4a5'
down_revision: Union[str, None] = 'y9b0c1d2e3f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('sleep_need_min', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('sleep_need_min')
