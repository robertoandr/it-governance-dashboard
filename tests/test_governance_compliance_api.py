"""Testes do endpoint GET /api/v1/governance/compliance — cache e wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

_DADOS_FAKE = {
    "current_score": 121.0,
    "max_score": 283.0,
    "pct": 42.8,
    "category_breakdown": {"Apps": 50.0},
    "recomendacoes": [],
}


@pytest.fixture
def flask_app():
    from flask import Flask
    from flask_restx import Api

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-only"

    api = Api(app, prefix="/api/v1")

    from itgov.api.v1.governance_compliance import ns as compliance_ns

    api.add_namespace(compliance_ns, path="/governance")
    return app


@pytest.fixture
def cliente(flask_app):
    with flask_app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def limpar_cache():
    import itgov.api.v1.governance_compliance as mod

    mod._cache_dados = None
    mod._cache_ts = 0.0
    yield
    mod._cache_dados = None
    mod._cache_ts = 0.0


class TestEndpointCompliance:
    def test_get_retorna_200_com_dados(self, cliente) -> None:
        with patch("itgov.api.v1.governance_compliance._buscar_do_graph", return_value=_DADOS_FAKE):
            resp = cliente.get("/api/v1/governance/compliance")

        assert resp.status_code == 200
        assert resp.json["pct"] == 42.8

    def test_erro_inesperado_retorna_500(self, cliente) -> None:
        with patch("itgov.api.v1.governance_compliance._buscar_do_graph", side_effect=ValueError("boom")):
            resp = cliente.get("/api/v1/governance/compliance")

        assert resp.status_code == 500


class TestCacheCompliance:
    def test_dois_gets_chamam_graph_uma_vez(self, cliente) -> None:
        with patch("itgov.api.v1.governance_compliance._buscar_do_graph", return_value=_DADOS_FAKE) as mock_graph:
            cliente.get("/api/v1/governance/compliance")
            cliente.get("/api/v1/governance/compliance")

        assert mock_graph.call_count == 1

    def test_cache_expirado_busca_novamente(self, cliente, monkeypatch) -> None:
        import itgov.api.v1.governance_compliance as mod

        with patch("itgov.api.v1.governance_compliance._buscar_do_graph", return_value=_DADOS_FAKE) as mock_graph:
            cliente.get("/api/v1/governance/compliance")
            monkeypatch.setattr(mod, "_cache_ts", 0.0)
            cliente.get("/api/v1/governance/compliance")

        assert mock_graph.call_count == 2


class TestGetCachedComplianceSummaryWrapper:
    def test_wrapper_publico_retorna_mesmos_dados(self) -> None:
        from itgov.api.v1.governance_compliance import get_cached_compliance_summary

        with patch("itgov.api.v1.governance_compliance._buscar_do_graph", return_value=_DADOS_FAKE):
            dados = get_cached_compliance_summary()

        assert dados == _DADOS_FAKE


class TestBuscarHistoricoInflux:
    def test_retorna_lista_vazia_quando_query_falha(self) -> None:
        from itgov.api.v1.governance_compliance import _buscar_historico_influx

        with patch("app.services.influxdb_provider.InfluxDBMetricsProvider") as mock_provider_cls:
            mock_provider_cls.side_effect = RuntimeError("influx indisponível")
            resultado = _buscar_historico_influx()

        assert resultado == []

    def test_converte_rows_do_influx_em_pontos(self) -> None:
        from datetime import UTC, datetime

        from itgov.api.v1.governance_compliance import _buscar_historico_influx

        fake_rows = [
            {"_time": datetime(2026, 6, 1, tzinfo=UTC), "_value": 40.0},
            {"_time": datetime(2026, 6, 2, tzinfo=UTC), "_value": 42.0},
        ]

        mock_provider = MagicMock()
        mock_provider._bucket_raw = "governance_raw"
        mock_provider._query.return_value = fake_rows

        with patch("app.services.influxdb_provider.InfluxDBMetricsProvider", return_value=mock_provider):
            resultado = _buscar_historico_influx()

        assert resultado == [
            {"time": "2026-06-01T00:00:00+00:00", "pct": 40.0},
            {"time": "2026-06-02T00:00:00+00:00", "pct": 42.0},
        ]
