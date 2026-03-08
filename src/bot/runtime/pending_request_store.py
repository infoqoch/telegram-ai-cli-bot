"""Persistent storage for short-lived session conflict requests."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional

from src.logging_config import logger

if TYPE_CHECKING:
    from src.repository import Repository


class PendingRequestStore:
    """Own in-memory + DB-backed storage for temporary pending requests."""

    def __init__(self, repo: Optional["Repository"], ttl_seconds: int = 300):
        self._repo = repo
        self._ttl_seconds = ttl_seconds
        self.data: dict[str, dict[str, Any]] = {}

    def save(self, key: str, data: dict[str, Any]) -> None:
        """Persist a pending request to memory and SQLite."""
        self.data[key] = data
        if not self._repo:
            return

        self._repo.save_pending_message(
            key=key,
            user_id=data["user_id"],
            chat_id=data["chat_id"],
            message=data["message"],
            model=data.get("model", ""),
            is_new_session=data.get("is_new_session", False),
            workspace_path=data.get("workspace_path", ""),
            current_session_id=data.get("current_session_id", ""),
            created_at=data.get("created_at", time.time()),
        )

    def delete(self, key: str) -> None:
        """Delete a pending request from memory and SQLite."""
        self.data.pop(key, None)
        if self._repo:
            self._repo.delete_pending_message(key)

    def restore(self) -> int:
        """Restore non-expired pending requests from SQLite into memory."""
        if not self._repo:
            return 0

        self._repo.clear_expired_pending_messages(ttl_seconds=self._ttl_seconds)
        all_pending = self._repo.get_all_pending_messages()
        now = time.time()
        restored = 0

        for key, data in all_pending.items():
            if now - data.get("created_at", 0) > self._ttl_seconds:
                continue
            self.data[key] = data
            restored += 1

        if restored:
            logger.info(f"DB에서 pending message {restored}개 복원")
        return restored
