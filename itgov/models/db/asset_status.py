"""Persistência de status de ativos monitorados via Zabbix.

AssetStatusDB       → estado ATUAL de cada host (upsert a cada poll de 60s).
AssetStatusHistoryDB → TRANSIÇÕES registradas apenas quando status muda.

Design anti-escala:
  Endpoints leem ESTA tabela — nunca chamam o Zabbix ao vivo.
  O job de polling (1 worker, 2 chamadas Zabbix) alimenta a tabela.

Design anti-mentira:
  status='unknown' quando host não tem item icmpping no Zabbix.
  Não assume 'up' por omissão.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from itgov.models.db.base import Base


class AssetStatusDB(Base):
    """Estado atual de um ativo monitorado via Zabbix.

    PK = hostid (string numérica do Zabbix). Upsertado a cada poll.

    Attributes:
        hostid: ID do host no Zabbix (PK, string numérica).
        host: Technical name do host no Zabbix.
        name: Visible name do host no Zabbix.
        asset_type: Tipo derivado do host group (camera/server/other/...).
        status: up | down | unknown.
        last_change: UTC do último evento de mudança de status.
        updated_at: UTC do último poll que tocou este registro.
    """

    __tablename__ = "asset_status"

    # ─── Identity ────────────────────────────────────────────────────
    hostid: Mapped[str] = mapped_column(String(20), primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # ─── Classificação ───────────────────────────────────────────────
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # ─── Status ──────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # ─── Timestamps ──────────────────────────────────────────────────
    last_change: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ─── Índice composto para filtros type+status (endpoint /assets) ─
    __table_args__ = (Index("ix_asset_status_type_status", "asset_type", "status"),)

    def __repr__(self) -> str:
        return (
            f"<AssetStatusDB(hostid={self.hostid!r}, host={self.host!r}, "
            f"type={self.asset_type!r}, status={self.status!r})>"
        )


class AssetStatusHistoryDB(Base):
    """Transição de status de um ativo — só gravada quando status muda.

    Attributes:
        id: UUID v4 (PK).
        hostid: ID Zabbix do host (FK lógico para asset_status).
        asset_type: Tipo do ativo no momento da transição.
        from_status: Status anterior à transição.
        to_status: Novo status após a transição.
        changed_at: UTC do momento da detecção da mudança.
        duration_seconds: Segundos no status anterior (None se primeiro poll).
    """

    __tablename__ = "asset_status_history"

    # ─── Identity ────────────────────────────────────────────────────
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hostid: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # ─── Transição ───────────────────────────────────────────────────
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)

    # ─── Temporais ───────────────────────────────────────────────────
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AssetStatusHistoryDB(hostid={self.hostid!r}, "
            f"{self.from_status!r}→{self.to_status!r}, "
            f"dur={self.duration_seconds}s)>"
        )
