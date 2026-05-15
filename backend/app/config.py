"""Application configuration for ADHDman.

Phase 0 keeps configuration intentionally small. The app is strictly single-user;
there are no auth, account, role, or multi-user settings by design.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    app_name: str = Field(default="ADHDman", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    database_path: Path = Field(default=Path("./data/adhdman.sqlite"), alias="DATABASE_PATH")

    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    openrouter_model: str = Field(
        default="inclusionai/ring-2.6-1t", alias="OPENROUTER_MODEL"
    )
    llm_timeout_seconds: float = Field(default=8.0, alias="LLM_TIMEOUT_SECONDS")
    rules_accept_threshold: float = Field(
        default=0.85, alias="RULES_ACCEPT_THRESHOLD"
    )
    classify_enabled: bool = Field(default=True, alias="CLASSIFY_ENABLED")

    local_timezone: str = Field(default="UTC", alias="LOCAL_TIMEZONE")
    search_max_candidates: int = Field(default=5, alias="SEARCH_MAX_CANDIDATES")
    search_ambiguity_threshold: float = Field(
        default=0.15, alias="SEARCH_AMBIGUITY_THRESHOLD"
    )
    undo_enabled: bool = Field(default=True, alias="UNDO_ENABLED")

    body_double_default_interval: int = Field(
        default=300, alias="BODY_DOUBLE_DEFAULT_INTERVAL"
    )
    body_double_min_interval: int = Field(
        default=60, alias="BODY_DOUBLE_MIN_INTERVAL"
    )
    body_double_max_interval: int = Field(
        default=1800, alias="BODY_DOUBLE_MAX_INTERVAL"
    )

    survival_max_tasks: int = Field(default=1, alias="SURVIVAL_MAX_TASKS")
    survival_max_events: int = Field(default=1, alias="SURVIVAL_MAX_EVENTS")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator(
        "body_double_default_interval",
        "body_double_min_interval",
        "body_double_max_interval",
    )
    @classmethod
    def _validate_body_double_intervals(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("body-double intervals must be positive")
        return value

    @model_validator(mode="after")
    def _validate_body_double_interval_order(self) -> "Settings":
        if self.body_double_min_interval > self.body_double_max_interval:
            raise ValueError(
                "BODY_DOUBLE_MIN_INTERVAL must be <= BODY_DOUBLE_MAX_INTERVAL"
            )
        if not (
            self.body_double_min_interval
            <= self.body_double_default_interval
            <= self.body_double_max_interval
        ):
            raise ValueError(
                "BODY_DOUBLE_DEFAULT_INTERVAL must be between min and max"
            )
        return self

    @field_validator("survival_max_tasks", "survival_max_events")
    @classmethod
    def _validate_survival_caps(cls, value: int) -> int:
        if value < 0:
            raise ValueError("survival caps must be non-negative")
        return value

    @field_validator("llm_timeout_seconds")
    @classmethod
    def _validate_llm_timeout_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be positive")
        return value

    @field_validator("search_max_candidates")
    @classmethod
    def _validate_search_max_candidates(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("SEARCH_MAX_CANDIDATES must be positive")
        return value

    @field_validator("search_ambiguity_threshold")
    @classmethod
    def _validate_search_ambiguity_threshold(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("SEARCH_AMBIGUITY_THRESHOLD must be in [0, 1]")
        return value

    @property
    def resolved_database_path(self) -> Path:
        """Return an absolute SQLite database path."""

        return self.database_path.expanduser().resolve()


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for the current process."""

    return Settings()
