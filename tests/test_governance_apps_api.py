"""Testes do endpoint GET /api/v1/governance/apps — cache e wiring."""

from __future__ import annotations

from unittest.mock import patch

import pytest

_DADOS_FAKE = {
    "total_apps": 47,
    "secrets_expirando_30d": 1,
    "secrets_expirados": 0,
    "expirando": [],
}


@pytest.fixture
def flask_app():
    from flask import Flask
    from flask_restx import Api

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-only"

    api = Api(app, prefix="/api/v1")

    from itgov.api.v1.governance_apps import ns as apps_ns

    api.add_namespace(apps_ns, path="/governance")
    return app


@pytest.fixture
def cliente(flask_app):
    with flask_app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def limpar_cache():
    import itgov.api.v1.governance_apps as mod

    mod._cache_dados = None
    mod._cache_ts = 0.0
    yield
    mod._cache_dados = None
    mod._cache_ts = 0.0


class TestEndpointApps:
    def test_get_retorna_200_com_dados(self, cliente) -> None:
        with patch("itgov.api.v1.governance_apps._buscar_do_graph", return_value=_DADOS_FAKE):
            resp = cliente.get("/api/v1/governance/apps")

        assert resp.status_code == 200
        assert resp.json["total_apps"] == 47

    def test_graph_nao_configurado_retorna_503(self, cliente) -> None:
        with patch("itgov.api.v1.governance_apps._buscar_do_graph", side_effect=RuntimeError("sem tenant")):
            resp = cliente.get("/api/v1/governance/apps")

        assert resp.status_code == 503

    def test_erro_inesperado_retorna_500(self, cliente) -> None:
        with patch("itgov.api.v1.governance_apps._buscar_do_graph", side_effect=ValueError("boom")):
            resp = cliente.get("/api/v1/governance/apps")

        assert resp.status_code == 500


class TestCacheApps:
    def test_dois_gets_chamam_graph_uma_vez(self, cliente) -> None:
        with patch("itgov.api.v1.governance_apps._buscar_do_graph", return_value=_DADOS_FAKE) as mock_graph:
            cliente.get("/api/v1/governance/apps")
            cliente.get("/api/v1/governance/apps")

        assert mock_graph.call_count == 1

    def test_cache_expirado_busca_novamente(self, cliente, monkeypatch) -> None:
        import itgov.api.v1.governance_apps as mod

        with patch("itgov.api.v1.governance_apps._buscar_do_graph", return_value=_DADOS_FAKE) as mock_graph:
            cliente.get("/api/v1/governance/apps")
            monkeypatch.setattr(mod, "_cache_ts", 0.0)
            cliente.get("/api/v1/governance/apps")

        assert mock_graph.call_count == 2


class TestGetCachedAppSummaryWrapper:
    def test_wrapper_publico_retorna_mesmos_dados(self) -> None:
        from itgov.api.v1.governance_apps import get_cached_app_summary

        with patch("itgov.api.v1.governance_apps._buscar_do_graph", return_value=_DADOS_FAKE):
            dados = get_cached_app_summary()

        assert dados == _DADOS_FAKE
