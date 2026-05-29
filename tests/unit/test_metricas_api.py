"""Unit tests for the Metricas API endpoints — InfluxDB mocked."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import create_app


@pytest.fixture(scope="module")
def app():
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_CONTRATO_UUID = str(uuid.uuid4())
_FORNECEDOR_UUID = str(uuid.uuid4())

_VALID_INGEST = {
    "contrato_id": _CONTRATO_UUID,
    "fornecedor_id": _FORNECEDOR_UUID,
    "criticidade": "alta",
    "uptime_pct": 98.5,
    "incidentes": 1,
    "tempo_indisponivel_min": 30,
}


# ---------------------------------------------------------------------------
# POST /api/v1/metricas/sla
# ---------------------------------------------------------------------------
class TestSlaIngest:
    def test_valid_payload_returns_201(self, client) -> None:
        with patch(
            "app.api.v1.metricas.influx_service.write_point",
            new_callable=AsyncMock,
        ) as mock_write:
            mock_write.return_value = None
            resp = client.post(
                "/api/v1/metricas/sla",
                data=json.dumps(_VALID_INGEST),
                content_type="application/json",
            )
        assert resp.status_code == 201
        assert resp.get_json()["status"] == "ok"
        mock_write.assert_called_once()

    def test_missing_field_returns_422(self, client) -> None:
        resp = client.post(
            "/api/v1/metricas/sla",
            data=json.dumps({"contrato_id": _CONTRATO_UUID}),
            content_type="application/json",
        )
        assert resp.status_code == 422

    def test_invalid_criticidade_returns_422(self, client) -> None:
        payload = {**_VALID_INGEST, "criticidade": "maxima"}
        resp = client.post(
            "/api/v1/metricas/sla",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 422
        assert "errors" in resp.get_json()

    def test_uptime_above_100_returns_422(self, client) -> None:
        payload = {**_VALID_INGEST, "uptime_pct": 101.0}
        resp = client.post(
            "/api/v1/metricas/sla",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 422

    def test_perfect_uptime_with_incidents_returns_422(self, client) -> None:
        payload = {
            **_VALID_INGEST,
            "uptime_pct": 100.0,
            "incidentes": 2,
            "tempo_indisponivel_min": 0,
        }
        resp = client.post(
            "/api/v1/metricas/sla",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 422

    def test_influx_error_returns_503(self, client) -> None:
        with patch(
            "app.api.v1.metricas.influx_service.write_point",
            new_callable=AsyncMock,
        ) as mock_write:
            mock_write.side_effect = Exception("Connection refused")
            resp = client.post(
                "/api/v1/metricas/sla",
                data=json.dumps(_VALID_INGEST),
                content_type="application/json",
            )
        assert resp.status_code == 503
        assert "InfluxDB" in resp.get_json()["message"]


# ---------------------------------------------------------------------------
# GET /api/v1/metricas/sla/<contrato_id>
# ---------------------------------------------------------------------------
class TestSlaSeriesResource:
    def _make_fake_contrato(self) -> MagicMock:
        c = MagicMock()
        c.id = uuid.UUID(_CONTRATO_UUID)
        c.sla_uptime_pct = 99.5
        c.numero_contrato = "CTR-MOCK-001"
        c.fornecedor = None
        return c

    def test_returns_200_with_series(self, client) -> None:
        fake_contrato = self._make_fake_contrato()
        fake_points = [
            {"timestamp": datetime(2026, 5, 1, 10, tzinfo=UTC), "uptime_pct": 99.2},
            {"timestamp": datetime(2026, 5, 1, 11, tzinfo=UTC), "uptime_pct": 98.8},
        ]

        with (
            patch(
                "app.api.v1.metricas.contrato_service.get_by_id",
                new_callable=AsyncMock,
                return_value=fake_contrato,
            ),
            patch(
                "app.api.v1.metricas.influx_service.query_sla_series",
                new_callable=AsyncMock,
                return_value=fake_points,
            ),
        ):
            resp = client.get(f"/api/v1/metricas/sla/{_CONTRATO_UUID}")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["contrato_id"] == _CONTRATO_UUID
        assert len(data["points"]) == 2
        assert data["uptime_medio_pct"] == pytest.approx(99.0)

    def test_nonexistent_contract_returns_404(self, client) -> None:
        with patch(
            "app.api.v1.metricas.contrato_service.get_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.get(
                f"/api/v1/metricas/sla/00000000-0000-0000-0000-000000000000"
            )
        assert resp.status_code == 404

    def test_invalid_uuid_returns_400(self, client) -> None:
        resp = client.get("/api/v1/metricas/sla/not-a-uuid")
        assert resp.status_code == 400

    def test_invalid_range_returns_422(self, client) -> None:
        fake_contrato = self._make_fake_contrato()
        with patch(
            "app.api.v1.metricas.contrato_service.get_by_id",
            new_callable=AsyncMock,
            return_value=fake_contrato,
        ):
            resp = client.get(
                f"/api/v1/metricas/sla/{_CONTRATO_UUID}?range=3d"
            )
        assert resp.status_code == 422

    def test_influx_unavailable_returns_503(self, client) -> None:
        with patch(
            "app.api.v1.metricas.contrato_service.get_by_id",
            new_callable=AsyncMock,
            return_value=self._make_fake_contrato(),
        ), patch(
            "app.api.v1.metricas.influx_service.query_sla_series",
            new_callable=AsyncMock,
            side_effect=Exception("timeout"),
        ):
            resp = client.get(f"/api/v1/metricas/sla/{_CONTRATO_UUID}")
        assert resp.status_code == 503

    def test_empty_series_returns_none_uptime_medio(self, client) -> None:
        with patch(
            "app.api.v1.metricas.contrato_service.get_by_id",
            new_callable=AsyncMock,
            return_value=self._make_fake_contrato(),
        ), patch(
            "app.api.v1.metricas.influx_service.query_sla_series",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = client.get(f"/api/v1/metricas/sla/{_CONTRATO_UUID}")
        assert resp.status_code == 200
        assert resp.get_json()["uptime_medio_pct"] is None
        assert resp.get_json()["points"] == []


# ---------------------------------------------------------------------------
# GET /api/v1/metricas/dashboard
# ---------------------------------------------------------------------------
class TestDashboardResource:
    def _fake_influx_data(self) -> list[dict]:
        return [
            {
                "contrato_id": _CONTRATO_UUID,
                "fornecedor_id": _FORNECEDOR_UUID,
                "criticidade": "critica",
                "uptime_pct": 95.0,  # below 99.5 committed -> at risk
            }
        ]

    def _fake_contrato(self) -> MagicMock:
        c = MagicMock()
        c.id = uuid.UUID(_CONTRATO_UUID)
        c.numero_contrato = "CTR-DASH-001"
        c.sla_uptime_pct = 99.5
        c.criticidade = "critica"
        c.deleted_at = None
        forn = MagicMock()
        forn.nome = "Vendor DASH"
        c.fornecedor = forn
        return c

    def test_returns_200_with_at_risk_contract(self, client) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        fake_contrato = self._fake_contrato()

        async def _fake_execute(stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = [fake_contrato]
            return result

        with (
            patch(
                "app.api.v1.metricas.influx_service.query_avg_per_contrato",
                new_callable=AsyncMock,
                return_value=self._fake_influx_data(),
            ),
            patch(
                "app.api.v1.metricas.get_session"
            ) as mock_session_ctx,
        ):
            # Build a minimal fake async context manager for get_session
            fake_session = AsyncMock(spec=AsyncSession)
            fake_session.execute = _fake_execute
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=fake_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.get("/api/v1/metricas/dashboard")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "uptime_medio_pct" in data
        assert "contratos_em_risco" in data
        assert "top5_piores_slas" in data
        assert data["contratos_em_risco"] == 1
        assert data["top5_piores_slas"][0]["deficit_pct"] == pytest.approx(4.5)

    def test_invalid_range_returns_400(self, client) -> None:
        resp = client.get("/api/v1/metricas/dashboard?range=5d")
        assert resp.status_code == 400

    def test_influx_unavailable_returns_503(self, client) -> None:
        with patch(
            "app.api.v1.metricas.influx_service.query_avg_per_contrato",
            new_callable=AsyncMock,
            side_effect=Exception("InfluxDB down"),
        ):
            resp = client.get("/api/v1/metricas/dashboard")
        assert resp.status_code == 503
