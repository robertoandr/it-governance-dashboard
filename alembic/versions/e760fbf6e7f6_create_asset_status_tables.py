"""create asset status tables

Revision ID: e760fbf6e7f6
Revises: 988e2c2e425a
Create Date: 2026-06-09 03:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e760fbf6e7f6'
down_revision: Union[str, Sequence[str], None] = '988e2c2e425a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Cria tabelas de status e histórico de ativos monitorados via Zabbix."""
    op.create_table(
        'asset_status',
        sa.Column('hostid', sa.String(length=20), nullable=False),
        sa.Column('host', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('asset_type', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('last_change', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('hostid'),
    )
    op.create_index(op.f('ix_asset_status_asset_type'), 'asset_status', ['asset_type'], unique=False)
    op.create_index(op.f('ix_asset_status_status'), 'asset_status', ['status'], unique=False)
    op.create_index('ix_asset_status_type_status', 'asset_status', ['asset_type', 'status'], unique=False)

    op.create_table(
        'asset_status_history',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('hostid', sa.String(length=20), nullable=False),
        sa.Column('asset_type', sa.String(length=50), nullable=False),
        sa.Column('from_status', sa.String(length=20), nullable=False),
        sa.Column('to_status', sa.String(length=20), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_asset_status_history_asset_type'), 'asset_status_history', ['asset_type'], unique=False)
    op.create_index(op.f('ix_asset_status_history_changed_at'), 'asset_status_history', ['changed_at'], unique=False)
    op.create_index(op.f('ix_asset_status_history_hostid'), 'asset_status_history', ['hostid'], unique=False)


def downgrade() -> None:
    """Remove tabelas de status e histórico de ativos."""
    op.drop_index(op.f('ix_asset_status_history_hostid'), table_name='asset_status_history')
    op.drop_index(op.f('ix_asset_status_history_changed_at'), table_name='asset_status_history')
    op.drop_index(op.f('ix_asset_status_history_asset_type'), table_name='asset_status_history')
    op.drop_table('asset_status_history')
    op.drop_index('ix_asset_status_type_status', table_name='asset_status')
    op.drop_index(op.f('ix_asset_status_status'), table_name='asset_status')
    op.drop_index(op.f('ix_asset_status_asset_type'), table_name='asset_status')
    op.drop_table('asset_status')
