"""itgov.services — Public re-exports for cross-module use."""

from itgov.services.zabbix_service import ZABBIX_JSONRPC_PATH, normalize_zabbix_base_url

__all__ = ["ZABBIX_JSONRPC_PATH", "normalize_zabbix_base_url"]
