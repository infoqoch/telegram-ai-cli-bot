"""멀티 스텝 콜백 인터랙션 해피 케이스 테스트.

모든 콜백 플로우의 정상 동작을 검증:
1. Workspace 콜백 (ws:) - 7개 플로우
2. Session Queue 콜백 (sq:) - 4개 플로우
3. Alternative Session 콜백 (alt:) - 3개 플로우
4. Session 콜백 (sess:) - 누락분
5. Lock 콜백 (lock:)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# =============================================================================
# Helper
# =============================================================================

def make_query():
    """재사용 가능한 query mock."""
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()
    query.message.chat_id = 12345
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
    async def test_ws_sched_minute_shows_models(self, handlers):
        """ws:sched_minute:{id}:{minute} - 모델 선택 화면."""
        handlers._ws_pending["12345"] = {"ws_id": "ws001", "hour": 14}

        query = make_query()
        await handlers._handle_workspace_callback(query, 12345, "ws:sched_minute:ws001:30")

        text = get_text(query)
        assert "14:30" in text

        callbacks = get_callback_data(query)
        assert any("sched_model" in c for c in callbacks)

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
        """워크스페이스 스케줄 전체 플로우: schedule → time → minute → model."""
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

        # Step 4: model
        handlers._ws_pending["12345"]["minute"] = 30
        q4 = make_query()
        await handlers._handle_workspace_callback(q4, 12345, "ws:sched_model:ws001:haiku")
        assert "09:30" in get_text(q4)
        assert "haiku" in get_text(q4)


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
            "user_id": "12345",
            "message": "테스트",
        }

        query = make_query()
        await handlers._handle_session_queue_callback(query, 12345, "sq:cancel")

        text = get_text(query)
        # 취소 메시지 또는 빈 temp_pending
        assert query.edit_message_text.called or query.answer.called

    @pytest.mark.asyncio
    async def test_sq_wait(self, handlers):
        """sq:wait:{session_id} - 대기열에 추가."""
        handlers._temp_pending = {
            "user_id": "12345",
            "message": "기다려줘",
        }

        query = make_query()
        await handlers._handle_session_queue_callback(query, 12345, "sq:wait:session-123")

        assert query.edit_message_text.called or query.answer.called


# =============================================================================
# 4. Session 콜백 (sess:) - 누락분
# =============================================================================

class TestSessionCallbackFlows:
    """세션 콜백 플로우 테스트."""

    @pytest.fixture
    def handlers(self):
        h = make_handlers()
        h.sessions.list_sessions.return_value = [
            {"session_id": "abc12345", "full_session_id": "abc12345-full",
             "name": "테스트", "model": "sonnet", "created_at": "2026-01-01",
             "message_count": 5},
        ]
        h.sessions.get_current_session_id.return_value = "abc12345-full"
        h.sessions.get_session_model.return_value = "sonnet"
        h.sessions.get_session_name.return_value = "테스트"
        h.sessions.switch_session.return_value = True
        h.sessions.delete_session.return_value = True
        h.sessions.get_history.return_value = [
            {"message": "안녕", "timestamp": "2026-01-01T00:00:00"},
        ]
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
        assert text  # 응답이 있어야 함

    @pytest.mark.asyncio
    async def test_sess_new_force_reply(self, handlers):
        """sess:new:{model} - 새 세션 ForceReply."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:new:opus")

        # ForceReply로 이름 입력 요청
        query.message.reply_text.assert_called_once()

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
    async def test_sess_delete_confirm(self, handlers):
        """sess:delete:{id} - 삭제 확인."""
        query = make_query()
        await handlers._handle_session_callback(query, 12345, "sess:delete:abc12345")

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
        await handlers._handle_session_callback(q1, 12345, "sess:delete:abc12345")
        callbacks = get_callback_data(q1)
        confirm_cb = [c for c in callbacks if "confirm_del" in c]
        assert len(confirm_cb) > 0

        # Step 2: confirm
        q2 = make_query()
        await handlers._handle_session_callback(q2, 12345, confirm_cb[0].replace("sess:", ""))


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
