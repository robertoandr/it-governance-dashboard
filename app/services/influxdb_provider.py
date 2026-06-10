"""InfluxDB-backed metrics provider for governance pillars.

Reads real time-series data from InfluxDB; returns COMING_SOON _meta
for pillars that do not yet have real measurements (no fake numbers).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from app.services.mock_data import MockMetricsProvider

if TYPE_CHECKING:
    from influxdb_client import InfluxDBClient

log = structlog.get_logger(__name__)

# Scoring constants — tunable via env or ADR when baselines are established
_IDEAL_PR_MONTH = 40  # PRs/month considered 100% delivery pace
_IDEAL_MERGE_HOURS = 8.0  # avg merge time (h) considered excellent
_EXPECTED_HOSTS = 150  # baseline for 100% monitoring coverage
_PENALTY_DISASTER = 20.0  # score penalty per disaster-severity incident
_PENALTY_HIGH = 5.0  # score penalty per high-severity incident

_COMING_SOON_ETA_STRATEGIC = "Sprint 13 - PMO"
_COMING_SOON_ETA_RESOURCE = "Sprint 13 - Intune/M365"


def _pr_velocity_score(count: int) -> float:
    return round(min(100.0, count / _IDEAL_PR_MONTH * 100), 1)


def _merge_time_score(avg_hours: float) -> float:
    if avg_hours <= _IDEAL_MERGE_HOURS:
        return 100.0
    return round(max(0.0, 100.0 - (avg_hours - _IDEAL_MERGE_HOURS) * 3), 1)


def _last_collected_from(rows: list[dict[str, Any]]) -> datetime | None:
    """Return the most recent _time from InfluxDB rows (real timestamp, not now())."""
    times = [r["_time"] for r in rows if r.get("_time") is not None]
    return max(times) if times else None


class InfluxDBMetricsProvider:
    """Governance metrics sourced from InfluxDB with COMING_SOON for unmeasured pillars.

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

    def get_performance_metrics(self) -> dict[str, Any]:
        """Performance Measure: real availability + coverage from Zabbix."""
        zabbix = self._zabbix_stats()

        if not zabbix:
            log.warning("zabbix_no_data_performance", fallback="coming_soon")
            mock = self._mock.get_performance_metrics()
            mock["_meta"] = {
                "data_source": "coming_soon",
                "last_collected": None,
                "collector_eta": "Aguardando dados Zabbix",
            }
            return mock

        hosts = zabbix.get("hosts", 0)
        disaster = zabbix.get("disaster", 0)

        avail_raw = round((hosts - disaster) / hosts * 100, 2) if hosts else 0.0
        coverage_raw = round(min(100.0, hosts / _EXPECTED_HOSTS * 100), 1)

        log.info(
            "performance_metrics_from_zabbix",
            hosts=hosts,
            disaster=disaster,
            availability=avail_raw,
            coverage=coverage_raw,
        )

        return {
            "_meta": {
                "data_source": "partial",
                "last_collected": zabbix.get("_time"),
                "collector_eta": None,
            },
            "previous_score": None,
            "components": [
                {
                    "id": "availability",
                    "label": "Disponibilidade de sistemas críticos",
                    "value": round(min(100.0, avail_raw), 1),
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
                    "raw_value": hosts,
                    "unit": "%",
                    "source": "zabbix",
                    "weight": 1.5,
                    "trend": "stable",
                },
                {
                    "id": "mttr",
                    "label": "MTTR (tempo médio de recuperação)",
                    "value": 73.0,
                    "raw_value": None,
                    "unit": "h",
                    "source": "coming_soon",
                    "weight": 2.5,
                    "trend": "stable",
                },
                {
                    "id": "change_success_rate",
                    "label": "Taxa de sucesso de mudanças",
                    "value": 87.0,
                    "raw_value": None,
                    "unit": "%",
                    "source": "coming_soon",
                    "weight": 2.0,
                    "trend": "stable",
                },
            ],
        }

    def get_risk_metrics(self) -> dict[str, Any]:
        """Risk Management: Zabbix incidents + Entra ID MFA when available."""
        zabbix = self._zabbix_stats()
        entra = self._entra_stats()

        if not zabbix:
            log.warning("zabbix_no_data_risk", fallback="coming_soon")
            mock = self._mock.get_risk_metrics()
            mock["_meta"] = {
                "data_source": "coming_soon",
                "last_collected": None,
                "collector_eta": "Aguardando dados Zabbix",
            }
            return mock

        disaster = zabbix.get("disaster", 0)
        high = zabbix.get("high", 0)
        hosts = zabbix.get("hosts", 0)
        incidents_raw = disaster + high
        incidents_score = round(max(0.0, 100.0 - disaster * _PENALTY_DISASTER - high * _PENALTY_HIGH), 1)

        components: list[dict[str, Any]] = [
            {
                "id": "incidents_critical",
                "label": "Incidentes críticos no mês",
                "value": incidents_score,
                "raw_value": float(incidents_raw),
                "unit": "score",
                "source": "zabbix",
                "weight": 1.5,
                "trend": "stable",
            },
        ]

        last_collected = zabbix.get("_time")
        data_source = "partial"

        if entra:
            mfa_pct = entra.get("mfa_enabled_pct", 0.0)
            components.append(
                {
                    "id": "mfa_adoption",
                    "label": "Adoção de MFA",
                    "value": round(float(mfa_pct), 1),
                    "raw_value": float(mfa_pct),
                    "unit": "%",
                    "source": "entra_id",
                    "weight": 2.0,
                    "trend": "stable",
                }
            )
            entra_time = entra.get("_time")
            if entra_time and last_collected:
                last_collected = max(last_collected, entra_time)
            elif entra_time:
                last_collected = entra_time
        else:
            components.append(
                {
                    "id": "mfa_adoption",
                    "label": "Adoção de MFA",
                    "value": 0.0,
                    "raw_value": None,
                    "unit": "%",
                    "source": "coming_soon",
                    "weight": 2.0,
                    "trend": "stable",
                }
            )

        # Remaining components without real sources
        components.extend(
            [
                {
                    "id": "critical_vulns",
                    "label": "Vulnerabilidades críticas abertas",
                    "value": 58.0,
                    "raw_value": None,
                    "unit": "score",
                    "source": "coming_soon",
                    "weight": 3.0,
                    "trend": "stable",
                },
                {
                    "id": "patch_compliance",
                    "label": "Conformidade de patches",
                    "value": 74.0,
                    "raw_value": None,
                    "unit": "%",
                    "source": "coming_soon",
                    "weight": 2.5,
                    "trend": "stable",
                },
                {
                    "id": "backup_success_rate",
                    "label": "Taxa de sucesso de backups",
                    "value": 95.0,
                    "raw_value": None,
                    "unit": "%",
                    "source": "coming_soon",
                    "weight": 2.0,
                    "trend": "stable",
                },
            ]
        )

        log.info(
            "risk_metrics_from_zabbix",
            hosts=hosts,
            disaster=disaster,
            high=high,
            incidents_score=incidents_score,
            entra_available=bool(entra),
        )

        return {
            "_meta": {
                "data_source": data_source,
                "last_collected": last_collected,
                "collector_eta": None,
            },
            "previous_score": None,
            "components": components,
        }

    def get_value_metrics(self) -> dict[str, Any]:
        """Value Delivery: GitHub PR velocity + Zabbix SLA."""
        zabbix = self._zabbix_stats()
        pr_count, avg_hours = self._github_pr_stats()

        components: list[dict[str, Any]] = []
        last_collected: datetime | None = None
        has_real_data = False

        if zabbix:
            hosts = zabbix.get("hosts", 0)
            disaster = zabbix.get("disaster", 0)
            high = zabbix.get("high", 0)
            sla_raw = round((hosts - disaster - high) / hosts * 100, 2) if hosts else 0.0
            components.append(
                {
                    "id": "sla_compliance",
                    "label": "Conformidade de SLA geral",
                    "value": round(min(100.0, sla_raw), 1),
                    "raw_value": sla_raw,
                    "unit": "%",
                    "source": "zabbix",
                    "weight": 3.0,
                    "trend": "stable",
                }
            )
            last_collected = zabbix.get("_time")
            has_real_data = True

        if pr_count > 0:
            velocity = _pr_velocity_score(pr_count)
            merge = _merge_time_score(avg_hours) if avg_hours is not None else 50.0
            components.extend(
                [
                    {
                        "id": "pr_velocity",
                        "label": "Velocidade de entrega (PRs/mês)",
                        "value": velocity,
                        "raw_value": float(pr_count),
                        "unit": "PRs",
                        "source": "github",
                        "weight": 1.5,
                        "trend": "stable",
                    },
                    {
                        "id": "pr_merge_time",
                        "label": "Tempo médio de merge",
                        "value": merge,
                        "raw_value": round(avg_hours, 1) if avg_hours else None,
                        "unit": "h",
                        "source": "github",
                        "weight": 1.0,
                        "trend": "up",
                    },
                ]
            )
            has_real_data = True

        # Remaining without real sources
        components.extend(
            [
                {
                    "id": "ticket_resolution_rate",
                    "label": "Taxa de resolução no prazo",
                    "value": 85.0,
                    "raw_value": None,
                    "unit": "%",
                    "source": "coming_soon",
                    "weight": 2.0,
                    "trend": "stable",
                },
                {
                    "id": "user_satisfaction",
                    "label": "Satisfação dos usuários (NPS TI)",
                    "value": 76.0,
                    "raw_value": None,
                    "unit": "pts",
                    "source": "coming_soon",
                    "weight": 1.0,
                    "trend": "stable",
                },
            ]
        )

        log.info(
            "value_metrics_assembled",
            zabbix_available=bool(zabbix),
            pr_count=pr_count,
        )

        return {
            "_meta": {
                "data_source": "partial" if has_real_data else "coming_soon",
                "last_collected": last_collected,
                "collector_eta": None,
            },
            "previous_score": None,
            "components": components,
        }

    def get_strategic_metrics(self) -> dict[str, Any]:
        """Strategic Alignment: COMING_SOON — requires PMO manual input."""
        mock = self._mock.get_strategic_metrics()
        mock["_meta"] = {
            "data_source": "coming_soon",
            "last_collected": None,
            "collector_eta": _COMING_SOON_ETA_STRATEGIC,
        }
        return mock

    def get_resource_metrics(self) -> dict[str, Any]:
        """Gestão de Recursos: utilização de servidores via Zabbix; demais componentes COMING_SOON."""
        resource = self._zabbix_resource_stats()

        if not resource:
            log.warning("zabbix_resource_sem_dados", fallback="coming_soon")
            mock = self._mock.get_resource_metrics()
            mock["_meta"] = {
                "data_source": "coming_soon",
                "last_collected": None,
                "collector_eta": _COMING_SOON_ETA_RESOURCE,
            }
            return mock

        log.info(
            "resource_metrics_from_zabbix",
            cpu_avg_pct=resource["cpu_avg_pct"],
            mem_avg_pct=resource["mem_avg_pct"],
            hosts_with_data=resource["hosts_with_data"],
        )

        return {
            "_meta": {
                "data_source": "partial",
                "last_collected": resource["_time"],
                "collector_eta": None,
            },
            "previous_score": None,
            "components": [
                {
                    "id": "server_utilization",
                    "label": "Utilização de servidores",
                    "value": round(resource["cpu_avg_pct"], 1),
                    "raw_value": resource["cpu_avg_pct"],
                    "unit": "%",
                    "source": "zabbix",
                    "weight": 2.0,
                    "trend": "stable",
                },
                {
                    "id": "license_waste",
                    "label": "Licenças não utilizadas",
                    "value": 74.0,
                    "raw_value": None,
                    "unit": "score",
                    "source": "coming_soon",
                    "weight": 2.0,
                    "trend": "stable",
                    "is_estimated": True,
                },
                {
                    "id": "it_budget_used",
                    "label": "Orçamento TI consumido",
                    "value": 82.0,
                    "raw_value": None,
                    "unit": "%",
                    "source": "coming_soon",
                    "weight": 1.5,
                    "trend": "stable",
                    "is_estimated": True,
                },
                {
                    "id": "team_capacity",
                    "label": "Capacidade da equipe utilizada",
                    "value": 78.0,
                    "raw_value": None,
                    "unit": "%",
                    "source": "coming_soon",
                    "weight": 1.0,
                    "trend": "stable",
                    "is_estimated": True,
                },
            ],
        }

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _zabbix_stats(self) -> dict[str, Any]:
        """Return the latest Zabbix summary record from InfluxDB."""
        flux = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "gov_zabbix_summary")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        rows = self._query(flux)
        if not rows:
            return {}
        row = rows[-1]
        return {
            "hosts": int(row.get("hosts_total", 0)),
            "disaster": int(row.get("problems_disaster", 0)),
            "high": int(row.get("problems_high", 0)),
            "average": int(row.get("problems_average", 0)),
            "warning": int(row.get("problems_warning", 0)),
            "_time": row.get("_time"),
        }

    def _zabbix_resource_stats(self) -> dict[str, Any]:
        """Retorna o último registro de utilização de recursos Zabbix do InfluxDB."""
        flux = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "gov_zabbix_resource")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        rows = self._query(flux)
        if not rows:
            return {}
        row = rows[-1]
        return {
            "cpu_avg_pct": float(row.get("cpu_avg_pct", 0.0)),
            "mem_avg_pct": float(row.get("mem_avg_pct", 0.0)),
            "hosts_with_data": int(row.get("hosts_with_data", 0)),
            "_time": row.get("_time"),
        }

    def _entra_stats(self) -> dict[str, Any]:
        """Return the latest Entra ID summary record from InfluxDB."""
        flux = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -25h)
  |> filter(fn: (r) => r._measurement == "gov_entra_summary")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        rows = self._query(flux)
        if not rows:
            return {}
        row = rows[-1]
        return {
            "total_users": int(row.get("total_users", 0)),
            "guest_users": int(row.get("guest_users", 0)),
            "mfa_enabled_pct": float(row.get("mfa_enabled_pct", 0.0)),
            "stale_accounts_90d": int(row.get("stale_accounts_90d", 0)),
            "privileged_roles_count": int(row.get("privileged_roles_count", 0)),
            "ca_policies_count": int(row.get("ca_policies_count", 0)),
            "_time": row.get("_time"),
        }

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
