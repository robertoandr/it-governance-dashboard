"""Shared pytest fixtures — expanded in Phase 2 when create_app() exists."""

from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Inject minimum required env vars so Settings() can be instantiated in tests.

    Cache is cleared before and after to prevent lru_cache from bleeding state
    across tests (e.g. from module-level imports that run before monkeypatch).
    """
    from app.config import get_settings

    get_settings.cache_clear()

    monkeypatch.setenv("SECRET_KEY", "test-secret-key-minimum-32-chars-padding")
    monkeypatch.setenv("INFLUX_TOKEN", "test-token")
    monkeypatch.setenv("INFLUX_ORG", "test-org")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://itgov:Z66BroSKNH6LZcEFOghNRspeQH6oI2Bh@172.18.0.2:5432/itgov",
    )

    yield

    get_settings.cache_clear()
