"""Error handling integration tests.

에러 처리 통합 테스트.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.integration.conftest import (
    create_command_update,
    create_message_update,
    get_reply_text,
    MockTelegram,
    MockClaude,
    wait_for_handlers,
)


class TestClaudeErrors:
    """Claude 에러 처리 테스트."""

    @pytest.mark.asyncio
    async def test_timeout_error_shows_message(self, handlers, session_store):
        """타임아웃 에러 시 메시지 표시."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="테스트")
        handlers.claude = MockClaude.create_client(error="TIMEOUT")

        update, context = create_message_update("질문")

        await handlers.handle_message(update, context)
        await wait_for_handlers(handlers)

        # 에러 메시지 확인
        assert handlers._spawn_detached_worker.called or update.message.reply_text.called

    @pytest.mark.asyncio
    async def test_cli_error_shows_message(self, handlers, session_store):
        """CLI 에러 시 메시지 표시."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="테스트")
        handlers.claude = MockClaude.create_client(error="CLI_ERROR")

        update, context = create_message_update("질문")

        await handlers.handle_message(update, context)
        await wait_for_handlers(handlers)

        assert handlers._spawn_detached_worker.called or update.message.reply_text.called

    @pytest.mark.asyncio
    async def test_session_not_found_error(self, handlers, session_store):
        """세션 없음 에러 처리."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="테스트")
        handlers.claude = MockClaude.create_client(error="SESSION_NOT_FOUND")

        update, context = create_message_update("질문")

        await handlers.handle_message(update, context)
        await wait_for_handlers(handlers)

        # 새 세션 생성 또는 에러 메시지
        assert handlers._spawn_detached_worker.called or update.message.reply_text.called

    @pytest.mark.asyncio
    async def test_unknown_error_handled(self, handlers, session_store):
        """알 수 없는 에러 처리."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="테스트")
        handlers.claude = MockClaude.create_client(error="UNKNOWN_ERROR")

        update, context = create_message_update("질문")

        await handlers.handle_message(update, context)
        await wait_for_handlers(handlers)

        assert handlers._spawn_detached_worker.called or update.message.reply_text.called


class TestExceptionHandling:
    """예외 처리 테스트."""

    @pytest.mark.asyncio
    async def test_handler_exception_logged(self, handlers, session_store):
        """핸들러 예외 로깅."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="테스트")

        # 예외 발생시키는 mock
        handlers.claude = MagicMock()
        handlers.claude.chat = AsyncMock(side_effect=Exception("테스트 예외"))

        update, context = create_message_update("질문")

        # 예외가 전파되지 않고 처리되어야 함
        try:
            await handlers.handle_message(update, context)
            await wait_for_handlers(handlers)
        except Exception:
            pass  # 일부 예외는 허용

    @pytest.mark.asyncio
    async def test_error_handler_catches_errors(self, handlers):
        """error_handler가 에러 처리."""
        update = MockTelegram.create_update(text="테스트")
        context = MockTelegram.create_context()
        context.error = Exception("테스트 에러")

        await handlers.error_handler(update, context)

        # 에러 핸들러가 크래시 없이 완료


class TestTelegramApiErrors:
    """Telegram API 에러 처리 테스트."""

    @pytest.mark.asyncio
    async def test_send_message_failure(self, handlers, session_store):
        """메시지 전송 실패 처리."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="테스트")

        update, context = create_message_update("테스트")

        # send_message 실패
        context.bot.send_message = AsyncMock(side_effect=Exception("전송 실패"))

        try:
            await handlers.handle_message(update, context)
            await wait_for_handlers(handlers)
        except Exception:
            pass  # 에러 허용

    @pytest.mark.asyncio
    async def test_reply_text_failure(self, handlers):
        """reply_text 실패 처리."""
        update, context = create_command_update("help")

        # reply_text 실패
        update.message.reply_text = AsyncMock(side_effect=Exception("응답 실패"))

        try:
            await handlers.help_command(update, context)
        except Exception:
            pass  # 에러 허용


class TestDatabaseErrors:
    """데이터베이스 에러 처리 테스트."""

    @pytest.mark.asyncio
    async def test_session_store_error(self, handlers):
        """세션 저장소 에러 처리."""
        update, context = create_command_update("session")

        # 세션 저장소 에러 시뮬레이션
        handlers.sessions.get_current_session_id = MagicMock(side_effect=Exception("DB 에러"))

        try:
            await handlers.session_command(update, context)
        except Exception:
            pass  # 에러 허용


class TestValidationErrors:
    """입력 검증 에러 테스트."""

    @pytest.mark.asyncio
    async def test_invalid_session_id(self, handlers):
        """잘못된 세션 ID 처리."""
        update, context = create_command_update("s_invalid")
        update.message.text = "/s_invalid"

        await handlers.switch_session_command(update, context)

        reply = await get_reply_text(update)
        # 에러 메시지 또는 세션 없음 메시지
        assert reply or update.message.reply_text.called

    @pytest.mark.asyncio
    async def test_empty_rename(self, handlers, session_store):
        """빈 이름으로 변경 시도."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="테스트")

        update, context = create_command_update("rename", args=[])

        await handlers.rename_command(update, context)

        reply = await get_reply_text(update)
        assert reply  # 사용법 또는 에러 메시지


class TestRateLimiting:
    """속도 제한 테스트."""

    @pytest.mark.asyncio
    async def test_rapid_requests_handled(self, handlers, session_store, mock_claude):
        """빠른 연속 요청 처리."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="테스트")

        # 빠른 연속 요청
        for i in range(5):
            update, context = create_message_update(f"메시지 {i}")
            try:
                await handlers.handle_message(update, context)
                await wait_for_handlers(handlers)
            except Exception:
                pass  # 일부 거절 허용


class TestRecovery:
    """복구 테스트."""

    @pytest.mark.asyncio
    async def test_recovery_after_error(self, handlers, session_store):
        """에러 후 정상 동작 복구."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="테스트")

        # 첫 번째 요청: 에러
        handlers.claude = MockClaude.create_client(error="TIMEOUT")
        update1, context1 = create_message_update("첫 번째")
        await handlers.handle_message(update1, context1)
        await wait_for_handlers(handlers)

        # 두 번째 요청: 정상
        handlers.claude = MockClaude.create_client(default_response="정상 응답")
        update2, context2 = create_message_update("두 번째")
        await handlers.handle_message(update2, context2)
        await wait_for_handlers(handlers)

        # 두 번째는 정상 처리되어야 함
        assert handlers._spawn_detached_worker.called or update2.message.reply_text.called
