"""Testes para itgov/services/sensitivity_label_graph_client.py — sem rede real."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from itgov.services.sensitivity_label_graph_client import SensitivityLabelGraphClient


class TestSensitivityLabelGraphClient:
    def test_get_labels_retorna_itens_do_value(self) -> None:
        payload = {"value": [{"id": "1", "name": "Confidencial"}]}

        async def _fetch_token_mock(_client):
            return "fake-token"

        async def _get_mock(_client, _url, _token):
            return payload

        with (
            patch("itgov.services.sensitivity_label_graph_client._fetch_token", side_effect=_fetch_token_mock),
            patch("itgov.services.sensitivity_label_graph_client._get", side_effect=_get_mock),
        ):
            resultado = asyncio.run(SensitivityLabelGraphClient().get_labels())

        assert resultado == [{"id": "1", "name": "Confidencial"}]

    def test_get_labels_lista_vazia_quando_sem_labels(self) -> None:
        payload = {"value": []}

        async def _fetch_token_mock(_client):
            return "fake-token"

        async def _get_mock(_client, _url, _token):
            return payload

        with (
            patch("itgov.services.sensitivity_label_graph_client._fetch_token", side_effect=_fetch_token_mock),
            patch("itgov.services.sensitivity_label_graph_client._get", side_effect=_get_mock),
        ):
            resultado = asyncio.run(SensitivityLabelGraphClient().get_labels())

        assert resultado == []
