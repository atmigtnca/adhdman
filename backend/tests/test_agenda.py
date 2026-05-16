from pathlib import Path
import sqlite3

from app.agenda import get_agenda_now
from app.config import Settings
from app.db import init_db


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def insert_task(
    connection: sqlite3.Connection,
    title: str,
    *,
    due_at: str | None = None,
    do_before_event_id: int | None = None,
    status: str = "open",
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO tasks (
          title, status, due_at, do_before_event_id,
          created_at, updated_at, completed_at
        )
        VALUES (?, ?, ?, ?, '2026-05-31T08:00:00+09:00', '2026-05-31T08:00:00+09:00', NULL)
        """,
        (title, status, due_at, do_before_event_id),
    )
    return int(cursor.lastrowid)


def insert_event(
    connection: sqlite3.Connection,
    title: str,
    *,
    starts_at: str,
    ends_at: str | None = None,
    status: str = "open",
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO events (
          title, starts_at, ends_at, status,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, '2026-05-31T08:00:00+09:00', '2026-05-31T08:00:00+09:00')
        """,
        (title, starts_at, ends_at, status),
    )
    return int(cursor.lastrowid)


def seed_oscar_db_cpp_kcc(settings: Settings) -> dict[str, int]:
    init_db(settings)
    with sqlite3.connect(settings.resolved_database_path) as connection:
        oscar_id = insert_event(
            connection,
            "오스카 모임",
            starts_at="2026-05-31T13:00:00+09:00",
            ends_at="2026-05-31T18:00:00+09:00",
        )
        kcc_id = insert_event(
            connection,
            "KCC 학회",
            starts_at="2026-06-23T09:00:00+09:00",
            ends_at="2026-06-23T18:00:00+09:00",
        )
        cpp_id = insert_task(
            connection,
            "cpp 과제",
            due_at="2026-06-02T13:00:00+09:00",
        )
        db_id = insert_task(
            connection,
            "db 과제",
            due_at="2026-06-01T01:00:00+09:00",
        )
        before_oscar_id = insert_task(
            connection,
            "오스카모임 전까지 과제 끝내기",
            do_before_event_id=oscar_id,
        )
    return {
        "oscar": oscar_id,
        "kcc": kcc_id,
        "cpp": cpp_id,
        "db": db_id,
        "before_oscar": before_oscar_id,
    }


def test_before_event_task_is_recommended_before_upcoming_event(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    ids = seed_oscar_db_cpp_kcc(settings)

    agenda = get_agenda_now(
        now="2026-05-31T12:00:00+09:00",
        settings=settings,
    )

    assert agenda.now is not None
    assert agenda.now.kind == "task"
    assert agenda.now.id == ids["before_oscar"]
    assert agenda.now.urgency == "before_event"
    assert "오스카 모임" in agenda.now.reason
    assert [item.id for item in agenda.next][:2] == [ids["oscar"], ids["db"]]


def test_completed_before_event_task_reveals_event(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    ids = seed_oscar_db_cpp_kcc(settings)
    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.execute(
            "UPDATE tasks SET status = 'done', completed_at = '2026-05-31T12:10:00+09:00' WHERE id = ?",
            (ids["before_oscar"],),
        )

    agenda = get_agenda_now(
        now="2026-05-31T12:15:00+09:00",
        settings=settings,
    )

    assert agenda.now is not None
    assert agenda.now.kind == "event"
    assert agenda.now.id == ids["oscar"]
    assert agenda.now.urgency == "upcoming_event"


def test_after_event_earliest_deadline_wins(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    ids = seed_oscar_db_cpp_kcc(settings)
    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.execute(
            "UPDATE tasks SET status = 'done', completed_at = '2026-05-31T12:10:00+09:00' WHERE id = ?",
            (ids["before_oscar"],),
        )

    agenda = get_agenda_now(
        now="2026-05-31T19:00:00+09:00",
        settings=settings,
    )

    assert agenda.now is not None
    assert agenda.now.kind == "task"
    assert agenda.now.id == ids["db"]
    assert agenda.now.urgency == "deadline"
    assert [item.id for item in agenda.next][:1] == [ids["cpp"]]
    assert ids["kcc"] in [item.id for item in agenda.later]


def test_after_db_done_cpp_wins_before_later_conference(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    ids = seed_oscar_db_cpp_kcc(settings)
    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.execute(
            "UPDATE tasks SET status = 'done', completed_at = '2026-05-31T12:10:00+09:00' WHERE id IN (?, ?)",
            (ids["before_oscar"], ids["db"]),
        )

    agenda = get_agenda_now(
        now="2026-05-31T19:00:00+09:00",
        settings=settings,
    )

    assert agenda.now is not None
    assert agenda.now.kind == "task"
    assert agenda.now.id == ids["cpp"]
    assert ids["kcc"] in [item.id for item in agenda.later]
