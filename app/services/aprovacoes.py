"""Aprovação do super admin para alterações de cadastro e configuração.

Rotas marcadas com ``@requer_aprovacao`` não aplicam o POST de quem não é
super admin: o pedido (endpoint, argumentos da URL, formulário, arquivos ou
corpo JSON) vira uma ``Solicitacao`` pendente. Ao aprovar, a mesma rota roda
de novo com os dados originais e em nome de quem pediu — as validações e
permissões da rota continuam valendo; se ela recusar, a solicitação fica
como "falhou" com a mensagem.

Senhas nunca são guardadas: ao aprovar a criação de usuário, uma senha nova
é gerada e mostrada só ao super admin; credenciais SNMP de gravador ficam de
fora e, na aprovação, a rota mantém as que já estão no Zabbix.

Sem nenhum super admin ativo, o decorador não intercepta nada (o sistema
não pode travar esperando quem não existe).

Operações do dia a dia (triggers, tarefas) não passam por aqui.
"""

from __future__ import annotations

import base64
import io
import json
import os
import secrets
import string
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any
from urllib.parse import parse_qsl, urlparse

import structlog
from flask import current_app, flash, g, get_flashed_messages, jsonify, redirect, request, url_for
from flask_login import current_user
from werkzeug.datastructures import FileStorage, MultiDict
from werkzeug.exceptions import HTTPException

from app.extensions import db
from app.models.aprovacao import APROVADA, FALHOU, PENDENTE, REJEITADA, Solicitacao
from app.models.user import User

log = structlog.get_logger(__name__)

SUPER_ADMIN_PADRAO = "roberto@grupogadens.com.br"

# Nunca guardados na fila
CAMPOS_SENSIVEIS = frozenset(
    {"password", "confirm_password", "new_password", "snmp_community", "snmp_auth_senha", "snmp_priv_senha"}
)

# Endpoints cuja senha é gerada na aprovação (campo → campos a preencher)
_SENHA_GERADA: dict[str, tuple[str, ...]] = {"users.create_user": ("password", "confirm_password")}

Resumo = str | Callable[..., str]


def super_admin_email() -> str:
    """E-mail da conta super admin (``SUPER_ADMIN_EMAIL``; vazio desliga)."""
    return os.getenv("SUPER_ADMIN_EMAIL", SUPER_ADMIN_PADRAO).strip().lower()


def aprovacao_ativa() -> bool:
    """Há um super admin ativo para aprovar?"""
    return User.query.filter_by(super_admin=True, is_active=True).first() is not None


def eh_super_admin(user: Any) -> bool:
    """O usuário é o super admin (e está autenticado)?"""
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "super_admin", False))


def contar_pendentes() -> int:
    """Solicitações esperando decisão."""
    return Solicitacao.query.filter_by(status=PENDENTE).count()


def mensagem_enviada(sol: Solicitacao) -> str:
    """Aviso para quem pediu: a alteração ainda não vale.

    Args:
        sol: Solicitação recém-registrada.

    Returns:
        Texto mostrado na tela (e no JSON das rotas que respondem JSON).
    """
    return (
        f"Sua alteração foi enviada para aprovação (pedido #{sol.id}) e só passa a valer depois que "
        "o super admin aprovar. Você será avisado aqui quando ele decidir."
    )


def minhas_aprovacoes(user: Any, limite: int = 5) -> dict[str, Any]:
    """Pedidos do usuário para os avisos da tela.

    Args:
        user: Usuário autenticado.
        limite: Máximo de decisões não vistas devolvidas.

    Returns:
        ``{"pendentes": int, "decididas": [Solicitacao, ...]}`` — decisões
        ainda não vistas, mais recentes primeiro.
    """
    minhas = Solicitacao.query.filter_by(solicitante_id=user.id)
    pendentes = minhas.filter_by(status=PENDENTE).count()
    decididas = (
        minhas.filter(Solicitacao.status != PENDENTE, Solicitacao.ciente.is_(False))
        .order_by(Solicitacao.decidido_em.desc())
        .limit(limite)
        .all()
    )
    return {"pendentes": pendentes, "decididas": decididas}


def marcar_ciente(user: Any) -> list[int]:
    """Marca como vistas as decisões dos pedidos do usuário.

    Args:
        user: Quem pediu.

    Returns:
        Ids que estavam sem ver (para destacar na lista).
    """
    novas = (
        Solicitacao.query.filter_by(solicitante_id=user.id, ciente=False).filter(Solicitacao.status != PENDENTE).all()
    )
    for sol in novas:
        sol.ciente = True
    if novas:
        db.session.commit()
    return [sol.id for sol in novas]


def requer_aprovacao(resumo: Resumo) -> Callable:
    """Faz o POST de quem não é super admin virar solicitação pendente.

    Deve ficar abaixo de ``@require_role``: a permissão de pedir é conferida
    antes. GET passa direto (formulários continuam abrindo).

    Args:
        resumo: Texto da solicitação, ou função que recebe os argumentos da
            rota e lê ``request`` para montar o texto.
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if request.method != "POST" or g.get("_aprovando") or eh_super_admin(current_user) or not aprovacao_ativa():
                return fn(*args, **kwargs)
            texto = resumo(**kwargs) if callable(resumo) else resumo
            sol = registrar(texto, kwargs)
            msg = mensagem_enviada(sol)
            if request.is_json:
                return jsonify({"ok": True, "pendente": True, "mensagem": msg, "solicitacao": sol.id}), 202
            flash(msg, "aprovacao")
            return redirect(voltar())

        return wrapper

    return decorator


def registrar(resumo: str, view_args: dict[str, Any]) -> Solicitacao:
    """Guarda o pedido atual como solicitação pendente.

    Args:
        resumo: Texto exibido na fila.
        view_args: Argumentos da URL da rota.

    Returns:
        A solicitação criada.
    """
    form = [(k, v) for k, v in request.form.items(multi=True) if k not in CAMPOS_SENSIVEIS]
    arquivos = []
    for campo, arq in request.files.items(multi=True):
        conteudo = arq.read()
        if conteudo:
            arquivos.append(
                {
                    "campo": campo,
                    "nome": arq.filename or "",
                    "tipo": arq.mimetype or "application/octet-stream",
                    "b64": base64.b64encode(conteudo).decode("ascii"),
                }
            )
    sol = Solicitacao(
        resumo=resumo[:300],
        endpoint=request.endpoint or "",
        caminho=request.path,
        view_args_json=json.dumps({k: str(v) for k, v in view_args.items()}),
        form_json=json.dumps(form, ensure_ascii=False),
        arquivos_json=json.dumps(arquivos),
        corpo_json=json.dumps(request.get_json(silent=True), ensure_ascii=False) if request.is_json else None,
        solicitante_id=current_user.id,
        solicitante_nome=current_user.name,
    )
    db.session.add(sol)
    db.session.commit()
    log.info("aprovacoes.registrada", id=sol.id, endpoint=sol.endpoint, solicitante=current_user.email)
    return sol


def voltar() -> str:
    """Página de onde o pedido veio (mesmo site), senão a fila de aprovações.

    A URL é remontada com ``url_for`` a partir da rota casada, nunca devolvida
    crua: só destinos que existem no app saem daqui (evita open redirect).
    """
    ref = urlparse(request.referrer or "")
    if ref.netloc == request.host:
        caminho = ref.path
        raiz = request.script_root
        if raiz and caminho.startswith(raiz):
            caminho = caminho[len(raiz) :]
        try:
            endpoint, valores = current_app.url_map.bind(request.host).match(caminho, method="GET")
        except HTTPException:
            return url_for("aprovacoes.lista")
        consulta = {k: v for k, v in parse_qsl(ref.query) if k not in valores}
        return url_for(endpoint, **valores, **consulta)
    return url_for("aprovacoes.lista")


def _senha_aleatoria() -> str:
    alfabeto = string.ascii_letters + string.digits + "!@#$"
    return "".join(secrets.choice(alfabeto) for _ in range(14))


def aprovar(sol: Solicitacao, aprovador: User) -> tuple[bool, str]:
    """Executa a solicitação em nome de quem pediu e registra o resultado.

    Args:
        sol: Solicitação pendente.
        aprovador: Super admin que aprovou.

    Returns:
        ``(ok, mensagem)`` — mensagem com o que a rota respondeu e, se houve,
        a senha gerada (que não fica gravada na solicitação).
    """
    if sol.status != PENDENTE:
        return False, "Esta solicitação já foi decidida."
    ok, mensagem, segredo = _executar(sol)
    sol.status = APROVADA if ok else FALHOU
    # O segredo (senha gerada) só volta para quem aprovou; nunca vai ao banco
    sol.resultado = mensagem
    sol.decidido_por = aprovador.name
    sol.decidido_em = datetime.now(UTC)
    sol.ciente = False
    db.session.commit()
    log.info("aprovacoes.aprovada", id=sol.id, ok=ok, aprovador=aprovador.email)
    return ok, f"{mensagem} {segredo}".strip()


def rejeitar(sol: Solicitacao, aprovador: User, motivo: str) -> None:
    """Recusa a solicitação sem aplicar nada.

    Args:
        sol: Solicitação pendente.
        aprovador: Super admin que recusou.
        motivo: Texto opcional para quem pediu.
    """
    sol.status = REJEITADA
    sol.resultado = motivo.strip()[:1000]
    sol.decidido_por = aprovador.name
    sol.decidido_em = datetime.now(UTC)
    sol.ciente = False
    db.session.commit()
    log.info("aprovacoes.rejeitada", id=sol.id, aprovador=aprovador.email)


def _executar(sol: Solicitacao) -> tuple[bool, str, str]:
    solicitante = db.session.get(User, sol.solicitante_id) if sol.solicitante_id else None
    if solicitante is None or not solicitante.is_active:
        return False, "Quem pediu não existe mais ou está desativado.", ""
    view = current_app.view_functions.get(sol.endpoint)
    if view is None:
        return False, f"A rota {sol.endpoint} não existe mais.", ""

    dados: MultiDict[str, Any] = MultiDict(sol.form)
    extra = ""
    if sol.endpoint in _SENHA_GERADA:
        senha = _senha_aleatoria()
        for campo in _SENHA_GERADA[sol.endpoint]:
            dados.setlist(campo, [senha])
        extra = f"Senha gerada (anote, não fica guardada): {senha}"
    for arq in sol.arquivos:
        dados.add(
            arq["campo"],
            FileStorage(io.BytesIO(base64.b64decode(arq["b64"])), filename=arq["nome"], content_type=arq["tipo"]),
        )

    contexto: dict[str, Any] = {"method": "POST"}
    if sol.corpo is not None:
        contexto["json"] = sol.corpo
    else:
        contexto["data"] = dados

    # A requisição repetida divide o "g" do contexto de app com a do aprovador:
    # troca o usuário só durante a execução e devolve no fim.
    tinha_usuario = "_login_user" in g
    anterior = g.get("_login_user")
    try:
        with current_app.test_request_context(sol.caminho, **contexto):
            g._login_user = solicitante
            g._aprovando = True
            try:
                resp = current_app.make_response(view(**_tipar_args(sol)))
            except HTTPException as exc:
                db.session.rollback()
                return False, f"A rota recusou ({exc.code}): {exc.description}", ""
            mensagens = get_flashed_messages(with_categories=True)
    finally:
        if tinha_usuario:
            g._login_user = anterior
        else:
            g.pop("_login_user", None)
        g.pop("_aprovando", None)

    erros = [m for cat, m in mensagens if cat == "error"]
    outras = [m for cat, m in mensagens if cat != "error"]
    if resp.is_json:
        corpo = resp.get_json(silent=True) or {}
        if resp.status_code < 300 and corpo.get("ok", True):
            return True, "Aplicada.", extra
        return False, str(corpo.get("error") or f"HTTP {resp.status_code}"), ""
    if erros:
        return False, " ".join(erros), ""
    # Formulário re-renderizado (200) é a rota pedindo correção
    if not 300 <= resp.status_code < 400:
        return False, f"A rota não confirmou a alteração (HTTP {resp.status_code}).", ""
    # Mensagens com senha (reset gerado pela rota) vão só para quem aprovou
    publicas = [m for m in outras if "senha" not in m.lower()]
    segredos = [m for m in outras if "senha" in m.lower()]
    return True, " ".join(publicas) or "Aplicada.", " ".join([*segredos, extra]).strip()


def _tipar_args(sol: Solicitacao) -> dict[str, Any]:
    """Reconverte os argumentos da URL pelos conversores da própria rota."""
    adapter = current_app.url_map.bind("localhost")
    _endpoint, args = adapter.match(sol.caminho, method="POST")
    return args


STATUS_ROTULO = {PENDENTE: "Pendente", APROVADA: "Aprovada", REJEITADA: "Rejeitada", FALHOU: "Falhou"}
