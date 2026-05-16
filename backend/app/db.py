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
    CREATE TABLE IF NOT EXISTS classifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      inbox_item_id INTEGER NOT NULL,
      intent TEXT NOT NULL,
      confidence REAL NOT NULL,
      source TEXT NOT NULL,
      raw_response TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY(inbox_item_id) REFERENCES inbox_items(id)
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
    """
    CREATE TABLE IF NOT EXISTS focus_sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      kind TEXT NOT NULL,
      target_type TEXT,
      target_id INTEGER,
      status TEXT NOT NULL,
      started_at TEXT NOT NULL,
      ended_at TEXT,
      interval_seconds INTEGER,
      note TEXT,
      last_check_in_at TEXT
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


PHASE_3_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("tasks", "due_at", "ALTER TABLE tasks ADD COLUMN due_at TEXT"),
    (
        "events",
        "status",
        "ALTER TABLE events ADD COLUMN status TEXT NOT NULL DEFAULT 'open'",
    ),
    ("actions", "undone_at", "ALTER TABLE actions ADD COLUMN undone_at TEXT"),
)


PHASE_6_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (
        "tasks",
        "parent_task_id",
        "ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER REFERENCES tasks(id)",
    ),
    ("tasks", "block_state", "ALTER TABLE tasks ADD COLUMN block_state TEXT"),
)

PHASE_8_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (
        "tasks",
        "do_before_event_id",
        "ALTER TABLE tasks ADD COLUMN do_before_event_id INTEGER REFERENCES events(id)",
    ),
)


def _existing_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _apply_additive_columns(connection: sqlite3.Connection) -> None:
    additive_columns = (
        PHASE_3_ADDITIVE_COLUMNS + PHASE_6_ADDITIVE_COLUMNS + PHASE_8_ADDITIVE_COLUMNS
    )
    for table, column, statement in additive_columns:
        if column not in _existing_columns(connection, table):
            connection.execute(statement)


def init_db(settings: Settings | None = None) -> Path:
    """Initialize the SQLite schema and return the database path.

    Creates Phase 1/2 tables and applies additive Phase 3 columns idempotently,
    so existing databases gain new columns without losing data.
    """

    database_path = ensure_database_parent(settings)
    with get_connection(settings) as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        _apply_additive_columns(connection)
    return database_path
