from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class PullRequest(_FrozenModel):
    """A GitHub pull request."""

    number: int
    title: str
    state: str
    draft: bool = False
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    review_comments: int = 0
    created_at: datetime
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    user_login: str = Field("", alias="user")
    repo: str = ""

    @model_validator(mode="before")
    @classmethod
    def extract_user_login(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("user"), dict):
            data = dict(data)
            data["user"] = data["user"].get("login", "")
        return data


class WorkflowRun(_FrozenModel):
    """A GitHub Actions workflow run."""

    id: int
    name: str
    status: str
    conclusion: str | None = None
    created_at: datetime
    updated_at: datetime
    run_started_at: datetime | None = None
    head_branch: str | None = None
    event: str
    actor_login: str = Field("", alias="actor")
    repo: str = ""

    @model_validator(mode="before")
    @classmethod
    def extract_actor_login(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("actor"), dict):
            data = dict(data)
            data["actor"] = data["actor"].get("login", "")
        return data


class _RuleInfo(_FrozenModel):
    id: str = ""
    severity: str = ""
    security_severity_level: str | None = None


class CodeQLAlert(_FrozenModel):
    """A GitHub Code Scanning (CodeQL) alert."""

    number: int
    state: str
    rule_id: str = ""
    rule_severity: str = ""
    rule_security_severity_level: str | None = None
    created_at: datetime
    dismissed_at: datetime | None = None
    tool_name: str = ""
    repo: str = ""

    @model_validator(mode="before")
    @classmethod
    def flatten_rule_and_tool(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        rule = data.pop("rule", {}) or {}
        data.setdefault("rule_id", rule.get("id", ""))
        data.setdefault("rule_severity", rule.get("severity", ""))
        data.setdefault("rule_security_severity_level", rule.get("security_severity_level"))
        tool = data.pop("tool", {}) or {}
        data.setdefault("tool_name", tool.get("name", ""))
        return data


class DependabotAlert(_FrozenModel):
    """A GitHub Dependabot vulnerability alert."""

    number: int
    state: str
    dependency_name: str = ""
    dependency_ecosystem: str = ""
    severity: str = ""
    cvss_score: float | None = None
    created_at: datetime
    fixed_at: datetime | None = None
    repo: str = ""

    @model_validator(mode="before")
    @classmethod
    def flatten_dependency_and_advisory(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        dep = (data.pop("dependency", {}) or {}).get("package", {}) or {}
        data.setdefault("dependency_name", dep.get("name", ""))
        data.setdefault("dependency_ecosystem", dep.get("ecosystem", ""))
        advisory = data.pop("security_advisory", {}) or {}
        severity_info = advisory.get("severity", "")
        data.setdefault("severity", severity_info)
        cvss = advisory.get("cvss", {}) or {}
        data.setdefault("cvss_score", cvss.get("score"))
        return data


class RateLimitInfo(_FrozenModel):
    """Parsed X-RateLimit-* response headers."""

    limit: int
    remaining: int
    reset: datetime
    used: int = 0
