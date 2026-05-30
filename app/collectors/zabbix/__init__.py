"""Coletor Zabbix — busca problemas ativos e os grava em governance_raw."""

from app.collectors.zabbix.collector import ZabbixCollector
from app.collectors.zabbix.config import ZabbixSettings

__all__ = ["ZabbixCollector", "ZabbixSettings"]
