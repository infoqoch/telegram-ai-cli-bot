"""Delivery retry service tests."""

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import TimedOut

from src.network_guard import CircuitOpen
from src.repository.database import init_schema
from src.repository.repository import Repository
from src.services.delivery_retry_service import DeliveryRetryService


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    schema_path = Path(__file__).parent.parent / "src" / "repository" / "schema.sql"
    init_schema(conn, schema_path)
    return Repository(conn)


@pytest.mark.asyncio
async def test_retry_failed_deliveries_restores_inline_buttons(repo):
    repo._conn.execute(
        """INSERT INTO message_log
           (chat_id, session_id, model, request, request_at, processed, response,
            delivery_text, delivery_markup_json, delivery_status, delivery_attempts)
           VALUES (?, ?, ?, ?, ?, 2, ?, ?, ?, 'failed', 1)""",
        (
            12345,
            "sess1",
            "sonnet",
            "질문",
            repo._now(),
            "응답",
            "재전송 본문",
            '[['
            '{"text":"➡️ 계속 문제 풀기","callback_data":"qb:practice:all"},'
            '{"text":"💬 Session","callback_data":"resp:switch:sess1"}'
            ']]',
        ),
    )
    repo._conn.commit()

    bot = MagicMock()
    bot.send_message = AsyncMock()
    service = DeliveryRetryService(repo)

    result = await service.retry_failed_deliveries(bot)

    assert result == 1
    send_call = bot.send_message.await_args_list[-1]
    markup = send_call.kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert callbacks == ["qb:practice:all", "resp:switch:sess1"]


@pytest.mark.asyncio
async def test_retry_failed_deliveries_handles_schedule_delivery_log(repo):
    log_id = repo.insert_schedule_delivery_log(
        chat_id=12345,
        schedule_id="schedule-1",
        request="scheduled prompt",
        response="scheduled response",
        delivery_text="⏰ <b>Schedule</b>\n\nscheduled response",
        model="command",
    )
    repo.mark_message_delivery_failed(log_id, "telegram timeout")

    bot = MagicMock()
    bot.send_message = AsyncMock()
    service = DeliveryRetryService(repo)

    result = await service.retry_failed_deliveries(bot)

    assert result == 1
    row = repo.get_message_log(log_id)
    assert row["schedule_id"] == "schedule-1"
    assert row["processed"] == 2
    assert row["delivery_status"] == "sent"
    assert row["delivery_attempts"] == 1
    send_call = bot.send_message.await_args_list[-1]
    assert send_call.kwargs["chat_id"] == 12345
    assert "scheduled response" in send_call.kwargs["text"]


@pytest.mark.asyncio
async def test_retry_failed_deliveries_does_not_plain_fallback_on_network_error(repo):
    repo._conn.execute(
        """INSERT INTO message_log
           (chat_id, session_id, model, request, request_at, processed, response,
            delivery_text, delivery_status, delivery_attempts)
           VALUES (?, ?, ?, ?, ?, 2, ?, ?, 'failed', 1)""",
        (
            12345,
            "sess1",
            "sonnet",
            "질문",
            repo._now(),
            "응답",
            "재전송 본문",
        ),
    )
    repo._conn.commit()

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=TimedOut("Timed out"))
    service = DeliveryRetryService(repo)

    result = await service.retry_failed_deliveries(bot)

    assert result == 0
    assert bot.send_message.await_count == 1
    row = repo._conn.execute(
        "SELECT delivery_status, delivery_attempts, delivery_error FROM message_log"
    ).fetchone()
    assert row["delivery_status"] == "failed"
    assert row["delivery_attempts"] == 2
    assert "telegram:" in row["delivery_error"]


@pytest.mark.asyncio
async def test_retry_failed_deliveries_does_not_consume_attempt_when_circuit_open(repo):
    repo._conn.execute(
        """INSERT INTO message_log
           (chat_id, session_id, model, request, request_at, processed, response,
            delivery_text, delivery_status, delivery_attempts)
           VALUES (?, ?, ?, ?, ?, 2, ?, ?, 'failed', 9)""",
        (
            12345,
            "sess1",
            "sonnet",
            "질문",
            repo._now(),
            "응답",
            "재전송 본문",
        ),
    )
    repo._conn.commit()

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=CircuitOpen("telegram", "circuit open"))
    service = DeliveryRetryService(repo)

    result = await service.retry_failed_deliveries(bot)

    assert result == 0
    assert bot.send_message.await_count == 1
    row = repo._conn.execute(
        "SELECT delivery_status, delivery_attempts, delivery_error FROM message_log"
    ).fetchone()
    assert row["delivery_status"] == "failed"
    assert row["delivery_attempts"] == 9
    assert "circuit open" in row["delivery_error"]


@pytest.mark.asyncio
async def test_retry_failed_deliveries_abandons_after_actual_attempt_then_circuit_open(repo):
    repo._conn.execute(
        """INSERT INTO message_log
           (chat_id, session_id, model, request, request_at, processed, response,
            delivery_text, delivery_status, delivery_attempts)
           VALUES (?, ?, ?, ?, ?, 2, ?, ?, 'failed', 9)""",
        (
            12345,
            "sess1",
            "sonnet",
            "질문",
            repo._now(),
            "응답",
            "재전송 본문",
        ),
    )
    repo._conn.commit()

    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=[
            RuntimeError("bad html"),
            CircuitOpen("telegram", "circuit open"),
            CircuitOpen("telegram", "circuit open"),
        ]
    )
    service = DeliveryRetryService(repo)

    result = await service.retry_failed_deliveries(bot)

    assert result == 0
    row = repo._conn.execute(
        "SELECT delivery_status, delivery_attempts, delivery_error FROM message_log"
    ).fetchone()
    assert row["delivery_status"] == "abandoned"
    assert row["delivery_attempts"] == 10
    assert row["delivery_error"] == "Max retry attempts exceeded"
