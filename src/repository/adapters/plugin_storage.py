"""Plugin-facing storage adapters backed by the repository."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from src.repository.repository import Diary, Memo, Todo, WeatherLocation

if TYPE_CHECKING:
    from src.repository.repository import Repository


def _now_utc() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _require_conn(repo: "Repository") -> sqlite3.Connection:
    """Return the live SQLite connection from the repository."""
    conn = getattr(repo, "_conn", None)
    if conn is None:
        raise RuntimeError("Repository connection is unavailable for plugin storage")
    return conn


def _row_to_todo(row: sqlite3.Row) -> Todo:
    """Convert one SQLite row to the shared Todo dataclass."""
    return Todo(
        id=row["id"],
        chat_id=row["chat_id"],
        date=row["date"],
        slot=row["slot"],
        text=row["text"],
        done=bool(row["done"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class RepositoryPluginDatabase:
    """Small database adapter used by the plugin runtime."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def executescript(self, schema: str) -> None:
        """Execute one plugin schema script."""
        if not schema:
            return

        conn = _require_conn(self._repo)
        conn.executescript(schema)
        conn.commit()


class RepositoryMemoStore:
    """Memo store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def add(self, chat_id: int, content: str) -> Memo:
        now = _now_utc()
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "INSERT INTO memos (chat_id, content, created_at) VALUES (?, ?, ?)",
            (chat_id, content, now),
        )
        conn.commit()
        return Memo(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            content=content,
            created_at=now,
        )

    def get(self, memo_id: int) -> Optional[Memo]:
        conn = _require_conn(self._repo)
        row = conn.execute("SELECT * FROM memos WHERE id = ?", (memo_id,)).fetchone()
        if not row:
            return None
        return Memo(
            id=row["id"],
            chat_id=row["chat_id"],
            content=row["content"],
            created_at=row["created_at"],
        )

    def delete(self, memo_id: int) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute("DELETE FROM memos WHERE id = ?", (memo_id,))
        conn.commit()
        return cursor.rowcount > 0

    def list_by_chat(self, chat_id: int) -> list[Memo]:
        conn = _require_conn(self._repo)
        rows = conn.execute(
            "SELECT * FROM memos WHERE chat_id = ? ORDER BY created_at DESC",
            (chat_id,),
        ).fetchall()
        return [
            Memo(
                id=row["id"],
                chat_id=row["chat_id"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def clear_by_chat(self, chat_id: int) -> int:
        conn = _require_conn(self._repo)
        cursor = conn.execute("DELETE FROM memos WHERE chat_id = ?", (chat_id,))
        conn.commit()
        return cursor.rowcount


class RepositoryTodoStore:
    """Todo store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def add(self, chat_id: int, date: str, text: str) -> Todo:
        now = _now_utc()
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            """INSERT INTO todos (chat_id, date, slot, text, created_at, updated_at)
               VALUES (?, ?, 'default', ?, ?, ?)""",
            (chat_id, date, text, now, now),
        )
        conn.commit()
        return Todo(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            date=date,
            slot="default",
            text=text,
            done=False,
            created_at=now,
            updated_at=now,
        )

    def get(self, todo_id: int) -> Optional[Todo]:
        conn = _require_conn(self._repo)
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        return _row_to_todo(row) if row else None

    def toggle(self, todo_id: int) -> Optional[bool]:
        todo = self.get(todo_id)
        if not todo:
            return None

        new_state = not todo.done
        conn = _require_conn(self._repo)
        conn.execute("UPDATE todos SET done = ? WHERE id = ?", (int(new_state), todo_id))
        conn.commit()
        return new_state

    def delete(self, todo_id: int) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        conn.commit()
        return cursor.rowcount > 0

    def list_by_date(self, chat_id: int, date: str) -> list[Todo]:
        conn = _require_conn(self._repo)
        rows = conn.execute(
            "SELECT * FROM todos WHERE chat_id = ? AND date = ? ORDER BY id",
            (chat_id, date),
        ).fetchall()
        return [_row_to_todo(row) for row in rows]

    def clear_by_date(self, chat_id: int, date: str) -> int:
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "DELETE FROM todos WHERE chat_id = ? AND date = ?",
            (chat_id, date),
        )
        conn.commit()
        return cursor.rowcount

    def mark_done(self, todo_id: int, done: bool = True) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "UPDATE todos SET done = ? WHERE id = ?",
            (int(done), todo_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def pending_for_date(self, chat_id: int, date: str) -> list[Todo]:
        conn = _require_conn(self._repo)
        rows = conn.execute(
            "SELECT * FROM todos WHERE chat_id = ? AND date = ? AND done = 0 ORDER BY id",
            (chat_id, date),
        ).fetchall()
        return [_row_to_todo(row) for row in rows]

    def move_to_date(self, todo_ids: list[int], new_date: str) -> int:
        if not todo_ids:
            return 0
        conn = _require_conn(self._repo)
        placeholders = ",".join("?" * len(todo_ids))
        cursor = conn.execute(
            f"UPDATE todos SET date = ? WHERE id IN ({placeholders})",
            [new_date] + todo_ids,
        )
        conn.commit()
        return cursor.rowcount

    def by_date_range(
        self,
        chat_id: int,
        start_date: str,
        end_date: str,
    ) -> dict[str, list[Todo]]:
        conn = _require_conn(self._repo)
        rows = conn.execute(
            """SELECT * FROM todos
               WHERE chat_id = ? AND date >= ? AND date <= ?
               ORDER BY date, id""",
            (chat_id, start_date, end_date),
        ).fetchall()
        result: dict[str, list[Todo]] = {}
        for row in rows:
            todo = _row_to_todo(row)
            result.setdefault(todo.date, []).append(todo)
        return result

    def stats_for_date(self, chat_id: int, date: str) -> dict[str, int]:
        conn = _require_conn(self._repo)
        row = conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) as done,
                SUM(CASE WHEN done = 0 THEN 1 ELSE 0 END) as pending
               FROM todos WHERE chat_id = ? AND date = ?""",
            (chat_id, date),
        ).fetchone()
        return {
            "total": row["total"] or 0,
            "done": row["done"] or 0,
            "pending": row["pending"] or 0,
        }


def _row_to_diary(row: sqlite3.Row) -> Diary:
    """Convert one SQLite row to the shared Diary dataclass."""
    return Diary(
        id=row["id"],
        chat_id=row["chat_id"],
        date=row["date"],
        content=row["content"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class RepositoryDiaryStore:
    """Diary store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def add(self, chat_id: int, date: str, content: str) -> Diary:
        now = _now_utc()
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "INSERT INTO diaries (chat_id, date, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, date, content, now, now),
        )
        conn.commit()
        return Diary(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            date=date,
            content=content,
            created_at=now,
            updated_at=now,
        )

    def get(self, diary_id: int) -> Optional[Diary]:
        conn = _require_conn(self._repo)
        row = conn.execute("SELECT * FROM diaries WHERE id = ?", (diary_id,)).fetchone()
        return _row_to_diary(row) if row else None

    def get_by_date(self, chat_id: int, date: str) -> Optional[Diary]:
        conn = _require_conn(self._repo)
        row = conn.execute(
            "SELECT * FROM diaries WHERE chat_id = ? AND date = ?",
            (chat_id, date),
        ).fetchone()
        return _row_to_diary(row) if row else None

    def update(self, diary_id: int, content: str) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "UPDATE diaries SET content = ? WHERE id = ?",
            (content, diary_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete(self, diary_id: int) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute("DELETE FROM diaries WHERE id = ?", (diary_id,))
        conn.commit()
        return cursor.rowcount > 0

    def list_by_chat(self, chat_id: int, limit: int = 10, offset: int = 0) -> list[Diary]:
        conn = _require_conn(self._repo)
        rows = conn.execute(
            "SELECT * FROM diaries WHERE chat_id = ? ORDER BY date DESC LIMIT ? OFFSET ?",
            (chat_id, limit, offset),
        ).fetchall()
        return [_row_to_diary(row) for row in rows]

    def count_by_chat(self, chat_id: int) -> int:
        conn = _require_conn(self._repo)
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM diaries WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        return row["cnt"] if row else 0

    def list_by_month(self, chat_id: int, year: int, month: int) -> list[Diary]:
        start = f"{year:04d}-{month:02d}-01"
        if month == 12:
            end = f"{year + 1:04d}-01-01"
        else:
            end = f"{year:04d}-{month + 1:02d}-01"
        conn = _require_conn(self._repo)
        rows = conn.execute(
            "SELECT * FROM diaries WHERE chat_id = ? AND date >= ? AND date < ? ORDER BY date DESC",
            (chat_id, start, end),
        ).fetchall()
        return [_row_to_diary(row) for row in rows]


class RepositoryWeatherLocationStore:
    """Weather location store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def set(
        self,
        chat_id: int,
        name: str,
        lat: float,
        lon: float,
        country: Optional[str] = None,
    ) -> WeatherLocation:
        conn = _require_conn(self._repo)
        conn.execute(
            """INSERT OR REPLACE INTO weather_locations (chat_id, name, country, lat, lon, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chat_id, name, country, lat, lon, _now_utc()),
        )
        conn.commit()
        return WeatherLocation(
            chat_id=chat_id,
            name=name,
            country=country,
            lat=lat,
            lon=lon,
        )

    def get(self, chat_id: int) -> Optional[WeatherLocation]:
        conn = _require_conn(self._repo)
        row = conn.execute(
            "SELECT * FROM weather_locations WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if not row:
            return None
        return WeatherLocation(
            chat_id=row["chat_id"],
            name=row["name"],
            country=row["country"],
            lat=row["lat"],
            lon=row["lon"],
        )

    def delete(self, chat_id: int) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "DELETE FROM weather_locations WHERE chat_id = ?",
            (chat_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
