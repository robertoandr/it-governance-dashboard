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
    def parse_repos(cls, value: str | list[str]) -> list[str]:
        """Aceita repos como JSON array ou CSV — substitui validator do pai.

        Mesmo nome que o validator pai (`parse_repos`) garante override via
        MRO em vez de acumulação: apenas UM validator roda, com ordem
        previsível. Inclui a lógica CSV do pai mais JSON array.

        Exemplos válidos no .env:
            GITHUB_REPOS=["owner/repo1","owner/repo2"]   # JSON array
            GITHUB_REPOS=owner/repo1,owner/repo2          # CSV (pai)
        """
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                parsed: list[str] = json.loads(stripped)
                return parsed
            return [r.strip() for r in stripped.split(",") if r.strip()]
        return list(value)
