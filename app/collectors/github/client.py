"""Cliente GitHub para o coletor de governança.

Subclasse de GitHubClient (app.integrations.github) com:
- Métodos filtrados por `since` (datetime)
- Retorna dicts crus (não modelos Pydantic) para os models do coletor
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.integrations.github.auth import PATAuth
from app.integrations.github.client import GitHubClient

from .config import GitHubCollectorSettings

log = structlog.get_logger(__name__)


def _parse_dt(s: str) -> datetime:
    """Converte string ISO8601 (com Z ou +00:00) em datetime aware."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class GitHubCollectorClient(GitHubClient):
    """Extensão de GitHubClient com métodos filtrados por data.

    Reutiliza paginação, rate-limit handling e retry do cliente base.
    Retorna dicts crus para que os modelos do coletor façam a validação.

    Args:
        settings: Configuração do coletor (inclui lookback_hours).
    """

    def __init__(self, settings: GitHubCollectorSettings) -> None:
        auth = PATAuth(settings.token)
        super().__init__(settings, auth)

    async def list_pulls_since(self, repo: str, since: datetime) -> list[dict]:
        """Lista PRs atualizados após `since`, ordenados por updated desc.

        Interrompe a paginação quando encontra um item anterior a `since`
        (early-exit eficiente para repos com muitos PRs antigos).

        Args:
            repo: Repositório no formato owner/repo.
            since: Apenas PRs com updated_at >= since são retornados.

        Returns:
            Lista de dicts crus da API GitHub.
        """
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)

        params = {
            "state": "all",
            "per_page": self._settings.per_page,  # type: ignore[attr-defined]
            "sort": "updated",
            "direction": "desc",
        }
        results: list[dict] = []
        async for page in self._paginate(f"/repos/{repo}/pulls", params):
            for item in page:
                updated_str = item.get("updated_at", "")
                if updated_str and _parse_dt(updated_str) < since:
                    log.debug("github.pulls_cutoff_reached", repo=repo, updated_at=updated_str)
                    return results
                results.append(item)
        return results

    async def list_workflow_runs_since(self, repo: str, since: datetime) -> list[dict]:
        """Lista workflow runs iniciados após `since`.

        Args:
            repo: Repositório no formato owner/repo.
            since: Apenas runs com run_started_at >= since são retornados.

        Returns:
            Lista de dicts crus da API GitHub (desembrulhados de workflow_runs).
        """
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)

        params = {"per_page": self._settings.per_page}  # type: ignore[attr-defined]
        results: list[dict] = []
        async for page in self._paginate(f"/repos/{repo}/actions/runs", params):
            items: list[dict] = page if isinstance(page, list) else page.get("workflow_runs", [])
            for item in items:
                started_str = item.get("run_started_at") or item.get("created_at", "")
                if started_str and _parse_dt(started_str) < since:
                    return results
                results.append(item)
        return results
