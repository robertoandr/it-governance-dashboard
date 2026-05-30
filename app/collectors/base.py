"""BaseCollector — pipeline padronizado fetch → transform → write."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import structlog

from app.storage.influxdb.client import GovernanceInfluxClient
from app.storage.influxdb.models import GovernancePoint


class BaseCollector[T](ABC):
    """Base para todos os coletores (Zabbix, GitHub, Zendesk).

    Implementa pipeline padronizado: fetch → transform → write,
    com logging estruturado e métricas de duração.

    Args:
        influx: Cliente InfluxDB já configurado.
        name: Nome do coletor para logs estruturados.
    """

    def __init__(self, influx: GovernanceInfluxClient, name: str) -> None:
        self.influx = influx
        self.log = structlog.get_logger(collector=name)

    @abstractmethod
    async def fetch(self) -> list[T]:
        """Busca dados crus da fonte externa."""

    @abstractmethod
    def transform(self, raw: list[T]) -> list[GovernancePoint]:
        """Converte raw → pontos InfluxDB."""

    async def collect(self) -> int:
        """Pipeline completo: fetch → transform → write.

        Returns:
            Número de pontos escritos no InfluxDB.

        Raises:
            Exception: Propaga qualquer falha de fetch, transform ou write.
        """
        start = time.perf_counter()
        self.log.info("collect.start")
        try:
            raw = await self.fetch()
            self.log.info("fetch.done", count=len(raw))
            points = self.transform(raw)
            self.log.info("transform.done", count=len(points))
            if points:
                await self.influx.write_points(points)
            duration_ms = int((time.perf_counter() - start) * 1000)
            self.log.info("collect.done", count=len(points), duration_ms=duration_ms)
            return len(points)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            self.log.error("collect.failed", error=str(exc), duration_ms=duration_ms)
            raise
