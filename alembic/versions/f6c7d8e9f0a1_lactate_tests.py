"""Blood lactate tests, isolated by athlete; no change to recovery scores."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f6c7d8e9f0a1'
down_revision = 'e5b6c7d8e9f0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('lactate_tests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tested_on', sa.Date(), nullable=False),
        sa.Column('mode', sa.String(10), nullable=False),
        sa.Column('device', sa.String(80), nullable=False),
        sa.Column('protocol', sa.String(160), nullable=False),
        sa.Column('notes', sa.String(1000), nullable=False),
        sa.Column('stages', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_lactate_tests_user_day', 'lactate_tests', ['user_id', 'tested_on'])


def downgrade():
    op.drop_index('ix_lactate_tests_user_day', table_name='lactate_tests')
    op.drop_table('lactate_tests')
