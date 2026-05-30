"""Fixtures compartilhadas para testes do coletor Zabbix."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.collectors.zabbix.config import ZabbixSettings
from app.storage.influxdb.client import GovernanceInfluxClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_ZABBIX_URL = "http://zabbix.test/api_jsonrpc.php"


def load_fixture(name: str) -> object:
    """Carrega fixture JSON do diretório de fixtures."""
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture
def zabbix_settings() -> ZabbixSettings:
    return ZabbixSettings(
        url=_ZABBIX_URL,  # type: ignore[arg-type]
        token=SecretStr("test-token-not-real"),
        timeout_seconds=5,
        page_size=100,
        lookback_hours=1,
        max_retries=2,
    )


@pytest.fixture
def mock_influx() -> AsyncMock:
    """Mock do GovernanceInfluxClient com write_points assíncrono."""
    mock = AsyncMock(spec=GovernanceInfluxClient)
    mock.write_points = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def problems_payload() -> list[dict]:
    return load_fixture("problem_get.json")  # type: ignore[return-value]


@pytest.fixture
def hosts_payload() -> list[dict]:
    return load_fixture("host_get.json")  # type: ignore[return-value]


@pytest.fixture
def triggers_payload() -> list[dict]:
    return load_fixture("trigger_get.json")  # type: ignore[return-value]


def make_rpc_response(result: object, req_id: int = 1) -> dict:
    """Monta envelope JSON-RPC de sucesso."""
    return {"jsonrpc": "2.0", "result": result, "id": req_id}


def make_rpc_error(code: int, message: str, data: str = "", req_id: int = 1) -> dict:
    """Monta envelope JSON-RPC de erro."""
    return {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message, "data": data},
        "id": req_id,
    }
