"""Transformação: ZabbixProblem → GovernancePoint."""

from __future__ import annotations

from app.collectors.zabbix.models import ZabbixProblem
from app.storage.influxdb.models import GovernancePoint

_SEVERITY_LABELS = {
    0: "not_classified",
    1: "information",
    2: "warning",
    3: "average",
    4: "high",
    5: "disaster",
}


def problem_to_point(problem: ZabbixProblem) -> GovernancePoint:
    """Converte ZabbixProblem em GovernancePoint para governance_raw.

    Measurement: ``gov_zabbix_problem``

    Tags (cardinality controlada):
        - ``host``: nome do host primário do problema.
          ⚠️ Risco de cardinality alta em ambientes com milhares de hosts.
          Considerar agregação por host_group em versão futura se |hosts| > 500.
        - ``severity``: label do nível (not_classified … disaster).
        - ``acknowledged``: "true" ou "false".
        - ``state``: "open" ou "resolved".

    Fields:
        - ``eventid`` (str): ID do evento Zabbix.
        - ``trigger_name`` (str): Descrição do trigger.
        - ``duration_seconds`` (int): Duração em segundos; 0 se ainda aberto.
        - ``count`` (int): Sempre 1 — usado como acumulador em sum() no downsample.

    Args:
        problem: Problema Zabbix validado.

    Returns:
        GovernancePoint pronto para escrita em governance_raw.
    """
    state = "resolved" if problem.is_resolved else "open"
    duration = (problem.r_clock - problem.clock) if (problem.is_resolved and problem.r_clock) else 0
    host_name = problem.hosts[0].name if problem.hosts else "unknown"
    severity_label = _SEVERITY_LABELS.get(problem.severity, str(problem.severity))

    return GovernancePoint(
        measurement="gov_zabbix_problem",
        tags={
            "host": host_name,
            "severity": severity_label,
            "acknowledged": str(problem.is_acknowledged).lower(),
            "state": state,
        },
        fields={
            "eventid": problem.eventid,
            "trigger_name": problem.name,
            "duration_seconds": duration,
            "count": 1,
        },
        timestamp_ns=problem.clock * 1_000_000_000,
    )
