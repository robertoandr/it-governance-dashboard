"""
conftest.py — Fixtures compartilhadas pytest

Estratégia:
- Isolamento total: cada teste tem seus próprios state/history files
- monkeypatch dos paths hardcoded no maintenance_service
- OPS_PIN definido só pro escopo dos testes
"""

import os
import sys
from pathlib import Path

import pytest

# 🛠️ Garantir que /opt/it-gov-dashboard está no PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ─────────────────────────────────────────────────────────────────────
# 🔐 OPS_PIN: setar ANTES de qualquer import dos módulos
# ─────────────────────────────────────────────────────────────────────
os.environ["OPS_PIN"] = "TEST_PIN_1234"

# ─────────────────────────────────────────────────────────────────────
# 🧪 Variáveis de infra MOCK para testes
# Satisfaz config.py:_env() que valida envs obrigatórias no import.
# NENHUM teste deve conectar de verdade — use mocks/monkeypatch.
# ─────────────────────────────────────────────────────────────────────
# InfluxDB (obrigatórias)
os.environ.setdefault("INFLUX_URL", "http://localhost:8086")
os.environ.setdefault("INFLUX_TOKEN", "test-token-not-real")
os.environ.setdefault("INFLUX_ORG", "test-org")
os.environ.setdefault("INFLUX_BUCKET", "test-bucket")
# Desabilitar integração InfluxDB nos testes — .env de prod tem INFLUX__ENABLED=true
# env vars têm prioridade sobre o arquivo .env no Pydantic Settings
os.environ["INFLUX__ENABLED"] = "false"

# Zabbix (obrigatórias)
os.environ.setdefault("ZABBIX_URL", "http://localhost/zabbix/api_jsonrpc.php")
os.environ.setdefault("ZABBIX_USER", "test-user")
os.environ.setdefault("ZABBIX_PASSWORD", "test-password-not-real")
os.environ.setdefault("ZABBIX_FRONT_URL", "http://localhost/zabbix")

# Zendesk (obrigatórias)
os.environ.setdefault("ZENDESK_SUBDOMAIN", "test-corp")
os.environ.setdefault("ZENDESK_EMAIL", "test@test.com")
os.environ.setdefault("ZENDESK_API_TOKEN", "test-zendesk-token-not-real")

# Flask (boa prática)
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-pytest-only")

# ─────────────────────────────────────────────────────────────────────
# 🔒 Pré-importar o config legado (./config.py) com todas as env vars
# já definidas acima. Isso garante que sys.modules["config"] aponte
# para o config correto ANTES que tests/collector/* injetem mocks via
# sys.modules.setdefault("config", ...). Sem isso, o mock (sem
# ZABBIX_URL / ZENDESK_MAX_PAGES) vaza e quebra collectors/zabbix.py
# e itgov/services/zendesk_service.py na coleta.
import config as _config_preload  # noqa: F401, E402

# Limpar cache de AppSettings para garantir que INFLUX__ENABLED=false seja lido
try:
    from app.config import get_settings

    get_settings.cache_clear()
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────
# 🧪 Variáveis de infra MOCK — evita falha no import de config.py
# Nenhum teste deve conectar de verdade ao InfluxDB/AD/M365.
# Estes valores existem APENAS para satisfazer _env() em config.py.
# ─────────────────────────────────────────────────────────────────────
os.environ.setdefault("INFLUX_URL", "http://localhost:8086")
os.environ.setdefault("INFLUX_TOKEN", "test-token-not-real")
os.environ.setdefault("INFLUX_ORG", "test-org")
os.environ.setdefault("INFLUX_BUCKET", "test-bucket")
os.environ.setdefault("SECRET_KEY", "test-secret-key-pytest-only")
os.environ.setdefault("FLASK_ENV", "testing")


@pytest.fixture
def ops_pin():
    """Retorna o PIN configurado pra testes."""
    return "TEST_PIN_1234"


# ─────────────────────────────────────────────────────────────────────
# 🧪 Isolamento de state: cada teste roda em diretório temporário
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """
    Redireciona _STATE_FILE e _HISTORY_FILE pro tmp_path do pytest.
    Garante que testes NUNCA poluem o estado real de produção.
    """
    from services import maintenance_service as svc

    state_file = tmp_path / "manual_maintenance.json"
    history_file = tmp_path / "maintenance_history.jsonl"

    monkeypatch.setattr(svc, "_STATE_FILE", state_file)
    monkeypatch.setattr(svc, "_HISTORY_FILE", history_file)

    yield {
        "state_file": state_file,
        "history_file": history_file,
        "svc": svc,
    }


# ─────────────────────────────────────────────────────────────────────
# 🎬 Helper: pré-popular state com hosts em manutenção
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def populated_state(isolated_state):
    """Estado com 3 hosts pré-marcados pra testes."""
    svc = isolated_state["svc"]
    svc.mark(
        hosts=["CAM-8A-35", "CAM-8A-36", "SRV-CORE-01"],
        operator="test_setup",
        reason="Fixture populated_state",
        domain="cftv",
    )
    return isolated_state


# ═══════════════════════════════════════════════════════════════════
# 🏛️ FIXTURES — NEW APP FACTORY (feature/foundation-5-pillars)
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def test_settings():
    """AppSettings configured for testing (in-memory SQLite, no external deps)."""
    from app.config import AppConfig, AppSettings, DatabaseConfig, LoggingConfig

    return AppSettings(
        app=AppConfig(environment="testing", testing=True, debug=False, secret_key="test-secret"),
        db=DatabaseConfig(url="sqlite:///:memory:"),
        logging=LoggingConfig(level="WARNING", format="console"),
    )


@pytest.fixture(scope="session")
def factory_app(test_settings, tmp_path_factory):
    """Flask app created via factory with test settings."""
    from pathlib import Path

    # Point data_dir to a temp location so schema.sql is applied
    tmp = tmp_path_factory.mktemp("data")
    import shutil

    schema_src = Path(__file__).resolve().parent.parent / "data" / "schema.sql"
    schema_dst = tmp / "schema.sql"
    if schema_src.exists():
        shutil.copy(schema_src, schema_dst)

    from app.config import AppConfig, AppSettings, DatabaseConfig, LoggingConfig

    settings = AppSettings(
        app=AppConfig(environment="testing", testing=True, secret_key="test-secret"),
        db=DatabaseConfig(url=f"sqlite:///{tmp}/test.db", data_dir=tmp),
        logging=LoggingConfig(level="WARNING", format="console"),
    )

    # Patch schema path
    import app.services.db as db_module

    original_schema = db_module._SCHEMA_PATH
    db_module._SCHEMA_PATH = schema_dst

    from app import create_app

    flask_app = create_app(settings)
    flask_app.config["TESTING"] = True

    yield flask_app

    db_module._SCHEMA_PATH = original_schema


@pytest.fixture
def factory_client(factory_app):
    """Test client for the new factory app."""
    with factory_app.test_client() as c:
        yield c
