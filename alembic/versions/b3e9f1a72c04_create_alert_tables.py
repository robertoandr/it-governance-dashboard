"""create alert tables

Revision ID: b3e9f1a72c04
Revises: e760fbf6e7f6
Create Date: 2026-06-09 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3e9f1a72c04"
down_revision: Union[str, Sequence[str], None] = "e760fbf6e7f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Cria tabelas alert_log e active_alerts para o módulo de alertas com causa-raiz."""
    op.create_table(
        "alert_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("host_id", sa.String(length=20), nullable=False),
        sa.Column("hostgroup", sa.String(length=255), nullable=False),
        sa.Column("tipo", sa.String(length=50), nullable=False),
        sa.Column("severidade", sa.String(length=20), nullable=False),
        sa.Column("msg", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alert_log_host_id"), "alert_log", ["host_id"], unique=False)
    op.create_index(op.f("ix_alert_log_status"), "alert_log", ["status"], unique=False)
    op.create_index("ix_alert_log_host_status", "alert_log", ["host_id", "status"], unique=False)

    op.create_table(
        "active_alerts",
        sa.Column("host_id", sa.String(length=20), nullable=False),
        sa.Column("host_name", sa.String(length=255), nullable=False),
        sa.Column("hostgroup", sa.String(length=255), nullable=False),
        sa.Column("tipo", sa.String(length=50), nullable=False),
        sa.Column("severidade", sa.String(length=20), nullable=False),
        sa.Column("msg", sa.Text(), nullable=False),
        sa.Column("filhos_afetados", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("host_id"),
    )
    op.create_index("ix_active_alerts_tipo", "active_alerts", ["tipo"], unique=False)


def downgrade() -> None:
    """Remove tabelas de alertas."""
    op.drop_index("ix_active_alerts_tipo", table_name="active_alerts")
    op.drop_table("active_alerts")
    op.drop_index("ix_alert_log_host_status", table_name="alert_log")
    op.drop_index(op.f("ix_alert_log_status"), table_name="alert_log")
    op.drop_index(op.f("ix_alert_log_host_id"), table_name="alert_log")
    op.drop_table("alert_log")
