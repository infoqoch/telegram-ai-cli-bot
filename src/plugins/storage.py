"""Bounded persistence interfaces exposed to plugins."""

from __future__ import annotations

from typing import Optional, Protocol

from src.repository.repository import (
    Diary,
    Memo,
    QuestionBank,
    QuestionBankAttempt,
    QuestionBankOption,
    QuestionBankQuestion,
    QuestionBankScheduleConfig,
    Todo,
    WeatherLocation,
)


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


class DiaryStore(Protocol):
    """Diary persistence contract for plugins."""

    def add(self, chat_id: int, date: str, content: str) -> Diary:
        """Create one diary entry."""

    def get(self, diary_id: int) -> Optional[Diary]:
        """Return one diary by id."""

    def get_by_date(self, chat_id: int, date: str) -> Optional[Diary]:
        """Return the diary entry for a specific date."""

    def update(self, diary_id: int, content: str) -> bool:
        """Update diary content."""

    def delete(self, diary_id: int) -> bool:
        """Delete one diary entry."""

    def count_by_chat(self, chat_id: int) -> int:
        """Return total diary count for one chat."""

    def list_by_month(self, chat_id: int, year: int, month: int) -> list[Diary]:
        """List diary entries for one chat in a specific month, ordered by date descending."""


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


class QuestionBankStore(Protocol):
    """Question bank persistence contract for plugins."""

    def ensure_default_bank(self, chat_id: int) -> QuestionBank:
        """Return the default bank for one chat, creating it if needed."""

    def list_banks(self, chat_id: int) -> list[QuestionBank]:
        """List active banks for one chat."""

    def get_bank(self, bank_id: int, chat_id: int) -> Optional[QuestionBank]:
        """Return one active bank owned by a chat."""

    def stats(self, chat_id: int, bank_id: Optional[int] = None) -> dict[str, int]:
        """Return aggregate question/attempt stats for one chat or one bank."""

    def get_question(self, question_id: int, chat_id: int) -> Optional[QuestionBankQuestion]:
        """Return one active question owned by a chat."""

    def get_options(self, question_id: int) -> list[QuestionBankOption]:
        """Return multiple-choice options for one question."""

    def pick_question(
        self,
        chat_id: int,
        *,
        bank_id: Optional[int] = None,
        wrong_only: bool = False,
    ) -> Optional[QuestionBankQuestion]:
        """Pick one active question for practice."""

    def add_attempt(
        self,
        *,
        chat_id: int,
        question_id: int,
        answer_text: str,
        selected_option_no: Optional[int] = None,
        is_correct: Optional[bool] = None,
        score: Optional[float] = None,
        feedback: str = "",
        ai_status: str = "not_needed",
        ai_model: Optional[str] = None,
        ai_raw_response: Optional[str] = None,
    ) -> QuestionBankAttempt:
        """Persist one answer attempt."""

    def get_attempt(self, attempt_id: int, chat_id: int) -> Optional[QuestionBankAttempt]:
        """Return one attempt owned by a chat."""

    def recent_wrong_attempts(
        self,
        chat_id: int,
        limit: int = 10,
        bank_id: Optional[int] = None,
    ) -> list[QuestionBankAttempt]:
        """Return recent wrong attempts."""

    def save_schedule_config(
        self,
        *,
        schedule_id: str,
        chat_id: int,
        scope_type: str,
        bank_id: Optional[int] = None,
        question_count: int = 1,
    ) -> QuestionBankScheduleConfig:
        """Create or replace one question-bank schedule config."""

    def get_schedule_config(self, schedule_id: str, chat_id: int) -> Optional[QuestionBankScheduleConfig]:
        """Return one schedule config owned by a chat."""
