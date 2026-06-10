"""Persistência deduplicada de snapshots do score de governança.

Regras de dedupe — salva SE qualquer uma das condições for verdadeira:
  1. Nenhum snapshot anterior existe (primeiro registro).
  2. |global_score atual − último| >= DEDUPE_THRESHOLD (mudança material de score).
  3. Qualquer pilar mudou de data_source (mudança de cobertura real/estimada).

coverage_real e coverage_total são DERIVADOS da lista de PillarScore —
não modificam GovernanceScore, apenas registram a cobertura no momento
da persistência para que gráficos históricos não mascarem dados estimados.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.governance import DataSource, GovernanceScore, PillarScore
from itgov.models.db.score_snapshot import PillarSnapshotDB, ScoreSnapshotDB

log = structlog.get_logger(__name__)

DEDUPE_THRESHOLD: float = 0.5


def _coverage(pillars: list[PillarScore]) -> tuple[int, int]:
    """Retorna (coverage_real, coverage_total) derivado dos pilares."""
    real = sum(1 for p in pillars if p.data_source != DataSource.COMING_SOON)
    return real, len(pillars)


def _parse_computed_at(raw: str | datetime) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if not raw:
        return datetime.now(UTC)
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class SnapshotService:
    """Salva ScoreSnapshotDB com lógica de dedupe automática.

    Args:
        session: Session SQLAlchemy ativa para todas as operações de DB.

    Example:
        >>> with Session(engine) as session:
        ...     svc = SnapshotService(session)
        ...     snap = svc.maybe_save(governance_score)
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._log = log.bind(service="SnapshotService")

    # ─── Public ──────────────────────────────────────────────────────

    def maybe_save(self, score: GovernanceScore) -> ScoreSnapshotDB | None:
        """Persiste snapshot se as regras de dedupe forem satisfeitas.

        Args:
            score: GovernanceScore computado pelo MetricsAggregator.

        Returns:
            ScoreSnapshotDB persistido, ou None se ignorado por dedupe.
        """
        last = self._last_snapshot()
        if not self._should_save(score, last):
            self._log.info(
                "snapshot_dedupe_ignorado",
                global_score=score.global_score,
                last_score=last.global_score if last else None,
            )
            return None
        return self._persist(score)

    # ─── Internal ────────────────────────────────────────────────────

    def _last_snapshot(self) -> ScoreSnapshotDB | None:
        stmt = (
            select(ScoreSnapshotDB)
            .options(selectinload(ScoreSnapshotDB.pillars))
            .order_by(ScoreSnapshotDB.created_at.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def _should_save(self, score: GovernanceScore, last: ScoreSnapshotDB | None) -> bool:
        if last is None:
            return True
        if abs(score.global_score - last.global_score) >= DEDUPE_THRESHOLD:
            return True
        last_sources = {p.pillar_id: p.data_source for p in last.pillars}
        return any(last_sources.get(p.id.value) != p.data_source.value for p in score.pillars)

    def _persist(self, score: GovernanceScore) -> ScoreSnapshotDB:
        coverage_real, coverage_total = _coverage(score.pillars)

        snap = ScoreSnapshotDB(
            global_score=score.global_score,
            status=score.status,
            trend=score.trend,
            coverage_real=coverage_real,
            coverage_total=coverage_total,
            computed_at=_parse_computed_at(score.computed_at),
        )
        snap.pillars = [
            PillarSnapshotDB(
                pillar_id=p.id.value,
                score=p.score,
                weight=p.weight,
                status=p.status,
                trend=p.trend,
                data_source=p.data_source.value,
                is_real=(p.data_source != DataSource.COMING_SOON),
            )
            for p in score.pillars
        ]

        self._session.add(snap)
        self._session.commit()

        # Recarrega com pilares para acesso imediato pelo caller
        result = self._session.execute(
            select(ScoreSnapshotDB).options(selectinload(ScoreSnapshotDB.pillars)).where(ScoreSnapshotDB.id == snap.id)
        ).scalar_one()

        self._log.info(
            "snapshot_salvo",
            id=str(result.id),
            global_score=result.global_score,
            coverage=f"{coverage_real}/{coverage_total}",
        )
        return result
