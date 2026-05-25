from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    POSTGRES_DSN: str
    GITHUB_TOKEN: str
    GITHUB_ORG: str
    LOG_LEVEL: str = "INFO"
    TZ: str = "America/Sao_Paulo"

    class Config:
        env_file = ".env"


settings = Settings()
