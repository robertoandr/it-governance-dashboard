"""Cliente JSON-RPC assíncrono para a API Zabbix."""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.collectors.zabbix.config import ZabbixSettings
from app.collectors.zabbix.exceptions import ZabbixAPIError, ZabbixAuthError

log = structlog.get_logger(__name__)

_JSONRPC_VERSION = "2.0"
_AUTH_ERROR_CODES = {-32602, -32000}


def _is_server_error(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


class ZabbixClient:
    """Cliente JSON-RPC assíncrono para o Zabbix.

    Suporta autenticação por API token (Zabbix 6.0+, header Bearer).
    Implementa retry com back-off exponencial em erros 5xx.

    Usage::

        async with ZabbixClient(settings) as client:
            problems = await client.problem_get(time_from, time_till)

    Args:
        settings: Configuração lida do ambiente.
    """

    def __init__(self, settings: ZabbixSettings) -> None:
        self._settings = settings
        self._url = str(settings.url)
        self._client: httpx.AsyncClient | None = None
        self._req_id = 0

    def _build_client(self) -> httpx.AsyncClient:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._settings.token:
            # Zabbix 6.0+ — token no header evita expor no body
            headers["Authorization"] = f"Bearer {self._settings.token.get_secret_value()}"
        return httpx.AsyncClient(
            timeout=float(self._settings.timeout_seconds),
            headers=headers,
            follow_redirects=True,
        )

    async def __aenter__(self) -> ZabbixClient:
        self._client = self._build_client()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Fecha a sessão HTTP."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        """Executa uma chamada JSON-RPC com retry em 5xx.

        Args:
            method: Método Zabbix (ex: "problem.get").
            params: Parâmetros do método.

        Returns:
            Campo `result` da resposta JSON-RPC.

        Raises:
            ZabbixAuthError: Em erro de autenticação (código -32602/-32000).
            ZabbixAPIError: Em qualquer outro erro JSON-RPC.
            httpx.HTTPStatusError: Em erros HTTP após esgotar retries.
            httpx.TimeoutException: Em timeout.
        """
        return await self._call_with_retry(method, params)

    @retry(
        retry=retry_if_exception(_is_server_error),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=1, max=10),
        reraise=True,
    )
    async def _call_with_retry(self, method: str, params: dict[str, Any]) -> Any:
        if self._client is None:
            self._client = self._build_client()

        payload = {
            "jsonrpc": _JSONRPC_VERSION,
            "method": method,
            "params": params,
            "id": self._next_id(),
        }

        t0 = time.perf_counter()
        resp = await self._client.post(self._url, json=payload)
        duration_ms = int((time.perf_counter() - t0) * 1000)

        resp.raise_for_status()
        body = resp.json()

        if "error" in body:
            err = body["error"]
            code: int = err.get("code", 0)
            message: str = err.get("message", "")
            data: str = err.get("data", "")
            log.error("zabbix.rpc_error", method=method, code=code, message=message)
            if code in _AUTH_ERROR_CODES and "not authoris" in data.lower():
                raise ZabbixAuthError(code, message, data)
            raise ZabbixAPIError(code, message, data)

        result = body.get("result", [])
        count = len(result) if isinstance(result, list) else 1
        log.info("zabbix.rpc_ok", method=method, duration_ms=duration_ms, item_count=count)
        return result

    async def problem_get(self, time_from: int, time_till: int) -> list[dict[str, Any]]:
        """Busca problemas no intervalo de tempo especificado.

        Args:
            time_from: Timestamp Unix de início (inclusivo).
            time_till: Timestamp Unix de fim (inclusivo).

        Returns:
            Lista de dicts crus da API Zabbix.
        """
        return await self._call(
            "problem.get",
            {
                "output": "extend",
                # selectHosts removido no Zabbix 7.0 — hosts resolvidos via trigger.get
                "time_from": time_from,
                "time_till": time_till,
                "sortfield": ["eventid"],
                "sortorder": "DESC",
                "limit": self._settings.page_size,
            },
        )

    async def trigger_get(self, triggerids: list[str]) -> list[dict[str, Any]]:
        """Busca triggers com seus hosts associados.

        Zabbix 7.0: hosts são obtidos aqui via selectHosts, já que
        problem.get não suporta mais esse parâmetro.

        Args:
            triggerids: Lista de IDs de triggers (objectid dos problemas).

        Returns:
            Lista de dicts com triggerid, description, priority e hosts.
        """
        return await self._call(
            "trigger.get",
            {
                "output": ["triggerid", "description", "priority"],
                "selectHosts": ["hostid", "host", "name"],
                "triggerids": triggerids,
            },
        )

    async def host_get(self, hostids: list[str]) -> list[dict[str, Any]]:
        """Busca metadados de hosts pelos IDs.

        Args:
            hostids: Lista de IDs de hosts.

        Returns:
            Lista de dicts crus da API Zabbix.
        """
        return await self._call(
            "host.get",
            {
                "output": ["hostid", "host", "name"],
                "hostids": hostids,
            },
        )
