"""Unit tests for short-lived pending request storage."""

import time
from unittest.mock import MagicMock

from src.bot.runtime import PendingRequestStore


class TestPendingRequestStore:
    """PendingRequestStore tests."""

    def test_save_writes_memory_and_repository(self):
        repo = MagicMock()
        store = PendingRequestStore(repo)

        data = {
            "user_id": "12345",
            "chat_id": 12345,
            "message": "hello",
            "model": "sonnet",
            "is_new_session": False,
            "workspace_path": "",
            "current_session_id": "session-1",
            "created_at": 123.0,
        }

        store.save("pending-1", data)

        assert store.data["pending-1"] == data
        repo.save_pending_message.assert_called_once_with(
            key="pending-1",
            user_id="12345",
            chat_id=12345,
            message="hello",
            model="sonnet",
            is_new_session=False,
            workspace_path="",
            current_session_id="session-1",
            created_at=123.0,
        )

    def test_restore_filters_out_expired_rows(self):
        repo = MagicMock()
        now = time.time()
        repo.get_all_pending_messages.return_value = {
            "fresh": {"created_at": now - 10, "message": "fresh"},
            "expired": {"created_at": now - 400, "message": "expired"},
        }
        store = PendingRequestStore(repo, ttl_seconds=300)

        restored = store.restore()

        assert restored == 1
        assert "fresh" in store.data
        assert "expired" not in store.data
        repo.clear_expired_pending_messages.assert_called_once_with(ttl_seconds=300)
