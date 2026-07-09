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
    # Microsoft Entra ID (Azure AD) — required for Entra ID collector
    AZURE_TENANT_ID: str = ""
    AZURE_CLIENT_ID: str = ""
    AZURE_CLIENT_SECRET: str = ""

    # Active Directory / WinRM patch collector
    # Reutiliza vars LDAP_* já presentes no .env do projeto
    LDAP_SERVER: str = ""  # ex: ldap://dc.empresa.local  ou  ldap://192.168.1.10
    LDAP_BASE_DN: str = ""  # ex: DC=empresa,DC=local
    LDAP_BIND_DN: str = ""  # ex: EMPRESA\\svc-patch-ro  (NTLM) ou CN=svc,OU=Svc,DC=...
    LDAP_BIND_PASSWORD: str = ""
    WINRM_USER: str = ""  # ex: EMPRESA\\svc-patch-ro  (pode ser igual ao LDAP)
    WINRM_PASSWORD: str = ""
    PATCH_THRESHOLD_DAYS: int = 35
    PATCH_MAX_WORKERS: int = 20  # início conservador; subir para 50 em redes estáveis
    WINRM_TIMEOUT_S: int = 30
    PATCH_STALE_MACHINE_DAYS: int = 60  # exclui máquinas sem logon há N dias do inventário AD
    # Zabbix — coletor de riscos e disponibilidade
    ZABBIX_URL: str = ""
    ZABBIX_USER: str = ""
    ZABBIX_PASSWORD: str = ""
    ZABBIX_TOKEN: str = ""
    ZBX_VERIFY_TLS: bool = True
    # Acronis Cyber Cloud — coletor de agentes e proteção
    ACRONIS_BASE_URL: str = ""
    ACRONIS_CLIENT_ID: str = ""
    ACRONIS_CLIENT_SECRET: str = ""
    # SMTP — alerta de governança (alert_job)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_FROM: str = ""
    SMTP_TO: str = ""
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_AUTH: bool = True
    SMTP_TLS: bool = True
    ALERT_COOLDOWN_S: int = 3600
    ALERT_SCORE_THRESHOLD: float = 60.0
    ALERT_JOB_INTERVAL_S: int = 300
    # ThingSpeak — sensor de temperatura do datacenter (Nextcon DSB WIFI)
    NEXTCON_CHANNEL_ID: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
