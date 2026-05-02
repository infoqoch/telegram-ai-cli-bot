"""Detached job execution service tests."""

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.claude.client import ChatError, ChatResponse
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
    assert saved["delivery_text"] is not None
    assert saved["delivery_status"] == "sent"
    assert saved["delivery_attempts"] == 1
    assert saved["delivery_error"] is None
    assert saved["delivered_at"] is not None
    assert repo.get_session_lock("sess1") is None
    assert fake_bot.send_message.called
    sent_call = fake_bot.send_message.await_args_list[-1]
    markup = sent_call.kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert callbacks == ["resp:switch:sess1"]
    assert "/s_sess1" not in sent_call.kwargs["text"]
    assert "/h_sess1" not in sent_call.kwargs["text"]


@pytest.mark.asyncio
async def test_run_job_merges_extra_delivery_buttons(repo, session_service):
    """Detached AI delivery merges plugin-provided buttons with the default session button."""
    session_service.create_session("12345", "sess1", model="sonnet", name="테스트")
    job_id = repo.enqueue_message(
        chat_id=12345,
        session_id="sess1",
        request="질문",
        model="sonnet",
    )
    repo.set_message_delivery_markup(
        job_id,
        [[{"text": "➡️ 계속 문제 풀기", "callback_data": "qb:practice:b2"}]],
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
    sent_call = fake_bot.send_message.await_args_list[-1]
    markup = sent_call.kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
    assert callbacks == ["qb:practice:b2", "resp:switch:sess1"]


@pytest.mark.asyncio
async def test_run_job_uses_plugin_completion_hook_for_final_delivery(repo, session_service):
    """A plugin completion hook can replace the final delivered body with a plugin-rendered card."""
    session_service.create_session("12345", "sess1", model="sonnet", name="Question Bank Grading")
    job_id = repo.enqueue_message(
        chat_id=12345,
        session_id="sess1",
        request="채점 프롬프트",
        model="sonnet",
    )
    repo.set_message_completion_hook(
        job_id,
        {
            "plugin_name": "question_bank",
            "action": "render_attempt_result",
            "payload": {"attempt_id": 77, "scope_token": "wb2"},
        },
    )
    repo.reserve_session_lock("sess1", job_id)

    claude = MagicMock()
    claude.chat = AsyncMock(return_value=ChatResponse(text="원본 AI 응답", error=None, session_id="sess1"))

    plugin = MagicMock()
    plugin.handle_ai_completion = AsyncMock(
        return_value={
            "text": "❌ <b>오답</b>\n\n문제 #77",
            "delivery_buttons": [[{"text": "➡️ 계속 문제 풀기", "callback_data": "qb:practice:wb2"}]],
        }
    )
    plugin_loader = MagicMock()
    plugin_loader.get_plugin_by_name.return_value = plugin

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()

    service = JobService(
        repo=repo,
        session_service=session_service,
        claude_client=claude,
        telegram_token="test-token",
        plugin_loader=plugin_loader,
    )

    with patch("src.services.job_service.Bot", return_value=fake_bot):
        result = await service.run_job(job_id)

    assert result is True
    sent_call = fake_bot.send_message.await_args_list[-1]
    sent_text = sent_call.kwargs["text"]
    assert "❌ <b>오답</b>" in sent_text
    assert "[Claude" not in sent_text
    callbacks = [btn.callback_data for row in sent_call.kwargs["reply_markup"].inline_keyboard for btn in row]
    assert callbacks == ["qb:practice:wb2", "resp:switch:sess1"]
    saved = repo.get_message_log(job_id)
    assert saved["response"] == "원본 AI 응답"
    assert "문제 #77" in saved["delivery_text"]


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
        "SELECT processed, response, delivery_status FROM message_log WHERE session_id = ? ORDER BY id ASC",
        ("sess1",),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["processed"] == 2
    assert rows[1]["processed"] == 2
    assert rows[0]["delivery_status"] == "sent"
    assert rows[1]["delivery_status"] == "sent"


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
    assert saved["delivery_status"] == "sent"
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


@pytest.mark.asyncio
async def test_run_job_surfaces_usage_limit_message(repo, session_service):
    """Usage-limit failures should be delivered as a concrete user-facing message."""
    session_service.create_session("12345", "sess1", model="sonnet", name="테스트")
    job_id = repo.enqueue_message(
        chat_id=12345,
        session_id="sess1",
        request="질문",
        model="sonnet",
    )
    repo.reserve_session_lock("sess1", job_id)

    claude = MagicMock()
    claude.chat = AsyncMock(return_value=ChatResponse(
        text="You've hit your limit · resets 4pm (Asia/Seoul)",
        error=ChatError.USAGE_LIMIT,
        session_id="sess1",
    ))

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
    assert saved["error"] == "usage_limit"
    assert "You&#x27;ve hit your limit · resets 4pm (Asia/Seoul)" in saved["response"]
    sent_text = fake_bot.send_message.await_args_list[0].kwargs["text"]
    assert "You&#x27;ve hit your limit · resets 4pm (Asia/Seoul)" in sent_text


@pytest.mark.asyncio
async def test_run_job_preserves_response_when_delivery_fails(repo, session_service):
    """Generated responses survive Telegram delivery failures for later recovery."""
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
    fake_bot.send_message = AsyncMock(side_effect=[RuntimeError("bad html"), RuntimeError("Timed out")])

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
    assert saved["delivery_text"] is not None
    assert saved["delivery_status"] == "failed"
    assert saved["delivery_attempts"] == 2
    assert saved["delivery_error"] == "RuntimeError: Timed out"
    assert saved["delivered_at"] is None
    assert repo.get_session_lock("sess1") is None
