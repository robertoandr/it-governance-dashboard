from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All secrets are typed as SecretStr so they are never logged or serialized.
    Use get_settings() — never instantiate Settings() directly in modules.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "IT Governance Dashboard"
    app_version: str = "0.1.0"
    debug: bool = False
    secret_key: SecretStr = Field(..., min_length=32)

    # InfluxDB v2
    influx_url: str = "http://localhost:8086"
    influx_token: SecretStr = Field(...)
    influx_org: str = Field(...)
    influx_bucket: str = "it-governance"

    # GitHub
    github_token: SecretStr = Field(default=SecretStr(""))
    github_org: str = ""

    # Zabbix JSON-RPC
    zabbix_url: str = ""
    zabbix_user: str = ""
    zabbix_password: SecretStr = Field(default=SecretStr(""))

    # Zendesk
    zendesk_subdomain: str = ""
    zendesk_email: str = ""
    zendesk_token: SecretStr = Field(default=SecretStr(""))

    # Microsoft Entra ID (OAuth2 / MS Graph)
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: SecretStr = Field(default=SecretStr(""))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call).

    Returns:
        Settings: application configuration object.
    """
    return Settings()
