"""Apple Health: raw samples, one value per day and metric, and the personal push key on users

Revision ID: s3b4c5d6e7f8
Revises: r2a3b4c5d6e7
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 's3b4c5d6e7f8'
down_revision: Union[str, None] = 'r2a3b4c5d6e7'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        'health_samples',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('metric', sa.String(length=12), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False, server_default=''),
        sa.Column('start_at', sa.DateTime(), nullable=False),
        sa.Column('end_at', sa.DateTime(), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'metric', 'start_at', 'source', 'kind', name='uq_health_samples_key'),
    )
    op.create_table(
        'health_metrics',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('metric', sa.String(length=12), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('details', JSON, nullable=True),
        sa.Column('n_samples', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'date', 'metric', name='uq_health_metrics_day'),
    )
    op.create_index('ix_health_metrics_user_metric_date', 'health_metrics', ['user_id', 'metric', 'date'], unique=False)

    op.add_column('users', sa.Column('health_key_hash', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('health_key_prefix', sa.String(length=16), nullable=True))
    op.add_column('users', sa.Column('health_key_created_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('health_last_push_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('health_last_push_count', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_users_health_key_hash'), 'users', ['health_key_hash'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_health_key_hash'), table_name='users')
    op.drop_column('users', 'health_last_push_count')
    op.drop_column('users', 'health_last_push_at')
    op.drop_column('users', 'health_key_created_at')
    op.drop_column('users', 'health_key_prefix')
    op.drop_column('users', 'health_key_hash')
    op.drop_index('ix_health_metrics_user_metric_date', table_name='health_metrics')
    op.drop_table('health_metrics')
    op.drop_table('health_samples')
