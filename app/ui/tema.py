"""Cores de status do tema Gadens Institucional.

Os pilares tinham cada um uma cor fixa (azul, verde, vermelho, laranja, roxo)
que não dizia nada. No tema Institucional a cor de um score é a do seu
status — a mesma regra do painel de TV: acima de 85 operacional, de 60 a 85
atenção, abaixo de 60 crítico. Sem dado ainda, cinza.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

COR_OK = "#1E7F55"
COR_ATENCAO = "#A85F00"
COR_CRITICO = "#B42318"
COR_SEM_DADO = "#9EA3AA"


def cor_score(score: float | None) -> str:
    """Cor hex do status de um score de 0 a 100.

    Args:
        score: O score, ou ``None`` quando ainda não há dado.

    Returns:
        A cor do status (operacional, atenção, crítico ou sem dado).
    """
    if score is None:
        return COR_SEM_DADO
    if score > 85:
        return COR_OK
    if score >= 60:
        return COR_ATENCAO
    return COR_CRITICO


def cor_pilar(pilar: Mapping[str, Any] | Any) -> str:
    """Filtro Jinja ``cor_pilar``: cor do status de um pilar.

    Aceita o dicionário do pilar (``score`` e ``data_source``) ou um objeto com
    esses atributos. Pilar "coming_soon" não tem score real: fica cinza.

    Args:
        pilar: O pilar como vem de ``governance.pillars`` ou da página do pilar.

    Returns:
        A cor hex para bolinha, barra e destaques do pilar.
    """

    def campo(nome: str) -> Any:
        if isinstance(pilar, Mapping):
            return pilar.get(nome)
        return getattr(pilar, nome, None)

    if campo("data_source") == "coming_soon":
        return COR_SEM_DADO
    score = campo("score")
    return cor_score(float(score)) if score is not None else COR_SEM_DADO
