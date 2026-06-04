"""Mock metrics provider for development and testing."""

from __future__ import annotations

import random
from typing import Any


class MockMetricsProvider:
    """Provides realistic mock metrics for all five governance pillars.

    Args:
        seed: Random seed for reproducible values.
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)  # noqa: S311

    def _jitter(self, base: float, spread: float = 5.0) -> float:
        return round(min(100.0, max(0.0, base + self._rng.uniform(-spread, spread))), 1)

    def get_strategic_metrics(self) -> dict[str, Any]:
        """Return mock metrics for the Strategic Alignment pillar."""
        previous = self._jitter(74.0)
        return {
            "previous_score": previous,
            "components": [
                {
                    "id": "projects_aligned",
                    "label": "Projetos alinhados ao planejamento",
                    "value": self._jitter(82.0),
                    "raw_value": 41,
                    "unit": "%",
                    "source": "pmo",
                    "weight": 2.0,
                    "trend": "up",
                },
                {
                    "id": "kpi_coverage",
                    "label": "Cobertura de KPIs estratégicos",
                    "value": self._jitter(68.0),
                    "raw_value": 17,
                    "unit": "%",
                    "source": "scorecard",
                    "weight": 1.5,
                    "trend": "stable",
                },
                {
                    "id": "it_budget_alignment",
                    "label": "Aderência orçamentária TI vs negócio",
                    "value": self._jitter(79.0),
                    "raw_value": None,
                    "unit": "%",
                    "source": "finance",
                    "weight": 1.0,
                    "trend": "up",
                },
            ],
        }

    def get_value_metrics(self) -> dict[str, Any]:
        """Return mock metrics for the Value Delivery pillar."""
        previous = self._jitter(78.0)
        return {
            "previous_score": previous,
            "components": [
                {
                    "id": "sla_compliance",
                    "label": "Conformidade de SLA geral",
                    "value": self._jitter(91.0),
                    "raw_value": 91.3,
                    "unit": "%",
                    "source": "zabbix",
                    "weight": 3.0,
                    "trend": "up",
                },
                {
                    "id": "ticket_resolution_rate",
                    "label": "Taxa de resolução no prazo",
                    "value": self._jitter(85.0),
                    "raw_value": 340,
                    "unit": "%",
                    "source": "zendesk",
                    "weight": 2.0,
                    "trend": "stable",
                },
                {
                    "id": "project_delivery",
                    "label": "Projetos entregues no prazo",
                    "value": self._jitter(72.0),
                    "raw_value": 18,
                    "unit": "%",
                    "source": "pmo",
                    "weight": 1.5,
                    "trend": "down",
                },
                {
                    "id": "user_satisfaction",
                    "label": "Satisfação dos usuários (NPS TI)",
                    "value": self._jitter(76.0),
                    "raw_value": 7.6,
                    "unit": "pts",
                    "source": "survey",
                    "weight": 1.0,
                    "trend": "stable",
                },
            ],
        }

    def get_risk_metrics(self) -> dict[str, Any]:
        """Return mock metrics for the Risk Management pillar."""
        previous = self._jitter(65.0)
        return {
            "previous_score": previous,
            "components": [
                {
                    "id": "critical_vulns",
                    "label": "Vulnerabilidades críticas abertas",
                    "value": self._jitter(58.0),
                    "raw_value": 12,
                    "unit": "score",
                    "source": "vulnerability_scanner",
                    "weight": 3.0,
                    "trend": "down",
                },
                {
                    "id": "patch_compliance",
                    "label": "Conformidade de patches",
                    "value": self._jitter(74.0),
                    "raw_value": 74.0,
                    "unit": "%",
                    "source": "intune",
                    "weight": 2.5,
                    "trend": "up",
                },
                {
                    "id": "mfa_adoption",
                    "label": "Adoção de MFA",
                    "value": self._jitter(88.0),
                    "raw_value": 88.2,
                    "unit": "%",
                    "source": "entra_id",
                    "weight": 2.0,
                    "trend": "up",
                },
                {
                    "id": "backup_success_rate",
                    "label": "Taxa de sucesso de backups",
                    "value": self._jitter(95.0),
                    "raw_value": 95.0,
                    "unit": "%",
                    "source": "veeam",
                    "weight": 2.0,
                    "trend": "stable",
                },
                {
                    "id": "incidents_critical",
                    "label": "Incidentes críticos no mês",
                    "value": self._jitter(61.0),
                    "raw_value": 3,
                    "unit": "score",
                    "source": "zabbix",
                    "weight": 1.5,
                    "trend": "stable",
                },
            ],
        }

    def get_resource_metrics(self) -> dict[str, Any]:
        """Return mock metrics for the Resource Management pillar."""
        previous = self._jitter(71.0)
        return {
            "previous_score": previous,
            "components": [
                {
                    "id": "server_utilization",
                    "label": "Utilização de servidores",
                    "value": self._jitter(69.0),
                    "raw_value": 69.0,
                    "unit": "%",
                    "source": "influxdb",
                    "weight": 2.0,
                    "trend": "stable",
                },
                {
                    "id": "license_waste",
                    "label": "Licenças não utilizadas",
                    "value": self._jitter(74.0),
                    "raw_value": 42,
                    "unit": "score",
                    "source": "m365",
                    "weight": 2.0,
                    "trend": "up",
                },
                {
                    "id": "it_budget_used",
                    "label": "Orçamento TI consumido",
                    "value": self._jitter(82.0),
                    "raw_value": 82.0,
                    "unit": "%",
                    "source": "finance",
                    "weight": 1.5,
                    "trend": "stable",
                },
                {
                    "id": "team_capacity",
                    "label": "Capacidade da equipe utilizada",
                    "value": self._jitter(78.0),
                    "raw_value": 78.0,
                    "unit": "%",
                    "source": "pmo",
                    "weight": 1.0,
                    "trend": "down",
                },
            ],
        }

    def get_performance_metrics(self) -> dict[str, Any]:
        """Return mock metrics for the Performance Measurement pillar."""
        previous = self._jitter(80.0)
        return {
            "previous_score": previous,
            "components": [
                {
                    "id": "availability",
                    "label": "Disponibilidade de sistemas críticos",
                    "value": self._jitter(99.2, spread=0.5),
                    "raw_value": 99.2,
                    "unit": "%",
                    "source": "zabbix",
                    "weight": 3.0,
                    "trend": "stable",
                },
                {
                    "id": "mttr",
                    "label": "MTTR (tempo médio de recuperação)",
                    "value": self._jitter(73.0),
                    "raw_value": 2.4,
                    "unit": "h",
                    "source": "incidents",
                    "weight": 2.5,
                    "trend": "up",
                },
                {
                    "id": "change_success_rate",
                    "label": "Taxa de sucesso de mudanças",
                    "value": self._jitter(87.0),
                    "raw_value": 87.0,
                    "unit": "%",
                    "source": "itsm",
                    "weight": 2.0,
                    "trend": "stable",
                },
                {
                    "id": "monitoring_coverage",
                    "label": "Cobertura de monitoramento",
                    "value": self._jitter(83.0),
                    "raw_value": 83.0,
                    "unit": "%",
                    "source": "zabbix",
                    "weight": 1.5,
                    "trend": "up",
                },
            ],
        }
