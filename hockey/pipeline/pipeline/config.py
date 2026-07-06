"""Typed configuration from .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "hockey_lab"
    db_user: str = "postgres"
    db_password: str = ""

    # Mode: "local" uses SQLite, "prod" uses PostgreSQL
    mode: str = "local"

    # Local SQLite path
    local_db_path: str = str(Path(__file__).parent.parent / "hockey_lab.sqlite")

    # NHL API
    nhl_api_base: str = "https://api-web.nhle.com/v1"
    nhl_api_timeout: float = 30.0
    nhl_api_rate_limit: float = 1.0  # seconds between requests

    # Data source: "api" or "file"
    data_source: str = "file"

    # File-based data directory
    data_dir: str = str(Path(__file__).parent.parent / "data")

    # Logging
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        """Return the appropriate database URL based on mode."""
        if self.mode == "local":
            return f"sqlite:///{self.local_db_path}"
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
