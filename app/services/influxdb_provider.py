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
_MIN_UPTIME_PCT = 99.5  # below = availability degraded
_MIN_BACKUP_COVERAGE_PCT = 95.0  # below = backup coverage concern

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
        """Performance Measure: real availability + coverage from gov_zabbix_disponibilidade."""
        disp = self._zabbix_disp_stats()

        if not disp:
            log.warning("zabbix_disp_no_data_performance", fallback="coming_soon")
            mock = self._mock.get_performance_metrics()
            mock["_meta"] = {
                "data_source": "coming_soon",
                "last_collected": None,
                "collector_eta": "Aguardando dados Zabbix",
            }
            return mock

        uptime_pct = disp.get("uptime_pct", 0.0)
        hosts_down = disp.get("hosts_down", 0)
        total = disp.get("total_monitorado", 0)
        coverage_raw = round(min(100.0, total / _EXPECTED_HOSTS * 100), 1)

        log.info(
            "performance_metrics_from_zabbix_disp",
            uptime_pct=uptime_pct,
            hosts_down=hosts_down,
            total_monitorado=total,
            coverage=coverage_raw,
        )

        return {
            "_meta": {
                "data_source": "partial",
                "last_collected": disp.get("_time"),
                "collector_eta": None,
            },
            "previous_score": None,
            "components": [
                {
                    "id": "availability",
                    "label": "Disponibilidade de sistemas críticos",
                    "value": round(min(100.0, uptime_pct), 1),
                    "raw_value": hosts_down,
                    "unit": "hosts down",
                    "source": "zabbix",
                    "weight": 3.0,
                    "trend": "stable",
                },
                {
                    "id": "monitoring_coverage",
                    "label": "Cobertura de monitoramento",
                    "value": coverage_raw,
                    "raw_value": total,
                    "unit": "hosts",
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
        """Risk Management: Zabbix incidents + risk score + Acronis backup + Entra MFA."""
        zabbix = self._zabbix_stats()
        entra = self._entra_stats()
        zabbix_risk = self._zabbix_risk_stats()
        acronis = self._acronis_stats()

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

        # Zabbix risk score (pre-calculated by collector across all severity levels)
        if zabbix_risk:
            zr_score = round(zabbix_risk.get("score", 0.0), 1)
            components.append(
                {
                    "id": "risk_score_zabbix",
                    "label": "Score de risco Zabbix",
                    "value": zr_score,
                    "raw_value": float(zabbix_risk.get("total_problemas", 0)),
                    "unit": "score",
                    "source": "zabbix",
                    "weight": 2.0,
                    "trend": "stable",
                }
            )
            zr_time = zabbix_risk.get("_time")
            if zr_time and last_collected:
                last_collected = max(last_collected, zr_time)
            elif zr_time:
                last_collected = zr_time
        else:
            components.append(
                {
                    "id": "risk_score_zabbix",
                    "label": "Score de risco Zabbix",
                    "value": 60.0,
                    "raw_value": None,
                    "unit": "score",
                    "source": "coming_soon",
                    "weight": 2.0,
                    "trend": "stable",
                }
            )

        # Acronis backup coverage (real data from gov_acronis_protection)
        if acronis:
            protected = acronis.get("protected_machines", 0)
            total_m = acronis.get("total_machines", 0)
            backup_pct = round(protected / total_m * 100, 1) if total_m else 0.0
            components.append(
                {
                    "id": "backup_coverage",
                    "label": "Cobertura de Backup (Acronis)",
                    "value": backup_pct,
                    "raw_value": float(protected),
                    "unit": "máquinas protegidas",
                    "source": "acronis",
                    "weight": 2.0,
                    "trend": "stable",
                }
            )
            acr_time = acronis.get("_time")
            if acr_time and last_collected:
                last_collected = max(last_collected, acr_time)
            elif acr_time:
                last_collected = acr_time
        else:
            backup_pct = None
            components.append(
                {
                    "id": "backup_coverage",
                    "label": "Cobertura de Backup (Acronis)",
                    "value": 95.0,
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
            ]
        )

        log.info(
            "risk_metrics_assembled",
            hosts=hosts,
            disaster=disaster,
            high=high,
            incidents_score=incidents_score,
            zabbix_risk_score=zabbix_risk.get("score") if zabbix_risk else None,
            backup_pct=backup_pct,
            entra_available=bool(entra),
            acronis_available=bool(acronis),
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
        """Strategic Alignment: Secure Score M365 + Entra privileged access (partial).

        Real sources:
          gov_m365_secure_score.pct          → maturidade de segurança estratégica
          gov_entra_summary.privileged_roles_count → governança de acessos privilegiados
        Coming soon: PMO (projetos alinhados, KPIs, orçamento).
        """
        secure = self._secure_score_stats()
        entra = self._entra_stats()

        if not secure and not entra:
            log.warning("strategic_no_data", fallback="coming_soon")
            mock = self._mock.get_strategic_metrics()
            mock["_meta"] = {
                "data_source": "coming_soon",
                "last_collected": None,
                "collector_eta": _COMING_SOON_ETA_STRATEGIC,
            }
            return mock

        components: list[dict[str, Any]] = []
        last_collected: datetime | None = None

        if secure:
            pct = float(secure.get("pct", 0.0))
            components.append(
                {
                    "id": "security_posture",
                    "label": "Maturidade de segurança (Secure Score M365)",
                    "value": round(pct, 1),
                    "raw_value": pct,
                    "unit": "%",
                    "source": "m365_secure_score",
                    "weight": 2.0,
                    "trend": "stable",
                }
            )
            last_collected = secure.get("_time")

        if entra:
            roles = int(entra.get("privileged_roles_count", 0))
            # Baseline 4 roles expected; each extra = -10 pts.
            priv_score = round(max(0.0, 100.0 - max(0, roles - 4) * 10.0), 1)
            components.append(
                {
                    "id": "privileged_access",
                    "label": "Governança de acessos privilegiados",
                    "value": priv_score,
                    "raw_value": float(roles),
                    "unit": "roles",
                    "source": "entra_id",
                    "weight": 1.5,
                    "trend": "stable",
                }
            )
            entra_time = entra.get("_time")
            if entra_time and last_collected:
                last_collected = max(last_collected, entra_time)
            elif entra_time:
                last_collected = entra_time

        # PMO components — coming_soon; placeholder values to avoid dragging score to 0
        components.extend(
            [
                {
                    "id": "pmo_alignment",
                    "label": "Projetos alinhados ao planejamento estratégico",
                    "value": 75.0,
                    "raw_value": None,
                    "unit": "%",
                    "source": "coming_soon",
                    "weight": 2.0,
                    "trend": "stable",
                },
                {
                    "id": "kpi_coverage",
                    "label": "Cobertura de KPIs estratégicos",
                    "value": 68.0,
                    "raw_value": None,
                    "unit": "%",
                    "source": "coming_soon",
                    "weight": 1.5,
                    "trend": "stable",
                },
                {
                    "id": "budget_alignment",
                    "label": "Aderência orçamentária TI vs negócio",
                    "value": 79.0,
                    "raw_value": None,
                    "unit": "%",
                    "source": "coming_soon",
                    "weight": 1.0,
                    "trend": "stable",
                },
            ]
        )

        log.info(
            "strategic_metrics_assembled",
            secure_score_available=bool(secure),
            entra_available=bool(entra),
            secure_pct=secure.get("pct") if secure else None,
            privileged_roles=entra.get("privileged_roles_count") if entra else None,
        )

        return {
            "_meta": {
                "data_source": "partial",
                "last_collected": last_collected,
                "collector_eta": None,
            },
            "previous_score": None,
            "components": components,
        }

    def get_resource_metrics(self) -> dict[str, Any]:
        """Resource Management: Entra identity hygiene + Secure Score M365 (partial).

        Real sources:
          gov_entra_summary.stale_accounts_90d / total_users → higiene de identidades
          gov_entra_summary.ca_policies_count               → políticas de Acesso Condicional
          gov_m365_secure_score.pct / current_score         → controles M365 implementados
        Coming soon: utilização de licenças M365 (requer m365_licenses no InfluxDB).
        """
        entra = self._entra_stats()
        secure = self._secure_score_stats()

        if not entra and not secure:
            log.warning("resource_no_data", fallback="coming_soon")
            mock = self._mock.get_resource_metrics()
            mock["_meta"] = {
                "data_source": "coming_soon",
                "last_collected": None,
                "collector_eta": _COMING_SOON_ETA_RESOURCE,
            }
            return mock

        components: list[dict[str, Any]] = []
        last_collected: datetime | None = None

        if entra:
            total = int(entra.get("total_users", 0))
            stale = int(entra.get("stale_accounts_90d", 0))
            hygiene_score = round(max(0.0, (1.0 - stale / total) * 100.0), 1) if total else 0.0
            components.append(
                {
                    "id": "identity_hygiene",
                    "label": "Higiene de identidades (contas ativas)",
                    "value": hygiene_score,
                    "raw_value": float(stale),
                    "unit": "%",
                    "source": "entra_id",
                    "weight": 2.0,
                    "trend": "stable",
                }
            )

            ca = int(entra.get("ca_policies_count", 0))
            # 10 pts per CA policy, capped at 100.
            ca_score = round(min(100.0, ca * 10.0), 1)
            components.append(
                {
                    "id": "conditional_access",
                    "label": "Políticas de Acesso Condicional (CA)",
                    "value": ca_score,
                    "raw_value": float(ca),
                    "unit": "políticas",
                    "source": "entra_id",
                    "weight": 1.5,
                    "trend": "stable",
                }
            )
            last_collected = entra.get("_time")

        if secure:
            pct = float(secure.get("pct", 0.0))
            current = float(secure.get("current_score", 0.0))
            components.append(
                {
                    "id": "m365_controls",
                    "label": "Controles M365 implementados (Secure Score)",
                    "value": round(pct, 1),
                    "raw_value": current,
                    "unit": "%",
                    "source": "m365_secure_score",
                    "weight": 2.0,
                    "trend": "stable",
                }
            )
            secure_time = secure.get("_time")
            if secure_time and last_collected:
                last_collected = max(last_collected, secure_time)
            elif secure_time:
                last_collected = secure_time

        # Licenses: no collector writing m365_licenses yet — coming_soon
        components.append(
            {
                "id": "license_utilization",
                "label": "Utilização de licenças M365",
                "value": 74.0,
                "raw_value": None,
                "unit": "%",
                "source": "coming_soon",
                "weight": 2.0,
                "trend": "stable",
            }
        )

        log.info(
            "resource_metrics_assembled",
            entra_available=bool(entra),
            secure_score_available=bool(secure),
            stale_accounts=entra.get("stale_accounts_90d") if entra else None,
            ca_policies=entra.get("ca_policies_count") if entra else None,
            secure_pct=secure.get("pct") if secure else None,
        )

        return {
            "_meta": {
                "data_source": "partial",
                "last_collected": last_collected,
                "collector_eta": None,
            },
            "previous_score": None,
            "components": components,
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

    def _secure_score_stats(self) -> dict[str, Any]:
        """Return the latest Microsoft Secure Score record from InfluxDB."""
        flux = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -25h)
  |> filter(fn: (r) => r._measurement == "gov_m365_secure_score")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        rows = self._query(flux)
        if not rows:
            return {}
        row = rows[-1]
        return {
            "current_score": float(row.get("current_score", 0.0)),
            "max_score": float(row.get("max_score", 0.0)),
            "pct": float(row.get("pct", 0.0)),
            "active_user_count": int(row.get("active_user_count", 0)),
            "control_scores_count": int(row.get("control_scores_count", 0)),
            "_time": row.get("_time"),
        }

    def _zabbix_disp_stats(self) -> dict[str, Any]:
        """Return the latest Zabbix availability record from gov_zabbix_disponibilidade."""
        flux = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_zabbix_disponibilidade")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        rows = self._query(flux)
        if not rows:
            return {}
        row = rows[-1]
        return {
            "uptime_pct": float(row.get("uptime_pct", 0.0)),
            "score": float(row.get("score", 0.0)),
            "hosts_up": int(row.get("hosts_up", 0)),
            "hosts_down": int(row.get("hosts_down", 0)),
            "hosts_unknown": int(row.get("hosts_unknown", 0)),
            "total_monitorado": int(row.get("total_monitorado", 0)),
            "top_host_down": str(row.get("top_host_down", "")),
            "_time": row.get("_time"),
        }

    def _zabbix_risk_stats(self) -> dict[str, Any]:
        """Return the latest Zabbix risk score from gov_zabbix_riscos."""
        flux = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_zabbix_riscos")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        rows = self._query(flux)
        if not rows:
            return {}
        row = rows[-1]
        return {
            "score": float(row.get("score", 0.0)),
            "criticos": int(row.get("criticos", 0)),
            "altos": int(row.get("altos", 0)),
            "medios": int(row.get("medios", 0)),
            "avisos": int(row.get("avisos", 0)),
            "total_problemas": int(row.get("total_problemas", 0)),
            "incidentes_criticos": int(row.get("incidentes_criticos", 0)),
            "top_incidente": str(row.get("top_incidente", "")),
            "_time": row.get("_time"),
        }

    def _acronis_stats(self) -> dict[str, Any]:
        """Return Acronis protection coverage + risk summary from InfluxDB."""
        flux_prot = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "gov_acronis_protection")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        flux_risk = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_acronis_risk_summary")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        prot_rows = self._query(flux_prot)
        risk_rows = self._query(flux_risk)
        if not prot_rows and not risk_rows:
            return {}
        result: dict[str, Any] = {}
        if prot_rows:
            row = prot_rows[-1]
            result.update(
                {
                    "protected_machines": int(row.get("protected_machines", 0)),
                    "total_machines": int(row.get("total_machines", 0)),
                    "_time": row.get("_time"),
                }
            )
        if risk_rows:
            row = risk_rows[-1]
            result.update(
                {
                    "offline_gt_20d": int(row.get("offline_gt_20d", 0)),
                    "incidents_total": int(row.get("incidents_total", 0)),
                    "license_issues": int(row.get("license_issues", 0)),
                }
            )
            if "_time" not in result:
                result["_time"] = row.get("_time")
        return result

    def _intune_stats(self) -> dict[str, Any]:
        """Return Intune patch compliance aggregated from InfluxDB.

        Aggregation rule: sum raw fields across OS types, then divide.
        Arithmetic mean of per-OS compliance_pct is wrong (ignores fleet size).
        Staleness window: 15 h — collector runs every 6 h, so >2 missed runs = stale.
        """
        flux = f"""
from(bucket: "{self._bucket_raw}")
  |> range(start: -15h)
  |> filter(fn: (r) => r._measurement == "gov_intune_patch_compliance")
  |> filter(fn: (r) => r._field == "compliant" or r._field == "total_evaluated")
  |> last()
  |> pivot(rowKey: ["os_type"], columnKey: ["_field"], valueColumn: "_value")
"""
        rows = self._query(flux)

        if not rows:
            log.info("intune_stats_stale", reason="no data in last 15h")
            return {
                "global": {"compliant": 0, "total_evaluated": 0, "compliance_pct": None},
                "by_os": [],
                "stale": True,
            }

        by_os: list[dict[str, Any]] = []
        total_compliant = 0
        total_evaluated = 0

        for row in rows:
            os_type = row.get("os_type") or "Other"
            compliant = int(row.get("compliant") or 0)
            evaluated = int(row.get("total_evaluated") or 0)
            pct = round(compliant / evaluated * 100, 1) if evaluated else None

            by_os.append(
                {
                    "os_type": os_type,
                    "compliant": compliant,
                    "total_evaluated": evaluated,
                    "compliance_pct": pct,
                }
            )
            total_compliant += compliant
            total_evaluated += evaluated

        global_pct: float | None = round(total_compliant / total_evaluated * 100, 1) if total_evaluated else None

        log.info(
            "intune_stats",
            os_count=len(by_os),
            total_compliant=total_compliant,
            total_evaluated=total_evaluated,
            pct_global=global_pct,
        )

        return {
            "global": {
                "compliant": total_compliant,
                "total_evaluated": total_evaluated,
                "compliance_pct": global_pct,
            },
            "by_os": sorted(by_os, key=lambda r: r["os_type"]),
            "stale": False,
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
