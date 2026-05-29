"""Integration tests for the Contratos REST API endpoints."""

import asyncio
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import create_app

_DB_URL = "postgresql+asyncpg://itgov:Z66BroSKNH6LZcEFOghNRspeQH6oI2Bh@172.18.0.2:5432/itgov"

# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _clean_db() -> None:
    """Clean contratos and fornecedores before the module runs."""

    async def _do() -> None:
        engine = create_async_engine(_DB_URL, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            await s.execute(text("DELETE FROM contratos"))
            await s.execute(text("DELETE FROM fornecedores"))
            await s.commit()
        await engine.dispose()

    asyncio.run(_do())


@pytest.fixture(scope="module")
def app():
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


@pytest.fixture(scope="module")
def fornecedor_id(client) -> str:
    """Create a test fornecedor and return its UUID string."""
    resp = client.post(
        "/api/v1/fornecedores/",
        data=json.dumps({"nome": "Vendor para Contratos", "categoria": "Cloud"}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    return resp.get_json()["id"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def post_contrato(client, fornecedor_id: str, payload: dict | None = None) -> tuple[dict, int]:
    """POST a contrato with sensible defaults; override via payload."""
    base = {
        "fornecedor_id": fornecedor_id,
        "numero_contrato": "CTR-INT-UNIQUE",
        "objeto": "Hospedagem Cloud",
        "valor_mensal": 3000.0,
        "data_inicio": "2026-01-01",
        "data_fim": "2027-01-01",
        "sla_uptime_pct": 99.5,
        "criticidade": "alta",
    }
    if payload:
        base.update(payload)
    resp = client.post(
        "/api/v1/contratos/",
        data=json.dumps(base),
        content_type="application/json",
    )
    return resp.get_json(), resp.status_code


# ---------------------------------------------------------------------------
# GET /api/v1/contratos/
# ---------------------------------------------------------------------------
class TestListContratos:
    def test_returns_200_and_list_shape(self, client) -> None:
        resp = client.get("/api/v1/contratos/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data

    def test_pagination_defaults(self, client) -> None:
        resp = client.get("/api/v1/contratos/?skip=0&limit=5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["skip"] == 0
        assert data["limit"] == 5

    def test_limit_capped_at_100(self, client) -> None:
        resp = client.get("/api/v1/contratos/?limit=999")
        assert resp.status_code == 200
        assert resp.get_json()["limit"] <= 100

    def test_filter_by_invalid_vigente_em_returns_400(self, client) -> None:
        resp = client.get("/api/v1/contratos/?vigente_em=not-a-date")
        assert resp.status_code == 400

    def test_filter_by_valid_vigente_em(self, client, fornecedor_id: str) -> None:
        post_contrato(client, fornecedor_id, {"numero_contrato": "CTR-VIGENTE-EM"})
        resp = client.get("/api/v1/contratos/?vigente_em=2026-06-01")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/v1/contratos/
# ---------------------------------------------------------------------------
class TestCreateContrato:
    def test_create_returns_201_with_correct_fields(
        self, client, fornecedor_id: str
    ) -> None:
        body, status = post_contrato(
            client, fornecedor_id, {"numero_contrato": "CTR-CREATE-001"}
        )
        assert status == 201
        assert body["numero_contrato"] == "CTR-CREATE-001"
        assert body["status"] == "vigente"
        assert body["criticidade"] == "alta"
        assert "id" in body
        assert "created_at" in body

    def test_create_missing_required_field_returns_422(
        self, client, fornecedor_id: str
    ) -> None:
        resp = client.post(
            "/api/v1/contratos/",
            data=json.dumps({"fornecedor_id": fornecedor_id}),
            content_type="application/json",
        )
        assert resp.status_code == 422

    def test_create_invalid_status_returns_422(
        self, client, fornecedor_id: str
    ) -> None:
        body, status = post_contrato(
            client,
            fornecedor_id,
            {"numero_contrato": "CTR-BAD-STATUS", "status": "ativo"},
        )
        assert status == 422
        assert "errors" in body

    def test_create_data_fim_before_inicio_returns_422(
        self, client, fornecedor_id: str
    ) -> None:
        body, status = post_contrato(
            client,
            fornecedor_id,
            {
                "numero_contrato": "CTR-BAD-DATE",
                "data_inicio": "2026-12-01",
                "data_fim": "2026-01-01",
            },
        )
        assert status == 422

    def test_create_zero_valor_mensal_returns_422(
        self, client, fornecedor_id: str
    ) -> None:
        body, status = post_contrato(
            client,
            fornecedor_id,
            {"numero_contrato": "CTR-ZERO-VALOR", "valor_mensal": 0},
        )
        assert status == 422

    def test_create_nonexistent_fornecedor_returns_404(self, client) -> None:
        body, status = post_contrato(
            client,
            "00000000-0000-0000-0000-000000000000",
            {"numero_contrato": "CTR-NO-FORN"},
        )
        assert status == 404

    def test_create_duplicate_numero_returns_409(
        self, client, fornecedor_id: str
    ) -> None:
        post_contrato(client, fornecedor_id, {"numero_contrato": "CTR-DUP-INT"})
        body, status = post_contrato(
            client, fornecedor_id, {"numero_contrato": "CTR-DUP-INT"}
        )
        assert status == 409
        assert "message" in body


# ---------------------------------------------------------------------------
# GET /api/v1/contratos/<id>
# ---------------------------------------------------------------------------
class TestGetContrato:
    def test_get_existing_returns_200_with_fornecedor(
        self, client, fornecedor_id: str
    ) -> None:
        body, _ = post_contrato(client, fornecedor_id, {"numero_contrato": "CTR-GET-001"})
        cid = body["id"]
        resp = client.get(f"/api/v1/contratos/{cid}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == cid
        assert data["fornecedor"] is not None
        assert data["fornecedor"]["id"] == fornecedor_id

    def test_get_nonexistent_returns_404(self, client) -> None:
        resp = client.get("/api/v1/contratos/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_get_invalid_uuid_returns_400(self, client) -> None:
        resp = client.get("/api/v1/contratos/not-a-uuid")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# PATCH /api/v1/contratos/<id>
# ---------------------------------------------------------------------------
class TestPatchContrato:
    def test_patch_status_returns_200(self, client, fornecedor_id: str) -> None:
        body, _ = post_contrato(
            client, fornecedor_id, {"numero_contrato": "CTR-PATCH-001"}
        )
        cid = body["id"]
        resp = client.patch(
            f"/api/v1/contratos/{cid}",
            data=json.dumps({"status": "suspenso"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "suspenso"

    def test_patch_criticidade_returns_200(self, client, fornecedor_id: str) -> None:
        body, _ = post_contrato(
            client, fornecedor_id, {"numero_contrato": "CTR-PATCH-002"}
        )
        cid = body["id"]
        resp = client.patch(
            f"/api/v1/contratos/{cid}",
            data=json.dumps({"criticidade": "critica"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["criticidade"] == "critica"

    def test_patch_nonexistent_returns_404(self, client) -> None:
        resp = client.patch(
            "/api/v1/contratos/00000000-0000-0000-0000-000000000000",
            data=json.dumps({"status": "encerrado"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_patch_invalid_status_returns_422(self, client, fornecedor_id: str) -> None:
        body, _ = post_contrato(
            client, fornecedor_id, {"numero_contrato": "CTR-PATCH-003"}
        )
        cid = body["id"]
        resp = client.patch(
            f"/api/v1/contratos/{cid}",
            data=json.dumps({"status": "invalido"}),
            content_type="application/json",
        )
        assert resp.status_code == 422

    def test_patch_invalid_dates_returns_422(self, client, fornecedor_id: str) -> None:
        body, _ = post_contrato(
            client, fornecedor_id, {"numero_contrato": "CTR-PATCH-004"}
        )
        cid = body["id"]
        resp = client.patch(
            f"/api/v1/contratos/{cid}",
            data=json.dumps({"data_inicio": "2027-01-01", "data_fim": "2026-01-01"}),
            content_type="application/json",
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/v1/contratos/<id>
# ---------------------------------------------------------------------------
class TestDeleteContrato:
    def test_delete_returns_204(self, client, fornecedor_id: str) -> None:
        body, _ = post_contrato(
            client, fornecedor_id, {"numero_contrato": "CTR-DEL-001"}
        )
        cid = body["id"]
        resp = client.delete(f"/api/v1/contratos/{cid}")
        assert resp.status_code == 204

    def test_delete_makes_get_return_404(self, client, fornecedor_id: str) -> None:
        body, _ = post_contrato(
            client, fornecedor_id, {"numero_contrato": "CTR-DEL-002"}
        )
        cid = body["id"]
        client.delete(f"/api/v1/contratos/{cid}")
        resp = client.get(f"/api/v1/contratos/{cid}")
        assert resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, client) -> None:
        resp = client.delete("/api/v1/contratos/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_double_delete_returns_404(self, client, fornecedor_id: str) -> None:
        body, _ = post_contrato(
            client, fornecedor_id, {"numero_contrato": "CTR-DEL-003"}
        )
        cid = body["id"]
        client.delete(f"/api/v1/contratos/{cid}")
        resp = client.delete(f"/api/v1/contratos/{cid}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/fornecedores/<id>/contratos
# ---------------------------------------------------------------------------
class TestFornecedorContratos:
    def test_returns_200_with_list_shape(self, client, fornecedor_id: str) -> None:
        post_contrato(client, fornecedor_id, {"numero_contrato": "CTR-SUB-001"})
        resp = client.get(f"/api/v1/fornecedores/{fornecedor_id}/contratos")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data

    def test_returns_only_that_fornecedor_contracts(
        self, client, fornecedor_id: str
    ) -> None:
        # Create a second vendor and assign a contract to it
        resp2 = client.post(
            "/api/v1/fornecedores/",
            data=json.dumps({"nome": "Second Vendor", "categoria": "Software"}),
            content_type="application/json",
        )
        other_id = resp2.get_json()["id"]
        post_contrato(client, other_id, {"numero_contrato": "CTR-OTHER-VENDOR"})

        resp = client.get(f"/api/v1/fornecedores/{fornecedor_id}/contratos")
        items = resp.get_json()["items"]
        for item in items:
            assert item["fornecedor_id"] == fornecedor_id

    def test_nonexistent_fornecedor_returns_404(self, client) -> None:
        resp = client.get(
            "/api/v1/fornecedores/00000000-0000-0000-0000-000000000000/contratos"
        )
        assert resp.status_code == 404

    def test_invalid_uuid_returns_400(self, client) -> None:
        resp = client.get("/api/v1/fornecedores/not-a-uuid/contratos")
        assert resp.status_code == 400
