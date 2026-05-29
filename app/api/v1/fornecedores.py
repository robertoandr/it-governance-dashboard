"""REST endpoints for the Fornecedores (vendors) resource — API v1."""

import asyncio
import json
import uuid
from typing import Any

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields
from pydantic import ValidationError

from app.db.session import get_session
from app.models.fornecedor import FornecedorCreate, FornecedorResponse, FornecedorUpdate
from app.services.exceptions import FornecedorAlreadyExists, FornecedorNotFound
from app.services.fornecedor_service import fornecedor_service

log = structlog.get_logger(__name__)

ns = Namespace("fornecedores", description="Gestão de fornecedores de TI")

# ---------------------------------------------------------------------------
# Swagger models (for documentation only — validation is done by Pydantic)
# ---------------------------------------------------------------------------
_fornecedor_fields = ns.model(
    "Fornecedor",
    {
        "id": fields.String(readonly=True, description="UUID"),
        "nome": fields.String(required=True, description="Nome do fornecedor"),
        "cnpj": fields.String(description="CNPJ (14 dígitos, sem formatação)"),
        "categoria": fields.String(required=True, description="Categoria"),
        "status": fields.String(description="ativo | inativo | suspenso"),
        "contato_nome": fields.String,
        "contato_email": fields.String,
        "contato_telefone": fields.String,
        "suporte_24x7": fields.Boolean,
        "observacoes": fields.String,
        "created_at": fields.DateTime(readonly=True),
        "updated_at": fields.DateTime(readonly=True),
    },
)

_fornecedor_list_fields = ns.model(
    "FornecedorList",
    {
        "items": fields.List(fields.Nested(_fornecedor_fields)),
        "total": fields.Integer,
        "skip": fields.Integer,
        "limit": fields.Integer,
    },
)


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously inside Flask's sync context.

    Flask[async] supports async views natively, but Flask-RESTX Resource
    methods are detected as sync by default. Using asyncio.run() here is safe
    because each request gets its own thread from the WSGI server.

    Args:
        coro: Awaitable coroutine to execute.

    Returns:
        Any: The coroutine's return value.
    """
    return asyncio.run(coro)


def _validation_error_response(exc: ValidationError) -> tuple[dict[str, Any], int]:
    """Format a Pydantic ValidationError as a 422 response dict.

    Uses exc.json() to ensure all values (including ctx.error Exception objects)
    are serialized to JSON-safe types before returning to Flask-RESTX.

    Args:
        exc: The caught ValidationError.

    Returns:
        tuple[dict, int]: JSON-serialisable error body and HTTP 422 status.
    """
    return {"errors": json.loads(exc.json(include_url=False))}, 422


# ---------------------------------------------------------------------------
# Collection endpoints: /api/v1/fornecedores
# ---------------------------------------------------------------------------
@ns.route("/")
class FornecedorList(Resource):
    """List all active fornecedores or create a new one."""

    @ns.doc(
        params={
            "skip": "Número de registros a pular (default 0)",
            "limit": "Máximo de registros retornados (default 20)",
            "status": "Filtrar por status: ativo | inativo | suspenso",
            "categoria": "Filtrar por categoria",
        }
    )
    @ns.marshal_with(_fornecedor_list_fields)
    def get(self) -> Any:
        """List fornecedores (paginated, filterable)."""
        skip = int(request.args.get("skip", 0))
        limit = min(int(request.args.get("limit", 20)), 100)
        status = request.args.get("status")
        categoria = request.args.get("categoria")

        async def _list() -> Any:
            async with get_session() as session:
                items, total = await fornecedor_service.list(
                    session,
                    skip=skip,
                    limit=limit,
                    status=status,
                    categoria=categoria,
                )
                return {
                    "items": [FornecedorResponse.model_validate(f).model_dump(mode="json") for f in items],
                    "total": total,
                    "skip": skip,
                    "limit": limit,
                }

        log.info("api.fornecedores.list", skip=skip, limit=limit)
        return _run(_list())

    @ns.expect(_fornecedor_fields)
    @ns.response(201, "Fornecedor criado")
    @ns.response(409, "CNPJ já cadastrado")
    @ns.response(422, "Payload inválido")
    def post(self) -> Any:
        """Create a new fornecedor."""
        try:
            data = FornecedorCreate.model_validate(request.json)
        except ValidationError as exc:
            return _validation_error_response(exc)

        async def _create() -> Any:
            async with get_session() as session:
                fornecedor = await fornecedor_service.create(session, data)
                return FornecedorResponse.model_validate(fornecedor).model_dump(mode="json")

        try:
            result = _run(_create())
            log.info("api.fornecedores.created", id=str(result["id"]))
            return result, 201
        except FornecedorAlreadyExists as exc:
            return {"message": str(exc)}, 409


# ---------------------------------------------------------------------------
# Item endpoints: /api/v1/fornecedores/<id>
# ---------------------------------------------------------------------------
@ns.route("/<string:fornecedor_id>")
@ns.param("fornecedor_id", "UUID do fornecedor")
class FornecedorItem(Resource):
    """Retrieve, update, or delete a single fornecedor."""

    def _parse_id(self, raw: str) -> uuid.UUID:
        """Parse and validate a UUID path parameter.

        Args:
            raw: Raw string from the URL path.

        Returns:
            uuid.UUID: Parsed UUID.
        """
        try:
            return uuid.UUID(raw)
        except ValueError:
            ns.abort(400, f"'{raw}' não é um UUID válido")
            raise AssertionError("unreachable") from None  # ns.abort always raises

    @ns.marshal_with(_fornecedor_fields)
    @ns.response(404, "Fornecedor não encontrado")
    def get(self, fornecedor_id: str) -> Any:
        """Get a fornecedor by ID."""
        fid = self._parse_id(fornecedor_id)

        async def _get() -> Any:
            async with get_session() as session:
                return await fornecedor_service.get_by_id(session, fid)

        fornecedor = _run(_get())
        if fornecedor is None:
            ns.abort(404, f"Fornecedor '{fid}' não encontrado")
        log.info("api.fornecedores.get", id=str(fid))
        return FornecedorResponse.model_validate(fornecedor).model_dump(mode="json")

    @ns.expect(_fornecedor_fields)
    @ns.marshal_with(_fornecedor_fields)
    @ns.response(404, "Fornecedor não encontrado")
    @ns.response(409, "CNPJ já cadastrado")
    @ns.response(422, "Payload inválido")
    def patch(self, fornecedor_id: str) -> Any:
        """Partially update a fornecedor (only provided fields are changed)."""
        fid = self._parse_id(fornecedor_id)
        try:
            data = FornecedorUpdate.model_validate(request.json)
        except ValidationError as exc:
            return _validation_error_response(exc)

        async def _update() -> Any:
            async with get_session() as session:
                return await fornecedor_service.update(session, fid, data)

        try:
            fornecedor = _run(_update())
            log.info("api.fornecedores.updated", id=str(fid))
            return FornecedorResponse.model_validate(fornecedor).model_dump(mode="json")
        except FornecedorNotFound as exc:
            ns.abort(404, str(exc))
        except FornecedorAlreadyExists as exc:
            return {"message": str(exc)}, 409

    @ns.response(204, "Fornecedor removido (soft delete)")
    @ns.response(404, "Fornecedor não encontrado")
    def delete(self, fornecedor_id: str) -> Any:
        """Soft-delete a fornecedor (sets deleted_at, does not remove the row)."""
        fid = self._parse_id(fornecedor_id)

        async def _delete() -> Any:
            async with get_session() as session:
                await fornecedor_service.soft_delete(session, fid)

        try:
            _run(_delete())
            log.info("api.fornecedores.deleted", id=str(fornecedor_id))
            return "", 204
        except FornecedorNotFound as exc:
            ns.abort(404, str(exc))
