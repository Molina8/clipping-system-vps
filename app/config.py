"""Application configuration loaded from /opt/clipping-system/.env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="/opt/clipping-system/.env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    clipping_db_host: str = "127.0.0.1"
    clipping_db_port: int = 5432
    clipping_db_name: str = "clipping"
    clipping_db_user: str = "clipping_api"
    clipping_db_password: str = ""

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8080
    api_token: str = ""

    # App
    environment: str = "production"
    log_level: str = "INFO"

    # LLM API (for HttpLLMClient) — MiniMax portal, Anthropic-compatible endpoint
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.minimax.io/anthropic"
    anthropic_model: str = "minimax-m3"
    anthropic_version: str = "2023-06-01"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.clipping_db_user}:{self.clipping_db_password}"
            f"@{self.clipping_db_host}:{self.clipping_db_port}/{self.clipping_db_name}"
        )


settings = Settings()
