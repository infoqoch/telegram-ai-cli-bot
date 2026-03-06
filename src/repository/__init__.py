"""Repository package - unified SQLite storage for the bot."""

from pathlib import Path
from typing import Optional

from .database import get_connection, close_connection, init_schema, reset_connection
from .repository import Repository

_repository: Optional[Repository] = None


def init_repository(db_path: Path) -> Repository:
    """Initialize the repository singleton.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Repository instance
    """
    global _repository

    conn = get_connection(db_path)

    # message_queue → message_log 테이블명 변경 (기존 DB 호환)
    try:
        conn.execute("ALTER TABLE message_queue RENAME TO message_log")
        conn.commit()
    except Exception:
        pass

    # schedules 테이블에 plugin 관련 컬럼 추가
    for col in ["plugin_name TEXT", "action_name TEXT"]:
        try:
            conn.execute(f"ALTER TABLE schedules ADD COLUMN {col}")
            conn.commit()
        except Exception:
            pass

    schema_path = Path(__file__).parent / "schema.sql"
    init_schema(conn, schema_path)

    _repository = Repository(conn)

    # schedules type CHECK 제약 제거 마이그레이션 (plugin 타입 지원)
    if not _repository.is_migration_applied("schedules_remove_type_check_v1"):
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='schedules'"
            ).fetchone()
            if row and "CHECK" in row[0] and "type IN" in row[0]:
                conn.execute("ALTER TABLE schedules RENAME TO _schedules_old")
                conn.execute("""
                    CREATE TABLE schedules (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        chat_id INTEGER NOT NULL,
                        hour INTEGER NOT NULL CHECK (hour >= 0 AND hour <= 23),
                        minute INTEGER NOT NULL CHECK (minute >= 0 AND minute <= 59),
                        message TEXT NOT NULL,
                        name TEXT NOT NULL,
                        type TEXT NOT NULL DEFAULT 'claude',
                        model TEXT NOT NULL DEFAULT 'sonnet',
                        workspace_path TEXT,
                        plugin_name TEXT,
                        action_name TEXT,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        last_run TEXT,
                        last_error TEXT,
                        run_count INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                """)
                conn.execute("""
                    INSERT INTO schedules
                    SELECT id, user_id, chat_id, hour, minute, message, name, type, model,
                           workspace_path, plugin_name, action_name,
                           enabled, created_at, last_run, last_error, run_count
                    FROM _schedules_old
                """)
                conn.execute("DROP TABLE _schedules_old")
                conn.commit()
            _repository.mark_migration_applied("schedules_remove_type_check_v1")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"schedules migration failed: {e}")

    # todos slot CHECK 제약 제거 마이그레이션
    if not _repository.is_migration_applied("todos_remove_slot_check_v1"):
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='todos'"
            ).fetchone()
            if row and "CHECK" in row[0] and "slot IN" in row[0]:
                conn.execute("ALTER TABLE todos RENAME TO _todos_old")
                conn.execute("""
                    CREATE TABLE todos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER NOT NULL,
                        date TEXT NOT NULL,
                        slot TEXT NOT NULL DEFAULT 'default',
                        text TEXT NOT NULL,
                        done INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                """)
                conn.execute("""
                    INSERT INTO todos
                    SELECT id, chat_id, date, slot, text, done, created_at, updated_at
                    FROM _todos_old
                """)
                conn.execute("DROP TABLE _todos_old")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_chat_id ON todos(chat_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_date ON todos(date)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_chat_date ON todos(chat_id, date)")
                conn.commit()
            _repository.mark_migration_applied("todos_remove_slot_check_v1")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"todos migration failed: {e}")

    return _repository


def get_repository() -> Repository:
    """Get the repository singleton.

    Returns:
        Repository instance

    Raises:
        RuntimeError: If repository not initialized
    """
    if _repository is None:
        raise RuntimeError("Repository not initialized. Call init_repository() first.")
    return _repository


def shutdown_repository() -> None:
    """Shutdown repository and close database connection."""
    global _repository
    _repository = None
    close_connection()


__all__ = [
    "init_repository",
    "get_repository",
    "shutdown_repository",
    "Repository",
    "reset_connection",
]
