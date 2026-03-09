"""Bounded persistence interfaces exposed to plugins."""

from __future__ import annotations

from typing import Optional, Protocol

from src.repository.repository import Memo, Todo, WeatherLocation


class PluginDatabase(Protocol):
    """Minimal database surface available to the plugin runtime."""

    def executescript(self, schema: str) -> None:
        """Execute one plugin-owned schema or migration script."""


class MemoStore(Protocol):
    """Memo persistence contract for plugins."""

    def add(self, chat_id: int, content: str) -> Memo:
        """Create one memo."""

    def get(self, memo_id: int) -> Optional[Memo]:
        """Return one memo by id."""

    def delete(self, memo_id: int) -> bool:
        """Delete one memo."""

    def list_by_chat(self, chat_id: int) -> list[Memo]:
        """List memos for one chat."""

    def clear_by_chat(self, chat_id: int) -> int:
        """Delete all memos for one chat."""


class TodoStore(Protocol):
    """Todo persistence contract for plugins."""

    def add(self, chat_id: int, date: str, text: str) -> Todo:
        """Create one todo item."""

    def get(self, todo_id: int) -> Optional[Todo]:
        """Return one todo by id."""

    def toggle(self, todo_id: int) -> Optional[bool]:
        """Toggle one todo item and return the new state."""

    def delete(self, todo_id: int) -> bool:
        """Delete one todo item."""

    def list_by_date(self, chat_id: int, date: str) -> list[Todo]:
        """List todos for one date."""

    def clear_by_date(self, chat_id: int, date: str) -> int:
        """Delete all todos for one date."""

    def mark_done(self, todo_id: int, done: bool = True) -> bool:
        """Mark one todo done or undone."""

    def pending_for_date(self, chat_id: int, date: str) -> list[Todo]:
        """Return incomplete todos for one date."""

    def move_to_date(self, todo_ids: list[int], new_date: str) -> int:
        """Move todos to another date."""

    def by_date_range(
        self,
        chat_id: int,
        start_date: str,
        end_date: str,
    ) -> dict[str, list[Todo]]:
        """Return todos grouped by date for a date range."""

    def stats_for_date(self, chat_id: int, date: str) -> dict[str, int]:
        """Return aggregate todo stats for one date."""


class WeatherLocationStore(Protocol):
    """Weather location persistence contract for plugins."""

    def set(
        self,
        chat_id: int,
        name: str,
        lat: float,
        lon: float,
        country: Optional[str] = None,
    ) -> WeatherLocation:
        """Persist one weather location for a chat."""

    def get(self, chat_id: int) -> Optional[WeatherLocation]:
        """Return the saved weather location for one chat."""

    def delete(self, chat_id: int) -> bool:
        """Delete the saved weather location for one chat."""
