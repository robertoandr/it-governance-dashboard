"""Tests for MetricsAggregator."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.models.governance import PillarID
from app.services.metrics_aggregator import MetricsAggregator
from app.services.mock_data import MockMetricsProvider


@pytest.fixture
def aggregator() -> MetricsAggregator:
    return MetricsAggregator()


class TestCalculateFullScore:
    def test_returns_five_pillars(self, aggregator: MetricsAggregator) -> None:
        result = asyncio.run(aggregator.calculate_full_score())
        assert len(result.pillars) == 5

    def test_global_score_in_range(self, aggregator: MetricsAggregator) -> None:
        result = asyncio.run(aggregator.calculate_full_score())
        assert 0.0 <= result.global_score <= 100.0

    def test_status_is_valid(self, aggregator: MetricsAggregator) -> None:
        result = asyncio.run(aggregator.calculate_full_score())
        assert result.status in {"OPERACIONAL", "DEGRADADO", "CRÍTICO"}

    def test_all_pillars_have_components(self, aggregator: MetricsAggregator) -> None:
        result = asyncio.run(aggregator.calculate_full_score())
        for pillar in result.pillars:
            assert len(pillar.components) >= 3, f"{pillar.id} has too few components"

    def test_computed_at_set(self, aggregator: MetricsAggregator) -> None:
        result = asyncio.run(aggregator.calculate_full_score())
        assert result.computed_at != ""

    def test_reproducible_with_same_seed(self) -> None:
        agg1 = MetricsAggregator(provider=MockMetricsProvider(seed=1))
        agg2 = MetricsAggregator(provider=MockMetricsProvider(seed=1))
        r1 = asyncio.run(agg1.calculate_full_score())
        r2 = asyncio.run(agg2.calculate_full_score())
        assert r1.global_score == r2.global_score

    def test_different_seeds_differ(self) -> None:
        agg1 = MetricsAggregator(provider=MockMetricsProvider(seed=1))
        agg2 = MetricsAggregator(provider=MockMetricsProvider(seed=99))
        r1 = asyncio.run(agg1.calculate_full_score())
        r2 = asyncio.run(agg2.calculate_full_score())
        # Very unlikely to be exactly equal with different seeds
        assert r1.global_score != r2.global_score


class TestExceptionHandling:
    def test_failing_pillar_returns_critico(self) -> None:
        provider = MockMetricsProvider()
        # Make one method raise
        provider.get_risk_metrics = MagicMock(side_effect=RuntimeError("source down"))
        agg = MetricsAggregator(provider=provider)
        result = asyncio.run(agg.calculate_full_score())

        risk_pillar = next(p for p in result.pillars if p.id == PillarID.RISK_MANAGEMENT)
        assert risk_pillar.status == "CRÍTICO"
        assert risk_pillar.score == 0.0

    def test_single_failing_pillar_does_not_crash(self) -> None:
        provider = MockMetricsProvider()
        provider.get_strategic_metrics = MagicMock(side_effect=ValueError("boom"))
        agg = MetricsAggregator(provider=provider)
        result = asyncio.run(agg.calculate_full_score())
        assert len(result.pillars) == 5


class TestMockProvider:
    def test_all_methods_return_dicts(self) -> None:
        p = MockMetricsProvider()
        for method in [
            p.get_strategic_metrics,
            p.get_value_metrics,
            p.get_risk_metrics,
            p.get_resource_metrics,
            p.get_performance_metrics,
        ]:
            data = method()
            assert isinstance(data, dict)
            assert "components" in data
            assert isinstance(data["components"], list)
            assert len(data["components"]) >= 3

    def test_component_values_in_range(self) -> None:
        p = MockMetricsProvider()
        for data in [
            p.get_strategic_metrics(),
            p.get_value_metrics(),
            p.get_risk_metrics(),
            p.get_resource_metrics(),
            p.get_performance_metrics(),
        ]:
            for c in data["components"]:
                assert 0.0 <= c["value"] <= 100.0, f"Value out of range: {c}"
