"""Painel de TV do NOC: modelos /gov/v1 … /gov/v6 com dados ao vivo.

Cada versão é um layout diferente sobre os MESMOS dados
(``app.services.painel_tv.get_painel``); a tela busca ``/gov/painel/dados`` a
cada 60 s. ``/gov/tv`` é a tela da TV: o modelo escolhido (``MODELO_TV``),
sem o menu de troca de modelos.

Acesso pelo login normal da dashboard: admin sempre vê; os demais só se
liberados em Usuários → coluna "Painel TV" (``User.ver_painel_tv``). A TV
entra com um usuário próprio marcando "lembrar-me".
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, NamedTuple

from flask import Blueprint, Response, abort, jsonify, render_template
from flask_login import current_user, login_required

from app.services.painel_tv import get_painel

bp = Blueprint("painel_tv", __name__)


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

# Modelo que a TV do NOC mostra em /gov/tv.
MODELO_TV = 6


def requer_painel_tv(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Só deixa passar quem está liberado para o painel da TV (403 para os demais)."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not current_user.pode_ver_painel_tv:
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


@bp.route("/tv")
@bp.route("/tv/")
@login_required
@requer_painel_tv
def tv() -> str:
    """Tela da TV do NOC: o modelo ``MODELO_TV`` com os dados atuais, sem o menu de modelos."""
    return render_template(f"tv/v{MODELO_TV}.html", painel=get_painel(), modelos=[], versao=MODELO_TV)


@bp.route("/v<int:versao>")
@bp.route("/v<int:versao>/")
@login_required
@requer_painel_tv
def tela(versao: int) -> str:
    """Renderiza o modelo ``versao`` do painel de TV já com os dados atuais."""
    if versao not in {m.versao for m in MODELOS}:
        abort(404)
    return render_template(f"tv/v{versao}.html", painel=get_painel(), modelos=MODELOS, versao=versao)


@bp.route("/painel/dados")
@login_required
@requer_painel_tv
def dados() -> Response:
    """Dados do painel em JSON (atualização da tela a cada 60 s)."""
    resp = jsonify(get_painel())
    resp.headers["Cache-Control"] = "no-store"
    return resp
