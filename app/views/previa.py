"""Prévia dos modelos de redesign com dados ao vivo (só admin).

``/gov/previa`` lista os modelos; ``/gov/previa/<a|b|c>`` mostra um deles com
os dados de agora (``app.services.previa_modelos``). As telas atuais não mudam:
é para testar o modelo antes de escolher qual vira a Visão Geral.
"""

from __future__ import annotations

from flask import Blueprint, abort, render_template
from flask_login import login_required

from app.auth.rbac import require_role
from app.services import previa_modelos

bp = Blueprint("previa", __name__)


@bp.route("/previa")
@login_required
@require_role("admin")
def previa_indice() -> str:
    """Lista os modelos em teste."""
    return render_template("previa/indice.html", modelos=previa_modelos.MODELOS, p=None, modelo="")


@bp.route("/previa/<modelo>")
@login_required
@require_role("admin")
def previa_modelo(modelo: str) -> str:
    """Mostra um modelo com os dados de agora."""
    if modelo not in previa_modelos.MODELOS:
        abort(404)
    return render_template(
        f"previa/modelo_{modelo}.html",
        p=previa_modelos.obter(),
        modelo=modelo,
        modelos=previa_modelos.MODELOS,
    )
