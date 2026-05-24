"""
Configuração central da dashboard.
Lê variáveis de ambiente do .env e expõe como constantes Python.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Carrega .env do diretório do app
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(var: str, default: str | None = None, required: bool = True) -> str:
    value = os.getenv(var, default)
    if required and (value is None or value == ""):
        raise OSError(
            f"Variável de ambiente obrigatória não definida: {var}. "
            f"Verifique o arquivo {BASE_DIR / '.env'}"
        )
    return value or ""


def _env_bool(var: str, default: bool = False) -> bool:
    return _env(var, str(default), required=False).strip().lower() in ("1", "true", "yes", "on")


def _env_int(var: str, default: int) -> int:
    try:
        return int(_env(var, str(default), required=False))
    except ValueError:
        return default


# ── Flask
FLASK_PORT = _env_int("FLASK_PORT", 8080)
FLASK_DEBUG = _env_bool("FLASK_DEBUG", False)
CACHE_TTL = _env_int("CACHE_TTL", 60)

# ── InfluxDB
INFLUX_URL = _env("INFLUX_URL")
INFLUX_TOKEN = _env("INFLUX_TOKEN")
INFLUX_ORG = _env("INFLUX_ORG")
INFLUX_BUCKET = _env("INFLUX_BUCKET")

# ── Zabbix
ZABBIX_URL = _env("ZABBIX_URL")
ZABBIX_FRONT_URL = _env("ZABBIX_FRONT_URL", "")
ZABBIX_USER = _env("ZABBIX_USER")
ZABBIX_PASSWORD = _env("ZABBIX_PASSWORD")

# ── Microsoft Graph
GRAPH_TENANT_ID = _env("AZURE_TENANT_ID", required=False)
GRAPH_CLIENT_ID = _env("AZURE_CLIENT_ID", required=False)
GRAPH_CLIENT_SECRET = _env("AZURE_CLIENT_SECRET", required=False)
GRAPH_ENABLED = bool(GRAPH_TENANT_ID and GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET)
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]

# ── Grafana
GRAFANA_URL = _env("GRAFANA_URL", required=False)
GRAFANA_TOKEN = _env("GRAFANA_TOKEN", required=False)
GRAFANA_ENABLED = bool(GRAFANA_URL and GRAFANA_TOKEN)

# ── LDAP
LDAP_ENABLED = _env_bool("LDAP_ENABLED", False)
LDAP_SERVER = _env("LDAP_SERVER", required=False)
LDAP_USER = _env("LDAP_USER", required=False)
LDAP_PASSWORD = _env("LDAP_PASSWORD", required=False)
LDAP_BASE_DN = _env("LDAP_BASE_DN", required=False)

# ── Thresholds dos KPIs (do documento de governança)
THRESHOLDS = {
    "secure_score": {"critical": 60, "warning": 85},  # < 60 crit, < 85 warn, >= 85 ok
    "mfa": {"critical": 80, "warning": 100},
    "intune": {"critical": 70, "warning": 100},
    "hosts_up_pct": {"critical": 80, "warning": 95},
}

# ── FINOPS
FINOPS_PIN = _env("FINOPS_PIN", "", required=False)
