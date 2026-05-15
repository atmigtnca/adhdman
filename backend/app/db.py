"""SQLite path helpers for ADHDman.

Phase 0 only prepares the database location. Domain schema and migrations are
introduced in later phases.
"""

from pathlib import Path

from app.config import Settings, get_settings


def get_database_path(settings: Settings | None = None) -> Path:
    """Return the resolved SQLite path from settings."""

    active_settings = settings or get_settings()
    return active_settings.resolved_database_path


def ensure_database_parent(settings: Settings | None = None) -> Path:
    """Ensure the SQLite database parent directory exists and return the DB path."""

    database_path = get_database_path(settings)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return database_path
