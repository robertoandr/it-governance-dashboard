from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    POSTGRES_DSN: str = ""
    GITHUB_TOKEN: str
    GITHUB_ORG: str
    GITHUB_REPOS: str = ""
    INFLUX_URL: str = "http://influxdb:8086"
    INFLUX_TOKEN: str
    INFLUX_ORG: str = "grupogadens"
    INFLUX_BUCKET_RAW: str = "governance_raw"
    LOG_LEVEL: str = "INFO"
    TZ: str = "America/Sao_Paulo"
    # Microsoft Entra ID (Azure AD) — necessário para o coletor Entra ID
    AZURE_TENANT_ID: str = ""
    AZURE_CLIENT_ID: str = ""
    AZURE_CLIENT_SECRET: str = ""
    # Zabbix — necessário para o coletor de utilização de recursos
    ZABBIX_URL: str = ""
    ZABBIX_USER: str = ""
    ZABBIX_PASSWORD: str = ""
    # Acronis Cyber Cloud — coletor de agentes e proteção
    ACRONIS_BASE_URL: str = ""
    ACRONIS_CLIENT_ID: str = ""
    ACRONIS_CLIENT_SECRET: str = ""
    # Zabbix Asset Status — job de polling a cada 60s (Pacote III)
    ZBX_URL: str = ""
    ZBX_TOKEN: str = ""
    ZBX_USER: str = ""
    ZBX_PASSWORD: str = ""
    # JSON: {"camera": ["Cameras"], "server": ["Linux servers"]}
    ZBX_GROUP_MAP: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
