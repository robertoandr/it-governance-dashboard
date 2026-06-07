"""InfluxDB-backed metrics provider for governance pillars.

Reads real time-series data from InfluxDB; falls back to MockMetricsProvider
for pillars that do not yet have real measurements.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from app.services.mock_data import MockMetricsProvider

if TYPE_CHECKING:
    from influxdb_client import InfluxDBClient

log = structlog.get_logger(__name__)

# Scoring constants — tunable via env or ADR when baselines are established
_IDEAL_PR_MONTH = 40  # PRs/month considered 100% delivery pace
_IDEAL_MERGE_HOURS = 8.0  # avg merge time (h) considered excellent


def _pr_velocity_score(count: int) -> float:
    return round(min(100.0, count / _IDEAL_PR_MONTH * 100), 1)


def _merge_time_score(avg_hours: float) -> float:
    if avg_hours <= _IDEAL_MERGE_HOURS:
        return 100.0
    # Lose 3 points per extra hour, floor at 0
    return round(max(0.0, 100.0 - (avg_hours - _IDEAL_MERGE_HOURS) * 3), 1)


class InfluxDBMetricsProvider:
    """Governance metrics sourced from InfluxDB with mock fallback.

    Args:
        url: InfluxDB base URL.
        token: InfluxDB auth token.
        org: InfluxDB organisation name.
        bucket_raw: Raw-resolution bucket name.
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        org: str | None = None,
        bucket_raw: str | None = None,
    ) -> None:
        from app.config import get_settings

        cfg = get_settings().influx
        self._url = url or cfg.url
        self._token = token or cfg.token.get_secret_value()
        self._org = org or cfg.org
        self._bucket_raw = bucket_raw or cfg.bucket_raw
        self._mock = MockMetricsProvider()
        self.__client: InfluxDBClient | None = None

    @property
    def _client(self) -> InfluxDBClient:
        if self.__client is None:
            from influxdb_client import InfluxDBClient

            self.__client = InfluxDBClient(
                url=self._url,
                token=self._token,
                org=self._org,
            )
        return self.__client

    def _query(self, flux: str) -> list[dict[str, Any]]:
        """Execute a Flux query and return records as plain dicts."""
        try:
            tables = self._client.query_api().query(flux, org=self._org)
            rows = []
            for table in tables:
                for record in table.records:
                    rows.append(dict(record.values))
            return rows
        except Exception as exc:
            log.warning("influxdb_query_failed", error=str(exc))
            return []

    # ── Public pillar methods ────────────────────────────────────────────────

    def get_value_metrics(self) -> dict[str, Any]:
        """Value Delivery: real PR data from gov_github_pr measurement."""
        pr_count, avg_hours = self._github_pr_stats()

        if pr_count == 0:
            log.warning("github_pr_no_data", fallback="mock")
            return self._mock.get_value_metrics()

        velocity_score = _pr_velocity_score(pr_count)
        merge_score = _merge_time_score(avg_hours) if avg_hours is not None else 50.0

        log.info(
            "value_metrics_from_influxdb",
            pr_count=pr_count,
            avg_merge_hours=round(avg_hours, 2) if avg_hours else None,
            velocity_score=velocity_score,
            merge_score=merge_score,
        )

        mock = self._mock.get_value_metrics()
        # Keep mock SLA and ticket components; replace project_delivery with real PR data
        real_components = [c for c in mock["components"] if c["id"] != "project_delivery"]
        real_components.extend(
            [
                {
                    "id": "pr_velocity",
                    "label": "Velocidade de entrega (PRs/mês)",
                    "value": velocity_score,
                    "raw_value": pr_count,
                    "unit": "PRs",
                    "source": "github",
                    "weight": 1.5,
                    "trend": "stable",
                },
                {
                    "id": "pr_merge_time",
                    "label": "Tempo médio de merge",
                    "value": merge_score,
                    "raw_value": round(avg_hours, 1) if avg_hours else None,
                    "unit": "h",
                    "source": "github",
                    "weight": 1.0,
                    "trend": "up",
                },
            ]
        )
        return {"previous_score": mock["previous_score"], "components": real_components}

    # ── Pillars still backed by mock (Caminho B/C will replace these) ────────

    def get_strategic_metrics(self) -> dict[str, Any]:
        return self._mock.get_strategic_metrics()

    def get_risk_metrics(self) -> dict[str, Any]:
        return self._mock.get_risk_metrics()

    def get_resource_metrics(self) -> dict[str, Any]:
        return self._mock.get_resource_metrics()

    def get_performance_metrics(self) -> dict[str, Any]:
        return self._mock.get_performance_metrics()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _github_pr_stats(self) -> tuple[int, float | None]:
        """Return (pr_count, avg_merge_hours) for the past 30 days."""
        flux = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "gov_github_pr")
  |> filter(fn: (r) => r._field == "time_to_merge_seconds")
"""
        rows = self._query(flux)
        values = [r["_value"] for r in rows if r.get("_value") is not None]
        if not values:
            return 0, None
        avg_hours = sum(values) / len(values) / 3600
        return len(values), avg_hours
