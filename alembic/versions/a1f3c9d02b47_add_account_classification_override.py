"""Adiciona tabela account_classification_override para reclassificação manual de MFA.

Revision ID: a1f3c9d02b47
Revises: b3e9f1a72c04
Create Date: 2026-06-14 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1f3c9d02b47"
down_revision: Union[str, Sequence[str], None] = "b3e9f1a72c04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_classification_override",
        sa.Column("upn", sa.String(320), primary_key=True, nullable=False),
        sa.Column("classificacao", sa.String(20), nullable=False),
        sa.Column("revisado_por", sa.String(200), nullable=False),
        sa.Column(
            "revisado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("account_classification_override")
