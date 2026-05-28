"""Integration test configuration — sets env vars at session scope."""

import os

import pytest

# Set required env vars at import time so module-scoped fixtures see them
_TEST_ENV = {
    "SECRET_KEY": "test-secret-key-minimum-32-chars-padding",
    "DATABASE_URL": "postgresql+asyncpg://itgov:Z66BroSKNH6LZcEFOghNRspeQH6oI2Bh@172.18.0.2:5432/itgov",
    "INFLUX_TOKEN": "test-token",
    "INFLUX_ORG": "test-org",
}
for _k, _v in _TEST_ENV.items():
    os.environ.setdefault(_k, _v)


@pytest.fixture(scope="session", autouse=True)
def _set_integration_env() -> None:
    """Ensure all required env vars are present for the integration test session."""
    from app.config import get_settings

    get_settings.cache_clear()
    for k, v in _TEST_ENV.items():
        os.environ[k] = v
    get_settings.cache_clear()
