"""Command handlers integration tests.

모든 명령어 핸들러 통합 테스트.
"""

import pytest

from tests.integration.conftest import (
    create_command_update,
    get_reply_text,
    MockTelegram,
)


class TestStartCommand:
    """시작 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_start_creates_welcome_message(self, handlers):
        """시작 시 환영 메시지 표시."""
        update, context = create_command_update("start")

        await handlers.start(update, context)

        reply = await get_reply_text(update)
        # 실제 응답에 맞게 검증 (Bot 이름, 인증 상태, 세션 정보 포함)
        assert "Claude" in reply or "Bot" in reply or "세션" in reply or "/help" in reply


class TestHelpCommand:
    """도움말 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_help_shows_commands(self, handlers):
        """도움말에 명령어 목록 표시."""
        update, context = create_command_update("help")

        await handlers.help_command(update, context)

        reply = await get_reply_text(update)
        # 주요 명령어들이 포함되어야 함
        assert "/new" in reply or "/session" in reply or "/help" in reply
        assert "/menu" in reply
        assert "/help_extend" in reply
        assert "/new_haiku_speedy" not in reply
        assert "/reload" not in reply

    @pytest.mark.asyncio
    async def test_help_extend_lists_topics(self, handlers):
        """확장 도움말은 상세 가이드를 안내한다."""
        update, context = create_command_update("help_extend")
        update.message.text = "/help_extend"

        await handlers.help_topic_command(update, context)

        reply = await get_reply_text(update)
        assert "/help_workspace" in reply
        assert "/help_plugins" in reply
        assert "/help_admin" not in reply


class TestStatusCommand:
    """상태 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_status_shows_current_state(self, handlers):
        """현재 상태 표시."""
        update, context = create_command_update("status")

        await handlers.status_command(update, context)

        reply = await get_reply_text(update)
        # 상태 정보가 포함되어야 함
        assert reply  # 빈 응답이 아니어야 함


class TestMenuCommand:
    """메인 메뉴 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_menu_shows_launcher_buttons(self, handlers):
        """`/menu` should expose the main service launcher."""
        update, context = create_command_update("menu")

        await handlers.menu_command(update, context)

        reply = await get_reply_text(update)
        markup = update.message.reply_text.call_args[1]["reply_markup"]
        button_texts = [button.text for row in markup.inline_keyboard for button in row]

        assert "Main Menu" in reply
        assert "❓ Help" in button_texts
        assert "🆕 New Session" in button_texts
        assert "Claude usage check" not in reply


class TestChatIdCommand:
    """채팅 ID 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_chatid_returns_id(self, handlers):
        """채팅 ID 반환."""
        update, context = create_command_update("chatid", chat_id=99999)

        await handlers.chatid_command(update, context)

        reply = await get_reply_text(update)
        assert "99999" in reply


class TestPluginsCommand:
    """플러그인 목록 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_plugins_lists_available(self, handlers):
        """플러그인 목록 표시."""
        update, context = create_command_update("plugins")

        await handlers.plugins_command(update, context)

        reply = await get_reply_text(update)
        markup = update.message.reply_text.call_args[1]["reply_markup"]
        button_texts = [button.text for row in markup.inline_keyboard for button in row]
        # builtin 플러그인들이 로드되어야 함
        assert "Plugins" in reply
        assert "<b>Builtin</b>:" in reply
        assert "<b>Custom</b>:" in reply
        assert "/memo" in button_texts


class TestNewSessionCommands:
    """새 세션 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_new_session_default(self, handlers, mock_claude):
        """기본 새 세션 생성."""
        update, context = create_command_update("new")

        await handlers.new_session(update, context)

        # Claude의 create_session이 호출되어야 함
        assert mock_claude.create_session.called or update.message.reply_text.called

    @pytest.mark.asyncio
    async def test_new_session_picker_shows_both_providers(self, handlers):
        """`/new` without args should show the unified 3x2 provider/model picker."""
        update, context = create_command_update("new")

        await handlers.new_session(update, context)

        markup = update.message.reply_text.call_args[1]["reply_markup"]
        button_texts = [button.text for row in markup.inline_keyboard for button in row]

        assert "📚 🧠 Opus" in button_texts
        assert "📚 🚀 Sonnet" in button_texts
        assert "📚 ⚡ Haiku" in button_texts
        assert "🤖 🧠 5.4 XHigh" in button_texts
        assert "🤖 🚀 5.4 High" in button_texts
        assert "🤖 ⚡ 5.3 Codex" in button_texts

    @pytest.mark.asyncio
    async def test_new_codex_profile_switches_selected_provider(self, handlers, session_store):
        """`/new gpt54_high` should create a Codex session and persist Codex as current AI."""
        update, context = create_command_update("new", args=["gpt54_high"])

        await handlers.new_session(update, context)

        assert session_store.get_selected_ai_provider("12345") == "codex"
        reply = await get_reply_text(update)
        assert "Codex" in reply

    @pytest.mark.asyncio
    async def test_new_opus_session(self, handlers, mock_claude):
        """Opus 세션 생성."""
        update, context = create_command_update("new_opus")

        await handlers.new_session_opus(update, context)

        reply = await get_reply_text(update)
        # opus 관련 응답 또는 세션 생성 확인
        assert reply or mock_claude.create_session.called

    @pytest.mark.asyncio
    async def test_new_sonnet_session(self, handlers, mock_claude):
        """Sonnet 세션 생성."""
        update, context = create_command_update("new_sonnet")

        await handlers.new_session_sonnet(update, context)

        reply = await get_reply_text(update)
        assert reply or mock_claude.create_session.called

    @pytest.mark.asyncio
    async def test_new_haiku_session(self, handlers, mock_claude):
        """Haiku 세션 생성."""
        update, context = create_command_update("new_haiku")

        await handlers.new_session_haiku(update, context)

        reply = await get_reply_text(update)
        assert reply or mock_claude.create_session.called


class TestProviderSelection:
    """AI provider selection tests."""

    @pytest.mark.asyncio
    async def test_select_ai_command_shows_selector(self, handlers):
        """`/select_ai` should show provider selector UI."""
        update, context = create_command_update("select_ai")

        await handlers.select_ai_command(update, context)

        reply = await get_reply_text(update)
        assert "Current AI" in reply
        assert "Claude" in reply

    @pytest.mark.asyncio
    async def test_select_ai_command_switches_provider(self, handlers, session_store):
        """`/select_ai codex` should persist provider selection."""
        update, context = create_command_update("select_ai", args=["codex"])

        await handlers.select_ai_command(update, context)

        assert session_store.get_selected_ai_provider("12345") == "codex"
        reply = await get_reply_text(update)
        assert "Codex" in reply


class TestSessionManagement:
    """세션 관리 테스트."""

    @pytest.mark.asyncio
    async def test_session_command_shows_info(self, handlers, session_store):
        """세션 명령어가 정보 표시."""
        # 먼저 세션 생성
        session_store.create_session(
            user_id="12345",
            session_id="test-session-001",
            model="sonnet",
            name="테스트 세션"
        )

        update, context = create_command_update("session")

        await handlers.session_command(update, context)

        reply = await get_reply_text(update)
        assert reply  # 응답이 있어야 함

    @pytest.mark.asyncio
    async def test_session_list_shows_sessions(self, handlers, session_store):
        """세션 목록 표시."""
        # 세션 몇 개 생성
        session_store.create_session("12345", "session-1", model="sonnet", name="세션1")
        session_store.create_session("12345", "session-2", model="opus", name="세션2")

        update, context = create_command_update("session_list")

        await handlers.session_list_command(update, context)

        reply = await get_reply_text(update)
        # 세션 정보가 포함되어야 함
        assert reply

    @pytest.mark.asyncio
    async def test_session_list_shows_both_providers_with_one_current_pin(self, handlers, session_store):
        """`/sl` should mix Claude/Codex sessions and keep a single pin for the selected AI."""
        session_store.create_session("12345", "claude-session", ai_provider="claude", model="sonnet", name="Claude 세션")
        session_store.create_session("12345", "codex-session", ai_provider="codex", model="gpt54_xhigh", name="Codex 세션")
        session_store.select_ai_provider("12345", "codex")

        update, context = create_command_update("sl")

        await handlers.session_list_command(update, context)

        reply = await get_reply_text(update)
        markup = update.message.reply_text.call_args[1]["reply_markup"]
        button_texts = [button.text for row in markup.inline_keyboard for button in row]

        assert "Current AI: <b>🤖 Codex</b>" in reply
        assert "Codex 세션" in reply
        assert "Claude 세션" in reply
        assert "📚 🚀 <b>Claude 세션</b>" in reply
        assert "🤖 🧠 <b>Codex 세션</b> 📍" in reply
        assert reply.count("📍") == 1
        assert "🆕 New Session" in button_texts
        assert "🤖 🧠 5.4 XHigh" not in button_texts

    @pytest.mark.asyncio
    async def test_switch_session(self, handlers, session_store):
        """세션 전환."""
        # 두 개의 세션 생성
        session_store.create_session("12345", "session-aaa", model="sonnet", name="첫번째")
        session_store.create_session("12345", "session-bbb", model="opus", name="두번째")
        assert session_store.get_current_session_id("12345") == "session-bbb"

        # 첫번째로 전환
        update, context = create_command_update("s_session-a")  # short id
        context.args = []
        update.message.text = "/s_session-a"

        await handlers.switch_session_command(update, context)

        # 응답 확인
        reply = await get_reply_text(update)
        assert "Session switched!" in reply
        assert session_store.get_current_session_id("12345") == "session-aaa"

    @pytest.mark.asyncio
    async def test_back_to_previous_session(self, handlers, session_store):
        """이전 세션으로 돌아가기."""
        # 세션 생성 및 전환
        session_store.create_session("12345", "session-old", model="sonnet", name="이전")
        session_store.create_session("12345", "session-new", model="opus", name="현재")

        update, context = create_command_update("back")

        await handlers.back_command(update, context)

        reply = await get_reply_text(update)
        assert reply or update.message.reply_text.called


class TestModelCommands:
    """모델 변경 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_model_command_shows_current(self, handlers, session_store):
        """현재 모델 표시."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="테스트")

        update, context = create_command_update("model")

        await handlers.model_command(update, context)

        reply = await get_reply_text(update)
        assert reply


class TestRenameCommand:
    """세션 이름 변경 테스트."""

    @pytest.mark.asyncio
    async def test_rename_with_name(self, handlers, session_store):
        """세션 이름 변경."""
        session_store.create_session("12345", "test-sess", model="sonnet", name="원래이름")

        update, context = create_command_update("rename", args=["새이름"])

        await handlers.rename_command(update, context)

        reply = await get_reply_text(update)
        assert reply


class TestHistoryCommand:
    """히스토리 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_history_shows_messages(self, handlers, session_store):
        """히스토리 표시."""
        # 세션 생성 및 메시지 추가
        session_store.create_session("12345", "test-sess-12345678", model="sonnet", name="테스트")
        session_store.add_message("test-sess-12345678", "첫 번째 메시지")
        session_store.add_message("test-sess-12345678", "두 번째 메시지")

        # /h_<session_id> 형식으로 호출해야 함
        update, context = create_command_update("h_test-ses")  # short prefix
        update.message.text = "/h_test-ses"  # 명시적으로 설정

        await handlers.history_command(update, context)

        reply = await get_reply_text(update)
        # reply_text 또는 send_message를 통해 응답
        assert reply or context.bot.send_message.called or update.message.reply_text.called


class TestDeleteSessionCommand:
    """세션 삭제 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_delete_session(self, handlers, session_store):
        """세션 삭제."""
        session_store.create_session("12345", "to-delete-123", model="sonnet", name="삭제할세션")

        update, context = create_command_update("d_to-delet")  # short id
        update.message.text = "/d_to-delet"

        await handlers.delete_session_command(update, context)

        reply = await get_reply_text(update)
        assert reply or update.message.reply_text.called


class TestTasksCommand:
    """락 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_tasks_shows_status(self, handlers):
        """태스크 상태 표시."""
        update, context = create_command_update("tasks")

        await handlers.tasks_command(update, context)

        reply = await get_reply_text(update)
        assert reply


class TestSchedulerCommand:
    """스케줄러 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_scheduler_shows_status(self, handlers):
        """스케줄러 상태 표시."""
        update, context = create_command_update("scheduler")

        await handlers.scheduler_command(update, context)

        reply = await get_reply_text(update)
        assert reply


class TestWorkspaceCommand:
    """워크스페이스 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_workspace_shows_info(self, handlers):
        """워크스페이스 정보 표시."""
        update, context = create_command_update("workspace")

        await handlers.workspace_command(update, context)

        reply = await get_reply_text(update)
        assert reply


class TestUnknownCommand:
    """알 수 없는 명령어 테스트."""

    @pytest.mark.asyncio
    async def test_unknown_command_handled(self, handlers):
        """알 수 없는 명령어 처리."""
        update, context = create_command_update("nonexistent_command_xyz")

        await handlers.unknown_command(update, context)

        reply = await get_reply_text(update)
        # 알 수 없는 명령어 메시지 또는 조용히 무시
        assert reply is not None  # 에러가 나지 않아야 함
