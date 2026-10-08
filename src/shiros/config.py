"""Environment-only credentials. Never log settings or connection URLs."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHIROS_", env_file=".env", extra="ignore")
    db_host: str = "127.0.0.1"
    db_port: int = Field(default=55432, ge=1, le=65535)
    db_name: str = "shiros"
    db_user: str = "shiros"
    db_password: SecretStr = SecretStr("")

    def database_url(self) -> URL:
        if not self.db_password.get_secret_value():
            raise ValueError("Set SHIROS_DB_PASSWORD in the environment or .env")
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )
