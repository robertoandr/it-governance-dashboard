"""Integration tests for itgov/api/v1/ativos.py — Flask-RESTX namespace.

Follows the same pattern as test_zabbix_api_namespace.py:
- Minimal Flask app with just the namespace registered
- AtivoService is mocked via patch('itgov.api.v1.ativos._svc')
- Tests cover happy paths + error paths for all endpoints
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from flask import Flask
from flask_restx import Api

from itgov.api.v1.ativos import ns
from itgov.models.ativo import AtivoStats
from itgov.services.ativo_service import AtivoDuplicateError, AtivoNotFoundError

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    api = Api(flask_app, prefix="/api/v1")
    api.add_namespace(ns, path="/ativos")
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _mock_svc(svc: MagicMock) -> MagicMock:
    """Wrap svc as a context manager (as _svc() is a @contextmanager)."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=svc)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _fake_ativo(**kwargs) -> MagicMock:
    """Build a MagicMock that looks like an AtivoDB instance."""
    from datetime import UTC, datetime

    ativo = MagicMock()
    ativo.id = kwargs.get("id", uuid4())
    ativo.nome = kwargs.get("nome", "srv-prod-01")
    ativo.tipo = kwargs.get("tipo", "servidor")
    ativo.ambiente = kwargs.get("ambiente", "prod")
    ativo.criticidade = kwargs.get("criticidade", "alta")
    ativo.owner = kwargs.get("owner", "infra@corp.com")
    ativo.tags = kwargs.get("tags", [])
    ativo.metadata_ = kwargs.get("metadata_", {})
    ativo.contrato_id = kwargs.get("contrato_id")
    ativo.created_at = kwargs.get("created_at", datetime.now(UTC))
    ativo.updated_at = kwargs.get("updated_at", datetime.now(UTC))
    return ativo


VALID_ID = str(uuid4())
SAMPLE_PAYLOAD = {
    "nome": "srv-prod-01",
    "tipo": "servidor",
    "ambiente": "prod",
    "criticidade": "alta",
    "owner": "infra@corp.com",
}


# ── GET /ativos ───────────────────────────────────────────────────────────────


class TestListAtivos:
    def test_returns_200_with_empty_list(self, client):
        svc = MagicMock()
        svc.list.return_value = []
        svc.count.return_value = 0

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/ativos")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_returns_list_with_items(self, client):
        ativo = _fake_ativo()
        svc = MagicMock()
        svc.list.return_value = [ativo]
        svc.count.return_value = 1

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/ativos")

        assert resp.status_code == 200
        assert len(resp.get_json()["items"]) == 1

    def test_passes_filters_to_service(self, client):
        svc = MagicMock()
        svc.list.return_value = []
        svc.count.return_value = 0

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            client.get("/api/v1/ativos?tipo=servidor&ambiente=prod&criticidade=alta&limit=10&offset=5")

        svc.list.assert_called_once_with(
            tipo="servidor",
            ambiente="prod",
            criticidade="alta",
            owner=None,
            include_deleted=False,
            limit=10,
            offset=5,
        )

    def test_serializes_id_as_string(self, client):
        fixed_id = uuid4()
        ativo = _fake_ativo(id=fixed_id)
        svc = MagicMock()
        svc.list.return_value = [ativo]
        svc.count.return_value = 1

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/ativos")

        assert resp.get_json()["items"][0]["id"] == str(fixed_id)


# ── POST /ativos ──────────────────────────────────────────────────────────────


class TestCreateAtivo:
    def test_create_returns_201(self, client):
        ativo = _fake_ativo()
        svc = MagicMock()
        svc.create.return_value = ativo

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.post("/api/v1/ativos", json=SAMPLE_PAYLOAD)

        assert resp.status_code == 201
        assert resp.get_json()["nome"] == "srv-prod-01"

    def test_duplicate_returns_409(self, client):
        svc = MagicMock()
        svc.create.side_effect = AtivoDuplicateError("já existe")

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.post("/api/v1/ativos", json=SAMPLE_PAYLOAD)

        assert resp.status_code == 409
        assert resp.get_json()["code"] == "DUPLICATE"

    def test_invalid_payload_returns_400(self, client):
        svc = MagicMock()
        svc.create.side_effect = ValueError("tipo inválido")

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.post("/api/v1/ativos", json=SAMPLE_PAYLOAD)

        assert resp.status_code == 400
        assert resp.get_json()["code"] == "INVALID_PAYLOAD"


# ── GET /ativos/stats ─────────────────────────────────────────────────────────


class TestAtivoStats:
    def test_stats_returns_200(self, client):
        svc = MagicMock()
        svc.get_stats.return_value = AtivoStats(
            total=3,
            por_tipo={"servidor": 2, "app": 1},
            por_criticidade={"alta": 2, "baixa": 1},
            por_ambiente={"prod": 3},
            sem_owner=0,
            sem_contrato=3,
        )

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/ativos/stats")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 3
        assert data["por_tipo"]["servidor"] == 2
        assert data["sem_contrato"] == 3

    def test_stats_empty(self, client):
        svc = MagicMock()
        svc.get_stats.return_value = AtivoStats(
            total=0,
            por_tipo={},
            por_criticidade={},
            por_ambiente={},
            sem_owner=0,
            sem_contrato=0,
        )

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.get("/api/v1/ativos/stats")

        assert resp.status_code == 200
        assert resp.get_json()["total"] == 0


# ── GET /ativos/<id> ──────────────────────────────────────────────────────────


class TestGetAtivo:
    def test_get_existing_returns_200(self, client):
        ativo = _fake_ativo(id=UUID(VALID_ID))
        svc = MagicMock()
        svc.get.return_value = ativo

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.get(f"/api/v1/ativos/{VALID_ID}")

        assert resp.status_code == 200
        assert resp.get_json()["id"] == VALID_ID

    def test_not_found_returns_404(self, client):
        svc = MagicMock()
        svc.get.side_effect = AtivoNotFoundError("não encontrado")

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.get(f"/api/v1/ativos/{VALID_ID}")

        assert resp.status_code == 404
        assert resp.get_json()["code"] == "NOT_FOUND"

    def test_invalid_uuid_returns_400(self, client):
        resp = client.get("/api/v1/ativos/not-a-uuid")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "INVALID_UUID"


# ── PATCH /ativos/<id> ────────────────────────────────────────────────────────


class TestUpdateAtivo:
    def test_patch_returns_200(self, client):
        ativo = _fake_ativo(id=UUID(VALID_ID), criticidade="baixa")
        svc = MagicMock()
        svc.update.return_value = ativo

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.patch(f"/api/v1/ativos/{VALID_ID}", json={"criticidade": "baixa"})

        assert resp.status_code == 200
        assert resp.get_json()["criticidade"] == "baixa"

    def test_not_found_returns_404(self, client):
        svc = MagicMock()
        svc.update.side_effect = AtivoNotFoundError("não encontrado")

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.patch(f"/api/v1/ativos/{VALID_ID}", json={"criticidade": "baixa"})

        assert resp.status_code == 404

    def test_invalid_uuid_returns_400(self, client):
        resp = client.patch("/api/v1/ativos/not-uuid", json={"criticidade": "baixa"})
        assert resp.status_code == 400

    def test_duplicate_constraint_returns_409(self, client):
        svc = MagicMock()
        svc.update.side_effect = AtivoDuplicateError("unique violation")

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.patch(f"/api/v1/ativos/{VALID_ID}", json={"nome": "srv-dup"})

        assert resp.status_code == 409

    def test_invalid_value_returns_400(self, client):
        svc = MagicMock()
        svc.update.side_effect = ValueError("ambiente inválido")

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.patch(f"/api/v1/ativos/{VALID_ID}", json={"ambiente": "producao"})

        assert resp.status_code == 400


# ── DELETE /ativos/<id> ───────────────────────────────────────────────────────


class TestDeleteAtivo:
    def test_soft_delete_returns_204(self, client):
        svc = MagicMock()
        svc.delete.return_value = None

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.delete(f"/api/v1/ativos/{VALID_ID}")

        assert resp.status_code == 204
        svc.delete.assert_called_once_with(UUID(VALID_ID), soft=True)

    def test_hard_delete_query_param(self, client):
        svc = MagicMock()
        svc.delete.return_value = None

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.delete(f"/api/v1/ativos/{VALID_ID}?hard=true")

        assert resp.status_code == 204
        svc.delete.assert_called_once_with(UUID(VALID_ID), soft=False)

    def test_not_found_returns_404(self, client):
        svc = MagicMock()
        svc.delete.side_effect = AtivoNotFoundError("não encontrado")

        with patch("itgov.api.v1.ativos._svc", return_value=_mock_svc(svc)):
            resp = client.delete(f"/api/v1/ativos/{VALID_ID}")

        assert resp.status_code == 404

    def test_invalid_uuid_returns_400(self, client):
        resp = client.delete("/api/v1/ativos/not-a-uuid")
        assert resp.status_code == 400
