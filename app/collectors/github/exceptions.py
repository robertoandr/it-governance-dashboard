"""Exceções do coletor GitHub.

Re-exporta as exceções da camada de integração para isolar os collectors
de `app.integrations` — se a estrutura interna mudar, apenas este arquivo
precisa ser atualizado.

Também define exceções próprias do coletor que não existem na integração base:
- `GitHubCollectorError`: todos os repos falharam em uma coleta.
"""

from app.integrations.github.exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubServerError,
    GitHubValidationError,
)


class GitHubCollectorError(RuntimeError):
    """Levantado quando TODOS os repos falham na mesma coleta.

    Distinto do partial failure (1 repo falha, outros continuam).
    Indica problema sistêmico: rede, token expirado, rate limit global.

    Args:
        repos: Lista de repos que falharam.
        errors: Mapeamento repo → mensagem de erro.
    """

    def __init__(self, repos: list[str], errors: dict[str, str]) -> None:
        self.repos = repos
        self.errors = errors
        summary = "; ".join(f"{r}: {e}" for r, e in errors.items())
        super().__init__(f"Todos os {len(repos)} repos falharam: {summary}")


__all__ = [
    "GitHubAPIError",
    "GitHubAuthError",
    "GitHubCollectorError",
    "GitHubNotFoundError",
    "GitHubRateLimitError",
    "GitHubServerError",
    "GitHubValidationError",
]
