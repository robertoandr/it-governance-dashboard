"""REST endpoints for the Contratos (contracts) resource — API v1."""

import asyncio
import json
import uuid
from datetime import date
from typing import Any

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields
from pydantic import ValidationError

from app.db.session import get_session
from app.models.contrato import (
    ContratoCreate,
    ContratoDetailResponse,
    ContratoResponse,
    ContratoUpdate,
)
from app.services.exceptions import (
    ContratoAlreadyExists,
    ContratoNotFound,
    FornecedorNotFound,
)
from app.services.contrato_service import contrato_service

log = structlog.get_logger(__name__)

ns = Namespace("contratos", description="Gestão de contratos de fornecedores")

# ---------------------------------------------------------------------------
# Swagger field models (documentation only — Pydantic handles validation)
# ---------------------------------------------------------------------------
_fornecedor_embed_fields = ns.model(
    "FornecedorEmbed",
    {
        "id": fields.String(readonly=True),
        "nome": fields.String,
        "cnpj": fields.String,
        "categoria": fields.String,
        "status": fields.String,
    },
)

_contrato_fields = ns.model(
    "Contrato",
    {
        "id": fields.String(readonly=True, description="UUID"),
        "fornecedor_id": fields.String(required=True, description="UUID do fornecedor"),
        "numero_contrato": fields.String(required=True, description="Número único do contrato"),
        "objeto": fields.String(required=True, description="Escopo/objeto do contrato"),
        "valor_mensal": fields.Float(required=True, description="Valor mensal em BRL (> 0)"),
        "data_inicio": fields.Date(required=True, description="Data de início (YYYY-MM-DD)"),
        "data_fim": fields.Date(required=True, description="Data de término (YYYY-MM-DD)"),
        "status": fields.String(description="vigente | encerrado | suspenso | renovacao"),
        "sla_uptime_pct": fields.Float(description="SLA de uptime comprometido (0–100)"),
        "criticidade": fields.String(description="baixa | media | alta | critica"),
        "created_at": fields.DateTime(readonly=True),
        "updated_at": fields.DateTime(readonly=True),
    },
)

_contrato_detail_fields = ns.inherit(
    "ContratoDetail",
    _contrato_fields,
    {"fornecedor": fields.Nested(_fornecedor_embed_fields, allow_null=True)},
)

_contrato_list_fields = ns.model(
    "ContratoList",
    {
        "items": fields.List(fields.Nested(_contrato_fields)),
        "total": fields.Integer,
        "skip": fields.Integer,
        "limit": fields.Integer,
    },
)


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously inside Flask's sync context.

    Args:
        coro: Awaitable coroutine to execute.

    Returns:
        Any: The coroutine's return value.
    """
    return asyncio.run(coro)


def _validation_error_response(exc: ValidationError) -> tuple[dict[str, Any], int]:
    """Format a Pydantic ValidationError as a 422 response dict.

    Args:
        exc: The caught ValidationError.

    Returns:
        tuple[dict, int]: JSON-serialisable error body and HTTP 422 status.
    """
    return {"errors": json.loads(exc.json(include_url=False))}, 422


def _parse_uuid(raw: str, ns_ref: Any) -> uuid.UUID:
    """Parse and validate a UUID path parameter, aborting 400 on failure.

    Args:
        raw: Raw string from the URL path.
        ns_ref: Flask-RESTX Namespace used to call abort on failure.

    Returns:
        uuid.UUID: Parsed UUID.
    """
    try:
        return uuid.UUID(raw)
    except ValueError:
        ns_ref.abort(400, f"'{raw}' não é um UUID válido")
        raise AssertionError("unreachable") from None


# ---------------------------------------------------------------------------
# Collection: GET /contratos/  POST /contratos/
# ---------------------------------------------------------------------------
@ns.route("/")
class ContratoList(Resource):
    """List all active contratos or create a new one."""

    @ns.doc(
        params={
            "skip": "Registros a pular (default 0)",
            "limit": "Máximo de registros retornados (default 20)",
            "status": "Filtrar por status: vigente | encerrado | suspenso | renovacao",
            "fornecedor_id": "Filtrar por UUID do fornecedor",
            "criticidade": "Filtrar por criticidade: baixa | media | alta | critica",
            "vigente_em": "Filtrar contratos vigentes em YYYY-MM-DD",
        }
    )
    @ns.marshal_with(_contrato_list_fields)
    def get(self) -> Any:
        """List contratos (paginated, filterable)."""
        skip = int(request.args.get("skip", 0))
        limit = min(int(request.args.get("limit", 20)), 100)
        status = request.args.get("status")
        criticidade = request.args.get("criticidade")

        fornecedor_id: uuid.UUID | None = None
        raw_fid = request.args.get("fornecedor_id")
        if raw_fid:
            fornecedor_id = _parse_uuid(raw_fid, ns)

        vigente_em: date | None = None
        raw_date = request.args.get("vigente_em")
        if raw_date:
            try:
                vigente_em = date.fromisoformat(raw_date)
            except ValueError:
                ns.abort(400, f"vigente_em '{raw_date}' não é uma data válida (YYYY-MM-DD)")

        async def _list() -> Any:
            async with get_session() as session:
                items, total = await contrato_service.list(
                    session,
                    skip=skip,
                    limit=limit,
                    status=status,
                    fornecedor_id=fornecedor_id,
                    criticidade=criticidade,
                    vigente_em=vigente_em,
                )
                return {
                    "items": [
                        ContratoResponse.model_validate(c).model_dump(mode="json")
                        for c in items
                    ],
                    "total": total,
                    "skip": skip,
                    "limit": limit,
                }

        log.info("api.contratos.list", skip=skip, limit=limit)
        return _run(_list())

    @ns.expect(_contrato_fields)
    @ns.response(201, "Contrato criado")
    @ns.response(404, "Fornecedor não encontrado")
    @ns.response(409, "Número de contrato já cadastrado")
    @ns.response(422, "Payload inválido")
    def post(self) -> Any:
        """Create a new contrato."""
        try:
            data = ContratoCreate.model_validate(request.json)
        except ValidationError as exc:
            return _validation_error_response(exc)

        async def _create() -> Any:
            async with get_session() as session:
                contrato = await contrato_service.create(session, data)
                return ContratoResponse.model_validate(contrato).model_dump(mode="json")

        try:
            result = _run(_create())
            log.info(
                "api.contratos.created",
                id=str(result["id"]),
                fornecedor_id=str(data.fornecedor_id),
            )
            return result, 201
        except FornecedorNotFound as exc:
            ns.abort(404, str(exc))
        except ContratoAlreadyExists as exc:
            return {"message": str(exc)}, 409


# ---------------------------------------------------------------------------
# Item: GET/PATCH/DELETE /contratos/<id>
# ---------------------------------------------------------------------------
@ns.route("/<string:contrato_id>")
@ns.param("contrato_id", "UUID do contrato")
class ContratoItem(Resource):
    """Retrieve, update, or soft-delete a single contrato."""

    @ns.marshal_with(_contrato_detail_fields)
    @ns.response(200, "OK")
    @ns.response(404, "Contrato não encontrado")
    def get(self, contrato_id: str) -> Any:
        """Get a contrato by ID (fornecedor embedded in response)."""
        cid = _parse_uuid(contrato_id, ns)
        bound_log = log.bind(contrato_id=str(cid))

        async def _get() -> Any:
            async with get_session() as session:
                return await contrato_service.get_by_id(session, cid, with_fornecedor=True)

        contrato = _run(_get())
        if contrato is None:
            ns.abort(404, f"Contrato '{cid}' não encontrado")

        bound_log.info("api.contratos.get")
        return ContratoDetailResponse.model_validate(contrato).model_dump(mode="json")

    @ns.expect(_contrato_fields)
    @ns.marshal_with(_contrato_fields)
    @ns.response(200, "OK")
    @ns.response(404, "Contrato não encontrado")
    @ns.response(409, "Número de contrato já cadastrado")
    @ns.response(422, "Payload inválido")
    def patch(self, contrato_id: str) -> Any:
        """Partially update a contrato (only provided fields are changed)."""
        cid = _parse_uuid(contrato_id, ns)
        try:
            data = ContratoUpdate.model_validate(request.json)
        except ValidationError as exc:
            return _validation_error_response(exc)

        async def _update() -> Any:
            async with get_session() as session:
                return await contrato_service.update(session, cid, data)

        try:
            contrato = _run(_update())
            log.info("api.contratos.updated", contrato_id=str(cid))
            return ContratoResponse.model_validate(contrato).model_dump(mode="json")
        except ContratoNotFound as exc:
            ns.abort(404, str(exc))
        except ContratoAlreadyExists as exc:
            return {"message": str(exc)}, 409

    @ns.response(204, "Contrato removido (soft delete)")
    @ns.response(404, "Contrato não encontrado")
    def delete(self, contrato_id: str) -> Any:
        """Soft-delete a contrato (sets deleted_at, does not remove the row)."""
        cid = _parse_uuid(contrato_id, ns)

        async def _delete() -> Any:
            async with get_session() as session:
                await contrato_service.soft_delete(session, cid)

        try:
            _run(_delete())
            log.info("api.contratos.deleted", contrato_id=str(cid))
            return "", 204
        except ContratoNotFound as exc:
            ns.abort(404, str(exc))
