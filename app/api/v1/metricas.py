"""REST endpoints for SLA metrics — InfluxDB-backed time-series data."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields
from pydantic import ValidationError

from app.db.session import get_session
from app.models.metrica import (
    DashboardResponse,
    SlaPointCreate,
    SlaQueryParams,
    SlaRiscoItem,
    SlaSeriesPoint,
    SlaSeriesResponse,
)
from app.services.contrato_service import contrato_service
from app.services.influx_client import SlaPoint, influx_service

log = structlog.get_logger(__name__)

ns = Namespace("metricas", description="Métricas de SLA — série temporal InfluxDB")

# ---------------------------------------------------------------------------
# Swagger field models
# ---------------------------------------------------------------------------
_sla_ingest_fields = ns.model(
    "SlaPointCreate",
    {
        "contrato_id": fields.String(required=True, description="UUID do contrato"),
        "fornecedor_id": fields.String(required=True, description="UUID do fornecedor"),
        "criticidade": fields.String(
            required=True, description="baixa | media | alta | critica"
        ),
        "uptime_pct": fields.Float(
            required=True, description="Uptime observado no período (0–100)"
        ),
        "incidentes": fields.Integer(required=True, description="Nº de incidentes"),
        "tempo_indisponivel_min": fields.Integer(
            required=True, description="Minutos de indisponibilidade"
        ),
        "timestamp": fields.DateTime(description="ISO-8601 UTC; omitir = agora"),
    },
)

_sla_series_point_fields = ns.model(
    "SlaSeriesPoint",
    {
        "timestamp": fields.DateTime,
        "uptime_pct": fields.Float,
    },
)

_sla_series_fields = ns.model(
    "SlaSeriesResponse",
    {
        "contrato_id": fields.String,
        "range": fields.String,
        "window": fields.String,
        "uptime_medio_pct": fields.Float,
        "points": fields.List(fields.Nested(_sla_series_point_fields)),
    },
)

_risco_item_fields = ns.model(
    "SlaRiscoItem",
    {
        "contrato_id": fields.String,
        "numero_contrato": fields.String,
        "fornecedor_nome": fields.String,
        "criticidade": fields.String,
        "uptime_atual_pct": fields.Float,
        "uptime_contratado_pct": fields.Float,
        "deficit_pct": fields.Float(description="Positivo = abaixo do contratado (em risco)"),
    },
)

_dashboard_fields = ns.model(
    "DashboardResponse",
    {
        "uptime_medio_pct": fields.Float,
        "contratos_em_risco": fields.Integer,
        "total_contratos_monitorados": fields.Integer,
        "top5_piores_slas": fields.List(fields.Nested(_risco_item_fields)),
        "range": fields.String,
        "gerado_em": fields.DateTime,
    },
)


def _run(coro: Any) -> Any:
    """Execute an async coroutine synchronously inside Flask's sync context.

    Args:
        coro: Awaitable to execute.

    Returns:
        Any: The coroutine's return value.
    """
    return asyncio.run(coro)


def _validation_error_response(exc: ValidationError) -> tuple[dict[str, Any], int]:
    """Format a Pydantic ValidationError as a 422 response.

    Args:
        exc: Caught ValidationError.

    Returns:
        tuple[dict, int]: Error body and HTTP 422 status.
    """
    return {"errors": json.loads(exc.json(include_url=False))}, 422


# ---------------------------------------------------------------------------
# POST /metricas/sla — ingestão de pontos
# ---------------------------------------------------------------------------
@ns.route("/sla")
class SlaIngest(Resource):
    """Ingest a single SLA measurement point into InfluxDB."""

    @ns.expect(_sla_ingest_fields)
    @ns.response(201, "Ponto ingestado")
    @ns.response(422, "Payload inválido")
    @ns.response(503, "InfluxDB indisponível")
    def post(self) -> Any:
        """Ingest a SLA uptime measurement (tags: contrato_id, fornecedor_id, criticidade)."""
        try:
            data = SlaPointCreate.model_validate(request.json)
        except ValidationError as exc:
            return _validation_error_response(exc)

        bound = log.bind(
            contrato_id=str(data.contrato_id),
            fornecedor_id=str(data.fornecedor_id),
        )

        async def _ingest() -> None:
            point = SlaPoint(
                contrato_id=data.contrato_id,
                fornecedor_id=data.fornecedor_id,
                criticidade=data.criticidade,
                uptime_pct=data.uptime_pct,
                incidentes=data.incidentes,
                tempo_indisponivel_min=data.tempo_indisponivel_min,
                timestamp=data.timestamp,
            )
            await influx_service.write_point(point)

        try:
            _run(_ingest())
        except Exception as exc:
            bound.error("api.metricas.sla.ingest_failed", error=str(exc))
            return {"message": f"InfluxDB indisponível: {exc}"}, 503

        bound.info("api.metricas.sla.ingested")
        return {"status": "ok"}, 201


# ---------------------------------------------------------------------------
# GET /metricas/sla/<contrato_id> — série temporal
# ---------------------------------------------------------------------------
@ns.route("/sla/<string:contrato_id>")
@ns.param("contrato_id", "UUID do contrato")
class SlaSeriesResource(Resource):
    """Retrieve SLA uptime timeseries for a contract."""

    @ns.marshal_with(_sla_series_fields)
    @ns.doc(
        params={
            "range": "Janela de consulta: 1d | 7d | 30d | 90d  (default 7d)",
            "window": "Granularidade de agregação: 1h | 6h | 1d  (default 1h)",
        }
    )
    @ns.response(200, "OK")
    @ns.response(400, "Parâmetros inválidos")
    @ns.response(404, "Contrato não encontrado")
    @ns.response(503, "InfluxDB indisponível")
    def get(self, contrato_id: str) -> Any:
        """Get windowed mean uptime for a contract over the requested range."""
        try:
            cid = uuid.UUID(contrato_id)
        except ValueError:
            ns.abort(400, f"'{contrato_id}' não é um UUID válido")

        try:
            params = SlaQueryParams.model_validate(
                {"range": request.args.get("range", "7d"), "window": request.args.get("window", "1h")}
            )
        except ValidationError as exc:
            return _validation_error_response(exc)

        bound = log.bind(contrato_id=str(cid), range=params.range, window=params.window)

        async def _fetch() -> Any:
            # Verify contract exists in PostgreSQL
            async with get_session() as session:
                contrato = await contrato_service.get_by_id(session, cid)
            if contrato is None:
                return None

            points_raw = await influx_service.query_sla_series(
                cid, range_str=params.range, window=params.window
            )
            return contrato, points_raw

        try:
            result = _run(_fetch())
        except Exception as exc:
            bound.error("api.metricas.sla.series_failed", error=str(exc))
            return {"message": f"InfluxDB indisponível: {exc}"}, 503

        if result is None:
            ns.abort(404, f"Contrato '{cid}' não encontrado")

        contrato, points_raw = result
        points = [
            SlaSeriesPoint(timestamp=p["timestamp"], uptime_pct=p["uptime_pct"])
            for p in points_raw
        ]
        uptime_medio = (
            round(sum(p.uptime_pct for p in points) / len(points), 4) if points else None
        )

        bound.info("api.metricas.sla.series", points=len(points))
        return SlaSeriesResponse(
            contrato_id=cid,
            range=params.range,
            window=params.window,
            uptime_medio_pct=uptime_medio,
            points=points,
        ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /metricas/dashboard — agregado executivo
# ---------------------------------------------------------------------------
@ns.route("/dashboard")
class DashboardResource(Resource):
    """Return aggregated SLA health for the executive dashboard."""

    @ns.marshal_with(_dashboard_fields)
    @ns.doc(
        params={
            "range": "Janela de análise: 1d | 7d | 30d | 90d  (default 30d)",
        }
    )
    @ns.response(200, "OK")
    @ns.response(503, "InfluxDB indisponível")
    def get(self) -> Any:
        """Aggregated dashboard: global uptime, at-risk contracts, top-5 worst SLAs.

        Joins InfluxDB actual uptime with PostgreSQL committed SLA (sla_uptime_pct)
        to identify contracts in breach. Results are sorted by worst deficit first.
        """
        range_str = request.args.get("range", "30d")
        if range_str not in ("1d", "7d", "30d", "90d"):
            ns.abort(400, f"range '{range_str}' inválido. Use: 1d | 7d | 30d | 90d")

        async def _build_dashboard() -> dict[str, Any]:
            # Parallel fetch: InfluxDB (actual uptime) + PostgreSQL (contracts + committed SLA)
            influx_data, (contratos, _) = await asyncio.gather(
                influx_service.query_avg_per_contrato(range_str),
                _list_all_contratos(),
            )

            # Build lookup: contrato_id (str) → influx avg uptime
            influx_map: dict[str, float] = {
                item["contrato_id"]: item["uptime_pct"]
                for item in influx_data
                if item["uptime_pct"] is not None
            }

            # Build lookup: contrato_id → (numero_contrato, fornecedor_nome, committed_sla)
            contrato_meta: dict[str, dict[str, Any]] = {}
            for c in contratos:
                contrato_meta[str(c.id)] = {
                    "numero_contrato": c.numero_contrato,
                    "fornecedor_nome": c.fornecedor.nome if c.fornecedor else "—",
                    "criticidade": c.criticidade,
                    "sla_contratado": float(c.sla_uptime_pct),
                }

            # Join and classify
            at_risk: list[SlaRiscoItem] = []
            for cid_str, actual_pct in influx_map.items():
                meta = contrato_meta.get(cid_str)
                if meta is None:
                    continue
                deficit = round(meta["sla_contratado"] - actual_pct, 4)
                if deficit > 0:
                    at_risk.append(
                        SlaRiscoItem(
                            contrato_id=uuid.UUID(cid_str),
                            numero_contrato=meta["numero_contrato"],
                            fornecedor_nome=meta["fornecedor_nome"],
                            criticidade=meta["criticidade"],
                            uptime_atual_pct=round(actual_pct, 4),
                            uptime_contratado_pct=meta["sla_contratado"],
                            deficit_pct=deficit,
                        )
                    )

            at_risk.sort(key=lambda x: x.deficit_pct, reverse=True)

            global_avg = (
                round(sum(influx_map.values()) / len(influx_map), 4) if influx_map else 100.0
            )

            return DashboardResponse(
                uptime_medio_pct=global_avg,
                contratos_em_risco=len(at_risk),
                total_contratos_monitorados=len(influx_map),
                top5_piores_slas=at_risk[:5],
                range=range_str,
                gerado_em=datetime.now(UTC),
            ).model_dump(mode="json")

        async def _list_all_contratos() -> Any:
            """Load contracts with their fornecedor relationship eagerly."""
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            from app.db.models import Contrato

            async with get_session() as session:
                stmt = (
                    select(Contrato)
                    .options(selectinload(Contrato.fornecedor))
                    .where(Contrato.deleted_at.is_(None))
                )
                result = await session.execute(stmt)
                items = list(result.scalars().all())
                return items, len(items)

        try:
            dashboard = _run(_build_dashboard())
        except Exception as exc:
            log.error("api.metricas.dashboard.failed", error=str(exc))
            return {"message": f"Erro ao gerar dashboard: {exc}"}, 503

        log.info(
            "api.metricas.dashboard.built",
            em_risco=dashboard["contratos_em_risco"],
            monitorados=dashboard["total_contratos_monitorados"],
        )
        return dashboard
