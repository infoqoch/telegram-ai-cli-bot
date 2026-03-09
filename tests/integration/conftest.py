"""Integration test fixtures.

텔레그램만 목킹, Repository/Plugin은 실제 사용.
"""

import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# 테스트 환경 설정 (import 전에 설정)
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("REQUIRE_AUTH", "false")
os.environ.setdefault("ALLOWED_CHAT_IDS", "")

from src.bot.handlers import BotHandlers
from src.bot.middleware import AuthManager
from src.claude.client import ClaudeClient
from src.plugins.loader import PluginLoader
from src.repository import init_repository, get_repository, Repository
from src.services.session_service import SessionService


# =============================================================================
# Mock Factories
# =============================================================================

class MockTelegram:
    """텔레그램 목 팩토리."""

    @staticmethod
    def create_user(
        user_id: int = 12345,
        username: str = "testuser",
        first_name: str = "Test",
        last_name: str = "User",
        is_bot: bool = False,
    ) -> MagicMock:
        """Mock User 생성."""
        user = MagicMock()
        user.id = user_id
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.is_bot = is_bot
        return user

    @staticmethod
    def create_chat(
        chat_id: int = 12345,
        chat_type: str = "private",
        title: Optional[str] = None,
    ) -> MagicMock:
        """Mock Chat 생성."""
        chat = MagicMock()
        chat.id = chat_id
        chat.type = chat_type
        chat.title = title
        return chat

    @staticmethod
    def create_message(
        message_id: int = 1,
        text: str = "",
        chat_id: int = 12345,
        user_id: int = 12345,
        reply_to_message: Optional[MagicMock] = None,
    ) -> MagicMock:
        """Mock Message 생성."""
        message = MagicMock()
        message.message_id = message_id
        message.text = text
        message.chat = MockTelegram.create_chat(chat_id)
        message.from_user = MockTelegram.create_user(user_id)
        message.date = datetime.now()
        message.reply_to_message = reply_to_message

        # Async methods
        message.reply_text = AsyncMock(return_value=MagicMock(message_id=message_id + 1))
        message.reply_html = AsyncMock(return_value=MagicMock(message_id=message_id + 1))
        message.edit_text = AsyncMock()
        message.delete = AsyncMock()

        return message

    @staticmethod
    def create_update(
        update_id: int = 1,
        text: str = "",
        chat_id: int = 12345,
        user_id: int = 12345,
        command: Optional[str] = None,
        callback_data: Optional[str] = None,
    ) -> MagicMock:
        """Mock Update 생성."""
        update = MagicMock()
        update.update_id = update_id

        # Message
        if command:
            text = f"/{command}"
        message = MockTelegram.create_message(
            message_id=update_id,
            text=text,
            chat_id=chat_id,
            user_id=user_id,
        )
        update.message = message
        update.effective_message = message
        update.effective_chat = message.chat
        update.effective_user = message.from_user

        # Callback query (for inline buttons)
        if callback_data:
            callback = MagicMock()
            callback.data = callback_data
            callback.message = message
            callback.from_user = message.from_user
            callback.answer = AsyncMock()
            callback.edit_message_text = AsyncMock()
            update.callback_query = callback
        else:
            update.callback_query = None

        return update

    @staticmethod
    def create_context(bot: Optional[MagicMock] = None) -> MagicMock:
        """Mock Context 생성."""
        context = MagicMock()

        if bot is None:
            bot = MagicMock()
            bot.send_message = AsyncMock(return_value=MagicMock(message_id=100))
            bot.send_chat_action = AsyncMock()
            bot.edit_message_text = AsyncMock()
            bot.delete_message = AsyncMock()

        context.bot = bot
        context.args = []
        context.user_data = {}
        context.chat_data = {}
        context.job_queue = MagicMock()

        return context


class MockClaude:
    """Claude CLI 목 팩토리."""

    @staticmethod
    def create_client(
        default_response: str = "Claude 응답입니다.",
        error: Optional[str] = None,
    ) -> MagicMock:
        """Mock ClaudeClient 생성."""
        from src.claude.client import ChatResponse, ChatError

        client = MagicMock(spec=ClaudeClient)

        if error:
            # Map error string to ChatError enum
            error_mapping = {
                "CLI_ERROR": ChatError.CLI_ERROR,
                "TIMEOUT": ChatError.TIMEOUT,
                "SESSION_NOT_FOUND": ChatError.SESSION_NOT_FOUND,
            }
            error_obj = error_mapping.get(error, ChatError.CLI_ERROR)
            response = ChatResponse(text="", error=error_obj, session_id=None)
        else:
            response = ChatResponse(text=default_response, error=None, session_id=None)

        client.chat = AsyncMock(return_value=response)
        client.create_session = AsyncMock(return_value="new-session-id-12345678")
        client.get_usage_snapshot = AsyncMock(return_value={
            "subscription_type": "max",
            "five_hour_percent": "2",
            "five_hour_reset": "3h58m",
            "weekly_percent": "56",
            "weekly_reset": "3d21h",
        })

        # resume_session also returns ChatResponse
        resume_response = ChatResponse(text=default_response, error=None, session_id=None)
        client.resume_session = AsyncMock(return_value=resume_response)

        return client


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_db_path(tmp_path):
    """임시 데이터베이스 경로."""
    return tmp_path / "test_bot.db"


@pytest.fixture
def repository(temp_db_path) -> Repository:
    """실제 Repository (테스트 DB 사용)."""
    from src.repository.database import reset_connection

    # 이전 연결 정리 (테스트 격리)
    reset_connection()

    repo = init_repository(temp_db_path)
    yield repo

    # 테스트 후 연결 정리
    reset_connection()


@pytest.fixture
def session_store(repository) -> SessionService:
    """Session service."""
    return SessionService(repository, session_timeout_hours=24)


@pytest.fixture
def mock_claude() -> MagicMock:
    """Mock Claude 클라이언트."""
    return MockClaude.create_client()


@pytest.fixture
def auth_manager() -> AuthManager:
    """인증 매니저 (인증 불필요 모드)."""
    return AuthManager(secret_key="test-secret", timeout_minutes=30)


@pytest.fixture
def plugin_loader(repository) -> PluginLoader:
    """실제 플러그인 로더."""
    from pathlib import Path

    # 프로젝트 루트 (load_all이 base_dir/plugins/builtin을 찾음)
    project_root = Path(__file__).parent.parent.parent

    loader = PluginLoader(base_dir=project_root, repository=repository)
    loader.load_all()
    return loader


@pytest.fixture
async def handlers(
    session_store,
    mock_claude,
    auth_manager,
    plugin_loader,
) -> BotHandlers:
    """BotHandlers 인스턴스."""
    handler = BotHandlers(
        session_service=session_store,
        claude_client=mock_claude,
        auth_manager=auth_manager,
        require_auth=False,  # 테스트에서는 인증 비활성화
        allowed_chat_ids=[],  # 빈 리스트 = 모두 허용
        plugin_loader=plugin_loader,
    )
    handler._spawn_detached_worker = MagicMock(return_value=os.getpid())
    return handler


@pytest.fixture
def mock_update() -> MagicMock:
    """기본 Mock Update."""
    return MockTelegram.create_update()


@pytest.fixture
def mock_context() -> MagicMock:
    """기본 Mock Context."""
    return MockTelegram.create_context()


# =============================================================================
# Helper Functions
# =============================================================================

def create_command_update(
    command: str,
    args: list[str] = None,
    chat_id: int = 12345,
    user_id: int = 12345,
) -> tuple[MagicMock, MagicMock]:
    """명령어 테스트용 Update/Context 생성."""
    text = f"/{command}"
    if args:
        text += " " + " ".join(args)

    update = MockTelegram.create_update(
        text=text,
        chat_id=chat_id,
        user_id=user_id,
    )

    context = MockTelegram.create_context()
    context.args = args or []

    return update, context


def create_message_update(
    text: str,
    chat_id: int = 12345,
    user_id: int = 12345,
) -> tuple[MagicMock, MagicMock]:
    """일반 메시지 테스트용 Update/Context 생성."""
    update = MockTelegram.create_update(
        text=text,
        chat_id=chat_id,
        user_id=user_id,
    )
    context = MockTelegram.create_context()

    return update, context


def create_callback_update(
    callback_data: str,
    chat_id: int = 12345,
    user_id: int = 12345,
) -> tuple[MagicMock, MagicMock]:
    """콜백 쿼리 테스트용 Update/Context 생성."""
    update = MockTelegram.create_update(
        callback_data=callback_data,
        chat_id=chat_id,
        user_id=user_id,
    )
    context = MockTelegram.create_context()

    return update, context


async def get_reply_text(update: MagicMock) -> str:
    """응답 텍스트 추출."""
    if update.message.reply_text.called:
        call_args = update.message.reply_text.call_args
        if call_args:
            return call_args[0][0] if call_args[0] else call_args[1].get("text", "")
    if update.message.reply_html.called:
        call_args = update.message.reply_html.call_args
        if call_args:
            return call_args[0][0] if call_args[0] else call_args[1].get("text", "")
    return ""


async def get_sent_message(context: MagicMock) -> str:
    """send_message로 보낸 텍스트 추출."""
    if context.bot.send_message.called:
        call_args = context.bot.send_message.call_args
        if call_args:
            return call_args[1].get("text", "")
    return ""


async def wait_for_handlers(handlers, timeout: float = 2.0):
    """Wait for background tasks in handlers to complete."""
    del handlers, timeout
    await asyncio.sleep(0.2)
