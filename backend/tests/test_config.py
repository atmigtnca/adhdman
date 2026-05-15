from pathlib import Path

from app.config import Settings
from app.db import ensure_database_parent, get_database_path


def test_settings_have_safe_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "ADHDman"
    assert settings.app_env == "development"
    assert settings.database_path == Path("data/adhdman.sqlite")


def test_database_path_resolves_absolute() -> None:
    settings = Settings(DATABASE_PATH="./data/test.sqlite")

    assert get_database_path(settings).is_absolute()
    assert get_database_path(settings).name == "test.sqlite"


def test_phase_3_settings_have_safe_defaults(monkeypatch) -> None:
    for name in (
        "LOCAL_TIMEZONE",
        "SEARCH_MAX_CANDIDATES",
        "SEARCH_AMBIGUITY_THRESHOLD",
        "UNDO_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.local_timezone == "UTC"
    assert settings.search_max_candidates == 5
    assert settings.search_ambiguity_threshold == 0.15
    assert settings.undo_enabled is True


def test_ensure_database_parent_creates_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "adhdman.sqlite"
    settings = Settings(DATABASE_PATH=str(db_path))

    resolved = ensure_database_parent(settings)

    assert resolved == db_path.resolve()
    assert resolved.parent.exists()
