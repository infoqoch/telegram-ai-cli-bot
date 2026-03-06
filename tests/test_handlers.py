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
        assert "권한이 없습니다" in call_args

    @pytest.mark.asyncio
    async def test_handle_message_unauthenticated(self, handlers, mock_auth_manager):
        """미인증 사용자의 메시지 처리."""
        mock_auth_manager.is_authenticated.return_value = False

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "Hello"
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await handlers.handle_message(update, context)

        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args[0][0]
        assert "인증이 필요합니다" in call_args

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
        assert "오류가 발생했습니다" in call_kwargs["text"]


class TestProcessClaudeRequest:
    """_process_claude_request 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_process_claude_request_success(
        self, handlers, mock_claude_client, mock_session_service
    ):
        """Claude 호출 성공 및 응답 전송."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        mock_claude_client.chat = AsyncMock(return_value=("응답 내용", None, None))
        mock_session_service.get_session_info.return_value = "abc12345"
        mock_session_service.get_history_count.return_value = 3
        mock_session_service.get_workspace_path.return_value = ""  # 일반 세션

        await handlers._process_claude_request(
            bot=bot,
            chat_id=12345,
            user_id="user1",
            session_id="session-123",
            message="테스트 질문",
            is_new_session=False,
            model="sonnet",
        )

        # Claude 호출 확인 (model + workspace_path 포함)
        mock_claude_client.chat.assert_called_once_with("테스트 질문", "session-123", model="sonnet", workspace_path=None)

        # 기존 세션이므로 메시지 추가 확인
        mock_session_service.add_message.assert_called_once_with(
            "session-123", "테스트 질문", processor="claude"
        )

        # 응답 전송 확인
        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args[1]
        assert call_kwargs["chat_id"] == 12345
        assert "응답 내용" in call_kwargs["text"]
        assert "[abc12345|#3]" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_process_claude_request_timeout(self, handlers, mock_claude_client):
        """TIMEOUT 에러 처리."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        mock_claude_client.chat = AsyncMock(return_value=("", "TIMEOUT", None))

        await handlers._process_claude_request(
            bot=bot,
            chat_id=12345,
            user_id="user1",
            session_id="session-123",
            message="질문",
            is_new_session=False,
        )

        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args[1]
        assert "응답 시간 초과" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_process_claude_request_cli_error(self, handlers, mock_claude_client):
        """CLI_ERROR 처리."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        mock_claude_client.chat = AsyncMock(return_value=("", "CLI_ERROR", None))

        await handlers._process_claude_request(
            bot=bot,
            chat_id=12345,
            user_id="user1",
            session_id="session-123",
            message="질문",
            is_new_session=False,
        )

        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args[1]
        assert "오류 발생" in call_kwargs["text"]
        assert "CLI_ERROR" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_process_claude_request_adds_message_existing_session(
        self, handlers, mock_session_service, mock_claude_client
    ):
        """기존 세션에서는 메시지 추가."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        mock_claude_client.chat = AsyncMock(return_value=("응답", None, None))

        await handlers._process_claude_request(
            bot=bot,
            chat_id=12345,
            user_id="user1",
            session_id="session-123",
            message="질문",
            is_new_session=False,
        )

        mock_session_service.add_message.assert_called_once_with(
            "session-123", "질문", processor="claude"
        )

    @pytest.mark.asyncio
    async def test_process_claude_request_skips_message_new_session(
        self, handlers, mock_session_service, mock_claude_client
    ):
        """새 세션에서는 메시지 추가 스킵."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        mock_claude_client.chat = AsyncMock(return_value=("응답", None, None))

        await handlers._process_claude_request(
            bot=bot,
            chat_id=12345,
            user_id="user1",
            session_id="session-123",
            message="질문",
            is_new_session=True,
        )

        # 새 세션이므로 add_message 호출되지 않음
        mock_session_service.add_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_claude_request_exception_handling(
        self, handlers, mock_claude_client
    ):
        """예외 발생 시 에러 메시지 전송."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        # Claude 호출 시 예외 발생
        mock_claude_client.chat = AsyncMock(side_effect=Exception("Internal error"))

        await handlers._process_claude_request(
            bot=bot,
            chat_id=12345,
            user_id="user1",
            session_id="session-123",
            message="질문",
            is_new_session=False,
        )

        # 에러 메시지 전송 확인
        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args[1]
        assert "오류가 발생했습니다" in call_kwargs["text"]


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
    """handle_message 메서드 테스트 (Fire-and-Forget 패턴)."""

    @pytest.mark.asyncio
    async def test_handle_message_creates_background_task(self, handlers, mock_session_service):
        """백그라운드 태스크 생성 확인."""
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "안녕하세요"
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.bot.send_chat_action = AsyncMock()
        context.bot.send_message = AsyncMock()

        mock_session_service.get_current_session_id.return_value = "existing-session"

        # 백그라운드 태스크를 실제로 실행 (경고 방지)
        original_create_task = asyncio.create_task
        created_tasks = []

        def tracking_create_task(coro):
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        with patch("asyncio.create_task", side_effect=tracking_create_task):
            await handlers.handle_message(update, context)

            # create_task 호출 확인 (watchdog + 실제 태스크)
            assert len(created_tasks) >= 1

            # typing indicator 확인
            context.bot.send_chat_action.assert_called_once_with(
                chat_id=12345, action="typing"
            )

            # 생성된 태스크들 정리 (경고 방지)
            for task in created_tasks:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    @pytest.mark.asyncio
    async def test_handle_message_returns_immediately(self, handlers, mock_session_service):
        """핸들러가 Claude 응답을 기다리지 않고 즉시 리턴."""
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "질문"
        context = MagicMock()
        context.bot.send_chat_action = AsyncMock()
        context.bot.send_message = AsyncMock()

        mock_session_service.get_current_session_id.return_value = "session-123"

        # 백그라운드 태스크를 실제로 실행 (경고 방지)
        original_create_task = asyncio.create_task
        created_tasks = []

        def tracking_create_task(coro):
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        with patch("asyncio.create_task", side_effect=tracking_create_task):
            await handlers.handle_message(update, context)

            # create_task가 호출되어 백그라운드 실행 확인
            assert len(created_tasks) >= 1

            # 생성된 태스크들 정리 (경고 방지)
            for task in created_tasks:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    @pytest.mark.asyncio
    async def test_handle_message_truncates_long_message(self, handlers, mock_session_service):
        """긴 메시지는 MAX_MESSAGE_LENGTH로 자름."""
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "A" * 5000  # MAX_MESSAGE_LENGTH(4096) 초과
        context = MagicMock()
        context.bot.send_chat_action = AsyncMock()
        context.bot.send_message = AsyncMock()

        mock_session_service.get_current_session_id.return_value = "session-123"

        # 백그라운드 태스크를 실제로 실행 (경고 방지)
        original_create_task = asyncio.create_task
        created_tasks = []

        def tracking_create_task(coro):
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        with patch("asyncio.create_task", side_effect=tracking_create_task):
            await handlers.handle_message(update, context)

            # 생성된 태스크들 정리 (경고 방지)
            for task in created_tasks:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    @pytest.mark.asyncio
    async def test_handle_message_new_session_creation(
        self, handlers, mock_session_service, mock_claude_client
    ):
        """세션이 없을 때 새 세션 생성."""
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "첫 질문"
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.bot.send_chat_action = AsyncMock()
        context.bot.send_message = AsyncMock()

        # 세션 없음
        mock_session_service.get_current_session_id.return_value = None
        mock_claude_client.create_session = AsyncMock(return_value="new-session-123")

        # 백그라운드 태스크를 실제로 실행 (경고 방지)
        original_create_task = asyncio.create_task
        created_tasks = []

        def tracking_create_task(coro):
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        with patch("asyncio.create_task", side_effect=tracking_create_task):
            await handlers.handle_message(update, context)

            # 새 세션 생성 확인
            mock_claude_client.create_session.assert_called_once()
            mock_session_service.create_session.assert_called_once_with(
                "12345", "new-session-123", first_message="첫 질문"
            )

            # 생성된 태스크들 정리 (경고 방지)
            for task in created_tasks:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    @pytest.mark.asyncio
    async def test_handle_message_uses_existing_session(
        self, handlers, mock_session_service, mock_claude_client
    ):
        """기존 세션이 있으면 새로 생성하지 않음."""
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "질문"
        context = MagicMock()
        context.bot.send_chat_action = AsyncMock()
        context.bot.send_message = AsyncMock()

        # 기존 세션 존재
        mock_session_service.get_current_session_id.return_value = "existing-session"

        # 백그라운드 태스크를 실제로 실행 (경고 방지)
        original_create_task = asyncio.create_task
        created_tasks = []

        def tracking_create_task(coro):
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        with patch("asyncio.create_task", side_effect=tracking_create_task):
            await handlers.handle_message(update, context)

            # 새 세션 생성하지 않음
            mock_claude_client.create_session.assert_not_called()
            mock_session_service.create_session.assert_not_called()

            # 생성된 태스크들 정리 (경고 방지)
            for task in created_tasks:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


class TestUserLock:
    """User Lock을 통한 Race Condition 방지 테스트."""

    @pytest.mark.asyncio
    async def test_user_lock_prevents_race_condition(
        self, handlers, mock_session_service, mock_claude_client
    ):
        """세션 생성 중 두 번째 메시지는 블로킹됨."""
        update1 = MagicMock()
        update1.effective_chat.id = 12345
        update1.message.text = "첫 번째 메시지"
        update1.message.reply_text = AsyncMock()
        context1 = MagicMock()
        context1.bot.send_chat_action = AsyncMock()
        context1.bot.send_message = AsyncMock()

        update2 = MagicMock()
        update2.effective_chat.id = 12345
        update2.message.text = "두 번째 메시지"
        update2.message.reply_text = AsyncMock()
        context2 = MagicMock()
        context2.bot.send_chat_action = AsyncMock()
        context2.bot.send_message = AsyncMock()

        # 세션 없음 (새 세션 생성 케이스)
        mock_session_service.get_current_session_id.return_value = None
        mock_claude_client.create_session = AsyncMock(return_value="new-session-123")

        call_order = []

        async def track_create_session():
            call_order.append("create_session_start")
            await asyncio.sleep(0.01)  # 시뮬레이션
            call_order.append("create_session_end")
            return "new-session-123"

        mock_claude_client.create_session = track_create_session

        # 백그라운드 태스크를 실제로 실행 (경고 방지)
        original_create_task = asyncio.create_task
        created_tasks = []

        def tracking_create_task(coro):
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        with patch("asyncio.create_task", side_effect=tracking_create_task):
            # 동시에 두 메시지 처리
            await asyncio.gather(
                handlers.handle_message(update1, context1),
                handlers.handle_message(update2, context2),
            )

            # 생성된 태스크들 정리 (경고 방지)
            for task in created_tasks:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # 세션 생성 중 블로킹으로 인해 create_session은 한 번만 호출됨
        # 두 번째 메시지는 "세션 준비 중" 메시지로 블로킹됨
        assert call_order == [
            "create_session_start",
            "create_session_end",
        ]
        # 두 번째 메시지에 블로킹 응답이 전송됨
        assert update2.message.reply_text.called
        reply_call = update2.message.reply_text.call_args
        assert "세션 준비 중" in reply_call[0][0]
