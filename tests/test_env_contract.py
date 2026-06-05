"""Contract test: env vars consumed by config.py must be documented in .env.example.

Prevents future drift between what the code reads and what operators know to set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PY = ROOT / "config.py"
ENV_EXAMPLE = ROOT / ".env.example"

# Vars that config.py (root) actually reads for Microsoft Graph / Azure.
AZURE_VARS_REQUIRED = {
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
}

# Env var keys that were historical aliases — must NOT appear inside _env() / os.getenv() calls.
# These patterns match the string literal being passed as the env var key, not Python identifiers.
ZOMBIE_ENV_KEY_PATTERNS = [
    r'_env\(["\']MSAL_TENANT_ID["\']',
    r'_env\(["\']MSAL_CLIENT_ID["\']',
    r'_env\(["\']MSAL_CLIENT_SECRET["\']',
    r'getenv\(["\']MSAL_TENANT_ID["\']',
    r'getenv\(["\']MSAL_CLIENT_ID["\']',
    r'getenv\(["\']MSAL_CLIENT_SECRET["\']',
    r'_env\(["\']GRAPH_CLIENT_SECRET["\']',
    r'getenv\(["\']GRAPH_CLIENT_SECRET["\']',
]

# Patterns to match zombie keys in .env.example (plain KEY= lines, not Python code).
ZOMBIE_EXAMPLE_KEY_PATTERNS = [
    r"^MSAL_TENANT_ID=",
    r"^MSAL_CLIENT_ID=",
    r"^MSAL_CLIENT_SECRET=",
]


def _config_source() -> str:
    return CONFIG_PY.read_text(encoding="utf-8")


def _example_source() -> str:
    return ENV_EXAMPLE.read_text(encoding="utf-8")


def _env_example_keys() -> set[str]:
    """Return every KEY from lines of the form KEY= or KEY=value in .env.example."""
    keys: set[str] = set()
    for line in _example_source().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


class TestAzureVarsDocumented:
    """Each Azure var read by config.py must appear in .env.example."""

    @pytest.mark.parametrize("var", sorted(AZURE_VARS_REQUIRED))
    def test_var_in_env_example(self, var: str) -> None:
        assert var in _env_example_keys(), (
            f"{var} is read by config.py but missing from .env.example. Add it so operators know it must be set."
        )

    @pytest.mark.parametrize("var", sorted(AZURE_VARS_REQUIRED))
    def test_var_in_config_py(self, var: str) -> None:
        assert f'"{var}"' in _config_source() or f"'{var}'" in _config_source(), (
            f"{var} is declared in AZURE_VARS_REQUIRED but not found in config.py. "
            "Update this test to match the current code."
        )


class TestZombieVarsAbsent:
    """Historical alias env var keys must not appear inside _env() / getenv() calls."""

    @pytest.mark.parametrize("pattern", ZOMBIE_ENV_KEY_PATTERNS)
    def test_no_zombie_env_key_in_config_py(self, pattern: str) -> None:
        source = _config_source()
        hits = [line for line in source.splitlines() if not line.strip().startswith("#") and re.search(pattern, line)]
        assert not hits, f"Zombie env key pattern '{pattern}' found as active read in config.py:\n" + "\n".join(
            f"  {ln}" for ln in hits
        )

    @pytest.mark.parametrize("pattern", ZOMBIE_EXAMPLE_KEY_PATTERNS)
    def test_no_zombie_key_in_env_example(self, pattern: str) -> None:
        example = _example_source()
        hits = [line for line in example.splitlines() if not line.strip().startswith("#") and re.search(pattern, line)]
        assert not hits, f"Zombie key pattern '{pattern}' found as active entry in .env.example:\n" + "\n".join(
            f"  {ln}" for ln in hits
        )


class TestNoDualReadShim:
    """config.py must not contain the old GRAPH_CLIENT_SECRET env key read with AZURE fallback."""

    def test_no_fallback_shim(self) -> None:
        source = _config_source()
        # The shim was: _env("GRAPH_CLIENT_SECRET", default=os.getenv("AZURE_CLIENT_SECRET", ""))
        shim_lines = [
            line
            for line in source.splitlines()
            if not line.strip().startswith("#")
            and re.search(r'_env\(["\']GRAPH_CLIENT_SECRET["\']', line)
            and "AZURE_CLIENT_SECRET" in line
        ]
        assert not shim_lines, (
            "Dual-read shim (_env('GRAPH_CLIENT_SECRET', default=getenv('AZURE_CLIENT_SECRET')) "
            "detected in config.py. The canonical env key is AZURE_CLIENT_SECRET."
        )
