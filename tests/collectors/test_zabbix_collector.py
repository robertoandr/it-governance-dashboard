"""Testes de regressão para collectors/zabbix.py — URL format e chamadas RPC."""

from __future__ import annotations

import warnings
from unittest.mock import patch

import responses as responses_lib

import collectors.zabbix as _zbx_mod

# ── Fixtures / helpers ────────────────────────────────────────────────────────

_BASE_URL = "http://zbx.test"
_RPC_URL = f"{_BASE_URL}/api_jsonrpc.php"
_TOKEN = "test-token-abc"

LOGIN_RESPONSE = {"jsonrpc": "2.0", "result": _TOKEN, "id": 1}
EMPTY_RESULT = {"jsonrpc": "2.0", "result": [], "id": 1}
HOST_RESULT = {
    "jsonrpc": "2.0",
    "result": [
        {"host": "web01", "status": "0", "interfaces": [{"available": "1"}]},
        {"host": "db01", "status": "0", "interfaces": [{"available": "2"}]},
    ],
    "id": 1,
}


def _make_collector(zabbix_url: str):
    """Instancia ZabbixCollector com ZABBIX_URL mockado."""
    with (
        patch("config.ZABBIX_URL", zabbix_url),
        patch("config.ZABBIX_USER", "Admin"),
        patch("config.ZABBIX_PASSWORD", "secret"),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore", DeprecationWarning)
        # Reimporta o módulo para que _ZABBIX_RPC_URL seja recalculado
        import importlib

        import collectors.zabbix as mod

        importlib.reload(mod)
        return mod.ZabbixCollector(), mod._ZABBIX_RPC_URL


# ── URL Construction Tests ─────────────────────────────────────────────────────


class TestZabbixRpcUrl:
    """Garante que _ZABBIX_RPC_URL é construída corretamente independente do formato de ZABBIX_URL."""

    def test_bare_url_gets_suffix_appended(self) -> None:
        _, rpc_url = _make_collector("http://zbx.test")
        assert rpc_url == "http://zbx.test/api_jsonrpc.php"

    def test_url_with_suffix_not_duplicated(self) -> None:
        """Regressão #77: ZABBIX_URL com sufixo não deve duplicar /api_jsonrpc.php."""
        _, rpc_url = _make_collector("http://zbx.test/api_jsonrpc.php")
        assert rpc_url == "http://zbx.test/api_jsonrpc.php"
        assert rpc_url.count("/api_jsonrpc.php") == 1

    def test_url_with_trailing_slash_normalized(self) -> None:
        _, rpc_url = _make_collector("http://zbx.test/")
        assert rpc_url == "http://zbx.test/api_jsonrpc.php"

    def test_subpath_url_preserved(self) -> None:
        _, rpc_url = _make_collector("http://zbx.test/zabbix")
        assert rpc_url == "http://zbx.test/zabbix/api_jsonrpc.php"

    def test_subpath_url_with_suffix_not_duplicated(self) -> None:
        _, rpc_url = _make_collector("http://zbx.test/zabbix/api_jsonrpc.php")
        assert rpc_url == "http://zbx.test/zabbix/api_jsonrpc.php"
        assert rpc_url.count("/api_jsonrpc.php") == 1


# ── HTTP Call Tests ────────────────────────────────────────────────────────────


class TestZabbixCollectorCalls:
    """Garante que o collector posta para a URL RPC correta.

    Usa patch.object em _ZABBIX_RPC_URL (evita importlib.reload dentro do
    contexto responses_lib, o que causava flakiness por estado de módulo).
    A construção correta da URL já é coberta por TestZabbixRpcUrl.
    """

    @responses_lib.activate
    def test_get_host_summary_posts_to_rpc_url(self) -> None:
        responses_lib.add(responses_lib.POST, _RPC_URL, json=LOGIN_RESPONSE)
        responses_lib.add(responses_lib.POST, _RPC_URL, json=HOST_RESULT)

        with patch.object(_zbx_mod, "_ZABBIX_RPC_URL", _RPC_URL):
            collector = _zbx_mod.ZabbixCollector()
            result = collector.get_host_summary()

        assert result["total"] == 2
        assert result["up"] == 1
        assert result["down"] == 1
        for call in responses_lib.calls:
            assert "/api_jsonrpc.php/api_jsonrpc.php" not in call.request.url

    @responses_lib.activate
    def test_url_with_suffix_posts_to_correct_endpoint(self) -> None:
        """Regressão #77: mesmo com ZABBIX_URL contendo sufixo, posta para URL correta."""
        responses_lib.add(responses_lib.POST, _RPC_URL, json=LOGIN_RESPONSE)
        responses_lib.add(responses_lib.POST, _RPC_URL, json=HOST_RESULT)

        # Verifica que _ZABBIX_RPC_URL calculado de URL com sufixo é correto
        _, rpc_url = _make_collector(f"{_BASE_URL}/api_jsonrpc.php")
        assert rpc_url == _RPC_URL

        # Verifica que o collector efectivamente posta para essa URL
        with patch.object(_zbx_mod, "_ZABBIX_RPC_URL", _RPC_URL):
            collector = _zbx_mod.ZabbixCollector()
            result = collector.get_host_summary()

        assert result["total"] == 2
        for call in responses_lib.calls:
            assert call.request.url == _RPC_URL
