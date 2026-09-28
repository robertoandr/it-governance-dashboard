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
from app.models.tarefas import Workspace, iso_utc
from app.services.tarefas import board_service as board_svc
from app.services.tarefas import card_service as card_svc
from app.services.tarefas import comment_service as com_svc
from app.services.tarefas import document_service as doc_svc
from app.services.tarefas import markdown
from app.services.tarefas import workspace_service as ws_svc
from app.services.tarefas.permissions import Acao, perfis, pode

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

card_edit_model = ns.model(
    "TarefasCardEditIn",
    {
        "version": fields.Integer(required=True),
        "title": fields.String(max_length=200),
        "assignee_id": fields.Integer(description="null remove o responsável"),
    },
)

documento_in_model = ns.model(
    "TarefasDocumentoIn",
    {
        "content_md": fields.String(required=True, description="Markdown (até 200 KB)"),
        "version": fields.Integer(required=True, description="Versão que o cliente tinha (0 = documento novo)"),
    },
)

comentario_in_model = ns.model(
    "TarefasComentarioIn", {"body_md": fields.String(required=True, max_length=10000, description="Markdown")}
)

preview_in_model = ns.model("TarefasPreviewIn", {"content_md": fields.String(required=True)})

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


def serialize_workspace(ws: Workspace) -> dict[str, Any]:
    """Converte um Workspace no formato da API."""
    return {
        "id": ws.id,
        "name": ws.name,
        "boards": [{"id": b.id, "name": b.name} for b in ws.boards_ativos],
        "created_at": iso_utc(ws.created_at),
        "updated_at": iso_utc(ws.updated_at),
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


# ── Card aberto no painel (Sprint 3) ─────────────────────────────────────────

_CARD_404 = (board_svc.CardNaoEncontradoError,)


def _nao_encontrado(exc: Exception) -> tuple[dict[str, str], int]:
    return {"error": str(exc), "code": "NOT_FOUND"}, 404


@ns.route("/users")
class Usuarios(Resource):
    """Usuários ativos, para escolher o responsável do card."""

    @ns.doc("tarefas_users")
    @require_role(*perfis(Acao.VER))
    def get(self) -> tuple[dict[str, Any], int]:
        """Id e nome dos usuários ativos."""
        itens = card_svc.usuarios_ativos()
        return {"items": itens, "total": len(itens), "limit": len(itens), "offset": 0}, 200


@ns.route("/cards/<int:card_id>")
@ns.param("card_id", "ID do card")
class CardDetalhe(Resource):
    """Card — detalhes e edição de título/responsável."""

    @ns.doc("tarefas_card_detail")
    @ns.response(404, "Não encontrado", error_model)
    @require_role(*perfis(Acao.VER))
    def get(self, card_id: int) -> tuple[dict[str, Any], int]:
        """Card com board, workspace e histórico recente."""
        try:
            return card_svc.detalhar(card_id), 200
        except _CARD_404 as exc:
            return _nao_encontrado(exc)

    @ns.doc("tarefas_card_edit")
    @ns.expect(card_edit_model)
    @ns.response(400, "Payload inválido", error_model)
    @ns.response(404, "Não encontrado", error_model)
    @ns.response(409, "Versão desatualizada", error_model)
    @require_role(*perfis(Acao.EDITAR_CARD))
    @exige_ajax
    def patch(self, card_id: int) -> tuple[dict[str, Any], int]:
        """Altera título e/ou responsável (envie a versão do card)."""
        try:
            dados = card_svc.CardEditIn.model_validate(request.get_json(silent=True) or {})
            card_svc.editar(card_id, dados, user_id=current_user.id)
            return card_svc.detalhar(card_id), 200
        except ValidationError as exc:
            return _erro_validacao(exc)
        except card_svc.ResponsavelInvalidoError as exc:
            return {"error": str(exc), "code": "INVALID_PAYLOAD"}, 400
        except _CARD_404 as exc:
            return _nao_encontrado(exc)
        except board_svc.ConflitoError as exc:
            return {"error": f"{exc} Recarregue o card.", "code": "CONFLICT"}, 409


@ns.route("/cards/<int:card_id>/document")
@ns.param("card_id", "ID do card")
class CardDocumento(Resource):
    """Documento Markdown do card."""

    @ns.doc("tarefas_card_document")
    @ns.response(404, "Não encontrado", error_model)
    @require_role(*perfis(Acao.VER))
    def get(self, card_id: int) -> tuple[dict[str, Any], int]:
        """Documento atual (versão 0 se ainda não existe), com o HTML renderizado."""
        try:
            return doc_svc.obter(card_id), 200
        except _CARD_404 as exc:
            return _nao_encontrado(exc)

    @ns.doc("tarefas_card_document_save")
    @ns.expect(documento_in_model)
    @ns.response(400, "Payload inválido", error_model)
    @ns.response(404, "Não encontrado", error_model)
    @ns.response(409, "Outra pessoa salvou antes; o corpo traz a versão atual")
    @require_role(*perfis(Acao.EDITAR_CARD))
    @exige_ajax
    def put(self, card_id: int) -> tuple[dict[str, Any], int]:
        """Salva o documento se a versão enviada ainda for a atual."""
        try:
            dados = doc_svc.DocumentoIn.model_validate(request.get_json(silent=True) or {})
            return doc_svc.salvar(card_id, dados, user_id=current_user.id), 200
        except ValidationError as exc:
            return _erro_validacao(exc)
        except _CARD_404 as exc:
            return _nao_encontrado(exc)
        except doc_svc.DocumentoConflitoError as exc:
            return {"error": str(exc), "code": "CONFLICT", "current": exc.atual}, 409


@ns.route("/markdown/preview")
class MarkdownPreview(Resource):
    """Pré-visualização do Markdown, renderizada e sanitizada no servidor."""

    @ns.doc("tarefas_markdown_preview")
    @ns.expect(preview_in_model)
    @require_role(*perfis(Acao.EDITAR_CARD))
    @exige_ajax
    def post(self) -> tuple[dict[str, Any], int]:
        """HTML seguro do Markdown enviado (mesmo limite de tamanho do documento)."""
        corpo = request.get_json(silent=True) or {}
        try:
            dados = doc_svc.DocumentoIn.model_validate({"content_md": corpo.get("content_md", ""), "version": 0})
        except ValidationError as exc:
            return _erro_validacao(exc)
        return {"html": markdown.renderizar(dados.content_md)}, 200


def _com_permissoes(comentario: dict[str, Any]) -> dict[str, Any]:
    autor = comentario["author"]["id"] == current_user.id
    return {
        **comentario,
        "can_edit": autor,
        "can_delete": autor or pode(current_user.role, Acao.MODERAR_COMENTARIO),
    }


@ns.route("/cards/<int:card_id>/comments")
@ns.param("card_id", "ID do card")
class CardComentarios(Resource):
    """Comentários do card."""

    @ns.doc("tarefas_card_comments")
    @ns.response(404, "Não encontrado", error_model)
    @require_role(*perfis(Acao.VER))
    def get(self, card_id: int) -> tuple[dict[str, Any], int]:
        """Comentários do mais antigo para o mais novo, com o que o usuário pode fazer em cada um."""
        try:
            itens = [_com_permissoes(c) for c in com_svc.listar(card_id)]
        except _CARD_404 as exc:
            return _nao_encontrado(exc)
        return {"items": itens, "total": len(itens), "limit": len(itens), "offset": 0}, 200

    @ns.doc("tarefas_card_comment_create")
    @ns.expect(comentario_in_model)
    @ns.response(201, "Criado")
    @ns.response(400, "Payload inválido", error_model)
    @ns.response(404, "Não encontrado", error_model)
    @require_role(*perfis(Acao.COMENTAR))
    @exige_ajax
    def post(self, card_id: int) -> tuple[dict[str, Any], int]:
        """Cria um comentário."""
        try:
            dados = com_svc.ComentarioIn.model_validate(request.get_json(silent=True) or {})
            return _com_permissoes(com_svc.criar(card_id, dados, user_id=current_user.id)), 201
        except ValidationError as exc:
            return _erro_validacao(exc)
        except _CARD_404 as exc:
            return _nao_encontrado(exc)


@ns.route("/comments/<int:comentario_id>")
@ns.param("comentario_id", "ID do comentário")
class Comentario(Resource):
    """Comentário individual — editar (autor) e excluir (autor ou admin)."""

    @ns.doc("tarefas_comment_edit")
    @ns.expect(comentario_in_model)
    @ns.response(403, "Não é o autor", error_model)
    @ns.response(404, "Não encontrado", error_model)
    @require_role(*perfis(Acao.COMENTAR))
    @exige_ajax
    def patch(self, comentario_id: int) -> tuple[dict[str, Any], int]:
        """Edita o próprio comentário."""
        try:
            dados = com_svc.ComentarioIn.model_validate(request.get_json(silent=True) or {})
            return _com_permissoes(com_svc.editar(comentario_id, dados, user_id=current_user.id)), 200
        except ValidationError as exc:
            return _erro_validacao(exc)
        except com_svc.ComentarioNaoEncontradoError as exc:
            return _nao_encontrado(exc)
        except com_svc.SemPermissaoError as exc:
            return {"error": str(exc), "code": "FORBIDDEN"}, 403

    @ns.doc("tarefas_comment_delete")
    @ns.response(204, "Excluído")
    @ns.response(403, "Sem permissão", error_model)
    @ns.response(404, "Não encontrado", error_model)
    @require_role(*perfis(Acao.COMENTAR))
    @exige_ajax
    def delete(self, comentario_id: int) -> tuple[Any, int]:
        """Exclui o comentário (autor, ou admin moderando)."""
        try:
            com_svc.excluir(
                comentario_id,
                user_id=current_user.id,
                pode_moderar=pode(current_user.role, Acao.MODERAR_COMENTARIO),
            )
        except com_svc.ComentarioNaoEncontradoError as exc:
            return _nao_encontrado(exc)
        except com_svc.SemPermissaoError as exc:
            return {"error": str(exc), "code": "FORBIDDEN"}, 403
        return "", 204
