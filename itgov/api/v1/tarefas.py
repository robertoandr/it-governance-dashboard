"""Flask-RESTX namespace do módulo Tarefas (Kanban com documentação no card).

Convenções da ADR-003: envelope ``{items, total, limit, offset}`` nas
listas, erros ``{error, code}`` e soft delete. Permissões pela matriz de
``app.services.tarefas.permissions`` (ADR 0007 do gerenciador-tarefas).

Escritas exigem o header ``X-Requested-With: XMLHttpRequest`` (e JSON nos
métodos com corpo): formulários de outros sites não conseguem enviá-lo,
o que bloqueia CSRF sem depender só do SameSite do cookie de sessão.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

import structlog
from flask import request
from flask_login import current_user
from flask_restx import Namespace, Resource, fields
from pydantic import ValidationError

from app.auth.rbac import require_role
from app.models.tarefas import Workspace, em_utc
from app.services.tarefas import board_service as board_svc
from app.services.tarefas import workspace_service as ws_svc
from app.services.tarefas.permissions import Acao, perfis

log = structlog.get_logger(__name__)

ns = Namespace("tarefas", description="Tarefas — workspaces, boards e cards")

# ── Swagger models ────────────────────────────────────────────────────────────

board_resumo_model = ns.model("TarefasBoardResumo", {"id": fields.Integer, "name": fields.String})

workspace_model = ns.model(
    "TarefasWorkspace",
    {
        "id": fields.Integer,
        "name": fields.String,
        "boards": fields.List(fields.Nested(board_resumo_model)),
        "created_at": fields.String(description="ISO 8601 UTC"),
        "updated_at": fields.String(description="ISO 8601 UTC"),
    },
)

workspace_in_model = ns.model(
    "TarefasWorkspaceIn",
    {"name": fields.String(required=True, min_length=3, max_length=60, example="Infraestrutura")},
)

workspace_list_model = ns.model(
    "TarefasWorkspaceList",
    {
        "items": fields.List(fields.Nested(workspace_model)),
        "total": fields.Integer,
        "limit": fields.Integer,
        "offset": fields.Integer,
    },
)

error_model = ns.model("TarefasError", {"error": fields.String, "code": fields.String})

responsavel_model = ns.model("TarefasResponsavel", {"id": fields.Integer, "name": fields.String})

card_model = ns.model(
    "TarefasCard",
    {
        "id": fields.Integer,
        "title": fields.String,
        "status": fields.String(description="backlog | todo | doing | done"),
        "position": fields.Integer,
        "version": fields.Integer,
        "assignee": fields.Nested(responsavel_model, allow_null=True),
        "has_document": fields.Boolean,
        "comment_count": fields.Integer,
    },
)

card_in_model = ns.model(
    "TarefasCardIn",
    {
        "title": fields.String(required=True, max_length=200, example="Trocar switch do CPD"),
        "status": fields.String(description="backlog | todo | doing | done (padrão: backlog)"),
    },
)

mover_in_model = ns.model(
    "TarefasMoverIn",
    {
        "status": fields.String(required=True, description="Coluna de destino"),
        "before_id": fields.Integer(description="Card que fica logo acima (null = topo)"),
        "after_id": fields.Integer(description="Card que fica logo abaixo (null = fim)"),
        "version": fields.Integer(required=True, description="Versão do card que o cliente tem"),
    },
)

mover_out_model = ns.model(
    "TarefasMoverOut",
    {
        "id": fields.Integer,
        "status": fields.String,
        "position": fields.Integer,
        "version": fields.Integer,
        "board_revision": fields.Integer,
        "reload": fields.Boolean(description="true = coluna renumerada; o cliente deve recarregar o board"),
    },
)

list_parser = ns.parser()
list_parser.add_argument("limit", type=int, default=100, location="args")
list_parser.add_argument("offset", type=int, default=0, location="args")


# ── Helpers ───────────────────────────────────────────────────────────────────


def exige_ajax(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Recusa escritas sem ``X-Requested-With`` (e sem JSON, quando há corpo)."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return {"error": "Requisição recusada: header X-Requested-With ausente", "code": "FORBIDDEN"}, 403
        if request.method in ("POST", "PUT", "PATCH") and not request.is_json:
            return {"error": "Envie o corpo como application/json", "code": "INVALID_PAYLOAD"}, 400
        return fn(*args, **kwargs)

    return wrapper


def _erro_validacao(exc: ValidationError) -> tuple[dict[str, str], int]:
    primeiro = exc.errors()[0]
    campo = ".".join(str(p) for p in primeiro.get("loc", ())) or "payload"
    return {"error": f"{campo}: {primeiro.get('msg', 'inválido')}", "code": "INVALID_PAYLOAD"}, 400


def _iso(valor: Any) -> str | None:
    utc = em_utc(valor)
    return utc.isoformat() if utc else None


def serialize_workspace(ws: Workspace) -> dict[str, Any]:
    """Converte um Workspace no formato da API."""
    return {
        "id": ws.id,
        "name": ws.name,
        "boards": [{"id": b.id, "name": b.name} for b in ws.boards_ativos],
        "created_at": _iso(ws.created_at),
        "updated_at": _iso(ws.updated_at),
    }


# ── Resources ─────────────────────────────────────────────────────────────────


@ns.route("/workspaces")
class WorkspaceCollection(Resource):
    """Workspaces — listar e criar."""

    @ns.doc("tarefas_list_workspaces")
    @ns.expect(list_parser)
    @ns.marshal_with(workspace_list_model)
    @require_role(*perfis(Acao.VER))
    def get(self) -> dict[str, Any]:
        """Lista os workspaces ativos (todos os perfis veem todos)."""
        args = list_parser.parse_args()
        limit, offset = ws_svc.normalizar_paginacao(args["limit"], args["offset"])
        itens, total = ws_svc.listar(limit=limit, offset=offset)
        return {
            "items": [serialize_workspace(w) for w in itens],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @ns.doc("tarefas_create_workspace")
    @ns.expect(workspace_in_model)
    @ns.response(201, "Criado", workspace_model)
    @ns.response(400, "Payload inválido", error_model)
    @ns.response(409, "Nome duplicado", error_model)
    @require_role(*perfis(Acao.GERENCIAR_WORKSPACE))
    @exige_ajax
    def post(self) -> tuple[dict[str, Any], int]:
        """Cria um workspace com o board padrão."""
        try:
            dados = ws_svc.WorkspaceIn.model_validate(request.get_json(silent=True) or {})
            ws = ws_svc.criar(dados, user_id=current_user.id)
        except ValidationError as exc:
            return _erro_validacao(exc)
        except ws_svc.WorkspaceDuplicadoError as exc:
            return {"error": str(exc), "code": "DUPLICATE"}, 409
        return serialize_workspace(ws), 201


@ns.route("/workspaces/<int:workspace_id>")
@ns.param("workspace_id", "ID do workspace")
class WorkspaceResource(Resource):
    """Workspace individual — renomear e excluir."""

    @ns.doc("tarefas_rename_workspace")
    @ns.expect(workspace_in_model)
    @ns.response(200, "Atualizado", workspace_model)
    @ns.response(400, "Payload inválido", error_model)
    @ns.response(404, "Não encontrado", error_model)
    @ns.response(409, "Nome duplicado", error_model)
    @require_role(*perfis(Acao.GERENCIAR_WORKSPACE))
    @exige_ajax
    def patch(self, workspace_id: int) -> tuple[dict[str, Any], int]:
        """Renomeia o workspace."""
        try:
            dados = ws_svc.WorkspaceIn.model_validate(request.get_json(silent=True) or {})
            ws = ws_svc.renomear(workspace_id, dados, user_id=current_user.id)
        except ValidationError as exc:
            return _erro_validacao(exc)
        except ws_svc.WorkspaceNaoEncontradoError as exc:
            return {"error": str(exc), "code": "NOT_FOUND"}, 404
        except ws_svc.WorkspaceDuplicadoError as exc:
            return {"error": str(exc), "code": "CONFLICT"}, 409
        return serialize_workspace(ws), 200

    @ns.doc("tarefas_delete_workspace")
    @ns.response(204, "Excluído")
    @ns.response(404, "Não encontrado", error_model)
    @require_role(*perfis(Acao.GERENCIAR_WORKSPACE))
    @exige_ajax
    def delete(self, workspace_id: int) -> tuple[Any, int]:
        """Exclui (soft delete) o workspace e os boards dele."""
        try:
            ws_svc.excluir(workspace_id, user_id=current_user.id)
        except ws_svc.WorkspaceNaoEncontradoError as exc:
            return {"error": str(exc), "code": "NOT_FOUND"}, 404
        return "", 204


@ns.route("/boards/<int:board_id>/cards")
@ns.param("board_id", "ID do board")
class BoardCards(Resource):
    """Cards do board — listar por coluna e criar."""

    @ns.doc("tarefas_board_cards")
    @ns.response(200, "Sucesso")
    @ns.response(404, "Não encontrado", error_model)
    @require_role(*perfis(Acao.VER))
    def get(self, board_id: int) -> tuple[dict[str, Any], int]:
        """Board com os cards agrupados por coluna, na ordem de exibição."""
        try:
            board, colunas = board_svc.listar_cards(board_id)
        except board_svc.BoardNaoEncontradoError as exc:
            return {"error": str(exc), "code": "NOT_FOUND"}, 404
        return {
            "board": {
                "id": board.id,
                "name": board.name,
                "revision": board.revision,
                "workspace": {"id": board.workspace.id, "name": board.workspace.name},
            },
            "columns": colunas,
        }, 200

    @ns.doc("tarefas_create_card")
    @ns.expect(card_in_model)
    @ns.response(201, "Criado", card_model)
    @ns.response(400, "Payload inválido", error_model)
    @ns.response(404, "Board não encontrado", error_model)
    @require_role(*perfis(Acao.EDITAR_CARD))
    @exige_ajax
    def post(self, board_id: int) -> tuple[dict[str, Any], int]:
        """Cria um card no topo da coluna."""
        try:
            dados = board_svc.CardIn.model_validate(request.get_json(silent=True) or {})
            card = board_svc.criar_card(board_id, dados, user_id=current_user.id)
        except ValidationError as exc:
            return _erro_validacao(exc)
        except board_svc.BoardNaoEncontradoError as exc:
            return {"error": str(exc), "code": "NOT_FOUND"}, 404
        return board_svc.card_para_dict(card), 201


@ns.route("/boards/<int:board_id>/revision")
@ns.param("board_id", "ID do board")
class BoardRevision(Resource):
    """Revisão do board — consultada a cada 15 s pelo front (ADR 0008)."""

    @ns.doc("tarefas_board_revision")
    @ns.response(404, "Não encontrado", error_model)
    @require_role(*perfis(Acao.VER))
    def get(self, board_id: int) -> tuple[dict[str, Any], int]:
        """Número que muda a cada escrita em qualquer card do board."""
        try:
            return {"revision": board_svc.revisao(board_id)}, 200
        except board_svc.BoardNaoEncontradoError as exc:
            return {"error": str(exc), "code": "NOT_FOUND"}, 404


@ns.route("/cards/<int:card_id>/move")
@ns.param("card_id", "ID do card")
class CardMove(Resource):
    """Mover card entre colunas e posições."""

    @ns.doc("tarefas_move_card")
    @ns.expect(mover_in_model)
    @ns.response(200, "Movido", mover_out_model)
    @ns.response(400, "Payload inválido", error_model)
    @ns.response(404, "Não encontrado", error_model)
    @ns.response(409, "Board mudou; recarregar", error_model)
    @require_role(*perfis(Acao.EDITAR_CARD))
    @exige_ajax
    def patch(self, card_id: int) -> tuple[dict[str, Any], int]:
        """Move o card; 409 se a versão ou os vizinhos não batem com o banco."""
        try:
            dados = board_svc.MoverIn.model_validate(request.get_json(silent=True) or {})
            movimento = board_svc.mover_card(card_id, dados, user_id=current_user.id)
        except ValidationError as exc:
            return _erro_validacao(exc)
        except board_svc.CardNaoEncontradoError as exc:
            return {"error": str(exc), "code": "NOT_FOUND"}, 404
        except board_svc.ConflitoError as exc:
            return {"error": f"{exc} Recarregue o board.", "code": "CONFLICT"}, 409
        card = movimento.card
        return {
            "id": card.id,
            "status": card.status,
            "position": card.position,
            "version": card.version,
            "board_revision": movimento.revisao,
            "reload": movimento.recarregar,
        }, 200
