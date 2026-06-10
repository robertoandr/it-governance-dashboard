"""Modelos ORM para o módulo de alertas com causa-raiz.

Tabelas:
  alert_log    — histórico imutável de todos os alertas disparados
  active_alerts — estado atual: 1 linha por host em alerta ativo
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from itgov.models.db.base import Base


class AlertLog(Base):
    """Registro imutável de cada alerta disparado ou resolvido."""

    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    hostgroup: Mapped[str] = mapped_column(String(255), nullable=False)
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    # critical | warning | info
    severidade: Mapped[str] = mapped_column(String(20), nullable=False)
    msg: Mapped[str] = mapped_column(Text, nullable=False)
    # open | resolved
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_alert_log_host_status", "host_id", "status"),)

    def __repr__(self) -> str:
        return f"<AlertLog id={self.id} host_id={self.host_id!r} tipo={self.tipo!r} status={self.status!r}>"


class ActiveAlert(Base):
    """Estado atual de alertas: exatamente 1 linha por host em alerta.

    Substituída (upsert) a cada transição de estado. Removida quando resolvida.
    Consumida pelo endpoint GET /v1/alerts/active e pelas telas de TV.
    """

    __tablename__ = "active_alerts"

    host_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    host_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hostgroup: Mapped[str] = mapped_column(String(255), nullable=False)
    # DVR_DOWN | CAMERA_DOWN | ATIVO_DOWN
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    severidade: Mapped[str] = mapped_column(String(20), nullable=False)
    msg: Mapped[str] = mapped_column(Text, nullable=False)
    # número de câmeras-filhas afetadas (apenas para DVR_DOWN)
    filhos_afetados: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_active_alerts_tipo", "tipo"),)

    def __repr__(self) -> str:
        return f"<ActiveAlert host_id={self.host_id!r} tipo={self.tipo!r}>"
