"""COROS: the athlete's OAuth link (encrypted tokens, discovered endpoints, sync state)
and the OAuth clients PaceForge registered itself with

Revision ID: t4c5d6e7f8a9
Revises: s3b4c5d6e7f8
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 't4c5d6e7f8a9'
down_revision: Union[str, None] = 's3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'oauth_clients',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('issuer', sa.String(length=255), nullable=False),
        sa.Column('redirect_uri', sa.String(length=500), nullable=False),
        sa.Column('client_id', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('issuer', 'redirect_uri', name='uq_oauth_clients_issuer_redirect'),
    )
    op.create_table(
        'coros_connections',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('issuer', sa.String(length=255), nullable=False),
        sa.Column('client_id', sa.String(length=255), nullable=False),
        sa.Column('access_token_encrypted', sa.LargeBinary(), nullable=False),
        sa.Column('refresh_token_encrypted', sa.LargeBinary(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resource_url', sa.String(length=500), nullable=False),
        sa.Column('token_endpoint', sa.String(length=500), nullable=False),
        sa.Column('revocation_endpoint', sa.String(length=500), nullable=True),
        sa.Column('connected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sync_claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('needs_reauth', sa.Boolean(), server_default='false', nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_coros_connections_user'),
    )


def downgrade() -> None:
    op.drop_table('coros_connections')
    op.drop_table('oauth_clients')
