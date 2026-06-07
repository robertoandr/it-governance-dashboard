"""Tests for collector/utils/team_mapper.py."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# Patch config (needed because team_mapper imports structlog which may pull config).
# Manually save/restore only "config" so collector modules stay in sys.modules.
_mock_config = types.ModuleType("config")

_config_before = sys.modules.get("config")
sys.modules["config"] = _mock_config
from collector.utils.team_mapper import _load_mapping, get_team  # noqa: E402

if _config_before is None:
    sys.modules.pop("config", None)
else:
    sys.modules["config"] = _config_before

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def teams_yaml(tmp_path: Path) -> Path:
    """Write a minimal teams.yaml to a temp file."""
    content = """
version: "1.0"
teams:
  backend:
    - alice
    - bob
  frontend:
    - carol
"""
    p = tmp_path / "teams.yaml"
    p.write_text(content)
    # Clear LRU cache so each test gets a fresh load
    _load_mapping.cache_clear()
    return p


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the LRU cache before each test."""
    _load_mapping.cache_clear()
    yield
    _load_mapping.cache_clear()


# ── get_team ─────────────────────────────────────────────────────────────────


def test_get_team_known_login(teams_yaml: Path) -> None:
    assert get_team("alice", teams_path=teams_yaml) == "backend"
    assert get_team("carol", teams_path=teams_yaml) == "frontend"


def test_get_team_unknown_login(teams_yaml: Path) -> None:
    assert get_team("unknown_user", teams_path=teams_yaml) == "unknown"


def test_get_team_empty_login(teams_yaml: Path) -> None:
    assert get_team("", teams_path=teams_yaml) == "unknown"


def test_get_team_missing_yaml(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    assert get_team("alice", teams_path=missing) == "unknown"


def test_get_team_multiple_logins_same_team(teams_yaml: Path) -> None:
    assert get_team("alice", teams_path=teams_yaml) == get_team("bob", teams_path=teams_yaml) == "backend"


# ── _load_mapping ─────────────────────────────────────────────────────────────


def test_load_mapping_inverts_correctly(teams_yaml: Path) -> None:
    mapping = _load_mapping(str(teams_yaml))
    assert mapping == {"alice": "backend", "bob": "backend", "carol": "frontend"}


def test_load_mapping_cached(teams_yaml: Path) -> None:
    mapping1 = _load_mapping(str(teams_yaml))
    mapping2 = _load_mapping(str(teams_yaml))
    assert mapping1 is mapping2  # same object from cache


def test_load_mapping_invalid_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(": invalid: {yaml")
    _load_mapping.cache_clear()
    result = _load_mapping(str(bad))
    assert result == {}
