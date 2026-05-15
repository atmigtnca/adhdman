"""Application configuration for ADHDman.

Phase 0 keeps configuration intentionally small. The app is strictly single-user;
there are no auth, account, role, or multi-user settings by design.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    app_name: str = Field(default="ADHDman", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    database_path: Path = Field(default=Path("./data/adhdman.sqlite"), alias="DATABASE_PATH")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def resolved_database_path(self) -> Path:
        """Return an absolute SQLite database path."""

        return self.database_path.expanduser().resolve()


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for the current process."""

    return Settings()
