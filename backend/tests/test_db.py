import sqlite3
from pathlib import Path

from app.config import Settings
from app.db import ensure_database_parent, get_connection, init_db


EXPECTED_TABLES = {"inbox_items", "tasks", "events", "actions"}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(DATABASE_PATH=str(tmp_path / "nested" / "adhdman.sqlite"))


def table_names(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row[0] for row in rows}


def column_names(database_path: Path, table: str) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def test_ensure_database_parent_creates_parent_directory(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    database_path = settings.resolved_database_path

    assert not database_path.parent.exists()

    resolved = ensure_database_parent(settings)

    assert resolved == database_path
    assert database_path.parent.exists()
    assert database_path.parent.is_dir()


def test_get_connection_enables_foreign_key_enforcement(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    with get_connection(settings) as connection:
        foreign_keys_enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert foreign_keys_enabled == 1


def test_init_db_creates_phase_1_tables(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    database_path = init_db(settings)

    assert database_path == settings.resolved_database_path
    assert EXPECTED_TABLES.issubset(table_names(database_path))


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    first_path = init_db(settings)
    second_path = init_db(settings)

    assert first_path == second_path == settings.resolved_database_path
    assert EXPECTED_TABLES.issubset(table_names(second_path))


def test_init_db_adds_phase_3_columns_on_fresh_db(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    database_path = init_db(settings)

    assert "due_at" in column_names(database_path, "tasks")
    assert "status" in column_names(database_path, "events")
    assert "undone_at" in column_names(database_path, "actions")


def test_init_db_adds_phase_3_columns_on_existing_db(tmp_path: Path) -> None:
    """Simulate a pre-Phase-3 database and verify init_db backfills columns."""

    settings = make_settings(tmp_path)
    database_path = settings.resolved_database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)

    legacy_schema = (
        """
        CREATE TABLE tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open',
          source_inbox_item_id INTEGER,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          completed_at TEXT
        );
        """,
        """
        CREATE TABLE events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          starts_at TEXT,
          ends_at TEXT,
          source_inbox_item_id INTEGER,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE actions (
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
    with sqlite3.connect(database_path) as connection:
        for statement in legacy_schema:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO events (title, created_at, updated_at) VALUES (?, ?, ?)",
            ("legacy event", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        connection.commit()

    assert "due_at" not in column_names(database_path, "tasks")
    assert "status" not in column_names(database_path, "events")
    assert "undone_at" not in column_names(database_path, "actions")

    init_db(settings)

    assert "due_at" in column_names(database_path, "tasks")
    assert "status" in column_names(database_path, "events")
    assert "undone_at" in column_names(database_path, "actions")

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT title, status FROM events WHERE title = 'legacy event'"
        ).fetchone()
    assert row == ("legacy event", "open")
