"""Testes do endpoint GET /api/v1/governance/devices — cache e wiring."""

from __future__ import annotations

from unittest.mock import patch

import pytest

_DADOS_FAKE = {
    "total_devices": 10,
    "stale_45d": 2,
    "managed_pct": None,
    "os_distribution": {"Windows": 10},
    "trust_type_distribution": {"Workplace": 10},
}


@pytest.fixture
def flask_app():
    from flask import Flask
    from flask_restx import Api

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-only"

    api = Api(app, prefix="/api/v1")

    from itgov.api.v1.governance_devices import ns as devices_ns

    api.add_namespace(devices_ns, path="/governance")
    return app


@pytest.fixture
def cliente(flask_app):
    with flask_app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def limpar_cache():
    import itgov.api.v1.governance_devices as mod

    mod._cache_dados = None
    mod._cache_ts = 0.0
    yield
    mod._cache_dados = None
    mod._cache_ts = 0.0


class TestEndpointDevices:
    def test_get_retorna_200_com_dados(self, cliente) -> None:
        with patch("itgov.api.v1.governance_devices._buscar_do_graph", return_value=_DADOS_FAKE):
            resp = cliente.get("/api/v1/governance/devices")

        assert resp.status_code == 200
        assert resp.json["total_devices"] == 10

    def test_graph_nao_configurado_retorna_503(self, cliente) -> None:
        with patch("itgov.api.v1.governance_devices._buscar_do_graph", side_effect=RuntimeError("sem tenant")):
            resp = cliente.get("/api/v1/governance/devices")

        assert resp.status_code == 503

    def test_erro_inesperado_retorna_500(self, cliente) -> None:
        with patch("itgov.api.v1.governance_devices._buscar_do_graph", side_effect=ValueError("boom")):
            resp = cliente.get("/api/v1/governance/devices")

        assert resp.status_code == 500


class TestCacheDevices:
    def test_dois_gets_chamam_graph_uma_vez(self, cliente) -> None:
        with patch("itgov.api.v1.governance_devices._buscar_do_graph", return_value=_DADOS_FAKE) as mock_graph:
            cliente.get("/api/v1/governance/devices")
            cliente.get("/api/v1/governance/devices")

        assert mock_graph.call_count == 1

    def test_cache_expirado_busca_novamente(self, cliente, monkeypatch) -> None:
        import itgov.api.v1.governance_devices as mod

        with patch("itgov.api.v1.governance_devices._buscar_do_graph", return_value=_DADOS_FAKE) as mock_graph:
            cliente.get("/api/v1/governance/devices")
            monkeypatch.setattr(mod, "_cache_ts", 0.0)
            cliente.get("/api/v1/governance/devices")

        assert mock_graph.call_count == 2


class TestGetCachedDeviceSummaryWrapper:
    def test_wrapper_publico_retorna_mesmos_dados(self) -> None:
        from itgov.api.v1.governance_devices import get_cached_device_summary

        with patch("itgov.api.v1.governance_devices._buscar_do_graph", return_value=_DADOS_FAKE):
            dados = get_cached_device_summary()

        assert dados == _DADOS_FAKE
