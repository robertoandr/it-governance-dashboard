"""Testes do namespace Flask-RESTX assets (GET /assets, /assets/summary, /assets/history).

Estratégia:
  - App Flask mínimo com apenas o namespace registrado (sem DB real).
  - AssetStatusService mockado via patch('itgov.api.v1.assets._svc').
  - Cada teste cobre um cenário de roteamento, serialização ou validação.

Casos cobertos:
  GET /assets:
    1. Lista vazia → 200 com items [] e total=0
    2. Lista com N ativos → items corretos, total=N
    3. Filtro asset_type passado ao service
    4. Filtro status passado ao service

  GET /assets/summary:
    5. Banco vazio → total=0
    6. Resumo com dados → por_tipo e por_status corretos

  GET /assets/history:
    7. Sem transições → 200 com items [] e total=0
    8. N transições → items corretos
    9. days=0 → 400
    10. days=731 → 400
    11. days=1 → 200 (limite inferior válido)
    12. days=730 → 200 (limite superior válido)
    13. Filtro hostid passado ao service
    14. duration_seconds no payload (pode ser None)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from flask import Flask
from flask_restx import Api

from itgov.api.v1.assets import ns
from itgov.models.db.asset_status import AssetStatusDB, AssetStatusHistoryDB

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def app():
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    api = Api(flask_app, prefix="/api/v1")
    api.add_namespace(ns, path="/assets")
    return flask_app


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


def _mock_svc(svc: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=svc)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ── Helpers de dados ──────────────────────────────────────────────────────────


def _asset(hostid: str = "1001", asset_type: str = "camera", status: str = "up") -> AssetStatusDB:
    a = MagicMock(spec=AssetStatusDB)
    a.hostid = hostid
    a.host = f"host-{hostid}"
    a.name = f"Nome {hostid}"
    a.asset_type = asset_type
    a.status = status
    a.last_change = None
    a.updated_at = datetime.now(UTC)
    return a


def _history_item(
    hostid: str = "1001",
    from_status: str = "up",
    to_status: str = "down",
    duration: int | None = 120,
) -> AssetStatusHistoryDB:
    h = MagicMock(spec=AssetStatusHistoryDB)
    h.id = uuid4()
    h.hostid = hostid
    h.asset_type = "camera"
    h.from_status = from_status
    h.to_status = to_status
    h.changed_at = datetime.now(UTC)
    h.duration_seconds = duration
    return h


# ── GET /assets ───────────────────────────────────────────────────────────────


class TestListAssets:
    def test_lista_vazia_retorna_200(self, client) -> None:
        svc = MagicMock()
        svc.list_assets.return_value = []
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_lista_com_ativos_retorna_corretos(self, client) -> None:
        svc = MagicMock()
        svc.list_assets.return_value = [_asset("1001", "camera", "up"), _asset("1002", "server", "down")]
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets")
        data = resp.get_json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert data["items"][0]["hostid"] == "1001"
        assert data["items"][1]["status"] == "down"

    def test_filtro_asset_type_passado_ao_service(self, client) -> None:
        svc = MagicMock()
        svc.list_assets.return_value = []
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            client.get("/api/v1/assets?asset_type=camera")
        svc.list_assets.assert_called_once_with(asset_type="camera", status=None)

    def test_filtro_status_passado_ao_service(self, client) -> None:
        svc = MagicMock()
        svc.list_assets.return_value = []
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            client.get("/api/v1/assets?status=down")
        svc.list_assets.assert_called_once_with(asset_type=None, status="down")

    def test_payload_asset_tem_todos_os_campos(self, client) -> None:
        svc = MagicMock()
        svc.list_assets.return_value = [_asset()]
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets")
        item = resp.get_json()["items"][0]
        for campo in ("hostid", "host", "name", "asset_type", "status", "last_change", "updated_at"):
            assert campo in item, f"campo '{campo}' ausente no payload"


# ── GET /assets/summary ────────────────────────────────────────────────────────


class TestSummary:
    def test_banco_vazio_retorna_zeros(self, client) -> None:
        svc = MagicMock()
        svc.summary.return_value = MagicMock(total=0, por_tipo={}, por_status={}, por_tipo_status={})
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["por_tipo"] == {}
        assert data["por_status"] == {}

    def test_resumo_com_dados(self, client) -> None:
        svc = MagicMock()
        svc.summary.return_value = MagicMock(
            total=520,
            por_tipo={"camera": 509, "server": 11},
            por_status={"up": 500, "down": 15, "unknown": 5},
            por_tipo_status={"camera": {"up": 490, "down": 14, "unknown": 5}},
        )
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/summary")
        data = resp.get_json()
        assert data["total"] == 520
        assert data["por_tipo"]["camera"] == 509
        assert data["por_status"]["down"] == 15


# ── GET /assets/history ───────────────────────────────────────────────────────


class TestHistory:
    def test_sem_transicoes_retorna_lista_vazia(self, client) -> None:
        svc = MagicMock()
        svc.history.return_value = []
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_n_transicoes_retornadas(self, client) -> None:
        svc = MagicMock()
        svc.history.return_value = [
            _history_item("1001", "up", "down"),
            _history_item("1002", "down", "up"),
        ]
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/history")
        data = resp.get_json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_payload_history_tem_todos_os_campos(self, client) -> None:
        svc = MagicMock()
        svc.history.return_value = [_history_item()]
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/history")
        item = resp.get_json()["items"][0]
        for campo in ("id", "hostid", "asset_type", "from_status", "to_status", "changed_at", "duration_seconds"):
            assert campo in item, f"campo '{campo}' ausente no payload"

    def test_duration_seconds_pode_ser_none(self, client) -> None:
        svc = MagicMock()
        svc.history.return_value = [_history_item(duration=None)]
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/history")
        assert resp.get_json()["items"][0]["duration_seconds"] is None

    def test_days_default_passado_ao_service(self, client) -> None:
        svc = MagicMock()
        svc.history.return_value = []
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            client.get("/api/v1/assets/history")
        _, kwargs = svc.history.call_args
        assert kwargs["days"] == 7

    def test_filtro_hostid_passado_ao_service(self, client) -> None:
        svc = MagicMock()
        svc.history.return_value = []
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            client.get("/api/v1/assets/history?hostid=1001")
        _, kwargs = svc.history.call_args
        assert kwargs["hostid"] == "1001"

    def test_days_zero_retorna_400(self, client) -> None:
        svc = MagicMock()
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/history?days=0")
        assert resp.status_code == 400

    def test_days_731_retorna_400(self, client) -> None:
        svc = MagicMock()
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/history?days=731")
        assert resp.status_code == 400

    def test_days_negativo_retorna_400(self, client) -> None:
        svc = MagicMock()
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/history?days=-1")
        assert resp.status_code == 400

    def test_days_1_valido(self, client) -> None:
        svc = MagicMock()
        svc.history.return_value = []
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/history?days=1")
        assert resp.status_code == 200
        assert resp.get_json()["days"] == 1

    def test_days_730_valido(self, client) -> None:
        svc = MagicMock()
        svc.history.return_value = []
        with patch("itgov.api.v1.assets._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/assets/history?days=730")
        assert resp.status_code == 200
        assert resp.get_json()["days"] == 730
