"""Telegram 핸들러 테스트.

BotHandlers 클래스의 핵심 기능 검증:
- 권한 검사
- 인증 검사
- 메시지 길이 제한
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers import BotHandlers
from src.bot.constants import MAX_MESSAGE_LENGTH


@pytest.fixture
def mock_session_service():
    """모의 세션 저장소."""
    store = MagicMock()
    store.get_current_session_info.return_value = "abc12345"
    store.get_history_count.return_value = 5
    store.get_current_session_id.return_value = None
    store.create_session.return_value = "new-session-id"
    return store


@pytest.fixture
def mock_claude_client():
    """모의 Claude 클라이언트."""
    client = MagicMock()
    client.chat = AsyncMock(return_value=("응답 텍스트", None, None))
    return client


@pytest.fixture
def mock_auth_manager():
    """모의 인증 관리자."""
    auth = MagicMock()
    auth.is_authenticated.return_value = True
    auth.get_remaining_minutes.return_value = 25
    return auth


@pytest.fixture
def handlers(mock_session_service, mock_claude_client, mock_auth_manager):
    """테스트용 핸들러 생성."""
    return BotHandlers(
        session_service=mock_session_service,
        claude_client=mock_claude_client,
        auth_manager=mock_auth_manager,
        require_auth=True,
        allowed_chat_ids=[12345],
    )


class TestBotHandlers:
    """BotHandlers 단위 테스트."""

    def test_is_authorized_allowed(self, handlers):
        """허용된 채팅 ID 확인."""
        assert handlers._is_authorized(12345) is True

    def test_is_authorized_not_allowed(self, handlers):
        """허용되지 않은 채팅 ID 확인."""
        assert handlers._is_authorized(99999) is False

    def test_is_authorized_empty_list(self, mock_session_service, mock_claude_client, mock_auth_manager):
        """빈 허용 목록은 모두 허용."""
        handlers = BotHandlers(
            session_service=mock_session_service,
            claude_client=mock_claude_client,
            auth_manager=mock_auth_manager,
            require_auth=True,
            allowed_chat_ids=[],
        )
        assert handlers._is_authorized(99999) is True

    def test_is_authenticated_required(self, handlers, mock_auth_manager):
        """인증 필수 시 인증 확인."""
        mock_auth_manager.is_authenticated.return_value = True
        assert handlers._is_authenticated("user123") is True

        mock_auth_manager.is_authenticated.return_value = False
        assert handlers._is_authenticated("user123") is False

    def test_is_authenticated_not_required(self, mock_session_service, mock_claude_client, mock_auth_manager):
        """인증 불필요 시 항상 True."""
        handlers = BotHandlers(
            session_service=mock_session_service,
            claude_client=mock_claude_client,
            auth_manager=mock_auth_manager,
            require_auth=False,
            allowed_chat_ids=[],
        )
        mock_auth_manager.is_authenticated.return_value = False
        assert handlers._is_authenticated("user123") is True

    def test_max_message_length_constant(self, handlers):
        """메시지 최대 길이 상수 확인."""
        assert MAX_MESSAGE_LENGTH == 4096

    @pytest.mark.asyncio
    async def test_start_unauthorized(self, handlers):
        """권한 없는 사용자의 /start 처리."""
        update = MagicMock()
        update.effective_chat.id = 99999  # 허용되지 않은 ID
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await handlers.start(update, context)

        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args[0][0]
        assert "Access denied" in call_args

    @pytest.mark.asyncio
    async def test_chatid_unauthorized(self, handlers):
        """권한 없는 사용자의 /chatid 처리."""
        update = MagicMock()
        update.effective_chat.id = 99999
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await handlers.chatid_command(update, context)

        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args[0][0]
        assert "Access denied" in call_args

    @pytest.mark.asyncio
    async def test_plugins_command_unauthenticated(self, handlers, mock_auth_manager):
        """미인증 사용자는 /plugins를 사용할 수 없다."""
        mock_auth_manager.is_authenticated.return_value = False

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        update.message.text = "/plugins"
        context = MagicMock()

        await handlers.plugins_command(update, context)

        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args[0][0]
        assert "Authentication required first" in call_args

    @pytest.mark.asyncio
    async def test_workspace_command_unauthorized(self, handlers):
        """권한 없는 사용자는 /workspace 를 사용할 수 없다."""
        update = MagicMock()
        update.effective_chat.id = 99999
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await handlers.workspace_command(update, context)

        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args[0][0]
        assert "Access denied" in call_args

    @pytest.mark.asyncio
    async def test_workspace_command_unauthenticated(self, handlers, mock_auth_manager):
        """미인증 사용자는 /workspace 를 사용할 수 없다."""
        mock_auth_manager.is_authenticated.return_value = False

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await handlers.workspace_command(update, context)

        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args[0][0]
        assert "Authentication required first" in call_args

    @pytest.mark.asyncio
    async def test_handle_message_unauthenticated(self, handlers, mock_auth_manager):
        """미인증 사용자의 메시지 처리."""
        mock_auth_manager.is_authenticated.return_value = False

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "Hello"
        update.message.reply_to_message = None
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await handlers.handle_message(update, context)

        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args[0][0]
        assert "Authentication required" in call_args

    @pytest.mark.asyncio
    async def test_handle_message_unauthenticated_skips_plugins(self, handlers, mock_auth_manager):
        """미인증 메시지는 플러그인 처리 전에 차단한다."""
        mock_auth_manager.is_authenticated.return_value = False
        handlers.plugins = MagicMock()
        handlers.plugins.process_message = AsyncMock()

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "메모"
        update.message.reply_to_message = None
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await handlers.handle_message(update, context)

        handlers.plugins.process_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_query_unauthorized(self, handlers):
        """권한 없는 콜백 쿼리는 즉시 차단한다."""
        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.data = "tasks:refresh"
        update.callback_query.message = MagicMock()
        update.callback_query.message.chat_id = 99999
        update.callback_query.answer = AsyncMock()
        context = MagicMock()

        await handlers.callback_query_handler(update, context)

        update.callback_query.answer.assert_awaited_once_with("⛔ Access denied.", show_alert=True)

    @pytest.mark.asyncio
    async def test_callback_query_unauthenticated(self, handlers, mock_auth_manager):
        """미인증 콜백 쿼리는 인증 안내를 반환한다."""
        mock_auth_manager.is_authenticated.return_value = False

        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.data = "tasks:refresh"
        update.callback_query.message = MagicMock()
        update.callback_query.message.chat_id = 12345
        update.callback_query.answer = AsyncMock()
        context = MagicMock()

        await handlers.callback_query_handler(update, context)

        update.callback_query.answer.assert_awaited_once_with(
            "🔒 Authentication required.\n/auth <key>",
            show_alert=True,
        )

    @pytest.mark.asyncio
    async def test_error_handler_generic_message(self, handlers):
        """에러 핸들러의 일반 메시지 응답 확인."""
        update = MagicMock()
        update.effective_chat.id = 12345
        context = MagicMock()
        context.error = Exception("Internal error details")
        context.bot.send_message = AsyncMock()

        await handlers.error_handler(update, context)

        # 사용자에게는 일반적인 메시지만 전송되어야 함
        call_kwargs = context.bot.send_message.call_args[1]
        assert "Internal error details" not in call_kwargs["text"]
        assert "error occurred" in call_kwargs["text"]


class TestSendMessageToChat:
    """_send_message_to_chat 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_send_message_short(self, handlers):
        """짧은 메시지는 직접 전송."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        await handlers._send_message_to_chat(bot, 12345, "짧은 메시지", max_length=100)

        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args[1]
        assert call_kwargs["chat_id"] == 12345
        assert call_kwargs["text"] == "짧은 메시지"
        assert call_kwargs["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_send_message_long_splits(self, handlers):
        """긴 메시지는 분할 전송."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        long_text = "A" * 150  # 150자 메시지

        await handlers._send_message_to_chat(bot, 12345, long_text, max_length=50)

        # 3번 호출되어야 함 (150 / 50 = 3)
        assert bot.send_message.call_count == 3

        # 각 호출이 50자씩 전송
        calls = bot.send_message.call_args_list
        assert calls[0][1]["text"] == "A" * 50
        assert calls[1][1]["text"] == "A" * 50
        assert calls[2][1]["text"] == "A" * 50

    @pytest.mark.asyncio
    async def test_send_message_html_fallback(self, handlers):
        """HTML 파싱 실패 시 일반 텍스트로 재전송."""
        bot = MagicMock()

        # 첫 번째 호출은 HTML 에러, 두 번째는 성공
        bot.send_message = AsyncMock(
            side_effect=[Exception("Bad HTML"), None]
        )

        await handlers._send_message_to_chat(bot, 12345, "메시지")

        # 2번 호출 확인
        assert bot.send_message.call_count == 2

        # 첫 번째: HTML 모드
        first_call = bot.send_message.call_args_list[0][1]
        assert first_call["parse_mode"] == "HTML"

        # 두 번째: parse_mode 없음
        second_call = bot.send_message.call_args_list[1][1]
        assert "parse_mode" not in second_call


class TestHandleMessage:
    """handle_message 메서드 테스트 (detached worker 패턴)."""

    @pytest.mark.asyncio
    async def test_handle_message_starts_detached_job(self, handlers, mock_session_service):
        """메시지가 detached worker job으로 시작되는지 확인."""
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "안녕하세요"
        update.message.reply_text = AsyncMock()
        update.message.reply_to_message = None
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        mock_session_service.get_current_session_id.return_value = "existing-session"

        with patch.object(handlers, "_is_session_locked", return_value=False), patch.object(
            handlers, "_start_detached_job", return_value=(1, None)
        ) as mock_start_job:
            await handlers.handle_message(update, context)

            mock_start_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_returns_immediately(self, handlers, mock_session_service):
        """핸들러가 detached job 시작 후 즉시 리턴."""
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "질문"
        update.message.reply_text = AsyncMock()
        update.message.reply_to_message = None
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        mock_session_service.get_current_session_id.return_value = "session-123"

        with patch.object(handlers, "_is_session_locked", return_value=False), patch.object(
            handlers, "_start_detached_job", return_value=(1, None)
        ) as mock_start_job:
            await handlers.handle_message(update, context)

            mock_start_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_truncates_long_message(self, handlers, mock_session_service):
        """긴 메시지는 MAX_MESSAGE_LENGTH로 자름."""
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "A" * 5000  # MAX_MESSAGE_LENGTH(4096) 초과
        update.message.reply_to_message = None
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        mock_session_service.get_current_session_id.return_value = "session-123"

        with patch.object(handlers, "_is_session_locked", return_value=False), patch.object(
            handlers, "_start_detached_job", return_value=(1, None)
        ) as mock_start_job:
            await handlers.handle_message(update, context)

            assert mock_start_job.call_args.kwargs["message"] == "A" * MAX_MESSAGE_LENGTH

    @pytest.mark.asyncio
    async def test_handle_message_new_session_creation(
        self, handlers, mock_session_service, mock_claude_client
    ):
        """세션이 없을 때 내부 세션 envelope 생성."""
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "첫 질문"
        update.message.reply_text = AsyncMock()
        update.message.reply_to_message = None
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        # 세션 없음
        mock_session_service.get_current_session_id.return_value = None
        mock_session_service.create_session.return_value = "new-session-123"
        mock_session_service.get_session_model.return_value = "sonnet"
        mock_session_service.get_workspace_path.return_value = None

        with patch.object(handlers, "_is_session_locked", return_value=False), patch.object(
            handlers, "_start_detached_job", return_value=(1, None)
        ):
            await handlers.handle_message(update, context)

            mock_claude_client.create_session.assert_not_called()
            mock_session_service.create_session.assert_called_once_with(
                user_id="12345",
                ai_provider="claude",
                model="sonnet",
                first_message="(new session)",
            )

    @pytest.mark.asyncio
    async def test_handle_message_uses_existing_session(
        self, handlers, mock_session_service, mock_claude_client
    ):
        """기존 세션이 있으면 새로 생성하지 않음."""
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "질문"
        update.message.reply_to_message = None
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        # 기존 세션 존재
        mock_session_service.get_current_session_id.return_value = "existing-session"

        with patch.object(handlers, "_is_session_locked", return_value=False), patch.object(
            handlers, "_start_detached_job", return_value=(1, None)
        ):
            await handlers.handle_message(update, context)

            # 새 세션 생성하지 않음
            mock_claude_client.create_session.assert_not_called()
            mock_session_service.create_session.assert_not_called()


class TestUserLock:
    """User Lock을 통한 Race Condition 방지 테스트."""

    @pytest.mark.asyncio
    async def test_user_lock_prevents_race_condition(
        self, handlers, mock_session_service, mock_claude_client
    ):
        """동시 메시지에서도 세션 envelope는 한 번만 생성됨."""
        update1 = MagicMock()
        update1.effective_chat.id = 12345
        update1.message.text = "첫 번째 메시지"
        update1.message.reply_text = AsyncMock()
        update1.message.reply_to_message = None
        context1 = MagicMock()
        context1.bot.send_message = AsyncMock()

        update2 = MagicMock()
        update2.effective_chat.id = 12345
        update2.message.text = "두 번째 메시지"
        update2.message.reply_text = AsyncMock()
        update2.message.reply_to_message = None
        context2 = MagicMock()
        context2.bot.send_message = AsyncMock()

        state = {"current": None}

        def get_current_session_id(*_args, **_kwargs):
            return state["current"]

        call_order = []

        def track_create_session(**_kwargs):
            call_order.append("create_session")
            state["current"] = "new-session-123"
            return "new-session-123"

        mock_session_service.get_current_session_id.side_effect = get_current_session_id
        mock_session_service.create_session.side_effect = track_create_session
        mock_session_service.get_session_model.return_value = "sonnet"
        mock_session_service.get_workspace_path.return_value = None

        with patch.object(handlers, "_is_session_locked", return_value=False), patch.object(
            handlers, "_start_detached_job", return_value=(1, None)
        ) as mock_start_job:
            # 동시에 두 메시지 처리
            await asyncio.gather(
                handlers.handle_message(update1, context1),
                handlers.handle_message(update2, context2),
            )

        assert call_order == ["create_session"]
        assert mock_start_job.call_count == 2
        mock_claude_client.create_session.assert_not_called()
        assert not update2.message.reply_text.called
