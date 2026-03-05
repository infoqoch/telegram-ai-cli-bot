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
    schema_path = Path(__file__).parent / "schema.sql"
    init_schema(conn, schema_path)

    _repository = Repository(conn)

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
