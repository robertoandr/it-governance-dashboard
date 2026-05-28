"""Integration tests for the Fornecedores REST API endpoints."""

import asyncio
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import create_app

_DB_URL = "postgresql+asyncpg://itgov:Z66BroSKNH6LZcEFOghNRspeQH6oI2Bh@172.18.0.2:5432/itgov"


@pytest.fixture(scope="module", autouse=True)
def _clean_db() -> None:
    """Truncate fornecedores table before the integration test module runs."""

    async def _do_clean() -> None:
        engine = create_async_engine(_DB_URL, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            await s.execute(text("DELETE FROM contratos"))
            await s.execute(text("DELETE FROM fornecedores"))
            await s.commit()
        await engine.dispose()

    asyncio.run(_do_clean())


@pytest.fixture(scope="module")
def app():
    """Create Flask test app with real database connection."""
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(scope="module")
def client(app):
    """Return a test client for the Flask app."""
    return app.test_client()


def post_fornecedor(client, payload: dict) -> tuple[dict, int]:
    """Helper to POST a fornecedor and return (body, status_code)."""
    resp = client.post(
        "/api/v1/fornecedores/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    return resp.get_json(), resp.status_code


# ---------------------------------------------------------------------------
# GET /api/v1/fornecedores/
# ---------------------------------------------------------------------------
class TestListFornecedores:
    def test_returns_200_and_list_shape(self, client) -> None:
        resp = client.get("/api/v1/fornecedores/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data

    def test_pagination_params(self, client) -> None:
        resp = client.get("/api/v1/fornecedores/?skip=0&limit=5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["limit"] == 5
        assert data["skip"] == 0

    def test_limit_capped_at_100(self, client) -> None:
        resp = client.get("/api/v1/fornecedores/?limit=999")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["limit"] <= 100


# ---------------------------------------------------------------------------
# POST /api/v1/fornecedores/
# ---------------------------------------------------------------------------
class TestCreateFornecedor:
    def test_create_returns_201(self, client) -> None:
        body, status = post_fornecedor(
            client,
            {"nome": "Test Vendor API", "cnpj": "33000167000101", "categoria": "Cloud"},
        )
        assert status == 201
        assert body["nome"] == "Test Vendor API"
        assert body["cnpj"] == "33000167000101"
        assert "id" in body
        assert body["status"] == "ativo"

    def test_create_invalid_cnpj_returns_422(self, client) -> None:
        body, status = post_fornecedor(
            client,
            {"nome": "Bad CNPJ", "cnpj": "00000000000000", "categoria": "Cloud"},
        )
        assert status == 422
        assert "errors" in body

    def test_create_missing_required_field_returns_422(self, client) -> None:
        body, status = post_fornecedor(
            client,
            {"nome": "No Category"},
        )
        assert status == 422

    def test_create_invalid_status_returns_422(self, client) -> None:
        body, status = post_fornecedor(
            client,
            {"nome": "Bad Status", "categoria": "Cloud", "status": "bloqueado"},
        )
        assert status == 422

    def test_create_duplicate_cnpj_returns_409(self, client) -> None:
        payload = {"nome": "First", "cnpj": "45997418000153", "categoria": "Software"}
        post_fornecedor(client, payload)
        body, status = post_fornecedor(
            client,
            {"nome": "Second", "cnpj": "45997418000153", "categoria": "Cloud"},
        )
        assert status == 409
        assert "message" in body


# ---------------------------------------------------------------------------
# GET /api/v1/fornecedores/<id>
# ---------------------------------------------------------------------------
class TestGetFornecedor:
    def test_get_existing_returns_200(self, client) -> None:
        body, _ = post_fornecedor(
            client,
            {"nome": "Fetch Me", "categoria": "Telecom"},
        )
        fid = body["id"]
        resp = client.get(f"/api/v1/fornecedores/{fid}")
        assert resp.status_code == 200
        assert resp.get_json()["id"] == fid

    def test_get_nonexistent_returns_404(self, client) -> None:
        resp = client.get("/api/v1/fornecedores/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_get_invalid_uuid_returns_400(self, client) -> None:
        resp = client.get("/api/v1/fornecedores/not-a-uuid")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# PATCH /api/v1/fornecedores/<id>
# ---------------------------------------------------------------------------
class TestPatchFornecedor:
    def test_patch_updates_status(self, client) -> None:
        body, _ = post_fornecedor(
            client,
            {"nome": "To Patch", "categoria": "Hardware"},
        )
        fid = body["id"]
        resp = client.patch(
            f"/api/v1/fornecedores/{fid}",
            data=json.dumps({"status": "suspenso"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "suspenso"

    def test_patch_nonexistent_returns_404(self, client) -> None:
        resp = client.patch(
            "/api/v1/fornecedores/00000000-0000-0000-0000-000000000000",
            data=json.dumps({"status": "inativo"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_patch_invalid_status_returns_422(self, client) -> None:
        body, _ = post_fornecedor(
            client,
            {"nome": "Patch Bad Status", "categoria": "Cloud"},
        )
        fid = body["id"]
        resp = client.patch(
            f"/api/v1/fornecedores/{fid}",
            data=json.dumps({"status": "inexistente"}),
            content_type="application/json",
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/v1/fornecedores/<id>
# ---------------------------------------------------------------------------
class TestDeleteFornecedor:
    def test_delete_returns_204(self, client) -> None:
        body, _ = post_fornecedor(
            client,
            {"nome": "To Delete", "categoria": "Cloud"},
        )
        fid = body["id"]
        resp = client.delete(f"/api/v1/fornecedores/{fid}")
        assert resp.status_code == 204

    def test_delete_makes_get_return_404(self, client) -> None:
        body, _ = post_fornecedor(
            client,
            {"nome": "Gone", "categoria": "Software"},
        )
        fid = body["id"]
        client.delete(f"/api/v1/fornecedores/{fid}")
        resp = client.get(f"/api/v1/fornecedores/{fid}")
        assert resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, client) -> None:
        resp = client.delete("/api/v1/fornecedores/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
