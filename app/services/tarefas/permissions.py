"""Matriz de permissões do módulo Tarefas por perfil do dashboard (ADR 0007).

Fonte única: views, API e templates perguntam aqui em vez de repetir
listas de perfis. Não há associação por workspace; todo usuário ativo
vê todos os workspaces.
"""

from __future__ import annotations

from enum import StrEnum


class Acao(StrEnum):
    """Ações controladas no módulo."""

    VER = "ver"
    GERENCIAR_WORKSPACE = "gerenciar_workspace"
    EDITAR_CARD = "editar_card"
    COMENTAR = "comentar"
    MODERAR_COMENTARIO = "moderar_comentario"


_TODOS = ("admin", "gestor", "operador", "visualizador")

_MATRIZ: dict[Acao, tuple[str, ...]] = {
    Acao.VER: _TODOS,
    Acao.GERENCIAR_WORKSPACE: ("admin", "gestor"),
    Acao.EDITAR_CARD: ("admin", "gestor", "operador"),
    Acao.COMENTAR: ("admin", "gestor", "operador"),
    Acao.MODERAR_COMENTARIO: ("admin",),
}


def perfis(acao: Acao) -> tuple[str, ...]:
    """Perfis autorizados para a ação, no formato aceito por ``require_role``.

    Args:
        acao: Ação a verificar.

    Returns:
        Tupla com os nomes dos perfis autorizados.
    """
    return _MATRIZ[acao]


def pode(role: str | None, acao: Acao) -> bool:
    """Informa se o perfil pode executar a ação.

    Args:
        role: Perfil do usuário (``None`` para anônimo).
        acao: Ação a verificar.

    Returns:
        ``True`` se o perfil está autorizado.
    """
    return role in _MATRIZ[acao]
