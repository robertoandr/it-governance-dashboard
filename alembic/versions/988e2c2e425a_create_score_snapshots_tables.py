"""create score snapshots tables

Revision ID: 988e2c2e425a
Revises: 4a2c8e291c96
Create Date: 2026-06-09 02:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '988e2c2e425a'
down_revision: Union[str, Sequence[str], None] = '4a2c8e291c96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Cria tabelas de histórico de score de governança."""
    op.create_table(
        'score_snapshots',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('global_score', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('trend', sa.String(length=10), nullable=False),
        sa.Column('coverage_real', sa.Integer(), nullable=False),
        sa.Column('coverage_total', sa.Integer(), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_score_snapshots_created_at', 'score_snapshots', ['created_at'], unique=False)
    op.create_index('ix_score_snapshots_computed_at', 'score_snapshots', ['computed_at'], unique=False)

    op.create_table(
        'pillar_snapshots',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('snapshot_id', sa.Uuid(), nullable=False),
        sa.Column('pillar_id', sa.String(length=50), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('weight', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('trend', sa.String(length=10), nullable=False),
        sa.Column('data_source', sa.String(length=20), nullable=False),
        sa.Column('is_real', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['snapshot_id'], ['score_snapshots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_pillar_snapshots_snapshot_id', 'pillar_snapshots', ['snapshot_id'], unique=False)


def downgrade() -> None:
    """Remove tabelas de histórico de score."""
    op.drop_index('ix_pillar_snapshots_snapshot_id', table_name='pillar_snapshots')
    op.drop_table('pillar_snapshots')
    op.drop_index('ix_score_snapshots_computed_at', table_name='score_snapshots')
    op.drop_index('ix_score_snapshots_created_at', table_name='score_snapshots')
    op.drop_table('score_snapshots')
