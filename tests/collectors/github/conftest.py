"""Fixtures compartilhadas para testes do coletor GitHub."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.collectors.github.config import GitHubCollectorSettings
from app.storage.influxdb.client import GovernanceInfluxClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_GITHUB_API = "https://api.github.com"
_REPO = "test-owner/test-repo"
_REPO_NAME = "test-repo"

# lookback_hours alto para que todos os fixtures passem no filtro de data,
# independente de quando o teste roda. Fixtures têm timestamps fixos de 2026-05.
_LOOKBACK_HOURS_TEST = 9999

# Satisfaz settings sem bater na API real
os.environ.setdefault("GITHUB_TOKEN", "ghp_test_not_real")
os.environ.setdefault("GITHUB_REPOS", '["test-owner/test-repo"]')


def load_fixture(name: str) -> object:
    return json.loads((FIXTURES_DIR / name).read_text())


def rl_headers(remaining: int = 4999) -> dict[str, str]:
    """Rate-limit headers padrão para mocks."""
    return {
        "X-RateLimit-Limit": "5000",
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": "9999999999",
        "X-RateLimit-Used": str(5000 - remaining),
    }


@pytest.fixture
def settings() -> GitHubCollectorSettings:
    return GitHubCollectorSettings(
        token=SecretStr("ghp_test_not_real"),
        repos=[_REPO],
        lookback_hours=_LOOKBACK_HOURS_TEST,
    )


@pytest.fixture
def mock_influx() -> AsyncMock:
    mock = AsyncMock(spec=GovernanceInfluxClient)
    mock.write_points = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def pulls_fixture() -> list[dict]:
    return load_fixture("pulls_list.json")  # type: ignore[return-value]


@pytest.fixture
def pull_detail_fixture() -> dict:
    return load_fixture("pull_detail.json")  # type: ignore[return-value]


@pytest.fixture
def workflow_runs_fixture() -> dict:
    return load_fixture("workflow_runs.json")  # type: ignore[return-value]
