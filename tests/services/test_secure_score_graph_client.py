"""Testes para itgov/services/secure_score_graph_client.py — sem rede real."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from itgov.services.secure_score_graph_client import SecureScoreGraphClient


class TestSecureScoreGraphClient:
    def test_retorna_primeiro_registro_quando_ha_dados(self) -> None:
        payload = {"value": [{"currentScore": 121.0, "maxScore": 283.0}]}

        async def _fetch_token_mock(_client):
            return "fake-token"

        async def _get_mock(_client, _url, _token):
            return payload

        with (
            patch("itgov.services.secure_score_graph_client._fetch_token", side_effect=_fetch_token_mock),
            patch("itgov.services.secure_score_graph_client._get", side_effect=_get_mock),
        ):
            resultado = asyncio.run(SecureScoreGraphClient().get_latest_secure_score())

        assert resultado == {"currentScore": 121.0, "maxScore": 283.0}

    def test_retorna_none_quando_sem_dados(self) -> None:
        payload = {"value": []}

        async def _fetch_token_mock(_client):
            return "fake-token"

        async def _get_mock(_client, _url, _token):
            return payload

        with (
            patch("itgov.services.secure_score_graph_client._fetch_token", side_effect=_fetch_token_mock),
            patch("itgov.services.secure_score_graph_client._get", side_effect=_get_mock),
        ):
            resultado = asyncio.run(SecureScoreGraphClient().get_latest_secure_score())

        assert resultado is None
