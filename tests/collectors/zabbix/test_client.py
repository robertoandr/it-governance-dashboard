"""Testes do ZabbixClient (JSON-RPC async)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.zabbix.client import ZabbixClient
from app.collectors.zabbix.exceptions import ZabbixAPIError, ZabbixAuthError
from tests.collectors.zabbix.conftest import _ZABBIX_URL, make_rpc_error, make_rpc_response


@pytest.mark.asyncio
class TestZabbixClientProblemGet:
    async def test_problem_get_success(self, zabbix_settings, problems_payload) -> None:
        with respx.mock:
            respx.post(_ZABBIX_URL).mock(return_value=httpx.Response(200, json=make_rpc_response(problems_payload)))
            async with ZabbixClient(zabbix_settings) as client:
                result = await client.problem_get(1748000000, 1748003600)

        assert len(result) == 2
        assert result[0]["eventid"] == "1001"

    async def test_jsonrpc_error_raises(self, zabbix_settings) -> None:
        with respx.mock:
            respx.post(_ZABBIX_URL).mock(
                return_value=httpx.Response(200, json=make_rpc_error(-32602, "Invalid params", "Wrong param"))
            )
            async with ZabbixClient(zabbix_settings) as client:
                with pytest.raises(ZabbixAPIError) as exc_info:
                    await client.problem_get(0, 1)

        assert exc_info.value.code == -32602

    async def test_auth_error_raises_zabbix_auth_error(self, zabbix_settings) -> None:
        with respx.mock:
            respx.post(_ZABBIX_URL).mock(
                return_value=httpx.Response(
                    200,
                    json=make_rpc_error(-32602, "Not authorised", "Not authorised. [problemGet]"),
                )
            )
            async with ZabbixClient(zabbix_settings) as client:
                with pytest.raises(ZabbixAuthError):
                    await client.problem_get(0, 1)

    async def test_retry_on_5xx_then_success(self, zabbix_settings, problems_payload) -> None:
        """Primeira chamada retorna 503, segunda retorna 200."""
        call_count = 0

        def side_effect(_request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503, text="Service Unavailable")
            return httpx.Response(200, json=make_rpc_response(problems_payload))

        with respx.mock:
            respx.post(_ZABBIX_URL).mock(side_effect=side_effect)
            async with ZabbixClient(zabbix_settings) as client:
                result = await client.problem_get(0, 9999999999)

        assert call_count == 2
        assert len(result) == 2

    async def test_timeout_raises(self, zabbix_settings) -> None:
        with respx.mock:
            respx.post(_ZABBIX_URL).mock(side_effect=httpx.TimeoutException("timeout"))
            async with ZabbixClient(zabbix_settings) as client:
                with pytest.raises(httpx.TimeoutException):
                    await client.problem_get(0, 1)

    async def test_http_error_after_retries_raises(self, zabbix_settings) -> None:
        """Esgota todos os retries em 503 e levanta HTTPStatusError."""
        with respx.mock:
            respx.post(_ZABBIX_URL).mock(return_value=httpx.Response(503, text="down"))
            async with ZabbixClient(zabbix_settings) as client:
                with pytest.raises(httpx.HTTPStatusError):
                    await client.problem_get(0, 1)


@pytest.mark.asyncio
class TestZabbixClientHostGet:
    async def test_host_get_success(self, zabbix_settings, hosts_payload) -> None:
        with respx.mock:
            respx.post(_ZABBIX_URL).mock(return_value=httpx.Response(200, json=make_rpc_response(hosts_payload)))
            async with ZabbixClient(zabbix_settings) as client:
                result = await client.host_get(["201", "202"])

        assert len(result) == 2
        assert result[0]["hostid"] == "201"

    async def test_empty_hostids_returns_empty(self, zabbix_settings) -> None:
        with respx.mock:
            respx.post(_ZABBIX_URL).mock(return_value=httpx.Response(200, json=make_rpc_response([])))
            async with ZabbixClient(zabbix_settings) as client:
                result = await client.host_get([])

        assert result == []
