"""SQLite database helpers for ADHDman."""

from pathlib import Path
import sqlite3

from app.config import Settings, get_settings


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS inbox_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      text TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'open',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      promoted_to_type TEXT,
      promoted_to_id INTEGER
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'open',
      source_inbox_item_id INTEGER,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      completed_at TEXT,
      FOREIGN KEY(source_inbox_item_id) REFERENCES inbox_items(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      starts_at TEXT,
      ends_at TEXT,
      source_inbox_item_id INTEGER,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY(source_inbox_item_id) REFERENCES inbox_items(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS actions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      action_type TEXT NOT NULL,
      target_type TEXT NOT NULL,
      target_id INTEGER NOT NULL,
      before_json TEXT,
      after_json TEXT,
      created_at TEXT NOT NULL
    );
    """,
)


def get_database_path(settings: Settings | None = None) -> Path:
    """Return the resolved SQLite path from settings."""

    active_settings = settings or get_settings()
    return active_settings.resolved_database_path


def ensure_database_parent(settings: Settings | None = None) -> Path:
    """Ensure the SQLite database parent directory exists and return the DB path."""

    database_path = get_database_path(settings)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return database_path


def get_connection(settings: Settings | None = None) -> sqlite3.Connection:
    """Return a SQLite connection with foreign-key enforcement enabled."""

    database_path = ensure_database_parent(settings)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(settings: Settings | None = None) -> Path:
    """Initialize the Phase 1 SQLite schema and return the database path."""

    database_path = ensure_database_parent(settings)
    with get_connection(settings) as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
    return database_path
