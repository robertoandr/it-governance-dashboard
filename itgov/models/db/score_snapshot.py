"""Persistência do histórico de score de governança.

ScoreSnapshotDB  → uma linha por execução de cálculo (score global + cobertura).
PillarSnapshotDB → uma linha por pilar dentro de cada snapshot.

Campos anti-mentira temporal:
  data_source  → DataSource real do pilar no momento da coleta (COMING_SOON = estimado).
  is_real      → bool derivado: data_source != COMING_SOON.
  coverage_*   → contagem agregada no snapshot para filtrar gráficos históricos
                 e nunca mascarar score estimado como real.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from itgov.models.db.base import Base


class ScoreSnapshotDB(Base):
    """Snapshot do score global de governança.

    Attributes:
        id: Identificador UUID v4.
        global_score: Score ponderado de todos os pilares (0-100).
        status: OPERACIONAL | DEGRADADO | CRÍTICO.
        trend: up | down | stable.
        coverage_real: Quantidade de pilares com fonte real (não COMING_SOON).
        coverage_total: Total de pilares avaliados.
        computed_at: Momento em que o score foi calculado (do GovernanceScore).
        created_at: Momento em que o snapshot foi persistido.
        pillars: Lista de PillarSnapshotDB associados.
    """

    __tablename__ = "score_snapshots"

    # ─── Identity ────────────────────────────────────────────────────
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # ─── Score global ────────────────────────────────────────────────
    global_score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    trend: Mapped[str] = mapped_column(String(10), nullable=False)

    # ─── Cobertura — anti-mentira temporal ───────────────────────────
    coverage_real: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_total: Mapped[int] = mapped_column(Integer, nullable=False)

    # ─── Timestamps ──────────────────────────────────────────────────
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ─── Relacionamento ──────────────────────────────────────────────
    pillars: Mapped[list[PillarSnapshotDB]] = relationship(
        "PillarSnapshotDB",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # ─── Índices ─────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_score_snapshots_created_at", "created_at"),
        Index("ix_score_snapshots_computed_at", "computed_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ScoreSnapshotDB(id={self.id!s:.8}, "
            f"global_score={self.global_score}, "
            f"coverage={self.coverage_real}/{self.coverage_total})>"
        )


class PillarSnapshotDB(Base):
    """Score de um pilar individual dentro de um ScoreSnapshotDB.

    Attributes:
        id: Identificador UUID v4.
        snapshot_id: FK para score_snapshots (CASCADE DELETE).
        pillar_id: Valor do PillarID enum (ex. "risk_management").
        score: Score do pilar (0-100) no momento do snapshot.
        weight: Peso do pilar na composição do score global.
        status: OPERACIONAL | DEGRADADO | CRÍTICO.
        trend: up | down | stable.
        data_source: Valor do DataSource enum — preserva origem real do dado.
        is_real: True se data_source != COMING_SOON (dado real, não estimado).
        snapshot: Referência ao ScoreSnapshotDB pai.
    """

    __tablename__ = "pillar_snapshots"

    # ─── Identity ────────────────────────────────────────────────────
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("score_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ─── Pilar ───────────────────────────────────────────────────────
    pillar_id: Mapped[str] = mapped_column(String(50), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    trend: Mapped[str] = mapped_column(String(10), nullable=False)

    # ─── Qualidade do dado — anti-mentira temporal ───────────────────
    data_source: Mapped[str] = mapped_column(String(20), nullable=False)
    is_real: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # ─── Relacionamento ──────────────────────────────────────────────
    snapshot: Mapped[ScoreSnapshotDB] = relationship(
        "ScoreSnapshotDB",
        back_populates="pillars",
    )

    def __repr__(self) -> str:
        return (
            f"<PillarSnapshotDB(pillar={self.pillar_id!r}, "
            f"score={self.score}, source={self.data_source!r}, "
            f"is_real={self.is_real})>"
        )
