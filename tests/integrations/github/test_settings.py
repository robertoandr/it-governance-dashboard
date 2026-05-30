"""Testes de configuração e validators de GitHubCollectorSettings."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.collectors.github.config import GitHubCollectorSettings


def _make(repos: str | list) -> GitHubCollectorSettings:
    return GitHubCollectorSettings(token=SecretStr("ghp_" + "x" * 40), repos=repos)


class TestReposValidator:
    def test_accepts_json_array(self) -> None:
        s = _make('["owner/repo-a", "owner/repo-b"]')
        assert s.repos == ["owner/repo-a", "owner/repo-b"]

    def test_accepts_csv(self) -> None:
        s = _make("owner/repo-a,owner/repo-b")
        assert s.repos == ["owner/repo-a", "owner/repo-b"]

    def test_accepts_csv_with_spaces(self) -> None:
        s = _make("owner/repo-a , owner/repo-b")
        assert s.repos == ["owner/repo-a", "owner/repo-b"]

    def test_accepts_list_directly(self) -> None:
        s = _make(["owner/repo-a", "owner/repo-b"])
        assert s.repos == ["owner/repo-a", "owner/repo-b"]

    def test_accepts_single_json_entry(self) -> None:
        s = _make('["owner/only-repo"]')
        assert s.repos == ["owner/only-repo"]

    def test_rejects_invalid_format(self) -> None:
        with pytest.raises(ValidationError, match="owner/repo"):
            _make("not-a-valid-repo")

    def test_validator_is_deterministic(self) -> None:
        """Mesmo input 50× → sempre mesmo resultado (sem flap por ordem de MRO)."""
        results = {tuple(_make("owner/a,owner/b").repos) for _ in range(50)}
        assert len(results) == 1
        assert results.pop() == ("owner/a", "owner/b")

    def test_json_and_csv_yield_same_result(self) -> None:
        via_json = _make('["owner/a", "owner/b"]').repos
        via_csv = _make("owner/a,owner/b").repos
        assert via_json == via_csv


class TestCollectorDefaults:
    def test_lookback_hours_default(self) -> None:
        s = _make(["owner/repo"])
        assert s.lookback_hours == 24

    def test_org_accepts_empty_string(self) -> None:
        s = GitHubCollectorSettings(token=SecretStr("ghp_" + "x" * 40), repos=["owner/repo"], org="")
        assert s.org == ""

    def test_org_accepts_name(self) -> None:
        s = GitHubCollectorSettings(token=SecretStr("ghp_" + "x" * 40), repos=["owner/repo"], org="myorg")
        assert s.org == "myorg"
