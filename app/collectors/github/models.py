"""Modelos Pydantic para dados crus da API GitHub (coletor de governança).

Distintos dos modelos em app.integrations.github.models:
- GitHubPR adiciona base_ref (alvo do PR) e merged (bool inferido)
- GitHubWorkflowRun adiciona run_attempt
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class GitHubUser(BaseModel):
    """Autor de um PR ou ator de um workflow."""

    login: str
    id: int = 0


class GitHubPR(BaseModel):
    """Pull Request do GitHub.

    populate_by_name=True: aceita tanto o alias "base" (da API) quanto
    "base_ref" (em construção direta nos testes).

    ⚠️ Cardinality: tag 'author' (user.login) pode explodir em orgs grandes.
    Mitigação futura: agrupar por equipe via CODEOWNERS.
    """

    model_config = ConfigDict(populate_by_name=True)

    number: int
    title: str
    state: Literal["open", "closed"]
    merged: bool = False
    user: GitHubUser
    base_ref: str = Field(default="unknown", alias="base")
    created_at: datetime
    merged_at: datetime | None = None
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    review_comments: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_state(self) -> Literal["open", "closed", "merged"]:
        """Estado efetivo do PR: "merged" se merged=True, senão state."""
        if self.merged:
            return "merged"
        return self.state

    @model_validator(mode="before")
    @classmethod
    def _infer_merged_from_merged_at(cls, data: Any) -> Any:
        """Infere merged=True quando merged_at está presente mas merged não está.

        A API de lista (/pulls) não retorna o campo merged — apenas a API de
        detalhe (/pulls/{number}) o inclui. Inferimos pelo merged_at.
        """
        if isinstance(data, dict) and "merged" not in data:
            data = dict(data)
            data["merged"] = data.get("merged_at") is not None
        return data

    @field_validator("base_ref", mode="before")
    @classmethod
    def _extract_base_ref(cls, v: Any) -> str:
        """Extrai base.ref do objeto aninhado retornado pela API."""
        if isinstance(v, dict):
            return v.get("ref", "unknown")
        return str(v) if v is not None else "unknown"

    @field_validator("user", mode="before")
    @classmethod
    def _coerce_user(cls, v: Any) -> Any:
        """Aceita user como dict ou string (login direto)."""
        if isinstance(v, str):
            return {"login": v, "id": 0}
        return v


class GitHubWorkflowRun(BaseModel):
    """GitHub Actions workflow run.

    ⚠️ Cardinality:
    - workflow_name: BAIXO (< 20 por repo) — seguro como tag.
    - branch: MÉDIO — feature branches podem explodir.
      Considerar agrupar por padrão (feature/*, hotfix/*) em versão futura.
    """

    id: int
    name: str
    head_branch: str = Field(default="unknown")
    event: str
    status: str
    conclusion: str | None = None
    run_started_at: datetime
    updated_at: datetime
    run_attempt: int = Field(default=1)
