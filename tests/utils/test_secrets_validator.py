"""Testes do secrets_validator — fail-fast em tokens placeholder."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.utils.secrets_validator import (
    PLACEHOLDER_MARKERS,
    PlaceholderSecretError,
    validate_github_token,
)


def _tok(value: str) -> SecretStr:
    return SecretStr(value)


def _valid_classic() -> SecretStr:
    return _tok("ghp_" + "A" * 40)


def _valid_fine_grained() -> SecretStr:
    return _tok("github_pat_" + "B" * 80)


# ─────────────────────────────────────────────────────────────────────
# Guard 1: placeholder markers
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("marker", PLACEHOLDER_MARKERS)
def test_rejects_placeholder_marker(marker: str) -> None:
    with pytest.raises(PlaceholderSecretError, match="placeholder"):
        validate_github_token(_tok(f"ghp_{marker}" + "a" * 40))


def test_rejects_lowercase_placeholder() -> None:
    with pytest.raises(PlaceholderSecretError):
        validate_github_token(_tok("ghp_your_token_here_" + "a" * 40))


# ─────────────────────────────────────────────────────────────────────
# Guard 2: minimum length
# ─────────────────────────────────────────────────────────────────────


def test_rejects_short_token() -> None:
    with pytest.raises(PlaceholderSecretError, match="chars"):
        validate_github_token(_tok("ghp_short"))


def test_rejects_empty_token() -> None:
    with pytest.raises(PlaceholderSecretError, match="chars"):
        validate_github_token(_tok(""))


def test_rejects_exactly_39_chars() -> None:
    with pytest.raises(PlaceholderSecretError, match="chars"):
        validate_github_token(_tok("ghp_" + "a" * 35))  # 4 + 35 = 39


def test_accepts_exactly_40_chars() -> None:
    validate_github_token(_tok("ghp_" + "a" * 36))  # 4 + 36 = 40


# ─────────────────────────────────────────────────────────────────────
# Guard 3: known prefix (warning only)
# ─────────────────────────────────────────────────────────────────────


def test_accepts_classic_token() -> None:
    validate_github_token(_valid_classic())


def test_accepts_fine_grained_token() -> None:
    validate_github_token(_valid_fine_grained())


def test_accepts_other_github_prefixes() -> None:
    for prefix in ("gho_", "ghu_", "ghs_"):
        validate_github_token(_tok(prefix + "a" * 40))


def test_unknown_prefix_warns_but_does_not_raise() -> None:
    # Prefix not in known list → warning (guard 3) but no exception
    validate_github_token(_tok("tok_" + "a" * 40))


# ─────────────────────────────────────────────────────────────────────
# field_name in error messages
# ─────────────────────────────────────────────────────────────────────


def test_error_message_includes_field_name() -> None:
    with pytest.raises(PlaceholderSecretError, match="MY_CUSTOM_FIELD"):
        validate_github_token(_tok("short"), field_name="MY_CUSTOM_FIELD")


def test_default_field_name_is_github_token() -> None:
    with pytest.raises(PlaceholderSecretError, match="GITHUB_TOKEN"):
        validate_github_token(_tok("short"))
