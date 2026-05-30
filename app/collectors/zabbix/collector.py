"""ZabbixCollector — pipeline fetch → transform → write para governance_raw."""

from __future__ import annotations

import time

from app.collectors.base import BaseCollector
from app.collectors.zabbix.client import ZabbixClient
from app.collectors.zabbix.config import ZabbixSettings
from app.collectors.zabbix.models import ZabbixHost, ZabbixProblem
from app.collectors.zabbix.transformer import problem_to_point
from app.storage.influxdb.client import GovernanceInfluxClient
from app.storage.influxdb.models import GovernancePoint


class ZabbixCollector(BaseCollector[ZabbixProblem]):
    """Coletor Zabbix: busca problemas e os grava em governance_raw.

    Args:
        client: Cliente JSON-RPC Zabbix já autenticado.
        influx: Cliente InfluxDB com escopo em governance_raw.
        settings: Configuração do coletor.
    """

    def __init__(
        self,
        client: ZabbixClient,
        influx: GovernanceInfluxClient,
        settings: ZabbixSettings,
    ) -> None:
        super().__init__(influx=influx, name="zabbix")
        self.client = client
        self.settings = settings

    async def fetch(self) -> list[ZabbixProblem]:
        """Busca problemas do Zabbix e enriquece com dados de hosts.

        Janela de busca: [now - lookback_hours, now].

        Returns:
            Lista de ZabbixProblem com hosts resolvidos.
        """
        now = int(time.time())
        time_from = now - (self.settings.lookback_hours * 3600)

        raw_problems = await self.client.problem_get(time_from, now)
        if not raw_problems:
            return []

        # Zabbix 7.0: problem.get não retorna hosts — resolve via trigger.get
        # objectid em um problema de trigger = triggerid
        trigger_ids = list({p["objectid"] for p in raw_problems if p.get("objectid")})
        triggers_raw = await self.client.trigger_get(trigger_ids) if trigger_ids else []

        # Monta mapa triggerid → lista de ZabbixHost
        trigger_hosts: dict[str, list[ZabbixHost]] = {}
        for t in triggers_raw:
            tid = t.get("triggerid", "")
            hosts_for_trigger = [ZabbixHost(**h) for h in t.get("hosts", []) if isinstance(h, dict)]
            trigger_hosts[tid] = hosts_for_trigger

        problems: list[ZabbixProblem] = []
        for raw in raw_problems:
            host_objs = trigger_hosts.get(raw.get("objectid", ""), [])
            problem_data = {k: v for k, v in raw.items() if k != "hosts"}
            problems.append(ZabbixProblem(**problem_data, hosts=host_objs))

        return problems

    def transform(self, raw: list[ZabbixProblem]) -> list[GovernancePoint]:
        """Converte ZabbixProblem em GovernancePoint.

        Args:
            raw: Lista de problemas validados.

        Returns:
            Lista de GovernancePoint prontos para escrita.
        """
        return [problem_to_point(p) for p in raw]
