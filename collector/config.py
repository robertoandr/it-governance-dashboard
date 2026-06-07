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
    ZABBIX_URL: str = "http://172.21.0.1:8080/api_jsonrpc.php"
    ZABBIX_TOKEN: str = ""
    LOG_LEVEL: str = "INFO"
    TZ: str = "America/Sao_Paulo"

    class Config:
        env_file = ".env"


settings = Settings()
