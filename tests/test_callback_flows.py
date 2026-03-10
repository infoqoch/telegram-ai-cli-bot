"""멀티 스텝 콜백 인터랙션 해피 케이스 테스트.

모든 콜백 플로우의 정상 동작을 검증:
1. Workspace 콜백 (ws:) - 7개 플로우
2. Session Queue 콜백 (sq:) - 핵심 2개 플로우
3. Session 콜백 (sess:) - 누락분
4. Lock 콜백 (lock:)
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup


# =============================================================================
# Helper
# =============================================================================

def make_query():
    """재사용 가능한 query mock."""
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.from_user = MagicMock()
    query.from_user.id = 12345
    query.message = MagicMock()
    query.message.reply_text = AsyncMock(return_value=MagicMock(message_id=987))
    query.message.chat_id = 12345
    query.get_bot = MagicMock(return_value=MagicMock())
    return query


def get_text(query):
    """edit_message_text의 첫 인자."""
    call = query.edit_message_text.call_args
    if not call:
        return ""
    return call[0][0] if call[0] else call[1].get("text", "")


def get_buttons(query):
    """edit_message_text의 버튼 텍스트 목록."""
    call = query.edit_message_text.call_args
    if not call:
        return []
    markup = call[1].get("reply_markup")
    if not markup:
        return []
    return [btn.text for row in markup.inline_keyboard for btn in row]


def get_callback_data(query):
    """edit_message_text의 callback_data 목록."""
    call = query.edit_message_text.call_args
    if not call:
        return []
    markup = call[1].get("reply_markup")
    if not markup:
        return []
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def make_handlers(**overrides):
    """BotHandlers mock 생성."""
    from src.bot.handlers import BotHandlers

    h = BotHandlers(
        session_service=overrides.get("session_service", MagicMock()),
        claude_client=overrides.get("claude_client", MagicMock()),
        auth_manager=overrides.get("auth_manager", MagicMock()),
        require_auth=False,
        allowed_chat_ids=[],
    )
    return h


# =============================================================================
# 1. Workspace 콜백 (ws:) 해피 케이스
# =============================================================================

class TestWorkspaceCallbackFlows:
    """워크스페이스 콜백 플로우 테스트."""

    @pytest.fixture
    def handlers(self):
        h = make_handlers()
        mock_ws = MagicMock()
        mock_ws.id = "ws001"
        mock_ws.name = "MyProject"
        mock_ws.path = "/Users/test/project"
        mock_ws.short_path = "~/project"
        mock_ws.description = "Test project"
        mock_ws.keywords = []

        registry = MagicMock()
        registry.get.return_value = mock_ws
        registry.get_status_text.return_value = "Workspace list"
        registry.list_by_user.return_value = [mock_ws]
        registry.add.return_value = mock_ws
        registry.remove.return_value = True
        h._workspace_registry = registry
        h._schedule_manager = MagicMock()
        return h

    @pytest.mark.asyncio
    async def test_ws_refresh(self, handlers):
        """ws:refresh - 목록 새로고침."""
        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:refresh")

        assert query.edit_message_text.called
        text = get_text(query)
        assert text  # 텍스트가 있어야 함

    @pytest.mark.asyncio
    async def test_ws_select(self, handlers):
        """ws:select:{id} - 워크스페이스 선택 → 액션 메뉴."""
        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:select:ws001")

        text = get_text(query)
        assert "MyProject" in text

        callbacks = get_callback_data(query)
        assert any("ws:session:" in c for c in callbacks)
        assert any("ws:schedule:" in c for c in callbacks)

    @pytest.mark.asyncio
    async def test_ws_session_model_selection(self, handlers):
        """ws:session:{id} - 모델 선택 화면."""
        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:session:ws001")

        text = get_text(query)
        assert "MyProject" in text

        callbacks = get_callback_data(query)
        assert any("sess_model" in c for c in callbacks)

    @pytest.mark.asyncio
    async def test_ws_sess_model_creates_session(self, handlers):
        """ws:sess_model:{id}:{model} - 세션 생성."""
        handlers.claude = MagicMock()
        handlers.claude.create_session = AsyncMock(return_value="uuid-1234")

        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:sess_model:ws001:sonnet")

        text = get_text(query)
        assert "Session Created" in text or "sonnet" in text.lower()
        handlers.sessions.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_ws_schedule_shows_hours(self, handlers):
        """ws:schedule:{id} - 시간 선택 화면."""
        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:schedule:ws001")

        text = get_text(query)
        assert "Schedule" in text

        callbacks = get_callback_data(query)
        assert any("sched_time" in c for c in callbacks)

    @pytest.mark.asyncio
    async def test_ws_sched_time_shows_minutes(self, handlers):
        """ws:sched_time:{id}:{hour} - 분 선택 화면."""
        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:sched_time:ws001:14")

        text = get_text(query)
        assert "14" in text

        callbacks = get_callback_data(query)
        assert any("sched_minute" in c for c in callbacks)

    @pytest.mark.asyncio
    async def test_ws_sched_minute_shows_trigger_modes(self, handlers):
        """ws:sched_minute:{id}:{minute} - 스케줄 모드 선택 화면."""
        handlers._ws_pending["12345"] = {"ws_id": "ws001", "hour": 14}

        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:sched_minute:ws001:30")

        text = get_text(query)
        assert "14:30" in text

        callbacks = get_callback_data(query)
        assert any("sched_trigger" in c for c in callbacks)

    @pytest.mark.asyncio
    async def test_ws_sched_model_shows_force_reply(self, handlers):
        """ws:sched_model:{id}:{model} - 메시지 입력 ForceReply."""
        handlers._ws_pending["12345"] = {
            "ws_id": "ws001", "hour": 14, "minute": 30
        }

        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:sched_model:ws001:opus")

        text = get_text(query)
        assert "14:30" in text
        assert "opus" in text

        # ForceReply 전송 확인
        query.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_ws_delete(self, handlers):
        """ws:delete:{id} - 삭제."""
        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:delete:ws001")

        handlers._workspace_registry.remove.assert_called_once_with("ws001")

    @pytest.mark.asyncio
    async def test_ws_add_shows_force_reply(self, handlers):
        """ws:add - AI 추천을 위한 목적 입력."""
        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:add")

        text = get_text(query)
        assert "Register" in text or "purpose" in text.lower()

        # ForceReply 전송 확인
        query.message.reply_text.assert_called_once()
        # pending state 설정 확인
        assert handlers._ws_pending["12345"]["action"] == "recommend"

    @pytest.mark.asyncio
    async def test_ws_manual_shows_path_input(self, handlers):
        """ws:manual - 수동 경로 입력."""
        handlers._ws_pending["12345"] = {"action": "recommend"}

        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:manual")

        text = get_text(query)
        assert "Manual" in text

        query.message.reply_text.assert_called_once()
        assert handlers._ws_pending["12345"]["action"] == "manual_path"

    @pytest.mark.asyncio
    async def test_ws_full_schedule_flow(self, handlers):
        """워크스페이스 스케줄 전체 플로우: schedule → time → minute → trigger → provider → model."""
        # Step 1: schedule
        q1 = make_query()
        await handlers._handle_workspace_callback(q1, 12345, "ws:schedule:ws001")
        assert any("sched_time" in c for c in get_callback_data(q1))

        # Step 2: time (hour 9)
        q2 = make_query()
        await handlers._handle_workspace_callback(q2, 12345, "ws:sched_time:ws001:9")
        assert any("sched_minute" in c for c in get_callback_data(q2))

        # Step 3: minute (30)
        handlers._ws_pending["12345"] = {"ws_id": "ws001", "hour": 9}
        q3 = make_query()
        await handlers._handle_workspace_callback(q3, 12345, "ws:sched_minute:ws001:30")
        assert "09:30" in get_text(q3)
        assert any("sched_trigger" in c for c in get_callback_data(q3))

        # Step 4: trigger
        q4 = make_query()
        await handlers._handle_workspace_callback(q4, 12345, "ws:sched_trigger:ws001:cron")
        assert any("sched_provider" in c for c in get_callback_data(q4))

        # Step 5: provider
        q5 = make_query()
        await handlers._handle_workspace_callback(q5, 12345, "ws:sched_provider:ws001:claude")
        assert any("sched_model" in c for c in get_callback_data(q5))

        # Step 6: model
        handlers._ws_pending["12345"]["minute"] = 30
        q6 = make_query()
        await handlers._handle_workspace_callback(q6, 12345, "ws:sched_model:ws001:haiku")
        assert "09:30" in get_text(q6)
        assert "haiku" in get_text(q6)


# =============================================================================
# 2. Workspace ForceReply 플로우
# =============================================================================

class TestWorkspaceForceReplyFlows:
    """워크스페이스 ForceReply 응답 처리 테스트."""

    @pytest.fixture
    def handlers(self):
        h = make_handlers()
        mock_ws = MagicMock()
        mock_ws.id = "ws001"
        mock_ws.name = "MyProject"
        mock_ws.path = "/Users/test/project"
        mock_ws.short_path = "~/project"
        mock_ws.description = "Test"
        mock_ws.time_str = "09:30"

        registry = MagicMock()
        registry.get.return_value = mock_ws
        registry.get_status_text.return_value = "list"
        registry.recommend_paths = AsyncMock(return_value=[])
        registry.add.return_value = mock_ws
        h._workspace_registry = registry
        h._schedule_manager = MagicMock()

        schedule_mock = MagicMock()
        schedule_mock.time_str = "09:30"
        h._schedule_manager.add.return_value = schedule_mock
        return h

    @pytest.mark.asyncio
    async def test_recommend_no_results_falls_to_manual(self, handlers):
        """AI 추천 없으면 수동 입력으로 전환."""
        handlers._ws_pending["12345"] = {"action": "recommend"}

        update = MagicMock()
        update.message.reply_text = AsyncMock()

        await handlers._handle_workspace_force_reply(update, 12345, "투자 분석")

        # 수동 입력 모드 전환
        assert handlers._ws_pending["12345"]["action"] == "manual_path"

    @pytest.mark.asyncio
    async def test_manual_path_name_desc_flow(self, handlers):
        """수동 등록: path → name → desc."""
        import tempfile, os
        tmpdir = tempfile.mkdtemp()

        try:
            # Step 1: path
            handlers._ws_pending["12345"] = {"action": "manual_path"}
            update1 = MagicMock()
            update1.message.reply_text = AsyncMock()
            await handlers._handle_workspace_force_reply(update1, 12345, tmpdir)
            assert handlers._ws_pending["12345"]["action"] == "manual_name"

            # Step 2: name
            update2 = MagicMock()
            update2.message.reply_text = AsyncMock()
            await handlers._handle_workspace_force_reply(update2, 12345, "MyApp")
            assert handlers._ws_pending["12345"]["action"] == "manual_desc"

            # Step 3: description → registration
            update3 = MagicMock()
            update3.message.reply_text = AsyncMock()
            await handlers._handle_workspace_force_reply(update3, 12345, "My application")
            handlers._workspace_registry.add.assert_called_once()
            assert "12345" not in handlers._ws_pending
        finally:
            os.rmdir(tmpdir)

    @pytest.mark.asyncio
    async def test_schedule_message_input(self, handlers):
        """워크스페이스 스케줄 메시지 입력."""
        handlers._ws_pending["12345"] = {
            "ws_id": "ws001",
            "hour": 9,
            "minute": 30,
            "model": "sonnet",
        }

        update = MagicMock()
        update.message.reply_text = AsyncMock()

        await handlers._handle_workspace_force_reply(update, 12345, "오늘 할 일 정리해줘")

        handlers._schedule_manager.add.assert_called_once()
        assert "12345" not in handlers._ws_pending


# =============================================================================
# 3. Session Queue 콜백 (sq:)
# =============================================================================

class TestSessionQueueCallbackFlows:
    """세션 큐 콜백 플로우 테스트."""

    @pytest.fixture
    def handlers(self):
        h = make_handlers()
        h.sessions.get_current_session_id.return_value = "session-123"
        h.sessions.get_session_model.return_value = "sonnet"
        h.sessions.get_workspace_path.return_value = None
        h.sessions.get_session_name.return_value = "테스트세션"
        h.claude = MagicMock()
        h.claude.create_session = AsyncMock(return_value="new-uuid-123")
        return h

    @pytest.mark.asyncio
    async def test_sq_cancel(self, handlers):
        """sq:cancel - 큐 요청 취소."""
        handlers._temp_pending = {
            "pending-1": {
                "user_id": "12345",
                "message": "테스트",
                "model": "sonnet",
                "is_new_session": False,
                "workspace_path": "",
                "current_session_id": "session-123",
            },
        }

        query = make_query()
        await handlers._handle_session_queue_callback(query, 12345, "sq:cancel:pending-1")

        text = get_text(query)
        assert text == "Request cancelled."
        assert handlers._temp_pending == {}

    @pytest.mark.asyncio
    async def test_sq_wait(self, handlers):
        """sq:wait:{session_id} - 대기열에 추가."""
        repo = MagicMock()
        repo.get_queued_messages_by_session.return_value = [{"id": 1}, {"id": 2}]
        handlers.sessions._repo = repo
        handlers.sessions.get_session_info.return_value = "sess1"
        handlers._is_session_locked = MagicMock(return_value=True)
        handlers._temp_pending = {
            "pending-1": {
                "user_id": "12345",
                "message": "기다려줘",
                "model": "sonnet",
                "is_new_session": False,
                "workspace_path": "",
                "current_session_id": "session-123",
            },
        }

        query = make_query()
        await handlers._handle_session_queue_callback(query, 12345, "sq:wait:pending-1:session-123")

        repo.save_queued_message.assert_called_once_with(
            session_id="session-123",
            user_id="12345",
            chat_id=12345,
            message="기다려줘",
            model="sonnet",
            is_new_session=False,
            workspace_path="",
        )
        assert "Added to queue" in get_text(query)


# =============================================================================
# 4. Session 콜백 (sess:) - 누락분
# =============================================================================

class TestSessionCallbackFlows:
    """세션 콜백 플로우 테스트."""

    @pytest.fixture
    def handlers(self):
        h = make_handlers()
        session_rows = [
            {
                "session_id": "abc12345",
                "full_session_id": "abc12345-full",
                "name": "테스트",
                "model": "sonnet",
                "ai_provider": "claude",
                "created_at": "2026-01-01",
                "history_count": 5,
                "is_current": True,
            },
            {
                "session_id": "def67890",
                "full_session_id": "def67890-full",
                "name": "코덱스",
                "model": "gpt54_xhigh",
                "ai_provider": "codex",
                "created_at": "2026-01-02",
                "history_count": 3,
                "is_current": False,
            },
        ]
        session_map = {row["full_session_id"]: row for row in session_rows}
        h.sessions.list_sessions_for_all_providers.return_value = session_rows

        def current_session_id(user_id, provider=None):
            if provider in (None, "claude"):
                return "abc12345-full"
            return None

        def get_session_by_prefix(user_id, prefix):
            for row in session_rows:
                if row["session_id"].startswith(prefix[:8]):
                    return row
            return None

        h.sessions.get_current_session_id.side_effect = current_session_id
        h.sessions.get_session_model.return_value = "sonnet"
        h.sessions.get_session_name.return_value = "테스트"
        h.sessions.switch_session.return_value = True
        h.sessions.delete_session.return_value = True
        h.sessions.get_session_by_prefix.side_effect = get_session_by_prefix
        h.sessions.get_session.side_effect = lambda session_id: session_map.get(session_id)
        h.sessions.get_session_ai_provider.side_effect = lambda session_id: session_map.get(session_id, {}).get("ai_provider", "claude")
        h.sessions.get_session_history_entries.return_value = []
        h.sessions.get_session_by_provider_session_id.return_value = None
        h.sessions.create_session.return_value = "imported-session-full"
        h.sessions.get_history.return_value = [
            {"message": "안녕", "timestamp": "2026-01-01T00:00:00"},
        ]
        h._local_sessions = MagicMock()
        h.claude = MagicMock()
        h.claude.create_session = AsyncMock(return_value="new-uuid")
        return h

    @pytest.mark.asyncio
    async def test_sess_list(self, handlers):
        """sess:list - 세션 목록."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:list")

        assert query.edit_message_text.called
        text = get_text(query)
        buttons = get_buttons(query)
        assert "Current AI: <b>📚 Claude</b>" in text
        assert "📚 🚀 <b>테스트</b> 📍" in text
        assert "🤖 🧠 <b>코덱스</b>" in text
        assert text.count("📍") == 1
        assert "🆕 New Session" in buttons
        assert "📥 Import Local" in buttons
        assert "☑️ Multi Delete" in buttons
        assert "📚 🧠 Opus" not in buttons
        assert "🤖 🧠 5.4 XHigh" not in buttons

    @pytest.mark.asyncio
    async def test_sess_new_opens_model_picker(self, handlers):
        """sess:new - 새 세션 모델 선택 화면."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:new")

        text = get_text(query)
        buttons = get_buttons(query)
        assert "New Session" in text
        assert "Current AI: <b>📚 Claude</b>" in text
        assert "📚 🧠 Opus" in buttons
        assert "📚 🚀 Sonnet" in buttons
        assert "📚 ⚡ Haiku" in buttons
        assert "🤖 🧠 5.4 XHigh" in buttons
        assert "🤖 🚀 5.4 High" in buttons
        assert "🤖 ⚡ 5.3 Codex" in buttons

    @pytest.mark.asyncio
    async def test_sess_new_force_reply(self, handlers):
        """sess:new:{model} - 새 세션 ForceReply."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:new:opus")

        # ForceReply로 이름 입력 요청
        query.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_sess_new_codex_selection_switches_provider(self, handlers):
        """Codex 모델 선택 시 current AI도 Codex로 동기화된다."""
        query = make_query()
        await handlers._handle_new_session_callback(query, 12345, "gpt54_high")

        handlers.sessions.select_ai_provider.assert_called_once_with("12345", "codex")
        text = get_text(query)
        assert "🤖 Codex" in text
        assert "5.4 High" in text

    @pytest.mark.asyncio
    async def test_sess_switch(self, handlers):
        """sess:switch:{id} - 세션 전환."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:switch:abc12345")

        assert query.edit_message_text.called or query.answer.called

    @pytest.mark.asyncio
    async def test_sess_history(self, handlers):
        """sess:history:{id} - 히스토리 보기."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:history:abc12345")

        assert query.edit_message_text.called

    @pytest.mark.asyncio
    async def test_resp_switch_replies_without_editing_original(self, handlers):
        """AI 응답 shortcut의 switch는 follow-up 메시지로 열린다."""
        query = make_query()
        await handlers._handle_response_session_callback(query, 12345, "resp:switch:abc12345")

        query.message.reply_text.assert_called_once()
        query.edit_message_text.assert_not_called()
        reply_text = query.message.reply_text.call_args.kwargs["text"]
        assert "Session switched" in reply_text

    @pytest.mark.asyncio
    async def test_resp_list_replies_without_editing_original(self, handlers):
        """AI 응답 shortcut의 list는 원본 응답을 유지한다."""
        query = make_query()
        await handlers._handle_response_session_callback(query, 12345, "resp:list")

        query.message.reply_text.assert_called_once()
        query.edit_message_text.assert_not_called()
        reply_text = query.message.reply_text.call_args.kwargs["text"]
        assert "Session List" in reply_text

    @pytest.mark.asyncio
    async def test_menu_claude_usage_renders_usage_snapshot(self, handlers):
        """`menu:claude_usage` should render the Claude usage card."""
        query = make_query()
        handlers.claude.get_usage_snapshot = AsyncMock(return_value={
            "subscription_type": "max",
            "five_hour_percent": "2",
            "five_hour_reset": "3h58m",
            "weekly_percent": "56",
            "weekly_reset": "3d21h",
        })

        await handlers._handle_menu_callback(query, 12345, "menu:claude_usage")

        text = get_text(query)
        buttons = get_buttons(query)
        assert "Claude Usage" in text
        assert "5h:" in text
        assert "wk:" in text
        assert "🔄 Refresh" in buttons

    @pytest.mark.asyncio
    async def test_menu_claude_usage_renders_partial_status(self, handlers):
        """Usage details may be unavailable while auth plan still renders."""
        query = make_query()
        handlers.claude.get_usage_snapshot = AsyncMock(return_value={
            "subscription_type": "max",
            "checked_at": "2026-03-09 22:38:50",
            "unavailable_reason": "Usage endpoint temporarily unavailable",
        })

        await handlers._handle_menu_callback(query, 12345, "menu:claude_usage")

        text = get_text(query)
        assert "Claude Usage" in text
        assert "Plan: <b>max</b>" in text
        assert "5h / wk: unavailable right now" in text
        assert "Reason: Usage endpoint temporarily unavailable" in text

    @pytest.mark.asyncio
    async def test_menu_sessions_adds_back_to_menu_actions(self, handlers):
        """`menu:sessions` should keep the launcher context in its utility buttons."""
        query = make_query()

        await handlers._handle_menu_callback(query, 12345, "menu:sessions")

        callbacks = get_callback_data(query)
        assert "menu:new" in callbacks
        assert "menu:sessions" in callbacks
        assert "menu:tasks" in callbacks
        assert "menu:ai" in callbacks
        assert "menu:open" in callbacks

    @pytest.mark.asyncio
    async def test_menu_ai_selection_keeps_back_button(self, handlers):
        """AI selection opened from the launcher should return to menu."""
        query = make_query()

        await handlers._handle_menu_callback(query, 12345, "menu:ai")

        callbacks = get_callback_data(query)
        assert "ai:select:claude:menu" in callbacks
        assert "ai:select:codex:menu" in callbacks
        assert "menu:open" in callbacks

    @pytest.mark.asyncio
    async def test_menu_plugins_renders_dynamic_buttons(self, handlers):
        """`menu:plugins` should render compact text plus dynamic plugin buttons."""
        memo = MagicMock()
        memo.name = "memo"
        memo.description = "Save memos"
        todo = MagicMock()
        todo.name = "todo"
        todo.description = "Todo management"
        hourly_ping = MagicMock()
        hourly_ping.name = "hourly_ping"
        hourly_ping.description = "Hourly scheduler health-check"

        handlers.plugins = MagicMock()
        handlers.plugins.plugins = [memo, todo, hourly_ping]
        memo._source_group = "builtin"
        todo._source_group = "builtin"
        hourly_ping._source_group = "custom"

        query = make_query()
        await handlers._handle_menu_callback(query, 12345, "menu:plugins")

        text = get_text(query)
        callbacks = get_callback_data(query)
        assert "<b>Plugins</b>" in text
        assert "<b>Builtin</b>:" in text
        assert "plug:open:memo:menu" in callbacks
        assert "plug:open:hourly_ping:menu" in callbacks
        assert "menu:open" in callbacks

    @pytest.mark.asyncio
    async def test_plugin_hub_open_interactive_plugin_keeps_back(self, handlers):
        """Launcher-opened plugin screens should keep a return button to the plugin hub."""
        plugin = MagicMock()
        plugin.name = "memo"
        plugin.usage = "memo usage"
        plugin.handle = AsyncMock(return_value=MagicMock(
            handled=True,
            response="📝 <b>Memo</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("List", callback_data="memo:list"),
            ]]),
        ))

        handlers.plugins = MagicMock()
        handlers.plugins.get_plugin_by_name.return_value = plugin

        query = make_query()
        await handlers._handle_plugin_hub_callback(query, 12345, "plug:open:memo:menu")

        text = get_text(query)
        callbacks = get_callback_data(query)
        assert "Memo" in text
        assert "memo:list" in callbacks
        assert "plug:list:menu" in callbacks

    @pytest.mark.asyncio
    async def test_plugin_callback_preserves_launcher_back(self, handlers):
        """Plugin-internal callbacks should preserve the plugin-hub back row."""
        plugin = MagicMock()
        plugin.handle_callback_async = AsyncMock(return_value={
            "text": "updated",
            "reply_markup": InlineKeyboardMarkup([[
                InlineKeyboardButton("Refresh", callback_data="memo:list"),
            ]]),
            "edit": True,
        })

        query = make_query()
        query.message.reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("Back", callback_data="plug:list:menu"),
        ]])

        await handlers._handle_plugin_callback(query, 12345, "memo:list", plugin)

        callbacks = get_callback_data(query)
        assert "memo:list" in callbacks
        assert "plug:list:menu" in callbacks

    @pytest.mark.asyncio
    async def test_plugin_callback_registers_interaction_for_force_reply(self, handlers):
        """ForceReply 플러그인 콜백은 prompt message_id 기준으로 interaction을 등록한다."""
        plugin = MagicMock()
        plugin.name = "memo"
        plugin.handle_callback_async = AsyncMock(return_value={
            "text": "📝 <b>Add Memo</b>",
            "force_reply_prompt": "📝 Enter memo:",
            "force_reply": ForceReply(selective=True, input_field_placeholder="Enter memo..."),
            "edit": False,
        })

        query = make_query()
        await handlers._handle_plugin_callback(query, 12345, "memo:add", plugin)

        query.message.reply_text.assert_called_once()
        assert 987 in handlers._plugin_interactions
        interaction = handlers._plugin_interactions[987]
        assert interaction.plugin_name == "memo"
        assert interaction.chat_id == 12345

    @pytest.mark.asyncio
    async def test_sess_delete_confirm(self, handlers):
        """sess:delete:{id} - 삭제 확인."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:delete:def67890")

        text = get_text(query)
        callbacks = get_callback_data(query)
        # 확인 버튼이 있어야 함
        assert any("confirm_del" in c for c in callbacks)

    @pytest.mark.asyncio
    async def test_sess_confirm_del_executes(self, handlers):
        """sess:confirm_del:{id} - 삭제 실행."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:confirm_del:abc12345")

        # 삭제 실행됨
        assert query.edit_message_text.called or handlers.sessions.delete_session.called

    @pytest.mark.asyncio
    async def test_sess_delete_blocks_current_session_for_its_provider(self, handlers):
        """selected AI가 달라도 해당 provider의 current session은 삭제 금지."""
        handlers.sessions.get_current_session_id.side_effect = (
            lambda user_id, provider=None: "def67890-full" if provider == "codex" else "abc12345-full"
        )

        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:delete:def67890")

        assert "Cannot Delete" in get_text(query)

    @pytest.mark.asyncio
    async def test_sess_import_lists_recent_local_sessions(self, handlers):
        """sess:import - 최근 로컬 세션 picker."""
        local_session = MagicMock()
        local_session.provider_session_id = "550e8400-e29b-41d4-a716-446655440000"
        local_session.short_id = "550e8400"
        local_session.title = "Imported Claude"
        local_session.updated_at = "2026-03-10T10:00:00Z"
        local_session.workspace_path = "/Users/test/project"
        local_session.preview = "existing prompt"
        handlers._local_sessions.list_recent.return_value = [local_session]

        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:import")

        text = get_text(query)
        callbacks = get_callback_data(query)
        assert "Import Local Session" in text
        assert "Imported Claude" in text
        assert "sess:import_pick:claude:550e8400-e29b-41d4-a716-446655440000" in callbacks

    @pytest.mark.asyncio
    async def test_sess_import_pick_switches_existing_attached_session(self, handlers):
        """같은 external session을 다시 import하면 기존 bot session으로 전환."""
        local_session = MagicMock()
        local_session.short_id = "550e8400"
        local_session.title = "Imported Claude"
        local_session.workspace_path = "/Users/test/project"
        handlers._local_sessions.get.return_value = local_session
        handlers.sessions.get_session_by_provider_session_id.return_value = {
            "session_id": "ghi99999",
            "full_session_id": "ghi99999-full",
            "name": "Imported Claude",
            "model": "sonnet",
            "ai_provider": "claude",
            "workspace_path": "/Users/test/project",
        }

        query = make_query()
        await handlers._handle_session_callback(
            query,
            12345,
            "sess:import_pick:claude:550e8400-e29b-41d4-a716-446655440000",
        )

        handlers.sessions.switch_session.assert_called_with("12345", "ghi99999-full")
        assert "already attached" in get_text(query)

    @pytest.mark.asyncio
    async def test_sess_multi_delete_blocks_current_session_selection(self, handlers):
        """current session은 멀티 삭제 대상에 넣을 수 없다."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:multi_toggle:abc12345-full")

        query.answer.assert_called()
        assert "Current session cannot be deleted" in query.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_sess_multi_delete_executes_for_selected_sessions(self, handlers):
        """멀티 삭제 확인 후 선택 세션 삭제."""
        handlers._session_multi_selected["12345"] = {"def67890-full"}

        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:multi_execute")

        handlers.sessions.delete_session.assert_called_with("12345", "def67890-full")
        assert "deleted" in get_text(query)

    @pytest.mark.asyncio
    async def test_sess_model_change(self, handlers):
        """sess:model:{model}:{id} - 모델 변경."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:model:opus:abc12345")

        assert query.edit_message_text.called or query.answer.called

    @pytest.mark.asyncio
    async def test_sess_delete_then_confirm_flow(self, handlers):
        """세션 삭제 2단계: delete → confirm_del."""
        # Step 1: delete 확인
        q1 = make_query()
        await handlers._handle_session_callback(q1, 12345, "sess:delete:def67890")
        callbacks = get_callback_data(q1)
        confirm_cb = [c for c in callbacks if "confirm_del" in c]
        assert len(confirm_cb) > 0

        # Step 2: confirm
        q2 = make_query()
        await handlers._handle_session_callback(q2, 12345, confirm_cb[0])


# =============================================================================
# 5. Lock 콜백
# =============================================================================

class TestTasksCallbackFlows:
    """Tasks 콜백 테스트."""

    @pytest.mark.asyncio
    async def test_tasks_refresh(self):
        """tasks:refresh - 태스크 상태 새로고침."""
        h = make_handlers()
        h.sessions.list_sessions.return_value = []

        query = make_query()
        await h._handle_tasks_callback(query, 12345)

        assert query.edit_message_text.called
        text = get_text(query)
        assert "task" in text.lower() or "slot" in text.lower() or "No" in text

    def test_build_tasks_status_compacts_multiline_preview(self):
        """멀티라인 요청은 한 줄 미리보기로 정리된다."""
        h = make_handlers()
        repo = MagicMock()
        repo.list_processing_messages_by_user.return_value = [{
            "session_id": "session-1234",
            "session_name": "코닥스",
            "request_at": (datetime.now(timezone.utc) - timedelta(hours=2, minutes=39, seconds=55)).isoformat(),
            "request": "[Claude · Opus · 24bf6c58 (똘똘이)|#3]\nhey\n...",
        }]
        repo.list_queued_messages_by_user.return_value = []
        h.sessions._repo = repo
        h._get_live_session_lock = MagicMock(return_value={"job_id": 1})

        text, _ = h._build_tasks_status("12345")

        assert "<b>Processing</b> (1)" in text
        assert "2h 39m 55s elapsed" in text
        assert "<b>코닥스</b>" in text
        assert "hey ..." in text
        assert "[Claude" not in text
        assert "Detached workers" not in text

    def test_build_tasks_status_formats_queue_preview(self):
        """대기열 미리보기는 줄바꿈을 제거하고 HTML을 escape한다."""
        h = make_handlers()
        repo = MagicMock()
        repo.list_processing_messages_by_user.return_value = []
        repo.list_queued_messages_by_user.return_value = [{
            "session_id": "queue-1234",
            "session_name": "review<bot>",
            "message": "first line\n<script>alert(1)</script>",
        }]
        h.sessions._repo = repo
        h._get_live_session_lock = MagicMock(return_value=None)

        text, _ = h._build_tasks_status("12345")

        assert "<b>Queue</b> (1)" in text
        assert "<b>No active tasks</b>" not in text
        assert "<b>review&lt;bot&gt;</b>" in text
        assert "first line &lt;script&gt;alert(1)&lt;/s..." in text
        assert "<script>" not in text


# =============================================================================
# 6. Scheduler ForceReply 플로우
# =============================================================================

class TestSchedulerForceReplyFlows:
    """스케줄러 ForceReply 메시지 입력 테스트."""

    @pytest.fixture
    def handlers(self):
        h = make_handlers()
        h._schedule_manager = MagicMock()

        schedule_mock = MagicMock()
        schedule_mock.time_str = "10:25"
        schedule_mock.type_emoji = "💬"
        schedule_mock.name = "테스트"
        h._schedule_manager.add.return_value = schedule_mock
        return h

    @pytest.mark.asyncio
    async def test_schedule_message_creates_schedule(self, handlers):
        """스케줄 메시지 입력 → 스케줄 생성."""
        handlers._sched_pending["12345"] = {
            "type": "claude",
            "hour": 10,
            "minute": 25,
            "model": "sonnet",
        }

        update = MagicMock()
        update.message.reply_text = AsyncMock()

        await handlers._handle_schedule_force_reply(update, 12345, "매일 할 일 정리해줘")

        handlers._schedule_manager.add.assert_called_once()
        call_kwargs = handlers._schedule_manager.add.call_args[1]
        assert call_kwargs["hour"] == 10
        assert call_kwargs["minute"] == 25
        assert call_kwargs["model"] == "sonnet"
        assert call_kwargs["message"] == "매일 할 일 정리해줘"
        reply_text = update.message.reply_text.call_args[0][0]
        assert "Model:" not in reply_text

    @pytest.mark.asyncio
    async def test_workspace_schedule_message(self, handlers):
        """워크스페이스 스케줄 메시지 입력."""
        handlers._sched_pending["12345"] = {
            "type": "workspace",
            "hour": 8,
            "minute": 0,
            "model": "opus",
            "workspace_path": "/Users/test/project",
        }

        update = MagicMock()
        update.message.reply_text = AsyncMock()

        await handlers._handle_schedule_force_reply(update, 12345, "코드 리뷰해줘")

        call_kwargs = handlers._schedule_manager.add.call_args[1]
        assert call_kwargs["schedule_type"] == "workspace"
        assert call_kwargs["workspace_path"] == "/Users/test/project"
        reply_text = update.message.reply_text.call_args[0][0]
        assert "Model:" not in reply_text

    @pytest.mark.asyncio
    async def test_one_time_schedule_message_sets_once_trigger(self, handlers):
        """1회성 스케줄 메시지 입력."""
        handlers._sched_pending["12345"] = {
            "type": "chat",
            "hour": 22,
            "minute": 20,
            "trigger_type": "once",
        }

        update = MagicMock()
        update.message.reply_text = AsyncMock()

        await handlers._handle_schedule_force_reply(update, 12345, "손흥민 다음 경기")

        call_kwargs = handlers._schedule_manager.add.call_args[1]
        assert call_kwargs["schedule_type"] == "chat"
        assert call_kwargs["trigger_type"] == "once"
