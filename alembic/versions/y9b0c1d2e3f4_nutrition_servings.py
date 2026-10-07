"""Nutrition products: the prises in one unit (a PF 90 pouch = 3 prises of 30 g)

Adds nutrition_products.servings (default 1) and, once, sets 3 on the
« Precision Fuel PF 90 Gel » rows already in a pantry (the owner's seeded
Transjeju product among them). Idempotent.

Revision ID: y9b0c1d2e3f4
Revises: x8a9b0c1d2e3
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'y9b0c1d2e3f4'
down_revision: Union[str, None] = 'x8a9b0c1d2e3'
branch_labels = None
depends_on = None


def apply(bind) -> int:
    """The one-time data step: PF 90 rows get their 3 prises (only rows still at 1)."""
    res = bind.execute(sa.text(
        "UPDATE nutrition_products SET servings = 3 "
        "WHERE lower(name) LIKE 'precision fuel pf 90%' AND servings = 1"
    ))
    return res.rowcount or 0


def upgrade() -> None:
    with op.batch_alter_table('nutrition_products') as batch:
        batch.add_column(sa.Column('servings', sa.Integer(), nullable=False, server_default='1'))
    apply(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table('nutrition_products') as batch:
        batch.drop_column('servings')
