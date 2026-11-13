from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# repo/backend/src/it_procurement_api/settings.py -> repo
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Local development settings. Environment variables override infra/.env."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / "infra" / ".env", extra="ignore")

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5433  # host port published by infra/compose.yaml
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
