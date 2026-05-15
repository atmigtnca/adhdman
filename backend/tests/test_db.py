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
