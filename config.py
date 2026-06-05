"""
Configuração central da dashboard.
Lê variáveis de ambiente do .env e expõe como constantes Python.
"""

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings

# Carrega .env do diretório do app
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(var: str, default: str | None = None, required: bool = True) -> str:
    value = os.getenv(var, default)
    if required and (value is None or value == ""):
        raise OSError(f"Variável de ambiente obrigatória não definida: {var}. Verifique o arquivo {BASE_DIR / '.env'}")
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

# ── Zendesk
ZENDESK_SUBDOMAIN = _env("ZENDESK_SUBDOMAIN")
ZENDESK_EMAIL = _env("ZENDESK_EMAIL")
ZENDESK_API_TOKEN = _env("ZENDESK_API_TOKEN")
ZENDESK_MAX_PAGES = _env_int("ZENDESK_MAX_PAGES", 100)

# ── Maintenance PIN (vazio = endpoints de escrita bloqueados por design)
OPS_PIN = _env("OPS_PIN", "", required=False)

# ── Microsoft Graph
GRAPH_TENANT_ID = _env("AZURE_TENANT_ID", required=False)
GRAPH_CLIENT_ID = _env("AZURE_CLIENT_ID", required=False)
GRAPH_CLIENT_SECRET = _env("AZURE_CLIENT_SECRET", required=False)
GRAPH_ENABLED = bool(GRAPH_TENANT_ID and GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET)
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]
INTUNE_ENABLED = _env_bool("INTUNE_ENABLED", True)

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

# ── Database (SQLite dev / PostgreSQL prod via env var)
DATABASE_URL = _env("DATABASE_URL", "sqlite:///itgov.db", required=False)

# ── OpenTelemetry
OTEL_ENABLED = _env_bool("OTEL_ENABLED", False)
OTEL_SERVICE_NAME = _env("OTEL_SERVICE_NAME", "itgov", required=False)
OTEL_SERVICE_VERSION = _env("OTEL_SERVICE_VERSION", "dev", required=False)
OTEL_ENVIRONMENT = _env("OTEL_ENVIRONMENT", "development", required=False)
OTEL_ENDPOINT = _env("OTEL_ENDPOINT", "localhost:4317", required=False)


# ── Microsoft Entra ID (Sprint 6 — SSO)
class AzureSettings(BaseSettings):
    """Microsoft Entra ID OAuth2 configuration (Authorization Code + PKCE)."""

    tenant_id: str = Field(..., description="Azure AD Tenant ID")
    client_id: str = Field(..., description="App Registration Client ID")
    client_secret: SecretStr = Field(..., description="App Registration Secret")
    redirect_uri: str = Field(
        default="http://localhost:5000/auth/callback",
        description="OAuth2 redirect URI registered in App Registration",
    )
    scopes: list[str] = Field(
        default=["openid", "profile", "email", "User.Read"],
    )

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def logout_uri(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/logout"

    model_config: Any = {
        "env_prefix": "AZURE_",
        "env_file": ".env",
        "extra": "ignore",
    }


def get_azure_settings() -> AzureSettings:
    """Return AzureSettings, raises ValidationError if env vars are missing."""
    return AzureSettings()  # type: ignore[call-arg]


AZURE_SSO_ENABLED = bool(
    os.getenv("AZURE_TENANT_ID") and os.getenv("AZURE_CLIENT_ID") and os.getenv("AZURE_CLIENT_SECRET")
)

# ── Flask-Session (server-side sessions)
SESSION_TYPE = _env("SESSION_TYPE", "filesystem", required=False)
SESSION_FILE_DIR = _env("SESSION_FILE_DIR", "./data/sessions", required=False)
PERMANENT_SESSION_LIFETIME = _env_int("PERMANENT_SESSION_LIFETIME", 3600)

# ── Production SSO guard
# Fails fast at startup when FLASK_ENV=production and SSO credentials are absent.
# Prevents the catastrophic scenario where credentials are accidentally removed in
# production, leaving the dashboard exposed without authentication.
_FLASK_ENV = _env("FLASK_ENV", "development", required=False).lower()
if _FLASK_ENV in ("production", "prod") and not AZURE_SSO_ENABLED:
    raise RuntimeError(
        "SSO is mandatory in production. "
        "Set AZURE_TENANT_ID, AZURE_CLIENT_ID and AZURE_CLIENT_SECRET in the environment, "
        "or set FLASK_ENV=development to run without authentication."
    )
