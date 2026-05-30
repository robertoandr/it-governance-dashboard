"""Configuração do coletor GitHub."""

from __future__ import annotations

import json

from pydantic import field_validator

from app.integrations.github.config import GitHubSettings


class GitHubCollectorSettings(GitHubSettings):
    """Estende GitHubSettings com campos específicos do coletor de governança.

    Herda token, repos, api_base_url, timeout, max_retries, per_page e
    model_config (env_prefix=GITHUB_, env_file=.env) do pai sem redeclarar.
    Adiciona org (opcional) e lookback_hours para filtro temporal.
    """

    org: str = ""
    lookback_hours: int = 24

    @field_validator("repos", mode="before")
    @classmethod
    def _parse_repos_json_or_csv(cls, value: object) -> object:
        """Aceita repos como JSON array ou CSV.

        Exemplos válidos no .env:
            GITHUB_REPOS=["owner/repo1","owner/repo2"]
            GITHUB_REPOS=owner/repo1,owner/repo2
        """
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
        return value  # parent validator trata CSV
