"""Role-based access control decorator."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from flask import abort, request
from flask_login import current_user

_LEITURA = frozenset({"GET", "HEAD", "OPTIONS"})


def require_role(*allowed: str, pagina: str | None = None, nivel: str | None = None) -> Callable:
    """Restrict view to users with one of the allowed roles.

    Com ``pagina``, o ajuste de permissão do usuário naquela página (tela
    Usuários → Permissões) vence o perfil: pode liberar quem o perfil não
    deixava ou barrar quem deixava. Sem ajuste, valem os perfis ``allowed``.

    Returns 401 if not authenticated, 403 if authenticated but not allowed.

    Args:
        *allowed: Perfis aceitos quando não há ajuste para a página.
        pagina: Chave da página em ``app.permissoes.PAGINAS``.
        nivel: ``ver`` ou ``alterar``; sem ele, GET/HEAD pedem ``ver`` e os
            demais métodos, ``alterar``.
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if pagina is not None:
                precisa = nivel or ("ver" if request.method in _LEITURA else "alterar")
                if not current_user.pode(pagina, precisa, allowed):
                    abort(403)
            elif current_user.role not in allowed:
                abort(403)
            return fn(*args, **kwargs)

        return wrapper

    return decorator
