"""Plugin-facing storage adapters backed by the repository."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.repository.repository import Memo, Repository, Todo, WeatherLocation


class RepositoryPluginDatabase:
    """Small database adapter used by the plugin runtime."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def executescript(self, schema: str) -> None:
        """Execute one plugin schema script."""
        if not schema:
            return

        conn = getattr(self._repo, "_conn", None)
        if conn is None:
            raise RuntimeError("Repository connection is unavailable for plugin schema execution")

        conn.executescript(schema)
        conn.commit()


class RepositoryMemoStore:
    """Memo store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def add(self, chat_id: int, content: str) -> "Memo":
        return self._repo.add_memo(chat_id, content)

    def get(self, memo_id: int) -> Optional["Memo"]:
        return self._repo.get_memo(memo_id)

    def delete(self, memo_id: int) -> bool:
        return self._repo.delete_memo(memo_id)

    def list_by_chat(self, chat_id: int) -> list["Memo"]:
        return self._repo.list_memos(chat_id)

    def clear_by_chat(self, chat_id: int) -> int:
        return self._repo.clear_memos(chat_id)


class RepositoryTodoStore:
    """Todo store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def add(self, chat_id: int, date: str, text: str) -> "Todo":
        return self._repo.add_todo(chat_id, date, text)

    def get(self, todo_id: int) -> Optional["Todo"]:
        return self._repo.get_todo(todo_id)

    def toggle(self, todo_id: int) -> Optional[bool]:
        return self._repo.toggle_todo(todo_id)

    def delete(self, todo_id: int) -> bool:
        return self._repo.delete_todo(todo_id)

    def list_by_date(self, chat_id: int, date: str) -> list["Todo"]:
        return self._repo.list_todos_by_date(chat_id, date)

    def clear_by_date(self, chat_id: int, date: str) -> int:
        return self._repo.clear_todos_by_date(chat_id, date)

    def mark_done(self, todo_id: int, done: bool = True) -> bool:
        return self._repo.mark_todo_done(todo_id, done)

    def pending_for_date(self, chat_id: int, date: str) -> list["Todo"]:
        return self._repo.get_pending_todos(chat_id, date)

    def move_to_date(self, todo_ids: list[int], new_date: str) -> int:
        return self._repo.move_todos_to_date(todo_ids, new_date)

    def by_date_range(
        self,
        chat_id: int,
        start_date: str,
        end_date: str,
    ) -> dict[str, list["Todo"]]:
        return self._repo.get_todos_by_date_range(chat_id, start_date, end_date)

    def stats_for_date(self, chat_id: int, date: str) -> dict[str, int]:
        return self._repo.get_todo_stats(chat_id, date)


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
    ) -> "WeatherLocation":
        return self._repo.set_weather_location(
            chat_id=chat_id,
            name=name,
            lat=lat,
            lon=lon,
            country=country,
        )

    def get(self, chat_id: int) -> Optional["WeatherLocation"]:
        return self._repo.get_weather_location(chat_id)

    def delete(self, chat_id: int) -> bool:
        return self._repo.delete_weather_location(chat_id)
