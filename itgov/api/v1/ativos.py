"""Flask-RESTX namespace for IT Asset Inventory REST API.

Exposes AtivoService via HTTP with OpenAPI documentation.
Aligned with ADR-008 (Asset Inventory Strategy).
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields

from itgov.services.ativo_service import (
    AtivoDuplicateError,
    AtivoNotFoundError,
    AtivoService,
)

log = structlog.get_logger(__name__)

ns = Namespace("ativos", description="Inventário de Ativos de TI")

# ── Swagger models ────────────────────────────────────────────────────────────

_ativo_fields = {
    "id": fields.String(description="UUID do ativo"),
    "nome": fields.String(description="Nome único (com tipo)"),
    "tipo": fields.String(description="servidor | switch | app | licenca | endpoint"),
    "ambiente": fields.String(description="prod | hml | dev"),
    "criticidade": fields.String(description="alta | media | baixa"),
    "owner": fields.String(description="Email do responsável técnico"),
    "tags": fields.List(fields.String, description="Tags livres"),
    "metadata": fields.Raw(description="Atributos específicos por tipo"),
    "contrato_id": fields.String(allow_null=True, description="UUID do contrato vinculado"),
    "created_at": fields.String(description="ISO 8601 UTC"),
    "updated_at": fields.String(description="ISO 8601 UTC"),
}

ativo_model = ns.model("Ativo", _ativo_fields)

ativo_create_model = ns.model(
    "AtivoCreate",
    {
        "nome": fields.String(required=True, example="srv-prod-01"),
        "tipo": fields.String(
            required=True,
            description="servidor | switch | app | licenca | endpoint",
            example="servidor",
        ),
        "ambiente": fields.String(
            required=True,
            description="prod | hml | dev",
            example="prod",
        ),
        "criticidade": fields.String(
            required=True,
            description="alta | media | baixa",
            example="alta",
        ),
        "owner": fields.String(required=True, example="infra@corp.com"),
        "tags": fields.List(fields.String, description="Tags livres"),
        "metadata": fields.Raw(description="Atributos específicos por tipo"),
        "contrato_id": fields.String(allow_null=True),
    },
)

ativo_update_model = ns.model(
    "AtivoUpdate",
    {
        "nome": fields.String(description="Novo nome (atualiza a chave única)"),
        "ambiente": fields.String(description="prod | hml | dev"),
        "criticidade": fields.String(description="alta | media | baixa"),
        "owner": fields.String(description="Email do novo responsável"),
        "tags": fields.List(fields.String),
        "metadata": fields.Raw(description="Novos metadados (substituição total)"),
        "contrato_id": fields.String(allow_null=True),
    },
)

ativo_list_model = ns.model(
    "AtivoList",
    {
        "items": fields.List(fields.Nested(ativo_model)),
        "total": fields.Integer(description="Total de ativos correspondentes ao filtro"),
        "limit": fields.Integer,
        "offset": fields.Integer,
    },
)

stats_model = ns.model(
    "AtivoStats",
    {
        "total": fields.Integer,
        "por_tipo": fields.Raw(description="Contagem por tipo"),
        "por_criticidade": fields.Raw(description="Contagem por criticidade"),
        "por_ambiente": fields.Raw(description="Contagem por ambiente"),
        "sem_owner": fields.Integer,
        "sem_contrato": fields.Integer,
    },
)

error_model = ns.model(
    "Error",
    {
        "error": fields.String(description="Mensagem de erro"),
        "code": fields.String(description="Código do erro"),
    },
)

list_parser = ns.parser()
list_parser.add_argument("tipo", type=str, location="args", help="Filtrar por tipo")
list_parser.add_argument("ambiente", type=str, location="args", help="Filtrar por ambiente")
list_parser.add_argument("criticidade", type=str, location="args", help="Filtrar por criticidade")
list_parser.add_argument("owner", type=str, location="args", help="Filtrar por owner (email)")
list_parser.add_argument(
    "include_deleted",
    type=lambda x: x.lower() in ("1", "true", "yes"),
    default=False,
    location="args",
    help="Incluir soft-deleted",
)
list_parser.add_argument("limit", type=int, default=100, location="args")
list_parser.add_argument("offset", type=int, default=0, location="args")


# ── Session / service factory ─────────────────────────────────────────────────


@contextmanager
def _svc():
    """Context manager that yields an AtivoService with an active DB session."""
    from itgov.db.session import get_session

    with get_session() as session:
        yield AtivoService(session)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _serialize(ativo) -> dict:
    """Convert AtivoDB ORM instance to API response dict."""
    return {
        "id": str(ativo.id),
        "nome": ativo.nome,
        "tipo": ativo.tipo,
        "ambiente": ativo.ambiente,
        "criticidade": ativo.criticidade,
        "owner": ativo.owner,
        "tags": ativo.tags or [],
        "metadata": ativo.metadata_ or {},
        "contrato_id": str(ativo.contrato_id) if ativo.contrato_id else None,
        "created_at": ativo.created_at.isoformat() if ativo.created_at else None,
        "updated_at": ativo.updated_at.isoformat() if ativo.updated_at else None,
    }


def _invalid_uuid(ativo_id: str) -> bool:
    try:
        UUID(ativo_id)
        return False
    except ValueError:
        return True


# ── Resources ─────────────────────────────────────────────────────────────────


@ns.route("")
class AtivoCollection(Resource):
    """Coleção de ativos — listar e criar."""

    @ns.doc("list_ativos")
    @ns.expect(list_parser)
    @ns.marshal_with(ativo_list_model)
    def get(self):
        """Lista ativos com filtros opcionais e paginação."""
        args = list_parser.parse_args()
        with _svc() as svc:
            items = svc.list(
                tipo=args.get("tipo"),
                ambiente=args.get("ambiente"),
                criticidade=args.get("criticidade"),
                owner=args.get("owner"),
                include_deleted=args.get("include_deleted", False),
                limit=args.get("limit", 100),
                offset=args.get("offset", 0),
            )
            total = svc.count(
                tipo=args.get("tipo"),
                include_deleted=args.get("include_deleted", False),
            )
        return {
            "items": [_serialize(a) for a in items],
            "total": total,
            "limit": args.get("limit", 100),
            "offset": args.get("offset", 0),
        }

    @ns.doc("create_ativo")
    @ns.expect(ativo_create_model)
    @ns.response(201, "Criado", ativo_model)
    @ns.response(400, "Payload inválido", error_model)
    @ns.response(409, "Ativo duplicado", error_model)
    def post(self):
        """Cria um novo ativo (valida via Pydantic AtivoCreate)."""
        payload = ns.payload or {}
        try:
            with _svc() as svc:
                ativo = svc.create(payload)
            return _serialize(ativo), 201
        except AtivoDuplicateError as exc:
            return {"error": str(exc), "code": "DUPLICATE"}, 409
        except ValueError as exc:
            return {"error": str(exc), "code": "INVALID_PAYLOAD"}, 400


@ns.route("/stats")
class AtivoStatsResource(Resource):
    """Estatísticas agregadas — para widgets de dashboard."""

    @ns.doc("get_ativo_stats")
    @ns.marshal_with(stats_model)
    def get(self):
        """Retorna contagens agregadas do inventário (ativas, por tipo, etc.)."""
        with _svc() as svc:
            stats = svc.get_stats()
        return {
            "total": stats.total,
            "por_tipo": stats.por_tipo,
            "por_criticidade": stats.por_criticidade,
            "por_ambiente": stats.por_ambiente,
            "sem_owner": stats.sem_owner,
            "sem_contrato": stats.sem_contrato,
        }


@ns.route("/<string:ativo_id>")
@ns.param("ativo_id", "UUID do ativo")
class AtivoResource(Resource):
    """Recurso individual — buscar, atualizar, deletar."""

    @ns.doc("get_ativo")
    @ns.response(200, "Sucesso", ativo_model)
    @ns.response(400, "UUID inválido", error_model)
    @ns.response(404, "Não encontrado", error_model)
    def get(self, ativo_id: str):
        """Busca ativo por UUID."""
        if _invalid_uuid(ativo_id):
            return {"error": "UUID inválido", "code": "INVALID_UUID"}, 400
        try:
            with _svc() as svc:
                ativo = svc.get(UUID(ativo_id))
            return _serialize(ativo), 200
        except AtivoNotFoundError as exc:
            return {"error": str(exc), "code": "NOT_FOUND"}, 404

    @ns.doc("update_ativo")
    @ns.expect(ativo_update_model)
    @ns.response(200, "Atualizado", ativo_model)
    @ns.response(400, "Payload inválido", error_model)
    @ns.response(404, "Não encontrado", error_model)
    @ns.response(409, "Conflito de unique constraint", error_model)
    def patch(self, ativo_id: str):
        """Atualização parcial do ativo (PATCH)."""
        if _invalid_uuid(ativo_id):
            return {"error": "UUID inválido", "code": "INVALID_UUID"}, 400
        payload = ns.payload or {}
        try:
            with _svc() as svc:
                ativo = svc.update(UUID(ativo_id), payload)
            return _serialize(ativo), 200
        except AtivoNotFoundError as exc:
            return {"error": str(exc), "code": "NOT_FOUND"}, 404
        except AtivoDuplicateError as exc:
            return {"error": str(exc), "code": "CONFLICT"}, 409
        except ValueError as exc:
            return {"error": str(exc), "code": "INVALID_PAYLOAD"}, 400

    @ns.doc("delete_ativo")
    @ns.response(204, "Deletado")
    @ns.response(400, "UUID inválido", error_model)
    @ns.response(404, "Não encontrado", error_model)
    def delete(self, ativo_id: str):
        """Soft delete por padrão. Use ?hard=true para remoção física."""
        if _invalid_uuid(ativo_id):
            return {"error": "UUID inválido", "code": "INVALID_UUID"}, 400
        hard = request.args.get("hard", "false").lower() == "true"
        try:
            with _svc() as svc:
                svc.delete(UUID(ativo_id), soft=not hard)
            return "", 204
        except AtivoNotFoundError as exc:
            return {"error": str(exc), "code": "NOT_FOUND"}, 404
