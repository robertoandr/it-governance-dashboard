"""Painel de TV do NOC: modelos /gov/v1 … /gov/v6 com dados ao vivo.

Cada versão é um layout diferente sobre os MESMOS dados
(``app.services.painel_tv.get_painel``); a tela busca ``/gov/painel/dados`` a
cada 60 s. As versões pedem login normal.

A TV do NOC abre ``/gov/tv/<token>`` sem login: mostra o modelo escolhido
(``MODELO_TV``) e busca os dados em ``/gov/tv/<token>/dados``. Os tokens ficam
em ``PAINEL_TV_TOKENS`` (separados por vírgula, um por aparelho) e o nginx só
libera ``/gov/tv/`` para a rede interna.
"""

from __future__ import annotations

import hmac
import os
from typing import NamedTuple

import structlog
from flask import Blueprint, Response, abort, jsonify, render_template, request, url_for
from flask_login import login_required

from app.auth.rbac import require_role
from app.services.painel_tv import get_painel

bp = Blueprint("painel_tv", __name__)
log = structlog.get_logger(__name__)

# Token curto demais é ignorado: protege contra PAINEL_TV_TOKENS=1 por engano.
TOKEN_MINIMO = 32


class Modelo(NamedTuple):
    """Uma versão do painel."""

    versao: int
    nome: str


MODELOS = [
    Modelo(1, "Sala de Controle"),
    Modelo(2, "Mural Executivo"),
    Modelo(3, "Três Camadas"),
    Modelo(4, "NOC Bento"),
    Modelo(5, "KIT ExStart"),
    Modelo(6, "Sala de Controle Bento"),
]

# Modelo que a TV do NOC mostra em /gov/tv/<token>.
MODELO_TV = 6


@bp.route("/v<int:versao>")
@bp.route("/v<int:versao>/")
@login_required
@require_role("admin", "gestor", "visualizador")
def tela(versao: int) -> str:
    """Renderiza o modelo ``versao`` do painel de TV já com os dados atuais."""
    if versao not in {m.versao for m in MODELOS}:
        abort(404)
    return render_template(
        f"tv/v{versao}.html",
        painel=get_painel(),
        modelos=MODELOS,
        versao=versao,
        url_dados=url_for("painel_tv.dados"),
    )


@bp.route("/painel/dados")
@login_required
@require_role("admin", "gestor", "visualizador")
def dados() -> Response:
    """Dados do painel em JSON (atualização da tela a cada 60 s)."""
    resp = jsonify(get_painel())
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _token_valido(token: str) -> bool:
    """Confere ``token`` contra ``PAINEL_TV_TOKENS`` em tempo constante."""
    validos = [t.strip() for t in os.getenv("PAINEL_TV_TOKENS", "").split(",") if len(t.strip()) >= TOKEN_MINIMO]
    return any(hmac.compare_digest(token.encode(), t.encode()) for t in validos)


def _exigir_token(token: str) -> None:
    if not _token_valido(token):
        log.warning("painel_tv.token_recusado", ip=request.remote_addr)
        abort(404)


@bp.route("/tv/<token>")
@bp.route("/tv/<token>/")
def tv(token: str) -> str:
    """Tela da TV do NOC, sem login: o modelo ``MODELO_TV`` com os dados atuais."""
    _exigir_token(token)
    return render_template(
        f"tv/v{MODELO_TV}.html",
        painel=get_painel(),
        modelos=[],
        versao=MODELO_TV,
        url_dados=url_for("painel_tv.tv_dados", token=token),
    )


@bp.route("/tv/<token>/dados")
def tv_dados(token: str) -> Response:
    """Dados do painel para a TV (mesmo JSON de ``/gov/painel/dados``)."""
    _exigir_token(token)
    resp = jsonify(get_painel())
    resp.headers["Cache-Control"] = "no-store"
    return resp
