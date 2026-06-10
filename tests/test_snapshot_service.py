"""Testes da lógica de dedupe do SnapshotService.

Cobertura:
  - Primeiro snapshot: sempre persiste.
  - Mudança material de score (|delta| >= DEDUPE_THRESHOLD): persiste.
  - Dedupe correto (|delta| < DEDUPE_THRESHOLD, mesma data_source): ignora.
  - Mudança de data_source em qualquer pilar: persiste mesmo sem delta de score.

Estratégia de isolamento: SQLite in-memory por teste — sem contaminação entre casos.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models.governance import DataSource, GovernanceScore, PillarID, PillarScore
from itgov.models.db.base import Base
from itgov.models.db.score_snapshot import ScoreSnapshotDB
from itgov.services.snapshot_service import DEDUPE_THRESHOLD, SnapshotService

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def session():
    """Sessão SQLAlchemy em banco SQLite in-memory isolado por teste."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def svc(session: Session) -> SnapshotService:
    return SnapshotService(session)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _pillar(
    pillar_id: PillarID,
    score: float,
    data_source: DataSource = DataSource.COMING_SOON,
) -> PillarScore:
    return PillarScore(
        id=pillar_id,
        label=pillar_id.value,
        score=score,
        weight=0.2,
        color="#000000",
        status="OPERACIONAL",
        trend="stable",
        data_source=data_source,
    )


def _score(
    global_score: float,
    data_sources: dict[PillarID, DataSource] | None = None,
) -> GovernanceScore:
    ds = data_sources or {}
    pillars = [_pillar(pid, global_score, ds.get(pid, DataSource.COMING_SOON)) for pid in PillarID]
    return GovernanceScore(
        global_score=global_score,
        status="OPERACIONAL",
        trend="stable",
        pillars=pillars,
        computed_at="2026-06-09T02:00:00+00:00",
    )


def _count_snapshots(session: Session) -> int:
    return session.execute(select(func.count()).select_from(ScoreSnapshotDB)).scalar_one()


# ─── Primeiro snapshot ───────────────────────────────────────────────────────


class TestPrimeiroSnapshot:
    def test_sempre_salva(self, svc: SnapshotService) -> None:
        snap = svc.maybe_save(_score(80.0))
        assert snap is not None
        assert snap.id is not None

    def test_global_score_correto(self, svc: SnapshotService) -> None:
        snap = svc.maybe_save(_score(73.5))
        assert snap.global_score == 73.5

    def test_todos_os_pilares_persistidos(self, svc: SnapshotService) -> None:
        snap = svc.maybe_save(_score(80.0))
        assert len(snap.pillars) == len(list(PillarID))

    def test_coverage_derivado_sem_dados_reais(self, svc: SnapshotService) -> None:
        snap = svc.maybe_save(_score(80.0))
        assert snap.coverage_total == len(list(PillarID))
        assert snap.coverage_real == 0

    def test_coverage_derivado_com_um_dado_real(self, svc: SnapshotService) -> None:
        ds = {PillarID.RISK_MANAGEMENT: DataSource.LIVE}
        snap = svc.maybe_save(_score(80.0, ds))
        assert snap.coverage_real == 1
        assert snap.coverage_total == len(list(PillarID))


# ─── Mudança material de score ───────────────────────────────────────────────


class TestMudancaMaterial:
    def test_delta_exato_threshold_salva(self, svc: SnapshotService, session: Session) -> None:
        svc.maybe_save(_score(80.0))
        snap2 = svc.maybe_save(_score(80.0 + DEDUPE_THRESHOLD))
        assert snap2 is not None
        assert _count_snapshots(session) == 2

    def test_delta_negativo_exato_threshold_salva(self, svc: SnapshotService, session: Session) -> None:
        svc.maybe_save(_score(80.0))
        snap2 = svc.maybe_save(_score(80.0 - DEDUPE_THRESHOLD))
        assert snap2 is not None
        assert _count_snapshots(session) == 2

    def test_delta_acima_threshold_salva(self, svc: SnapshotService, session: Session) -> None:
        svc.maybe_save(_score(80.0))
        snap2 = svc.maybe_save(_score(90.0))
        assert snap2 is not None
        assert _count_snapshots(session) == 2


# ─── Dedupe correto ──────────────────────────────────────────────────────────


class TestDedupeCorreto:
    def test_score_identico_nao_salva(self, svc: SnapshotService, session: Session) -> None:
        svc.maybe_save(_score(80.0))
        result = svc.maybe_save(_score(80.0))
        assert result is None
        assert _count_snapshots(session) == 1

    def test_delta_abaixo_threshold_nao_salva(self, svc: SnapshotService, session: Session) -> None:
        svc.maybe_save(_score(80.0))
        result = svc.maybe_save(_score(80.0 + DEDUPE_THRESHOLD - 0.01))
        assert result is None
        assert _count_snapshots(session) == 1

    def test_variacao_minima_multiplas_chamadas_nao_acumula(self, svc: SnapshotService, session: Session) -> None:
        svc.maybe_save(_score(80.0))
        svc.maybe_save(_score(80.1))
        svc.maybe_save(_score(80.2))
        svc.maybe_save(_score(80.3))
        assert _count_snapshots(session) == 1

    def test_retorna_none_no_dedupe(self, svc: SnapshotService) -> None:
        svc.maybe_save(_score(80.0))
        assert svc.maybe_save(_score(80.2)) is None


# ─── Mudança de data_source ──────────────────────────────────────────────────


class TestMudancaDataSource:
    def test_coming_soon_para_live_salva_sem_delta(self, svc: SnapshotService, session: Session) -> None:
        svc.maybe_save(_score(80.0))
        ds2 = {PillarID.RISK_MANAGEMENT: DataSource.LIVE}
        snap2 = svc.maybe_save(_score(80.0, ds2))
        assert snap2 is not None
        assert _count_snapshots(session) == 2

    def test_coming_soon_para_partial_salva_sem_delta(self, svc: SnapshotService, session: Session) -> None:
        svc.maybe_save(_score(80.0))
        ds2 = {PillarID.VALUE_DELIVERY: DataSource.PARTIAL}
        snap2 = svc.maybe_save(_score(80.0, ds2))
        assert snap2 is not None

    def test_data_source_registrado_corretamente_por_pilar(self, svc: SnapshotService) -> None:
        ds = {PillarID.VALUE_DELIVERY: DataSource.PARTIAL}
        snap = svc.maybe_save(_score(80.0, ds))
        vd = next(p for p in snap.pillars if p.pillar_id == PillarID.VALUE_DELIVERY.value)
        assert vd.data_source == DataSource.PARTIAL.value
        assert vd.is_real is True

    def test_coming_soon_is_real_false(self, svc: SnapshotService) -> None:
        snap = svc.maybe_save(_score(80.0))
        for p in snap.pillars:
            assert p.is_real is False

    def test_live_is_real_true(self, svc: SnapshotService) -> None:
        ds = dict.fromkeys(PillarID, DataSource.LIVE)
        snap = svc.maybe_save(_score(80.0, ds))
        for p in snap.pillars:
            assert p.is_real is True

    def test_mesma_data_source_nao_reaplica_save(self, svc: SnapshotService, session: Session) -> None:
        ds = {PillarID.RISK_MANAGEMENT: DataSource.LIVE}
        svc.maybe_save(_score(80.0, ds))
        result = svc.maybe_save(_score(80.0, ds))
        assert result is None
        assert _count_snapshots(session) == 1
