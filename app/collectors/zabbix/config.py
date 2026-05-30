"""Configuração do coletor Zabbix via variáveis de ambiente."""

from __future__ import annotations

from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ZabbixSettings(BaseSettings):
    """Configuração lida do ambiente com prefixo ZABBIX_.

    Suporta autenticação por API token (Zabbix 6.0+, preferido) ou
    por usuário/senha (versões anteriores).
    """

    model_config = SettingsConfigDict(
        env_prefix="ZABBIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    url: HttpUrl
    token: SecretStr | None = None
    timeout_seconds: int = 30
    page_size: int = 1000
    lookback_hours: int = 24
    max_retries: int = 3
