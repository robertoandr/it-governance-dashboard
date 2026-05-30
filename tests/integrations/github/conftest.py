from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from pydantic import SecretStr

from app.integrations.github.auth import PATAuth
from app.integrations.github.config import GitHubSettings

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> object:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture
def mock_settings() -> GitHubSettings:
    return GitHubSettings(
        token=SecretStr("ghp_test_token_not_real"),
        repos=["owner/repo"],
        api_base_url="https://api.github.com",
        timeout=5.0,
        max_retries=2,
        per_page=100,
    )


@pytest.fixture
def mock_auth() -> PATAuth:
    return PATAuth(SecretStr("ghp_test_token_not_real"))


@pytest.fixture
def prs_fixture() -> list[dict]:
    return load_fixture("prs.json")  # type: ignore[return-value]


@pytest.fixture
def workflows_fixture() -> dict:
    return load_fixture("workflows.json")  # type: ignore[return-value]


@pytest.fixture
def codeql_fixture() -> list[dict]:
    return load_fixture("codeql.json")  # type: ignore[return-value]


@pytest.fixture
def dependabot_fixture() -> list[dict]:
    return load_fixture("dependabot.json")  # type: ignore[return-value]


@pytest.fixture
def mock_respx():
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        yield mock
