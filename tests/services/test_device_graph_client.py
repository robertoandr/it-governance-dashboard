"""Testes para itgov/services/device_graph_client.py — sem rede real."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from itgov.services.device_graph_client import DeviceGraphClient


class TestDeviceGraphClient:
    def test_get_devices_pagina_e_retorna_todos_os_itens(self) -> None:
        pagina1 = {"value": [{"id": "1"}], "@odata.nextLink": "http://graph/page2"}
        pagina2 = {"value": [{"id": "2"}]}

        async def _get_side(_client, url, _token):
            return pagina2 if "page2" in url else pagina1

        async def _fetch_token_mock(_client):
            return "fake-token"

        with (
            patch("itgov.services.device_graph_client._fetch_token", side_effect=_fetch_token_mock),
            patch("itgov.services.mfa_graph_client._get", side_effect=_get_side),
        ):
            client = DeviceGraphClient()
            resultado = asyncio.run(client.get_devices("tenant-id"))

        assert len(resultado) == 2
        assert resultado[0]["id"] == "1"
        assert resultado[1]["id"] == "2"

    def test_get_devices_lista_vazia_quando_sem_dispositivos(self) -> None:
        pagina_vazia = {"value": []}

        async def _get_side(_client, _url, _token):
            return pagina_vazia

        async def _fetch_token_mock(_client):
            return "fake-token"

        with (
            patch("itgov.services.device_graph_client._fetch_token", side_effect=_fetch_token_mock),
            patch("itgov.services.mfa_graph_client._get", side_effect=_get_side),
        ):
            client = DeviceGraphClient()
            resultado = asyncio.run(client.get_devices("tenant-id"))

        assert resultado == []
