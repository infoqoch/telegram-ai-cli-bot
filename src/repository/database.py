"""SQLite database connection singleton."""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from src.ai import DEFAULT_PROVIDER, SUPPORTED_PROVIDERS, infer_provider_from_model
from src.schedule_utils import build_daily_cron

_connection: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def get_connection(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
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

        # str을 Path로 변환
        if isinstance(db_path, str):
            db_path = Path(db_path)

        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            timeout=30.0,
            autocommit=True,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
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
    _preflight_existing_schema(conn)
    conn.executescript(schema_sql)
    _migrate_schema(conn)
    conn.commit()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Return whether a table already exists."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _preflight_existing_schema(conn: sqlite3.Connection) -> None:
    """Add critical columns before executescript creates provider-aware indexes."""
    if _table_exists(conn, "users"):
        _ensure_column(conn, "users", "selected_ai_provider", "TEXT NOT NULL DEFAULT 'claude'")
    if _table_exists(conn, "sessions"):
        _ensure_column(conn, "sessions", "ai_provider", "TEXT NOT NULL DEFAULT 'claude'")
        _ensure_column(conn, "sessions", "provider_session_id", "TEXT")
    if _table_exists(conn, "schedules"):
        _ensure_column(conn, "schedules", "ai_provider", "TEXT NOT NULL DEFAULT 'claude'")
        _ensure_column(conn, "schedules", "trigger_type", "TEXT NOT NULL DEFAULT 'cron'")
        _ensure_column(conn, "schedules", "cron_expr", "TEXT")
        _ensure_column(conn, "schedules", "run_at_local", "TEXT")
    if _table_exists(conn, "message_log"):
        _ensure_column(conn, "message_log", "delivery_text", "TEXT")
        _ensure_column(conn, "message_log", "delivery_status", "TEXT NOT NULL DEFAULT 'not_ready'")
        _ensure_column(conn, "message_log", "delivery_attempts", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "message_log", "delivery_error", "TEXT")
        _ensure_column(conn, "message_log", "delivered_at", "TEXT")


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply lightweight in-place schema upgrades for existing local DBs."""
    _ensure_column(conn, "users", "selected_ai_provider", "TEXT NOT NULL DEFAULT 'claude'")
    _ensure_column(conn, "sessions", "ai_provider", "TEXT NOT NULL DEFAULT 'claude'")
    _ensure_column(conn, "sessions", "provider_session_id", "TEXT")
    _ensure_column(conn, "schedules", "ai_provider", "TEXT NOT NULL DEFAULT 'claude'")
    _ensure_column(conn, "schedules", "trigger_type", "TEXT NOT NULL DEFAULT 'cron'")
    _ensure_column(conn, "schedules", "cron_expr", "TEXT")
    _ensure_column(conn, "schedules", "run_at_local", "TEXT")
    _ensure_column(conn, "message_log", "delivery_text", "TEXT")
    _ensure_column(conn, "message_log", "delivery_status", "TEXT NOT NULL DEFAULT 'not_ready'")
    _ensure_column(conn, "message_log", "delivery_attempts", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "message_log", "delivery_error", "TEXT")
    _ensure_column(conn, "message_log", "delivered_at", "TEXT")

    # Workspace uniqueness must be provider-aware.
    conn.execute("DROP INDEX IF EXISTS idx_sessions_workspace_unique")
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_workspace_unique
           ON sessions(user_id, ai_provider, workspace_path)
           WHERE workspace_path IS NOT NULL AND deleted = 0"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_provider_state (
               user_id TEXT NOT NULL,
               ai_provider TEXT NOT NULL,
               current_session_id TEXT,
               previous_session_id TEXT,
               updated_at TEXT NOT NULL DEFAULT (datetime('now')),
               PRIMARY KEY (user_id, ai_provider)
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_provider_state_provider ON user_provider_state(ai_provider)"
    )
    conn.execute("DROP INDEX IF EXISTS idx_queued_messages_expires_at")

    # Historical rows that already have a stored response were necessarily sent successfully
    # in the previous implementation, because persistence happened after Telegram delivery.
    conn.execute(
        """UPDATE message_log
           SET delivery_status = 'sent',
               delivery_attempts = CASE WHEN delivery_attempts < 1 THEN 1 ELSE delivery_attempts END,
               delivered_at = COALESCE(delivered_at, processed_at)
           WHERE processed = 2
             AND response IS NOT NULL
             AND (delivery_status IS NULL OR delivery_status = '' OR delivery_status = 'not_ready')"""
    )

    _backfill_session_provider_data(conn)
    _cleanup_unsupported_provider_rows(conn)
    _initialize_provider_state(conn)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add one column if it does not exist."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {row[1] for row in rows}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _backfill_session_provider_data(conn: sqlite3.Connection) -> None:
    """Infer provider/provider_session_id for legacy rows."""
    rows = conn.execute("SELECT id, model FROM sessions").fetchall()
    for session_id, model in rows:
        provider = infer_provider_from_model(model)
        conn.execute(
            "UPDATE sessions SET ai_provider = ? WHERE id = ?",
            (provider, session_id),
        )

    # Clean up corrupted provider_session_id where internal hex ID was stored
    conn.execute(
        "UPDATE sessions SET provider_session_id = NULL WHERE provider_session_id = id"
    )

    schedule_rows = conn.execute("SELECT id, model FROM schedules").fetchall()
    for schedule_id, model in schedule_rows:
        provider = infer_provider_from_model(model)
        if provider not in SUPPORTED_PROVIDERS:
            provider = DEFAULT_PROVIDER
        conn.execute(
            "UPDATE schedules SET ai_provider = ? WHERE id = ?",
            (provider, schedule_id),
        )

    conn.execute(
        "UPDATE schedules SET schedule_type = 'chat' WHERE schedule_type IS NULL OR schedule_type = '' OR schedule_type = 'claude'"
    )

    schedule_rows = conn.execute(
        "SELECT id, hour, minute, trigger_type, cron_expr FROM schedules"
    ).fetchall()
    for schedule_id, hour, minute, trigger_type, cron_expr in schedule_rows:
        normalized_trigger = "once" if trigger_type == "once" else "cron"
        next_cron = cron_expr or build_daily_cron(hour, minute)
        conn.execute(
            "UPDATE schedules SET trigger_type = ?, cron_expr = ? WHERE id = ?",
            (normalized_trigger, next_cron if normalized_trigger == "cron" else cron_expr, schedule_id),
        )

    conn.execute(
        """UPDATE users
           SET selected_ai_provider = ?
           WHERE selected_ai_provider IS NULL OR selected_ai_provider = ''""",
        (DEFAULT_PROVIDER,),
    )


def _cleanup_unsupported_provider_rows(conn: sqlite3.Connection) -> None:
    """Soft-delete sessions for unsupported providers such as legacy Gemini rows."""
    placeholders = ",".join("?" for _ in SUPPORTED_PROVIDERS)
    unsupported = conn.execute(
        f"SELECT id FROM sessions WHERE ai_provider NOT IN ({placeholders}) AND deleted = 0",
        tuple(SUPPORTED_PROVIDERS),
    ).fetchall()
    if not unsupported:
        return

    ids = [row[0] for row in unsupported]
    id_placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE sessions SET deleted = 1 WHERE id IN ({id_placeholders})",
        ids,
    )
    conn.execute(
        f"UPDATE users SET current_session_id = NULL WHERE current_session_id IN ({id_placeholders})",
        ids,
    )
    conn.execute(
        f"UPDATE users SET previous_session_id = NULL WHERE previous_session_id IN ({id_placeholders})",
        ids,
    )
    conn.execute(
        f"""UPDATE user_provider_state
            SET current_session_id = NULL
            WHERE current_session_id IN ({id_placeholders})""",
        ids,
    )
    conn.execute(
        f"""UPDATE user_provider_state
            SET previous_session_id = NULL
            WHERE previous_session_id IN ({id_placeholders})""",
        ids,
    )


def _initialize_provider_state(conn: sqlite3.Connection) -> None:
    """Initialize provider-specific current/previous session rows from legacy user state."""
    users = conn.execute("SELECT id, current_session_id, previous_session_id FROM users").fetchall()
    for user_id, current_session_id, previous_session_id in users:
        provider = DEFAULT_PROVIDER
        if current_session_id:
            row = conn.execute(
                "SELECT ai_provider, deleted FROM sessions WHERE id = ?",
                (current_session_id,),
            ).fetchone()
            if row and not row[1] and row[0] in SUPPORTED_PROVIDERS:
                provider = row[0]
            else:
                current_session_id = None

        if previous_session_id:
            row = conn.execute(
                "SELECT ai_provider, deleted FROM sessions WHERE id = ?",
                (previous_session_id,),
            ).fetchone()
            if not row or row[1] or row[0] != provider:
                previous_session_id = None

        conn.execute(
            "INSERT OR IGNORE INTO user_provider_state (user_id, ai_provider) VALUES (?, ?)",
            (user_id, DEFAULT_PROVIDER),
        )
        conn.execute(
            """INSERT INTO user_provider_state (user_id, ai_provider, current_session_id, previous_session_id, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(user_id, ai_provider) DO UPDATE SET
                   current_session_id = COALESCE(user_provider_state.current_session_id, excluded.current_session_id),
                   previous_session_id = COALESCE(user_provider_state.previous_session_id, excluded.previous_session_id),
                   updated_at = datetime('now')""",
            (user_id, provider, current_session_id, previous_session_id),
        )


def reset_connection() -> None:
    """Reset connection for testing purposes."""
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
        _connection = None
