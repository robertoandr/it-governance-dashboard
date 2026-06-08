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
_EXPECTED_HOSTS = 150  # baseline fleet size for monitoring_coverage (%)
# Risk penalties per open Zabbix severity (deducted from 100)
_PENALTY_DISASTER = 20.0
_PENALTY_HIGH = 5.0


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
        """Value Delivery: real PR data (GitHub) + real SLA (Zabbix)."""
        pr_count, avg_hours = self._github_pr_stats()
        zabbix = self._zabbix_stats()

        # SLA compliance from live Zabbix data: hosts without disaster/high problems
        sla_component: dict[str, Any] | None = None
        if zabbix:
            hosts = zabbix.get("hosts_total", 1) or 1
            disaster = zabbix.get("problems_disaster", 0)
            high = zabbix.get("problems_high", 0)
            sla_raw = round((hosts - disaster - high) / hosts * 100, 1)
            sla_component = {
                "id": "sla_compliance",
                "label": "Conformidade de SLA geral",
                "value": sla_raw,
                "raw_value": sla_raw,
                "unit": "%",
                "source": "zabbix",
                "weight": 3.0,
                "trend": "up",
            }
            log.info("sla_from_zabbix", hosts=int(hosts), disaster=int(disaster), high=int(high), sla_pct=sla_raw)

        if pr_count == 0 and not sla_component:
            log.warning("value_no_real_data", fallback="mock")
            return self._mock.get_value_metrics()

        mock = self._mock.get_value_metrics()
        exclude = {"project_delivery"}
        if sla_component:
            exclude.add("sla_compliance")

        real_components: list[dict[str, Any]] = []
        if sla_component:
            real_components.append(sla_component)
        real_components.extend(c for c in mock["components"] if c["id"] not in exclude)

        if pr_count > 0:
            velocity_score = _pr_velocity_score(pr_count)
            merge_score = _merge_time_score(avg_hours) if avg_hours is not None else 50.0
            log.info(
                "value_metrics_from_influxdb",
                pr_count=pr_count,
                avg_merge_hours=round(avg_hours, 2) if avg_hours else None,
                velocity_score=velocity_score,
                merge_score=merge_score,
            )
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

    def get_strategic_metrics(self) -> dict[str, Any]:
        return self._mock.get_strategic_metrics()

    def get_risk_metrics(self) -> dict[str, Any]:
        """Risk Management: real incident counts from Zabbix via InfluxDB."""
        zabbix = self._zabbix_stats()
        if not zabbix:
            log.warning("zabbix_no_data", fallback="mock", pillar="risk")
            return self._mock.get_risk_metrics()

        disaster = int(zabbix.get("problems_disaster", 0))
        high = int(zabbix.get("problems_high", 0))
        incidents_raw = disaster + high
        incidents_score = round(max(0.0, 100.0 - disaster * _PENALTY_DISASTER - high * _PENALTY_HIGH), 1)

        log.info(
            "risk_metrics_from_influxdb",
            problems_disaster=disaster,
            problems_high=high,
            incidents_score=incidents_score,
        )

        mock = self._mock.get_risk_metrics()
        real_components = [c for c in mock["components"] if c["id"] != "incidents_critical"]
        real_components.append(
            {
                "id": "incidents_critical",
                "label": "Incidentes críticos no mês",
                "value": incidents_score,
                "raw_value": incidents_raw,
                "unit": "score",
                "source": "zabbix",
                "weight": 1.5,
                "trend": "stable",
            }
        )
        return {"previous_score": mock["previous_score"], "components": real_components}

    def get_resource_metrics(self) -> dict[str, Any]:
        return self._mock.get_resource_metrics()

    def get_performance_metrics(self) -> dict[str, Any]:
        """Performance Measurement: real availability + coverage from Zabbix."""
        zabbix = self._zabbix_stats()
        if not zabbix:
            log.warning("zabbix_no_data", fallback="mock", pillar="performance")
            return self._mock.get_performance_metrics()

        hosts = zabbix.get("hosts_total", 1) or 1
        disaster = zabbix.get("problems_disaster", 0)

        # Availability: hosts with no disaster-level outage / total hosts
        avail_raw = round((hosts - disaster) / hosts * 100, 2)
        # Monitoring coverage: monitored hosts vs expected fleet size
        coverage_raw = round(min(100.0, hosts / _EXPECTED_HOSTS * 100), 1)

        log.info(
            "performance_metrics_from_influxdb",
            hosts=int(hosts),
            problems_disaster=int(disaster),
            availability=avail_raw,
            monitoring_coverage=coverage_raw,
        )

        mock = self._mock.get_performance_metrics()
        real_components = [
            {
                "id": "availability",
                "label": "Disponibilidade de sistemas críticos",
                "value": avail_raw,
                "raw_value": avail_raw,
                "unit": "%",
                "source": "zabbix",
                "weight": 3.0,
                "trend": "stable",
            },
            {
                "id": "monitoring_coverage",
                "label": "Cobertura de monitoramento",
                "value": coverage_raw,
                "raw_value": int(hosts),
                "unit": "hosts",
                "source": "zabbix",
                "weight": 1.5,
                "trend": "up",
            },
        ]
        # Keep mock components we don't yet have real sources for
        real_components.extend(c for c in mock["components"] if c["id"] not in ("availability", "monitoring_coverage"))
        return {"previous_score": mock["previous_score"], "components": real_components}

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _zabbix_stats(self) -> dict[str, float]:
        """Return latest Zabbix summary snapshot from gov_zabbix_summary."""
        flux = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "gov_zabbix_summary")
  |> last()
  |> keep(columns: ["_field", "_value"])
"""
        rows = self._query(flux)
        result = {r["_field"]: r["_value"] for r in rows if "_field" in r and "_value" in r}
        if result:
            log.debug("zabbix_stats_fetched", fields=list(result.keys()))
        return result

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
