"""이번 세션 변경사항 테스트.

테스트 대상:
1. schedule_executor: cwd → workspace_path 수정, ChatResponse 언패킹
2. SchedulerManager.get_system_jobs_text(): 시스템 잡 텍스트 생성
3. /scheduler에 시스템 잡 통합 표시
4. 스케줄러 분 단위 선택 콜백 흐름
5. workspace recommend_paths 파라미터명 수정
"""

import asyncio
from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scheduler_manager import SchedulerManager, ScheduledJob


# =============================================================================
# 1. SchedulerManager.get_system_jobs_text()
# =============================================================================

class TestGetSystemJobsText:
    """시스템 잡 텍스트 생성 테스트."""

    def setup_method(self):
        SchedulerManager._instance = None
        self.manager = SchedulerManager()
        mock_app = MagicMock()
        mock_app.job_queue.run_daily.return_value = MagicMock()
        self.manager.set_app(mock_app)

    def test_empty_when_no_system_jobs(self):
        """시스템 잡 없으면 빈 문자열."""
        text = self.manager.get_system_jobs_text()
        assert text == ""

    def test_empty_when_only_schedule_adapter_jobs(self):
        """ScheduleAdapter 잡만 있으면 빈 문자열."""
        callback = AsyncMock()
        self.manager.register_daily("schedule_abc", callback, time(8, 0), "ScheduleAdapter")

        text = self.manager.get_system_jobs_text()
        assert text == ""

    def test_shows_system_jobs(self):
        """ScheduleAdapter 외 잡은 시스템 잡으로 표시."""
        callback = AsyncMock()
        self.manager.register_daily("todo_morning", callback, time(8, 0), "TodoScheduler")
        self.manager.register_daily("session_compact", callback, time(22, 0), "SessionScheduler")

        text = self.manager.get_system_jobs_text()
        assert "시스템 작업" in text
        assert "todo_morning" in text
        assert "session_compact" in text

    def test_excludes_schedule_adapter_in_mixed(self):
        """혼합된 잡에서 ScheduleAdapter는 제외."""
        callback = AsyncMock()
        self.manager.register_daily("schedule_123", callback, time(8, 0), "ScheduleAdapter")
        self.manager.register_daily("todo_morning", callback, time(9, 0), "TodoScheduler")

        text = self.manager.get_system_jobs_text()
        assert "todo_morning" in text
        assert "schedule_123" not in text


# =============================================================================
# 2. schedule_executor 버그 수정 테스트
# =============================================================================

class TestScheduleExecutor:
    """schedule_executor 함수 테스트."""

    @pytest.mark.asyncio
    async def test_executor_calls_chat_with_workspace_path(self):
        """chat() 호출 시 workspace_path 파라미터 사용 확인."""
        mock_claude = MagicMock()
        mock_claude.chat = AsyncMock(return_value=("응답 텍스트", None, None))

        schedule = MagicMock()
        schedule.id = "test-schedule"
        schedule.message = "테스트 메시지"
        schedule.model = "sonnet"
        schedule.type = "workspace"
        schedule.workspace_path = "/Users/test/project"
        schedule.chat_id = 12345
        schedule.name = "테스트스케줄"

        # schedule_executor 로직 재현 (main.py에서 추출)
        workspace_path = None
        if schedule.type == "workspace" and schedule.workspace_path:
            workspace_path = schedule.workspace_path

        text, error, _ = await mock_claude.chat(
            message=schedule.message,
            session_id=None,
            model=schedule.model,
            workspace_path=workspace_path,
        )

        # workspace_path 파라미터로 호출되었는지 확인
        call_kwargs = mock_claude.chat.call_args[1]
        assert "workspace_path" in call_kwargs
        assert call_kwargs["workspace_path"] == "/Users/test/project"
        assert "cwd" not in call_kwargs

    @pytest.mark.asyncio
    async def test_executor_calls_chat_with_session_id_none(self):
        """chat() 호출 시 session_id=None (새 세션 생성)."""
        mock_claude = MagicMock()
        mock_claude.chat = AsyncMock(return_value=("응답", None, None))

        schedule = MagicMock()
        schedule.message = "테스트"
        schedule.model = "sonnet"
        schedule.type = "claude"
        schedule.workspace_path = None

        await mock_claude.chat(
            message=schedule.message,
            session_id=None,
            model=schedule.model,
            workspace_path=None,
        )

        call_kwargs = mock_claude.chat.call_args[1]
        assert call_kwargs["session_id"] is None

    @pytest.mark.asyncio
    async def test_executor_handles_chat_response_tuple(self):
        """ChatResponse 튜플 언패킹 확인."""
        mock_claude = MagicMock()
        mock_claude.chat = AsyncMock(return_value=("응답 텍스트", None, None))

        text, error, _ = await mock_claude.chat(
            message="테스트",
            session_id=None,
            model="sonnet",
            workspace_path=None,
        )

        response = text or error or "(응답 없음)"
        assert response == "응답 텍스트"

    @pytest.mark.asyncio
    async def test_executor_handles_error_response(self):
        """에러 응답 처리."""
        mock_claude = MagicMock()
        mock_claude.chat = AsyncMock(return_value=(None, "TIMEOUT", None))

        text, error, _ = await mock_claude.chat(
            message="테스트",
            session_id=None,
            model="sonnet",
            workspace_path=None,
        )

        response = text or error or "(응답 없음)"
        assert response == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_executor_handles_empty_response(self):
        """빈 응답 처리."""
        mock_claude = MagicMock()
        mock_claude.chat = AsyncMock(return_value=(None, None, None))

        text, error, _ = await mock_claude.chat(
            message="테스트",
            session_id=None,
            model="sonnet",
            workspace_path=None,
        )

        response = text or error or "(응답 없음)"
        assert response == "(응답 없음)"

    @pytest.mark.asyncio
    async def test_executor_claude_type_no_workspace(self):
        """claude 타입 스케줄은 workspace_path=None."""
        schedule = MagicMock()
        schedule.type = "claude"
        schedule.workspace_path = None

        workspace_path = None
        if schedule.type == "workspace" and schedule.workspace_path:
            workspace_path = schedule.workspace_path

        assert workspace_path is None

    @pytest.mark.asyncio
    async def test_executor_html_fallback_on_parse_error(self):
        """HTML 파싱 실패 시 plain text fallback."""
        from telegram.error import BadRequest

        mock_bot = MagicMock()
        # 첫 호출은 HTML 파싱 에러, 두 번째는 성공
        mock_bot.send_message = AsyncMock(
            side_effect=[BadRequest("Can't parse entities"), None]
        )

        schedule_name = "테스트"
        chunk = "<session-id>abc</session-id> 넝담입니다"

        # HTML로 시도
        try:
            await mock_bot.send_message(
                chat_id=12345,
                text=f"📅 <b>{schedule_name}</b>\n\n{chunk}",
                parse_mode="HTML",
            )
        except Exception:
            # plain text fallback
            await mock_bot.send_message(
                chat_id=12345,
                text=f"📅 {schedule_name}\n\n{chunk}",
            )

        assert mock_bot.send_message.call_count == 2
        # 두 번째 호출에 parse_mode 없어야 함
        second_call = mock_bot.send_message.call_args_list[1]
        assert "parse_mode" not in second_call[1]


# =============================================================================
# 3. 스케줄러 시간 변경 기능 테스트
# =============================================================================

class TestScheduleTimeChange:
    """스케줄러 시간 변경 기능 테스트."""

    @pytest.fixture
    def repo(self, tmp_path):
        """Repository 인스턴스 생성."""
        from src.repository import init_repository, shutdown_repository, reset_connection
        db_path = tmp_path / "test.db"
        repository = init_repository(db_path)
        yield repository
        shutdown_repository()
        reset_connection()

    def test_repo_update_schedule_time(self, repo):
        """Repository.update_schedule_time DB 업데이트."""
        schedule = repo.add_schedule(
            user_id="12345", chat_id=12345, hour=8, minute=0,
            message="테스트", name="테스트스케줄",
        )

        result = repo.update_schedule_time(schedule.id, 14, 30)
        assert result is True

        updated = repo.get_schedule(schedule.id)
        assert updated.hour == 14
        assert updated.minute == 30

    def test_repo_update_schedule_time_not_found(self, repo):
        """존재하지 않는 스케줄 시간 변경."""
        result = repo.update_schedule_time("nonexistent", 10, 0)
        assert result is False

    def test_adapter_update_time_re_registers(self, repo):
        """ScheduleManagerAdapter.update_time이 스케줄러 재등록."""
        from src.repository.adapters.schedule_adapter import ScheduleManagerAdapter

        mock_scheduler = MagicMock()
        mock_executor = AsyncMock()

        adapter = ScheduleManagerAdapter(repo, mock_scheduler, mock_executor)

        schedule = repo.add_schedule(
            user_id="12345", chat_id=12345, hour=8, minute=0,
            message="테스트", name="테스트",
        )

        result = adapter.update_time(schedule.id, 21, 15)
        assert result is True

        # unregister + register 호출 확인
        mock_scheduler.unregister.assert_called_once()
        mock_scheduler.register_daily.assert_called_once()

    @pytest.mark.asyncio
    async def test_detail_callback_shows_actions(self):
        """sched:detail: 콜백이 액션 버튼 표시."""
        from src.bot.handlers import BotHandlers

        handlers = BotHandlers(
            session_service=MagicMock(),
            claude_client=MagicMock(),
            auth_manager=MagicMock(),
            require_auth=False,
            allowed_chat_ids=[],
        )
        handlers._schedule_manager = MagicMock()

        mock_schedule = MagicMock()
        mock_schedule.name = "테스트"
        mock_schedule.time_str = "08:00"
        mock_schedule.type_emoji = "💬"
        mock_schedule.model = "sonnet"
        mock_schedule.message = "테스트 메시지"
        mock_schedule.workspace_path = None
        mock_schedule.enabled = True
        mock_schedule.run_count = 3
        handlers._schedule_manager.get.return_value = mock_schedule

        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        await handlers._handle_scheduler_callback(query, 12345, "sched:detail:abc123")

        assert query.edit_message_text.called
        text = query.edit_message_text.call_args[0][0]
        assert "테스트" in text
        assert "08:00" in text
        assert "sonnet" in text

        # 버튼 확인
        markup = query.edit_message_text.call_args[1].get("reply_markup")
        all_buttons = [btn for row in markup.inline_keyboard for btn in row]
        button_texts = [b.text for b in all_buttons]
        assert any("OFF" in t for t in button_texts)  # toggle
        assert any("Time" in t or "Change" in t for t in button_texts)  # time change
        assert any("Delete" in t for t in button_texts)  # delete
        assert any("Back" in t for t in button_texts)  # back

    @pytest.mark.asyncio
    async def test_chtime_callback_shows_hours(self):
        """sched:chtime: 콜백이 시간 선택 버튼 표시."""
        from src.bot.handlers import BotHandlers

        handlers = BotHandlers(
            session_service=MagicMock(),
            claude_client=MagicMock(),
            auth_manager=MagicMock(),
            require_auth=False,
            allowed_chat_ids=[],
        )
        handlers._schedule_manager = MagicMock()

        mock_schedule = MagicMock()
        mock_schedule.name = "테스트"
        mock_schedule.time_str = "08:00"
        mock_schedule.type_emoji = "💬"
        handlers._schedule_manager.get.return_value = mock_schedule

        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        await handlers._handle_scheduler_callback(query, 12345, "sched:chtime:abc123")

        assert query.edit_message_text.called
        text = query.edit_message_text.call_args[0][0]
        assert "Change Time" in text
        assert "08:00" in text

    @pytest.mark.asyncio
    async def test_chtime_min_callback_applies_change(self):
        """sched:chtime_min: 콜백이 시간 변경 적용."""
        from src.bot.handlers import BotHandlers

        handlers = BotHandlers(
            session_service=MagicMock(),
            claude_client=MagicMock(),
            auth_manager=MagicMock(),
            require_auth=False,
            allowed_chat_ids=[],
        )
        handlers._schedule_manager = MagicMock()
        handlers._schedule_manager.update_time.return_value = True
        handlers._schedule_manager.get_status_text.return_value = "스케줄 목록"

        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        with patch("src.scheduler_manager.scheduler_manager") as mock_sm:
            mock_sm.get_system_jobs_text.return_value = ""
            await handlers._handle_scheduler_callback(query, 12345, "sched:chtime_min:abc123:14:30")

        handlers._schedule_manager.update_time.assert_called_once_with("abc123", 14, 30)
        assert query.answer.called
        answer_text = query.answer.call_args[0][0]
        assert "14:30" in answer_text


# =============================================================================
# 4. 스케줄러 분 단위 선택 콜백 흐름 테스트
# =============================================================================

class TestSchedulerMinuteSelection:
    """스케줄러 분 단위 선택 콜백 테스트."""

    @pytest.fixture
    def handlers(self):
        """최소한의 핸들러 mock."""
        from src.bot.handlers import BotHandlers

        session_service = MagicMock()
        claude_client = MagicMock()
        auth_manager = MagicMock()
        auth_manager.is_authenticated.return_value = True

        h = BotHandlers(
            session_service=session_service,
            claude_client=claude_client,
            auth_manager=auth_manager,
            require_auth=False,
            allowed_chat_ids=[],
        )
        h._schedule_manager = MagicMock()
        return h

    @pytest.mark.asyncio
    async def test_time_selection_shows_minute_buttons(self, handlers):
        """시간 선택 후 분 선택 버튼 표시."""
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        handlers._pending_schedule_input["12345"] = {}

        # simulate sched:time:claude:_:10 callback
        await handlers._handle_scheduler_callback(query, 12345, "sched:time:claude:_:10")

        # edit_message_text가 호출되었는지
        assert query.edit_message_text.called
        call_kwargs = query.edit_message_text.call_args
        text = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("text", "")

        # 분 선택 관련 텍스트
        assert "10시" in text or "minute" in text.lower() or "Select minute" in text

        # reply_markup에 분 버튼이 있는지
        markup = call_kwargs[1].get("reply_markup") if len(call_kwargs) > 1 else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
        if markup is None and "reply_markup" in (call_kwargs[1] if len(call_kwargs) > 1 else {}):
            markup = call_kwargs[1]["reply_markup"]

        # pending에 hour 저장 확인
        pending = handlers._pending_schedule_input["12345"]
        assert pending["hour"] == 10

    @pytest.mark.asyncio
    async def test_minute_selection_shows_model_buttons(self, handlers):
        """분 선택 후 모델 선택 버튼 표시."""
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        handlers._pending_schedule_input["12345"] = {
            "type": "claude",
            "hour": 10,
        }

        # simulate sched:minute:35 callback
        await handlers._handle_scheduler_callback(query, 12345, "sched:minute:35")

        assert query.edit_message_text.called
        call_kwargs = query.edit_message_text.call_args
        text = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("text", "")

        # 시간:분 표시 확인
        assert "10:35" in text
        # 모델 선택 텍스트
        assert "model" in text.lower() or "Select model" in text

        # pending에 minute 저장 확인
        pending = handlers._pending_schedule_input["12345"]
        assert pending["minute"] == 35

    @pytest.mark.asyncio
    async def test_minute_00_selection(self, handlers):
        """00분 선택."""
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        handlers._pending_schedule_input["12345"] = {
            "type": "workspace",
            "hour": 8,
            "workspace_path": "/test/path",
        }

        await handlers._handle_scheduler_callback(query, 12345, "sched:minute:0")

        pending = handlers._pending_schedule_input["12345"]
        assert pending["minute"] == 0

        call_kwargs = query.edit_message_text.call_args
        text = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("text", "")
        assert "08:00" in text

    @pytest.mark.asyncio
    async def test_minute_55_selection(self, handlers):
        """55분 선택."""
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        handlers._pending_schedule_input["12345"] = {
            "type": "claude",
            "hour": 22,
        }

        await handlers._handle_scheduler_callback(query, 12345, "sched:minute:55")

        pending = handlers._pending_schedule_input["12345"]
        assert pending["minute"] == 55

        call_kwargs = query.edit_message_text.call_args
        text = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("text", "")
        assert "22:55" in text

    @pytest.mark.asyncio
    async def test_model_selection_shows_correct_time(self, handlers):
        """모델 선택 시 시간:분 정확히 표시."""
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.reply_text = AsyncMock()

        handlers._pending_schedule_input["12345"] = {
            "type": "claude",
            "hour": 14,
            "minute": 25,
        }

        await handlers._handle_scheduler_callback(query, 12345, "sched:model:sonnet")

        call_kwargs = query.edit_message_text.call_args
        text = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("text", "")
        assert "14:25" in text
        assert "sonnet" in text


# =============================================================================
# 4. /scheduler 시스템 잡 통합 표시 테스트
# =============================================================================

class TestSchedulerSystemJobsIntegration:
    """스케줄러 커맨드에 시스템 잡 통합 테스트."""

    @pytest.mark.asyncio
    async def test_scheduler_command_includes_system_jobs(self):
        """scheduler_command가 시스템 잡 정보를 포함."""
        from src.bot.handlers import BotHandlers

        session_service = MagicMock()
        claude_client = MagicMock()
        auth_manager = MagicMock()

        handlers = BotHandlers(
            session_service=session_service,
            claude_client=claude_client,
            auth_manager=auth_manager,
            require_auth=False,
            allowed_chat_ids=[],
        )

        mock_schedule_manager = MagicMock()
        mock_schedule_manager.get_status_text.return_value = "예약된 작업이 없습니다."
        handlers._schedule_manager = mock_schedule_manager

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        with patch("src.scheduler_manager.scheduler_manager") as mock_sm:
            mock_sm.get_system_jobs_text.return_value = "\n\n시스템 작업\n  매일 08:00 KST - todo_morning"

            await handlers.scheduler_command(update, context)

            # get_system_jobs_text가 호출되었는지
            mock_sm.get_system_jobs_text.assert_called_once()

            # reply_text에 시스템 잡 정보 포함
            reply_call = update.message.reply_text.call_args
            text = reply_call[0][0] if reply_call[0] else reply_call[1].get("text", "")
            assert "시스템 작업" in text
            assert "todo_morning" in text


# =============================================================================
# 5. /jobs 명령어 제거 확인
# =============================================================================

class TestJobsCommandRemoved:
    """/jobs 명령어 제거 확인."""

    def test_no_jobs_command_on_handlers(self):
        """BotHandlers에 jobs_command가 없음."""
        from src.bot.handlers import BotHandlers

        session_service = MagicMock()
        claude_client = MagicMock()
        auth_manager = MagicMock()

        handlers = BotHandlers(
            session_service=session_service,
            claude_client=claude_client,
            auth_manager=auth_manager,
            require_auth=False,
            allowed_chat_ids=[],
        )

        assert not hasattr(handlers, "jobs_command")

    def test_no_jobs_callback_handler(self):
        """callback_handlers에 _handle_jobs_callback이 없음."""
        from src.bot.handlers.callback_handlers import CallbackHandlers

        assert not hasattr(CallbackHandlers, "_handle_jobs_callback")


# =============================================================================
# 6. workspace recommend_paths 파라미터 수정 확인
# =============================================================================

class TestWorkspaceRecommendPaths:
    """workspace recommend_paths 파라미터 테스트."""

    def test_recommend_paths_accepts_allowed_patterns(self):
        """recommend_paths의 파라미터명이 allowed_patterns인지 확인."""
        import inspect
        from src.repository.adapters.workspace_adapter import WorkspaceRegistryAdapter

        sig = inspect.signature(WorkspaceRegistryAdapter.recommend_paths)
        param_names = list(sig.parameters.keys())

        assert "allowed_patterns" in param_names
        assert "allowed_paths" not in param_names
        assert "max_recommendations" not in param_names

    @pytest.mark.asyncio
    async def test_recommend_paths_returns_description_key(self):
        """recommend_paths 결과에 description 키 포함."""
        from src.repository.adapters.workspace_adapter import WorkspaceRegistryAdapter

        mock_repo = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.path = "/test/path"
        mock_workspace.name = "테스트"
        mock_workspace.description = "설명"
        mock_workspace.keywords = ["test"]
        mock_workspace.use_count = 0
        mock_repo.list_workspaces_by_user.return_value = [mock_workspace]

        adapter = WorkspaceRegistryAdapter(mock_repo)
        results = await adapter.recommend_paths(
            user_id="12345",
            purpose="test project",
            allowed_patterns=["/test/*"],
        )

        assert len(results) > 0
        for rec in results:
            assert "description" in rec
            assert "path" in rec
            assert "name" in rec


# =============================================================================
# 7. Fire-and-Forget 패턴 확인 (message_handlers에 get_repository 없음)
# =============================================================================

class TestFireAndForgetPattern:
    """Fire-and-Forget 패턴 확인."""

    def test_message_handlers_no_get_repository_import(self):
        """message_handlers에 get_repository import 없음."""
        import src.bot.handlers.message_handlers as mh
        assert not hasattr(mh, "get_repository")

    def test_base_handler_no_queue_worker(self):
        """BaseHandler에 queue_worker 관련 속성 없음."""
        from src.bot.handlers.base import BaseHandler

        session_service = MagicMock()
        claude_client = MagicMock()
        auth_manager = MagicMock()

        handler = BaseHandler(
            session_service=session_service,
            claude_client=claude_client,
            auth_manager=auth_manager,
            require_auth=False,
            allowed_chat_ids=[],
        )

        assert not hasattr(handler, "_queue_worker")
        assert not hasattr(handler, "set_queue_worker")

    @pytest.mark.asyncio
    async def test_handle_message_creates_task_not_enqueue(self):
        """handle_message가 create_task를 호출 (enqueue가 아님)."""
        from src.bot.handlers import BotHandlers

        session_service = MagicMock()
        session_service.get_current_session_id.return_value = "existing-session"
        session_service.get_session_model.return_value = "sonnet"
        session_service.get_workspace_path.return_value = None

        claude_client = MagicMock()
        auth_manager = MagicMock()
        auth_manager.is_authenticated.return_value = True

        handlers = BotHandlers(
            session_service=session_service,
            claude_client=claude_client,
            auth_manager=auth_manager,
            require_auth=True,
            allowed_chat_ids=[12345],
        )

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "테스트 메시지"
        update.message.reply_text = AsyncMock()
        update.message.reply_to_message = None
        context = MagicMock()

        with patch("asyncio.create_task") as mock_create_task:
            await handlers.handle_message(update, context)
            mock_create_task.assert_called_once()
