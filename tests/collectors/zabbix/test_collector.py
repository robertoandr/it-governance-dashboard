"""Testes de integração do ZabbixCollector (fetch + transform + write)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.collectors.zabbix.client import ZabbixClient
from app.collectors.zabbix.collector import ZabbixCollector
from tests.collectors.zabbix.conftest import _ZABBIX_URL, make_rpc_response


@pytest.mark.asyncio
class TestZabbixCollectorE2E:
    async def test_e2e_success(self, zabbix_settings, mock_influx, problems_payload, triggers_payload) -> None:
        """Pipeline completo: fetch → transform → write com mocks."""
        call_count = 0
        responses = [
            make_rpc_response(problems_payload),
            make_rpc_response(triggers_payload),
        ]

        def side_effect(_req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return httpx.Response(200, json=resp)

        with respx.mock:
            respx.post(_ZABBIX_URL).mock(side_effect=side_effect)
            async with ZabbixClient(zabbix_settings) as client:
                collector = ZabbixCollector(client=client, influx=mock_influx, settings=zabbix_settings)
                count = await collector.collect()

        assert count == 2
        mock_influx.write_points.assert_awaited_once()
        written_points = mock_influx.write_points.call_args[0][0]
        assert len(written_points) == 2
        assert all(p.measurement == "gov_zabbix_problem" for p in written_points)

    async def test_empty_result_no_write(self, zabbix_settings, mock_influx) -> None:
        """Zero problemas → write_points nunca chamado."""
        with respx.mock:
            respx.post(_ZABBIX_URL).mock(return_value=httpx.Response(200, json=make_rpc_response([])))
            async with ZabbixClient(zabbix_settings) as client:
                collector = ZabbixCollector(client=client, influx=mock_influx, settings=zabbix_settings)
                count = await collector.collect()

        assert count == 0
        mock_influx.write_points.assert_not_awaited()

    async def test_fetch_error_propagates(self, zabbix_settings, mock_influx) -> None:
        """Falha no fetch levanta exceção e não chama write."""
        with respx.mock:
            respx.post(_ZABBIX_URL).mock(side_effect=httpx.TimeoutException("timeout"))
            async with ZabbixClient(zabbix_settings) as client:
                collector = ZabbixCollector(client=client, influx=mock_influx, settings=zabbix_settings)
                with pytest.raises(httpx.TimeoutException):
                    await collector.collect()

        mock_influx.write_points.assert_not_awaited()

    async def test_write_error_propagates(self, zabbix_settings, problems_payload, triggers_payload) -> None:
        """Falha no write levanta exceção."""
        call_count = 0
        responses = [make_rpc_response(problems_payload), make_rpc_response(triggers_payload)]

        def side_effect(_req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return httpx.Response(200, json=resp)

        failing_influx = AsyncMock()
        failing_influx.write_points = AsyncMock(side_effect=httpx.HTTPStatusError("err", request=None, response=None))

        with respx.mock:
            respx.post(_ZABBIX_URL).mock(side_effect=side_effect)
            async with ZabbixClient(zabbix_settings) as client:
                collector = ZabbixCollector(client=client, influx=failing_influx, settings=zabbix_settings)
                with pytest.raises(httpx.HTTPStatusError):
                    await collector.collect()

    async def test_points_have_correct_tags(
        self, zabbix_settings, mock_influx, problems_payload, triggers_payload
    ) -> None:
        """Verifica tags dos pontos gerados."""
        call_count = 0
        responses = [make_rpc_response(problems_payload), make_rpc_response(triggers_payload)]

        def side_effect(_req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return httpx.Response(200, json=resp)

        with respx.mock:
            respx.post(_ZABBIX_URL).mock(side_effect=side_effect)
            async with ZabbixClient(zabbix_settings) as client:
                collector = ZabbixCollector(client=client, influx=mock_influx, settings=zabbix_settings)
                await collector.collect()

        points = mock_influx.write_points.call_args[0][0]
        open_point = next(p for p in points if p.tags["state"] == "open")
        resolved_point = next(p for p in points if p.tags["state"] == "resolved")

        assert open_point.tags["host"] == "SRV-CORE-01"
        assert open_point.tags["severity"] == "high"
        assert open_point.tags["acknowledged"] == "false"

        assert resolved_point.tags["host"] == "SRV-DB-01"
        assert resolved_point.fields["duration_seconds"] == 1000
