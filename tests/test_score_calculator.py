"""Tests for ScoreCalculator."""

from __future__ import annotations

import pytest

from app.models.governance import PillarID
from app.services.score_calculator import ScoreCalculator, _status, _trend

SAMPLE_COMPONENTS = [
    {"id": "a", "label": "A", "value": 80.0, "weight": 2.0, "trend": "stable", "source": "mock"},
    {"id": "b", "label": "B", "value": 60.0, "weight": 1.0, "trend": "down", "source": "mock"},
]


@pytest.fixture
def calc() -> ScoreCalculator:
    return ScoreCalculator()


class TestStatusThresholds:
    def test_operacional(self) -> None:
        assert _status(85.0) == "OPERACIONAL"
        assert _status(100.0) == "OPERACIONAL"

    def test_degradado(self) -> None:
        assert _status(84.9) == "DEGRADADO"
        assert _status(60.0) == "DEGRADADO"

    def test_critico(self) -> None:
        assert _status(59.9) == "CRÍTICO"
        assert _status(0.0) == "CRÍTICO"


class TestTrend:
    def test_up(self) -> None:
        assert _trend(80.0, 75.0) == "up"

    def test_down(self) -> None:
        assert _trend(70.0, 75.0) == "down"

    def test_stable(self) -> None:
        assert _trend(75.0, 75.0) == "stable"
        assert _trend(75.5, 75.0) == "stable"

    def test_no_previous(self) -> None:
        assert _trend(80.0, None) == "stable"


class TestCalculatePillar:
    def test_weighted_average(self, calc: ScoreCalculator) -> None:
        pillar = calc.calculate_pillar(PillarID.RISK_MANAGEMENT, SAMPLE_COMPONENTS)
        # (80 * 2 + 60 * 1) / 3 = 73.33
        assert abs(pillar.score - 73.33) < 0.1

    def test_pillar_id_preserved(self, calc: ScoreCalculator) -> None:
        pillar = calc.calculate_pillar(PillarID.VALUE_DELIVERY, SAMPLE_COMPONENTS)
        assert pillar.id == PillarID.VALUE_DELIVERY

    def test_components_count(self, calc: ScoreCalculator) -> None:
        pillar = calc.calculate_pillar(PillarID.STRATEGIC_ALIGNMENT, SAMPLE_COMPONENTS)
        assert len(pillar.components) == 2

    def test_status_populated(self, calc: ScoreCalculator) -> None:
        pillar = calc.calculate_pillar(PillarID.PERFORMANCE_MEASURE, SAMPLE_COMPONENTS)
        assert pillar.status in {"OPERACIONAL", "DEGRADADO", "CRÍTICO"}

    def test_trend_with_previous(self, calc: ScoreCalculator) -> None:
        pillar = calc.calculate_pillar(PillarID.RISK_MANAGEMENT, SAMPLE_COMPONENTS, previous_score=60.0)
        assert pillar.trend == "up"

    def test_empty_components(self, calc: ScoreCalculator) -> None:
        pillar = calc.calculate_pillar(PillarID.RESOURCE_MANAGEMENT, [])
        assert pillar.score == 0.0
        assert pillar.status == "CRÍTICO"

    def test_score_clamped(self, calc: ScoreCalculator) -> None:
        comps = [{"id": "x", "label": "X", "value": 100.0, "weight": 1.0, "trend": "up", "source": "mock"}]
        pillar = calc.calculate_pillar(PillarID.VALUE_DELIVERY, comps)
        assert pillar.score <= 100.0

    def test_estimated_excluido_da_media(self, calc: ScoreCalculator) -> None:
        # Componente real vale 40; estimado vale 100 — não deve puxar a média para cima
        comps = [
            {
                "id": "real",
                "label": "Real",
                "value": 40.0,
                "weight": 1.0,
                "trend": "stable",
                "source": "zabbix",
                "is_estimated": False,
            },
            {
                "id": "est",
                "label": "Est",
                "value": 100.0,
                "weight": 1.0,
                "trend": "stable",
                "source": "coming_soon",
                "is_estimated": True,
            },
        ]
        pillar = calc.calculate_pillar(PillarID.RESOURCE_MANAGEMENT, comps)
        assert pillar.score == pytest.approx(40.0)

    def test_estimated_ainda_aparece_nos_components(self, calc: ScoreCalculator) -> None:
        # is_estimated=True não remove o componente da lista — só da média
        comps = [
            {
                "id": "real",
                "label": "Real",
                "value": 40.0,
                "weight": 1.0,
                "trend": "stable",
                "source": "zabbix",
                "is_estimated": False,
            },
            {
                "id": "est",
                "label": "Est",
                "value": 100.0,
                "weight": 1.0,
                "trend": "stable",
                "source": "coming_soon",
                "is_estimated": True,
            },
        ]
        pillar = calc.calculate_pillar(PillarID.RESOURCE_MANAGEMENT, comps)
        assert len(pillar.components) == 2

    def test_todos_estimados_usa_fallback(self, calc: ScoreCalculator) -> None:
        # Se 100% dos componentes forem estimados, usa todos (evita score=0 enganoso)
        comps = [
            {
                "id": "a",
                "label": "A",
                "value": 60.0,
                "weight": 1.0,
                "trend": "stable",
                "source": "coming_soon",
                "is_estimated": True,
            },
            {
                "id": "b",
                "label": "B",
                "value": 80.0,
                "weight": 1.0,
                "trend": "stable",
                "source": "coming_soon",
                "is_estimated": True,
            },
        ]
        pillar = calc.calculate_pillar(PillarID.RESOURCE_MANAGEMENT, comps)
        assert pillar.score == pytest.approx(70.0)


class TestCalculateGlobal:
    def test_five_pillars(self, calc: ScoreCalculator) -> None:
        pillars = []
        for pid in PillarID:
            pillars.append(calc.calculate_pillar(pid, SAMPLE_COMPONENTS))
        gov = calc.calculate_global(pillars)
        assert len(gov.pillars) == 5

    def test_global_score_is_weighted(self, calc: ScoreCalculator) -> None:
        # All pillars same score → global == pillar score
        comps = [{"id": "x", "label": "X", "value": 75.0, "weight": 1.0, "trend": "stable", "source": "mock"}]
        pillars = [calc.calculate_pillar(pid, comps) for pid in PillarID]
        gov = calc.calculate_global(pillars)
        assert abs(gov.global_score - 75.0) < 0.5

    def test_global_status_set(self, calc: ScoreCalculator) -> None:
        comps = [{"id": "x", "label": "X", "value": 90.0, "weight": 1.0, "trend": "up", "source": "mock"}]
        pillars = [calc.calculate_pillar(pid, comps) for pid in PillarID]
        gov = calc.calculate_global(pillars)
        assert gov.status == "OPERACIONAL"

    def test_computed_at_set(self, calc: ScoreCalculator) -> None:
        comps = [{"id": "x", "label": "X", "value": 70.0, "weight": 1.0, "trend": "stable", "source": "mock"}]
        pillars = [calc.calculate_pillar(pid, comps) for pid in PillarID]
        gov = calc.calculate_global(pillars)
        assert gov.computed_at != ""

    def test_pillars_ordered(self, calc: ScoreCalculator) -> None:
        comps = [{"id": "x", "label": "X", "value": 70.0, "weight": 1.0, "trend": "stable", "source": "mock"}]
        pillars = [calc.calculate_pillar(pid, comps) for pid in reversed(list(PillarID))]
        gov = calc.calculate_global(pillars)
        # Expect ordered by PILLAR_META order
        from app.models.governance import PILLAR_META

        orders = [PILLAR_META[p.id]["order"] for p in gov.pillars]
        assert orders == sorted(orders)
