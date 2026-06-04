"""Application configuration via Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """Core Flask settings."""

    name: str = "IT Governance Dashboard"
    version: str = "1.1.0"
    environment: Literal["development", "testing", "production"] = "development"
    secret_key: SecretStr = SecretStr("dev-secret-change-in-prod")
    debug: bool = False
    testing: bool = False
    host: str = "0.0.0.0"
    port: int = 5000


class DatabaseConfig(BaseSettings):
    """SQLite / PostgreSQL connection settings."""

    url: str = "sqlite:///data/govti.db"
    data_dir: Path = Path("data")

    @field_validator("data_dir", mode="after")
    @classmethod
    def ensure_data_dir(cls, v: Path) -> Path:
        v.mkdir(parents=True, exist_ok=True)
        return v


class InfluxConfig(BaseSettings):
    """InfluxDB v2 connection."""

    url: str = "http://localhost:8086"
    token: SecretStr = SecretStr("")
    org: str = "gadens"
    bucket: str = "govti"
    enabled: bool = False


class ZabbixConfig(BaseSettings):
    """Zabbix API connection."""

    url: str = "http://localhost/zabbix/api_jsonrpc.php"
    user: str = "Admin"
    password: SecretStr = SecretStr("")
    enabled: bool = False


class GitHubConfig(BaseSettings):
    """GitHub API integration."""

    token: SecretStr = SecretStr("")
    org: str = ""
    enabled: bool = False


class ZendeskConfig(BaseSettings):
    """Zendesk API integration."""

    url: str = ""
    email: str = ""
    token: SecretStr = SecretStr("")
    enabled: bool = False


class GraphConfig(BaseSettings):
    """Microsoft Graph (M365) integration."""

    tenant_id: str = ""
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    enabled: bool = False


class LoggingConfig(BaseSettings):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "console"


class ScoreConfig(BaseSettings):
    """Governance score thresholds and weights."""

    threshold_operational: float = 85.0
    threshold_degraded: float = 60.0

    weight_strategic_alignment: float = 0.15
    weight_value_delivery: float = 0.20
    weight_risk_management: float = 0.25
    weight_resource_management: float = 0.15
    weight_performance_measure: float = 0.25

    cache_ttl_seconds: int = 25


class AppSettings(BaseSettings):
    """Aggregate settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppConfig = AppConfig()
    db: DatabaseConfig = DatabaseConfig()
    influx: InfluxConfig = InfluxConfig()
    zabbix: ZabbixConfig = ZabbixConfig()
    github: GitHubConfig = GitHubConfig()
    zendesk: ZendeskConfig = ZendeskConfig()
    graph: GraphConfig = GraphConfig()
    logging: LoggingConfig = LoggingConfig()
    score: ScoreConfig = ScoreConfig()


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return cached application settings."""
    return AppSettings()
