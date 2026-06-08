"""Async metrics aggregator that collects data for all five governance pillars."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.config import get_settings
from app.models.governance import DataSource, GovernanceScore, PillarID, PillarScore
from app.services.mock_data import MockMetricsProvider
from app.services.score_calculator import ScoreCalculator

log = structlog.get_logger(__name__)


def _build_provider() -> MockMetricsProvider:
    """Return an InfluxDBMetricsProvider when InfluxDB is enabled, else mock."""
    settings = get_settings()
    if settings.influx.enabled:
        from app.services.influxdb_provider import InfluxDBMetricsProvider

        log.info("metrics_provider", kind="influxdb")
        return InfluxDBMetricsProvider()  # type: ignore[return-value]
    log.info("metrics_provider", kind="mock")
    return MockMetricsProvider()


def _apply_meta(pillar: PillarScore, data: dict[str, Any]) -> PillarScore:
    """Merge _meta from a provider response into a PillarScore."""
    meta = data.get("_meta")
    if not meta:
        return pillar
    ds = DataSource(meta.get("data_source", DataSource.MOCK))
    updated = pillar.model_copy(
        update={
            "data_source": ds,
            "last_collected": meta.get("last_collected"),
            "collector_eta": meta.get("collector_eta"),
        }
    )
    if ds == DataSource.MOCK:
        log.warning(
            "serving_mock_pillar",
            pillar=updated.id.value,
            eta=updated.collector_eta,
        )
    return updated


class MetricsAggregator:
    """Collects metrics from all sources and returns a full GovernanceScore.

    Args:
        provider: Metrics source; defaults to provider selected by config.
        calculator: Score engine; defaults to ScoreCalculator.
    """

    def __init__(
        self,
        provider: MockMetricsProvider | None = None,
        calculator: ScoreCalculator | None = None,
    ) -> None:
        self._provider = provider or _build_provider()
        self._calculator = calculator or ScoreCalculator()

    async def calculate_full_score(self) -> GovernanceScore:
        """Collect all pillar metrics in parallel and compute global score.

        Returns:
            Fully populated GovernanceScore.
        """
        log.info("aggregation_started")

        results = await asyncio.gather(
            self._collect_strategic(),
            self._collect_value(),
            self._collect_risk(),
            self._collect_resource(),
            self._collect_performance(),
            return_exceptions=True,
        )

        pillar_scores = []
        for pillar_id, result in zip(PillarID, results, strict=False):
            if isinstance(result, Exception):
                log.error(
                    "pillar_collection_failed",
                    pillar=pillar_id.value,
                    error=str(result),
                )
                from app.models.governance import PILLAR_META

                meta = PILLAR_META[pillar_id]
                pillar_scores.append(
                    PillarScore(
                        id=pillar_id,
                        label=meta["label"],
                        score=0.0,
                        weight=meta["weight"],
                        color=meta["color"],
                        status="CRÍTICO",
                        trend="down",
                        components=[],
                        previous_score=None,
                    )
                )
            else:
                pillar_scores.append(result)

        governance = self._calculator.calculate_global(pillar_scores)
        log.info("aggregation_completed", global_score=governance.global_score)
        return governance

    async def _collect_strategic(self) -> PillarScore:
        log.debug("collecting_pillar", pillar="strategic_alignment")
        data = self._provider.get_strategic_metrics()
        pillar = self._calculator.calculate_pillar(
            PillarID.STRATEGIC_ALIGNMENT,
            data["components"],
            data.get("previous_score"),
        )
        return _apply_meta(pillar, data)

    async def _collect_value(self) -> PillarScore:
        log.debug("collecting_pillar", pillar="value_delivery")
        data = self._provider.get_value_metrics()
        pillar = self._calculator.calculate_pillar(
            PillarID.VALUE_DELIVERY,
            data["components"],
            data.get("previous_score"),
        )
        return _apply_meta(pillar, data)

    async def _collect_risk(self) -> PillarScore:
        log.debug("collecting_pillar", pillar="risk_management")
        data = self._provider.get_risk_metrics()
        pillar = self._calculator.calculate_pillar(
            PillarID.RISK_MANAGEMENT,
            data["components"],
            data.get("previous_score"),
        )
        return _apply_meta(pillar, data)

    async def _collect_resource(self) -> PillarScore:
        log.debug("collecting_pillar", pillar="resource_management")
        data = self._provider.get_resource_metrics()
        pillar = self._calculator.calculate_pillar(
            PillarID.RESOURCE_MANAGEMENT,
            data["components"],
            data.get("previous_score"),
        )
        return _apply_meta(pillar, data)

    async def _collect_performance(self) -> PillarScore:
        log.debug("collecting_pillar", pillar="performance_measure")
        data = self._provider.get_performance_metrics()
        pillar = self._calculator.calculate_pillar(
            PillarID.PERFORMANCE_MEASURE,
            data["components"],
            data.get("previous_score"),
        )
        return _apply_meta(pillar, data)
