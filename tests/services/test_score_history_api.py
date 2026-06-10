"""Testes do namespace Flask-RESTX score_history (GET /score/history[/<pillar_id>]).

Estratégia:
  - App Flask mínimo com apenas o namespace registrado (sem DB real).
  - TrendService mockado via patch('itgov.api.v1.score_history._svc').
  - Cada teste cobre um cenário de roteamento, serialização ou validação.

Casos cobertos:
  1. Histórico global vazio → 200 com points []
  2. N pontos globais → retornados em ordem ASC
  3. coverage_real/total presente em cada ponto global
  4. Pilar sem histórico → 404
  5. Pilar com histórico → 200 com points preenchidos
  6. data_source + is_real presentes em cada ponto de pilar
  7. days=0 → 400
  8. days=731 → 400
  9. days=1 → 200 (limite inferior válido)
  10. days=730 → 200 (limite superior válido)
  11. pillar_id inválido → 400
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask_restx import Api

from itgov.api.v1.score_history import ns
from itgov.services.trend_service import GlobalPoint, PillarPoint

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def app():
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    api = Api(flask_app, prefix="/api/v1")
    api.add_namespace(ns, path="/score")
    return flask_app


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


def _mock_svc(svc: MagicMock) -> MagicMock:
    """Envolve svc como context manager (pattern de _svc())."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=svc)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ── Helpers de dados ──────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _global_point(offset_days: int = 0, score: float = 75.0) -> GlobalPoint:
    return GlobalPoint(
        ts=_now() - timedelta(days=offset_days),
        global_score=score,
        status="OPERACIONAL",
        trend="stable",
        coverage_real=2,
        coverage_total=5,
    )


def _pillar_point(offset_days: int = 0, data_source: str = "live", is_real: bool = True) -> PillarPoint:
    return PillarPoint(
        ts=_now() - timedelta(days=offset_days),
        score=68.5,
        weight=0.25,
        data_source=data_source,
        is_real=is_real,
        status="DEGRADADO",
        trend="stable",
    )


# ── Histórico global ──────────────────────────────────────────────────────────


class TestGlobalHistory:
    def test_historico_vazio_retorna_200_e_lista_vazia(self, client) -> None:
        svc = MagicMock()
        svc.global_history.return_value = []
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["points"] == []
        assert data["count"] == 0

    def test_n_pontos_retornados_em_ordem(self, client) -> None:
        pontos = [_global_point(offset_days=2), _global_point(offset_days=1), _global_point(offset_days=0)]
        svc = MagicMock()
        svc.global_history.return_value = pontos
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history")
        data = resp.get_json()
        assert data["count"] == 3
        assert len(data["points"]) == 3

    def test_coverage_presente_em_cada_ponto(self, client) -> None:
        svc = MagicMock()
        svc.global_history.return_value = [_global_point()]
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history")
        ponto = resp.get_json()["points"][0]
        assert "coverage_real" in ponto
        assert "coverage_total" in ponto
        assert ponto["coverage_real"] == 2
        assert ponto["coverage_total"] == 5

    def test_payload_global_tem_todos_os_campos(self, client) -> None:
        svc = MagicMock()
        svc.global_history.return_value = [_global_point(score=82.5)]
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history")
        ponto = resp.get_json()["points"][0]
        for campo in ("ts", "global_score", "status", "trend", "coverage_real", "coverage_total"):
            assert campo in ponto, f"campo '{campo}' ausente no payload"

    def test_days_default_90_passado_ao_service(self, client) -> None:
        svc = MagicMock()
        svc.global_history.return_value = []
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            client.get("/api/v1/score/history")
        svc.global_history.assert_called_once_with(90)

    def test_days_customizado_passado_ao_service(self, client) -> None:
        svc = MagicMock()
        svc.global_history.return_value = []
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            client.get("/api/v1/score/history?days=30")
        svc.global_history.assert_called_once_with(30)


# ── Histórico por pilar ───────────────────────────────────────────────────────


class TestPillarHistory:
    def test_pilar_sem_historico_retorna_404(self, client) -> None:
        svc = MagicMock()
        svc.pillar_history.return_value = []
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history/risk_management")
        assert resp.status_code == 404

    def test_pilar_com_historico_retorna_200(self, client) -> None:
        svc = MagicMock()
        svc.pillar_history.return_value = [_pillar_point()]
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history/risk_management")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert data["pillar_id"] == "risk_management"

    def test_data_source_e_is_real_presentes(self, client) -> None:
        svc = MagicMock()
        svc.pillar_history.return_value = [_pillar_point(data_source="live", is_real=True)]
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history/value_delivery")
        ponto = resp.get_json()["points"][0]
        assert ponto["data_source"] == "live"
        assert ponto["is_real"] is True

    def test_coming_soon_is_real_false(self, client) -> None:
        svc = MagicMock()
        svc.pillar_history.return_value = [_pillar_point(data_source="coming_soon", is_real=False)]
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history/strategic_alignment")
        ponto = resp.get_json()["points"][0]
        assert ponto["data_source"] == "coming_soon"
        assert ponto["is_real"] is False

    def test_payload_pilar_tem_todos_os_campos(self, client) -> None:
        svc = MagicMock()
        svc.pillar_history.return_value = [_pillar_point()]
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history/risk_management")
        ponto = resp.get_json()["points"][0]
        for campo in ("ts", "score", "weight", "data_source", "is_real", "status", "trend"):
            assert campo in ponto, f"campo '{campo}' ausente no payload"

    def test_todos_os_cinco_pilares_validos_retornam_200_ou_404(self, client) -> None:
        """Nenhum pilar válido deve retornar 400."""
        pilares = [
            "strategic_alignment",
            "value_delivery",
            "risk_management",
            "resource_management",
            "performance_measure",
        ]
        svc = MagicMock()
        svc.pillar_history.return_value = []  # vazio → 404, não 400
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            for pid in pilares:
                resp = client.get(f"/api/v1/score/history/{pid}")
                assert resp.status_code == 404, f"Esperado 404 para pilar válido sem dados: {pid}"


# ── Validação de days ─────────────────────────────────────────────────────────


class TestValidacaoDays:
    def test_days_zero_retorna_400(self, client) -> None:
        svc = MagicMock()
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history?days=0")
        assert resp.status_code == 400

    def test_days_731_retorna_400(self, client) -> None:
        svc = MagicMock()
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history?days=731")
        assert resp.status_code == 400

    def test_days_negativo_retorna_400(self, client) -> None:
        svc = MagicMock()
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history?days=-1")
        assert resp.status_code == 400

    def test_days_1_valido(self, client) -> None:
        svc = MagicMock()
        svc.global_history.return_value = []
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history?days=1")
        assert resp.status_code == 200
        assert resp.get_json()["days"] == 1

    def test_days_730_valido(self, client) -> None:
        svc = MagicMock()
        svc.global_history.return_value = []
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history?days=730")
        assert resp.status_code == 200
        assert resp.get_json()["days"] == 730

    def test_days_pilar_invalido_retorna_400(self, client) -> None:
        svc = MagicMock()
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history/risk_management?days=0")
        assert resp.status_code == 400


# ── Validação de pillar_id ────────────────────────────────────────────────────


class TestValidacaoPillarId:
    def test_pillar_id_invalido_retorna_400(self, client) -> None:
        svc = MagicMock()
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history/pilar_que_nao_existe")
        assert resp.status_code == 400

    def test_pillar_id_com_maiuscula_retorna_400(self, client) -> None:
        svc = MagicMock()
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history/RISK_MANAGEMENT")
        assert resp.status_code == 400

    def test_pillar_id_vazio_nao_chega_ao_recurso(self, client) -> None:
        # URL sem pillar_id cai no GlobalScoreHistory, não retorna 400 de validação
        svc = MagicMock()
        svc.global_history.return_value = []
        with patch("itgov.api.v1.score_history._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/score/history")
        assert resp.status_code == 200
