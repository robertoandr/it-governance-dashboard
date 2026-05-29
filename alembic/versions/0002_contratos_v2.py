"""Contratos v2: align table with new domain model.

Revision ID: 0002
Revises: None
Create Date: 2026-05-29

Changes
-------
- Drop generated column valor_anual
- Add numero_contrato (UNIQUE, NOT NULL), replaces numero
- Add objeto (NOT NULL), replaces descricao
- Add sla_uptime_pct (NOT NULL), replaces sla_prometido_pct
- Add criticidade (NOT NULL DEFAULT 'media')
- Make valor_mensal NOT NULL
- Migrate status 'ativo' → 'vigente'
- Drop deprecated columns: numero, descricao, sla_prometido_pct, moeda,
  renovacao_auto, anexo_path
- Add composite indexes on (fornecedor_id, status) and (data_inicio, data_fim)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply contratos v2 schema changes."""
    # 1. Drop the GENERATED column before any other ALTER (PG requires this first).
    op.execute("ALTER TABLE contratos DROP COLUMN IF EXISTS valor_anual")

    # 2. Add new columns as nullable to allow data migration on existing rows.
    op.add_column("contratos", sa.Column("numero_contrato", sa.Text(), nullable=True))
    op.add_column("contratos", sa.Column("objeto", sa.Text(), nullable=True))
    op.add_column("contratos", sa.Column("sla_uptime_pct", sa.Numeric(5, 2), nullable=True))
    op.add_column(
        "contratos",
        sa.Column("criticidade", sa.Text(), nullable=True, server_default="media"),
    )

    # 3. Migrate existing data.
    op.execute("UPDATE contratos SET numero_contrato = numero")
    op.execute("UPDATE contratos SET objeto = COALESCE(descricao, '')")
    op.execute("UPDATE contratos SET sla_uptime_pct = sla_prometido_pct")
    op.execute("UPDATE contratos SET criticidade = 'media' WHERE criticidade IS NULL")
    op.execute("UPDATE contratos SET valor_mensal = 0.00 WHERE valor_mensal IS NULL")
    # Align legacy status values with new enum.
    op.execute("UPDATE contratos SET status = 'vigente' WHERE status IN ('ativo', 'inativo')")
    op.execute("UPDATE contratos SET status = 'encerrado' WHERE status = 'encerrado'")

    # 4. Enforce NOT NULL on migrated and new required columns.
    op.alter_column("contratos", "numero_contrato", nullable=False)
    op.alter_column("contratos", "objeto", nullable=False)
    op.alter_column("contratos", "sla_uptime_pct", nullable=False)
    op.alter_column("contratos", "criticidade", nullable=False)
    op.alter_column("contratos", "valor_mensal", nullable=False)

    # 5. Add UNIQUE constraint on numero_contrato.
    op.create_unique_constraint(
        "uq_contratos_numero_contrato", "contratos", ["numero_contrato"]
    )

    # 6. Drop deprecated columns.
    op.drop_column("contratos", "numero")
    op.drop_column("contratos", "descricao")
    op.drop_column("contratos", "sla_prometido_pct")
    op.drop_column("contratos", "moeda")
    op.drop_column("contratos", "renovacao_auto")
    op.drop_column("contratos", "anexo_path")

    # 7. Add indexes required by the spec: (fornecedor_id, status, deleted_at).
    op.create_index(
        "idx_contratos_fornecedor_status",
        "contratos",
        ["fornecedor_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_contratos_status",
        "contratos",
        ["status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_contratos_deleted_at",
        "contratos",
        ["deleted_at"],
    )
    op.create_index(
        "idx_contratos_datas",
        "contratos",
        ["data_inicio", "data_fim"],
    )
    op.create_index(
        "idx_contratos_criticidade",
        "contratos",
        ["criticidade"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Revert contratos v2 schema changes."""
    # Drop new indexes.
    op.drop_index("idx_contratos_criticidade", table_name="contratos")
    op.drop_index("idx_contratos_datas", table_name="contratos")
    op.drop_index("idx_contratos_deleted_at", table_name="contratos")
    op.drop_index("idx_contratos_status", table_name="contratos")
    op.drop_index("idx_contratos_fornecedor_status", table_name="contratos")

    # Drop unique constraint.
    op.drop_constraint("uq_contratos_numero_contrato", "contratos", type_="unique")

    # Restore deprecated columns.
    op.add_column("contratos", sa.Column("numero", sa.Text(), nullable=True))
    op.add_column("contratos", sa.Column("descricao", sa.Text(), nullable=True))
    op.add_column("contratos", sa.Column("sla_prometido_pct", sa.Numeric(5, 2), nullable=True))
    op.add_column(
        "contratos",
        sa.Column("moeda", sa.String(3), nullable=True, server_default="BRL"),
    )
    op.add_column(
        "contratos",
        sa.Column("renovacao_auto", sa.Boolean(), nullable=True, server_default="false"),
    )
    op.add_column("contratos", sa.Column("anexo_path", sa.Text(), nullable=True))

    # Migrate data back.
    op.execute("UPDATE contratos SET numero = numero_contrato")
    op.execute("UPDATE contratos SET descricao = objeto")
    op.execute("UPDATE contratos SET sla_prometido_pct = sla_uptime_pct")
    op.execute("UPDATE contratos SET status = 'ativo' WHERE status = 'vigente'")

    # Restore NOT NULL on numero.
    op.alter_column("contratos", "numero", nullable=False)

    # Remove new columns.
    op.drop_column("contratos", "criticidade")
    op.drop_column("contratos", "sla_uptime_pct")
    op.drop_column("contratos", "objeto")
    op.drop_column("contratos", "numero_contrato")

    # Restore valor_mensal nullability.
    op.alter_column("contratos", "valor_mensal", nullable=True)

    # Re-add generated column.
    op.execute(
        "ALTER TABLE contratos "
        "ADD COLUMN valor_anual NUMERIC(14,2) "
        "GENERATED ALWAYS AS (valor_mensal * 12) STORED"
    )
