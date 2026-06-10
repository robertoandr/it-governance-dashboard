"""Queries de tendência histórica do score de governança.

Lê score_snapshots e pillar_snapshots com filtro indexado em
score_snapshots.created_at (índice ix_score_snapshots_created_at).

Todos os métodos retornam os dados brutos do ORM — a camada de API
é responsável pela serialização e pelo shape de resposta.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from itgov.models.db.score_snapshot import PillarSnapshotDB, ScoreSnapshotDB

log = structlog.get_logger(__name__)


class GlobalPoint(NamedTuple):
    """Ponto de tendência do score global."""

    ts: datetime
    global_score: float
    status: str
    trend: str
    coverage_real: int
    coverage_total: int


class PillarPoint(NamedTuple):
    """Ponto de tendência de um pilar específico."""

    ts: datetime
    score: float
    weight: float
    data_source: str
    is_real: bool
    status: str
    trend: str


class TrendService:
    """Consultas históricas de score com filtro indexado por created_at.

    Args:
        session: Session SQLAlchemy ativa.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._log = log.bind(service="TrendService")

    # ─── Histórico global ─────────────────────────────────────────────

    def global_history(self, days: int) -> list[GlobalPoint]:
        """Retorna pontos do score global para os últimos `days` dias.

        Ordena por created_at ASC — mais antigo primeiro (série temporal).
        Usa índice ix_score_snapshots_created_at.

        Args:
            days: Janela de tempo em dias (1–730).

        Returns:
            Lista de GlobalPoint ordenada por ts ASC. Vazia se sem dados.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(ScoreSnapshotDB)
            .where(ScoreSnapshotDB.created_at >= since)
            .order_by(ScoreSnapshotDB.created_at.asc())
        )
        rows = self._session.execute(stmt).scalars().all()

        self._log.debug("global_history_query", days=days, points=len(rows))
        return [
            GlobalPoint(
                ts=r.created_at,
                global_score=r.global_score,
                status=r.status,
                trend=r.trend,
                coverage_real=r.coverage_real,
                coverage_total=r.coverage_total,
            )
            for r in rows
        ]

    # ─── Histórico por pilar ──────────────────────────────────────────

    def pillar_history(self, pillar_id: str, days: int) -> list[PillarPoint]:
        """Retorna pontos do score de um pilar para os últimos `days` dias.

        Faz JOIN com score_snapshots para filtrar e ordenar por created_at,
        aproveitando o índice ix_score_snapshots_created_at.

        Args:
            pillar_id: Valor do PillarID enum (ex. "risk_management").
            days: Janela de tempo em dias (1–730).

        Returns:
            Lista de PillarPoint ordenada por ts ASC. Vazia se sem dados.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(
                ScoreSnapshotDB.created_at.label("ts"),
                PillarSnapshotDB.score,
                PillarSnapshotDB.weight,
                PillarSnapshotDB.data_source,
                PillarSnapshotDB.is_real,
                PillarSnapshotDB.status,
                PillarSnapshotDB.trend,
            )
            .join(ScoreSnapshotDB, PillarSnapshotDB.snapshot_id == ScoreSnapshotDB.id)
            .where(
                PillarSnapshotDB.pillar_id == pillar_id,
                ScoreSnapshotDB.created_at >= since,
            )
            .order_by(ScoreSnapshotDB.created_at.asc())
        )
        rows = self._session.execute(stmt).all()

        self._log.debug("pillar_history_query", pillar_id=pillar_id, days=days, points=len(rows))
        return [
            PillarPoint(
                ts=row.ts,
                score=row.score,
                weight=row.weight,
                data_source=row.data_source,
                is_real=row.is_real,
                status=row.status,
                trend=row.trend,
            )
            for row in rows
        ]
