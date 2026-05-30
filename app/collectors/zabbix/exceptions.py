"""Exceções do cliente Zabbix JSON-RPC."""

from __future__ import annotations


class ZabbixAPIError(Exception):
    """Erro retornado pela API JSON-RPC do Zabbix.

    Args:
        code: Código de erro JSON-RPC.
        message: Mensagem de erro do Zabbix.
        data: Detalhes adicionais (opcional).
    """

    def __init__(self, code: int, message: str, data: str = "") -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"Zabbix API error {code}: {message}" + (f" — {data}" if data else ""))


class ZabbixAuthError(ZabbixAPIError):
    """Token ou credenciais inválidos."""
