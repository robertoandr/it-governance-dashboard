from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GitHubSettings(BaseSettings):
    """Configuration for the GitHub API integration.

    Reads from environment variables with the ``GITHUB_`` prefix
    or from a ``.env`` file in the project root.
    """

    model_config = SettingsConfigDict(
        env_prefix="GITHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    token: SecretStr = Field(..., description="Fine-grained PAT or classic token")
    repos: list[str] = Field(
        default_factory=list,
        description="Comma-separated list of owner/repo pairs",
    )
    api_base_url: str = "https://api.github.com"
    timeout: float = 30.0
    max_retries: int = 3
    per_page: int = 100

    @field_validator("repos", mode="before")
    @classmethod
    def parse_repos(cls, value: str | list[str]) -> list[str]:
        """Accept either a CSV string or a list."""
        if isinstance(value, str):
            return [r.strip() for r in value.split(",") if r.strip()]
        return value

    @field_validator("repos")
    @classmethod
    def validate_repo_format(cls, repos: list[str]) -> list[str]:
        """Ensure every entry is in ``owner/repo`` format."""
        for repo in repos:
            if "/" not in repo or repo.count("/") != 1:
                raise ValueError(f"Invalid repo format '{repo}'. Expected 'owner/repo'.")
        return repos
