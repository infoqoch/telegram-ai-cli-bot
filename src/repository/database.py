"""SQLite database connection singleton."""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

_connection: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get or create SQLite connection singleton.

    Args:
        db_path: Path to database file. Only used on first call.

    Returns:
        SQLite connection with Row factory enabled.
    """
    global _connection

    if _connection is not None:
        return _connection

    with _lock:
        if _connection is not None:
            return _connection

        if db_path is None:
            raise ValueError("db_path required for initial connection")

        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            timeout=30.0
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")

        _connection = conn

    return _connection


def close_connection() -> None:
    """Close database connection."""
    global _connection

    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None


def init_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    """Initialize database schema from SQL file.

    Args:
        conn: SQLite connection
        schema_path: Path to schema.sql file
    """
    schema_sql = schema_path.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


def reset_connection() -> None:
    """Reset connection for testing purposes."""
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
        _connection = None
