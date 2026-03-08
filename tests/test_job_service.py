"""Detached job execution service tests."""

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.claude.client import ChatResponse
from src.repository.database import init_schema
from src.repository.repository import Repository
from src.services.job_service import JobService
from src.services.session_service import SessionService


@pytest.fixture
def repo(tmp_path):
    """Temporary repository fixture."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    schema_path = Path(__file__).parent.parent / "src" / "repository" / "schema.sql"
    init_schema(conn, schema_path)

    return Repository(conn)


@pytest.fixture
def session_service(repo):
    """Session service backed by the temp repository."""
    return SessionService(repo, session_timeout_hours=24)


@pytest.mark.asyncio
async def test_run_job_completes_message_and_releases_lock(repo, session_service):
    """Detached worker completes a job and releases its session lock."""
    session_service.create_session("12345", "sess1", model="sonnet", name="테스트")
    job_id = repo.enqueue_message(
        chat_id=12345,
        session_id="sess1",
        request="질문",
        model="sonnet",
    )
    repo.reserve_session_lock("sess1", job_id)

    claude = MagicMock()
    claude.chat = AsyncMock(return_value=ChatResponse(text="응답", error=None, session_id="sess1"))

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()

    service = JobService(
        repo=repo,
        session_service=session_service,
        claude_client=claude,
        telegram_token="test-token",
    )

    with patch("src.services.job_service.Bot", return_value=fake_bot):
        result = await service.run_job(job_id)

    assert result is True
    saved = repo.get_message_log(job_id)
    assert saved["processed"] == 2
    assert saved["response"] == "응답"
    assert repo.get_session_lock("sess1") is None
    assert fake_bot.send_message.called


@pytest.mark.asyncio
async def test_run_job_drains_persistent_queue(repo, session_service):
    """Detached worker keeps processing queued messages in the same session."""
    session_service.create_session("12345", "sess1", model="sonnet", name="테스트")
    first_job_id = repo.enqueue_message(
        chat_id=12345,
        session_id="sess1",
        request="첫 질문",
        model="sonnet",
    )
    repo.reserve_session_lock("sess1", first_job_id)
    repo.save_queued_message(
        session_id="sess1",
        user_id="12345",
        chat_id=12345,
        message="두 번째 질문",
        model="sonnet",
        is_new_session=False,
    )

    claude = MagicMock()
    claude.chat = AsyncMock(
        side_effect=[
            ChatResponse(text="첫 응답", error=None, session_id="sess1"),
            ChatResponse(text="두 응답", error=None, session_id="sess1"),
        ]
    )

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()

    original_rebind = repo.rebind_session_lock
    repo.rebind_session_lock = MagicMock(wraps=original_rebind)

    service = JobService(
        repo=repo,
        session_service=session_service,
        claude_client=claude,
        telegram_token="test-token",
    )

    with patch("src.services.job_service.Bot", return_value=fake_bot):
        result = await service.run_job(first_job_id)

    assert result is True
    assert claude.chat.await_count == 2
    assert repo.rebind_session_lock.call_count == 1
    assert repo.get_session_lock("sess1") is None
    assert repo.get_queued_messages_by_session("sess1") == []

    rows = repo._conn.execute(
        "SELECT processed, response FROM message_log WHERE session_id = ? ORDER BY id ASC",
        ("sess1",),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["processed"] == 2
    assert rows[1]["processed"] == 2


@pytest.mark.asyncio
async def test_run_job_marks_watchdog_timeout_without_completion_notice(repo, session_service):
    """Detached watchdog timeout stops the job and stores a timeout state."""
    session_service.create_session("12345", "sess1", model="sonnet", name="테스트")
    job_id = repo.enqueue_message(
        chat_id=12345,
        session_id="sess1",
        request="오래 걸리는 질문",
        model="sonnet",
    )
    repo.reserve_session_lock("sess1", job_id)

    async def hang(*_args, **_kwargs):
        await asyncio.sleep(3600)

    claude = MagicMock()
    claude.chat = AsyncMock(side_effect=hang)

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()

    service = JobService(
        repo=repo,
        session_service=session_service,
        claude_client=claude,
        telegram_token="test-token",
    )

    with (
        patch("src.services.job_service.Bot", return_value=fake_bot),
        patch("src.services.job_service.TASK_TIMEOUT_SECONDS", 1),
    ):
        result = await service.run_job(job_id)

    assert result is True
    saved = repo.get_message_log(job_id)
    assert saved["processed"] == 2
    assert saved["error"] == "watchdog_timeout"
    assert "Task exceeded 1 second and was stopped" in saved["response"]
    assert repo.get_session_lock("sess1") is None

    sent_texts = [call.kwargs["text"] for call in fake_bot.send_message.await_args_list]
    assert any("Task exceeded 1 second and was stopped" in text for text in sent_texts)
    assert not any("Task complete!" in text for text in sent_texts)


@pytest.mark.asyncio
async def test_run_job_escapes_session_and_message_metadata(repo, session_service):
    """Detached response envelope escapes user-controlled metadata for Telegram HTML."""
    session_service.create_session("12345", "sess1", model="sonnet", name="unsafe <tag>")
    job_id = repo.enqueue_message(
        chat_id=12345,
        session_id="sess1",
        request="질문 </code><i>x</i>",
        model="sonnet",
    )
    repo.reserve_session_lock("sess1", job_id)

    claude = MagicMock()
    claude.chat = AsyncMock(return_value=ChatResponse(text="응답", error=None, session_id="sess1"))

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()

    service = JobService(
        repo=repo,
        session_service=session_service,
        claude_client=claude,
        telegram_token="test-token",
    )

    with patch("src.services.job_service.Bot", return_value=fake_bot):
        result = await service.run_job(job_id)

    assert result is True
    sent_text = fake_bot.send_message.await_args_list[0].kwargs["text"]
    assert "unsafe &lt;tag&gt;" in sent_text
    assert "&lt;/code&gt;&lt;i&gt;x&lt;/i&gt;" in sent_text
    assert "unsafe <tag>" not in sent_text
    assert "</code><i>x</i>" not in sent_text
