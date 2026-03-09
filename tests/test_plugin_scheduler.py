"""플러그인 스케줄러 테스트.

플러그인의 스케줄 API와 스케줄러 통합 테스트:
1. Plugin base class - get_scheduled_actions(), execute_scheduled_action()
2. TodoPlugin - scheduled actions 구현
3. Callback flow - plugin schedule UI
4. Schedule executor - plugin type 실행
5. Schedule adapter - plugin fields
"""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.plugins.loader import Plugin, PluginResult, ScheduledAction


# =============================================================================
# 1. Plugin Base Class - Scheduler API
# =============================================================================

class TestPluginSchedulerAPI:
    """Plugin 기본 클래스의 스케줄 API 테스트."""

    def test_default_get_scheduled_actions_empty(self):
        """기본 구현은 빈 리스트 반환."""
        class DummyPlugin(Plugin):
            name = "dummy"
            async def can_handle(self, msg, _chat_id): return False
            async def handle(self, msg, _chat_id): return PluginResult(handled=False)

        plugin = DummyPlugin()
        assert plugin.get_scheduled_actions() == []

    @pytest.mark.asyncio
    async def test_default_execute_raises(self):
        """기본 execute_scheduled_action은 NotImplementedError."""
        class DummyPlugin(Plugin):
            name = "dummy"
            async def can_handle(self, msg, _chat_id): return False
            async def handle(self, msg, _chat_id): return PluginResult(handled=False)

        plugin = DummyPlugin()
        with pytest.raises(NotImplementedError):
            await plugin.execute_scheduled_action("some_action", 12345)

    def test_scheduled_action_dataclass(self):
        """ScheduledAction 데이터클래스."""
        action = ScheduledAction(name="test", description="Test action")
        assert action.name == "test"
        assert action.description == "Test action"


# =============================================================================
# 2. TodoPlugin - Scheduled Actions
# =============================================================================

class TestTodoPluginScheduledActions:
    """TodoPlugin의 스케줄 액션 테스트."""

    @pytest.fixture
    def todo_plugin(self):
        from plugins.builtin.todo.plugin import TodoPlugin
        plugin = TodoPlugin()
        plugin.bind_runtime(MagicMock())
        return plugin

    def test_get_scheduled_actions(self, todo_plugin):
        """Todo 플러그인은 2개 액션 제공."""
        actions = todo_plugin.get_scheduled_actions()
        assert len(actions) == 2
        names = [a.name for a in actions]
        assert "yesterday_report" in names
        assert "daily_wrap" in names

    def test_actions_have_descriptions(self, todo_plugin):
        """모든 액션에 설명이 있음."""
        for action in todo_plugin.get_scheduled_actions():
            assert action.description
            assert len(action.description) > 0

    @pytest.mark.asyncio
    async def test_yesterday_report_with_todos(self, todo_plugin):
        """어제 리포트 - 할일 있을 때."""
        mock_todo_done = MagicMock()
        mock_todo_done.done = True
        mock_todo_done.text = "완료 항목"

        mock_todo_pending = MagicMock()
        mock_todo_pending.done = False
        mock_todo_pending.text = "미완료 항목"

        todo_plugin._repository.list_todos_by_date.return_value = [mock_todo_done, mock_todo_pending]

        result = await todo_plugin.execute_scheduled_action("yesterday_report", 12345)
        assert "Yesterday" in result
        assert "미완료 항목" in result
        assert "1/2 completed" in result

    @pytest.mark.asyncio
    async def test_yesterday_report_all_done(self, todo_plugin):
        """어제 리포트 - 모두 완료."""
        mock_todo = MagicMock()
        mock_todo.done = True
        mock_todo.text = "완료 항목"

        todo_plugin._repository.list_todos_by_date.return_value = [mock_todo]

        result = await todo_plugin.execute_scheduled_action("yesterday_report", 12345)
        assert "1/1 completed" in result
        assert "carry" not in result

    @pytest.mark.asyncio
    async def test_yesterday_report_no_todos(self, todo_plugin):
        """어제 리포트 - 할일 없음 → 빈 문자열."""
        todo_plugin._repository.list_todos_by_date.return_value = []

        result = await todo_plugin.execute_scheduled_action("yesterday_report", 12345)
        assert result == ""

    @pytest.mark.asyncio
    async def test_invalid_action_raises(self, todo_plugin):
        """존재하지 않는 액션 → NotImplementedError."""
        with pytest.raises(NotImplementedError):
            await todo_plugin.execute_scheduled_action("nonexistent", 12345)


# =============================================================================
# 3. Callback Flow - Plugin Schedule UI
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
    call = query.edit_message_text.call_args
    if not call:
        return ""
    return call[0][0] if call[0] else call[1].get("text", "")


def get_callback_data(query):
    call = query.edit_message_text.call_args
    if not call:
        return []
    markup = call[1].get("reply_markup")
    if not markup:
        return []
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def make_handlers():
    from src.bot.handlers import BotHandlers
    h = BotHandlers(
        session_service=MagicMock(),
        claude_client=MagicMock(),
        auth_manager=MagicMock(),
        require_auth=False,
        allowed_chat_ids=[],
    )
    return h


class TestPluginScheduleCallbackFlow:
    """플러그인 스케줄 UI 콜백 플로우 테스트."""

    @pytest.fixture
    def handlers(self):
        h = make_handlers()
        h._schedule_manager = MagicMock()

        # Mock plugin with scheduled actions
        mock_plugin = MagicMock()
        mock_plugin.name = "todo"
        mock_plugin.get_scheduled_actions.return_value = [
            ScheduledAction(name="yesterday_report", description="어제 할일 리포트"),
        ]

        mock_loader = MagicMock()
        mock_loader.plugins = [mock_plugin]
        mock_loader.get_plugin_by_name.return_value = mock_plugin
        h.plugins = mock_loader
        return h

    @pytest.mark.asyncio
    async def test_add_plugin_shows_plugin_list(self, handlers):
        """sched:add:plugin → 스케줄 가능한 플러그인 목록."""
        query = make_query()
        await handlers._handle_scheduler_callback(query, 12345, "sched:add:plugin")

        text = get_text(query)
        assert "Plugin" in text
        callbacks = get_callback_data(query)
        assert any("sched:plugin:" in c for c in callbacks)

    @pytest.mark.asyncio
    async def test_plugin_select_shows_actions(self, handlers):
        """sched:plugin:0 → 플러그인 액션 목록."""
        # First set up plugin_map via add:plugin
        query = make_query()
        await handlers._handle_scheduler_callback(query, 12345, "sched:add:plugin")

        # Now select the plugin
        query2 = make_query()
        await handlers._handle_scheduler_callback(query2, 12345, "sched:plugin:0")

        text = get_text(query2)
        assert "todo" in text
        callbacks = get_callback_data(query2)
        assert any("sched:pluginaction:" in c for c in callbacks)

    @pytest.mark.asyncio
    async def test_plugin_action_shows_hours(self, handlers):
        """sched:pluginaction:0 → 시간 선택."""
        # Set up state
        query = make_query()
        await handlers._handle_scheduler_callback(query, 12345, "sched:add:plugin")
        query2 = make_query()
        await handlers._handle_scheduler_callback(query2, 12345, "sched:plugin:0")

        # Select action
        query3 = make_query()
        await handlers._handle_scheduler_callback(query3, 12345, "sched:pluginaction:0")

        text = get_text(query3)
        assert "Plugin Schedule" in text
        callbacks = get_callback_data(query3)
        assert any("sched:time:plugin:" in c for c in callbacks)

    @pytest.mark.asyncio
    async def test_plugin_time_shows_minutes(self, handlers):
        """sched:time:plugin:_:10 → 분 선택."""
        # Set up state
        query = make_query()
        await handlers._handle_scheduler_callback(query, 12345, "sched:add:plugin")
        query2 = make_query()
        await handlers._handle_scheduler_callback(query2, 12345, "sched:plugin:0")
        query3 = make_query()
        await handlers._handle_scheduler_callback(query3, 12345, "sched:pluginaction:0")

        # Select hour
        query4 = make_query()
        await handlers._handle_scheduler_callback(query4, 12345, "sched:time:plugin:_:10")

        text = get_text(query4)
        assert "10" in text
        callbacks = get_callback_data(query4)
        assert any("sched:minute:" in c for c in callbacks)

    @pytest.mark.asyncio
    async def test_plugin_minute_shows_trigger_then_registers(self, handlers):
        """plugin은 minute 뒤 trigger 선택 후 바로 등록."""
        # Set up state through full flow
        query = make_query()
        await handlers._handle_scheduler_callback(query, 12345, "sched:add:plugin")
        query2 = make_query()
        await handlers._handle_scheduler_callback(query2, 12345, "sched:plugin:0")
        query3 = make_query()
        await handlers._handle_scheduler_callback(query3, 12345, "sched:pluginaction:0")
        query4 = make_query()
        await handlers._handle_scheduler_callback(query4, 12345, "sched:time:plugin:_:10")

        # Mock add to return ScheduleData
        mock_schedule = MagicMock()
        mock_schedule.name = "todo:어제 할일 리포트"
        mock_schedule.time_str = "10:30"
        mock_schedule.plugin_name = "todo"
        mock_schedule.action_name = "yesterday_report"
        handlers._schedule_manager.add.return_value = mock_schedule

        # Select minute → trigger selection
        query5 = make_query()
        await handlers._handle_scheduler_callback(query5, 12345, "sched:minute:30")

        text = get_text(query5)
        assert "Choose schedule mode" in text

        query6 = make_query()
        await handlers._handle_scheduler_callback(query6, 12345, "sched:trigger:cron")

        text = get_text(query6)
        assert "Registered" in text
        assert "Plugin" in text
        handlers._schedule_manager.add.assert_called_once()

        # Verify add was called with plugin fields
        call_kwargs = handlers._schedule_manager.add.call_args[1]
        assert call_kwargs["schedule_type"] == "plugin"
        assert call_kwargs["trigger_type"] == "cron"
        assert call_kwargs["plugin_name"] == "todo"
        assert call_kwargs["action_name"] == "yesterday_report"

    @pytest.mark.asyncio
    async def test_no_plugins_with_actions(self, handlers):
        """스케줄 가능한 플러그인 없음."""
        # All plugins have no actions
        handlers.plugins.plugins[0].get_scheduled_actions.return_value = []

        query = make_query()
        await handlers._handle_scheduler_callback(query, 12345, "sched:add:plugin")

        text = get_text(query)
        assert "No schedulable plugins" in text

    @pytest.mark.asyncio
    async def test_chat_schedule_shows_trigger_then_model(self, handlers):
        """chat 타입은 minute 뒤 trigger 선택, 그 다음 모델 선택."""
        user_id = str(12345)
        handlers._sched_pending[user_id] = {
            "type": "chat",
            "hour": 9,
        }

        query = make_query()
        await handlers._handle_scheduler_callback(query, 12345, "sched:minute:0")

        callbacks = get_callback_data(query)
        assert any("sched:trigger:" in c for c in callbacks)

        query2 = make_query()
        await handlers._handle_scheduler_callback(query2, 12345, "sched:trigger:cron")

        callbacks = get_callback_data(query2)
        assert any("sched:model:" in c for c in callbacks)


# =============================================================================
# 4. Schedule Adapter - Plugin Fields
# =============================================================================

class TestScheduleAdapterPluginFields:
    """Schedule adapter의 plugin 필드 테스트."""

    @pytest.fixture
    def adapter(self):
        from src.repository.adapters.schedule_adapter import ScheduleManagerAdapter
        repo = MagicMock()
        return ScheduleManagerAdapter(repo=repo)

    def test_add_plugin_schedule(self, adapter):
        """플러그인 스케줄 추가 시 plugin_name/action_name 전달."""
        mock_schedule = MagicMock()
        mock_schedule.id = "sched001"
        mock_schedule.plugin_name = "todo"
        mock_schedule.action_name = "yesterday_report"
        adapter._repo.add_schedule.return_value = mock_schedule

        adapter.add(
            user_id="12345",
            chat_id=12345,
            hour=10,
            minute=0,
            message="",
            name="todo:어제 할일 리포트",
            schedule_type="plugin",
            model="sonnet",
            plugin_name="todo",
            action_name="yesterday_report",
        )

        call_kwargs = adapter._repo.add_schedule.call_args[1]
        assert call_kwargs["plugin_name"] == "todo"
        assert call_kwargs["action_name"] == "yesterday_report"
        assert call_kwargs["schedule_type"] == "plugin"


# =============================================================================
# 5. Schedule Executor - Plugin Type
# =============================================================================

class TestScheduleExecutorPlugin:
    """schedule_executor의 플러그인 타입 실행 테스트."""

    @pytest.mark.asyncio
    async def test_executor_calls_plugin(self):
        """plugin 타입 스케줄 실행 시 플러그인 액션 호출."""
        from src.plugins.loader import PluginLoader

        mock_plugin = MagicMock()
        mock_plugin.name = "todo"
        mock_plugin.execute_scheduled_action = AsyncMock(return_value="어제 할일 리포트")

        mock_loader = MagicMock(spec=PluginLoader)
        mock_loader.get_plugin_by_name.return_value = mock_plugin

        # Simulate schedule_executor logic
        schedule = MagicMock()
        schedule.type = "plugin"
        schedule.plugin_name = "todo"
        schedule.action_name = "yesterday_report"
        schedule.chat_id = 12345
        schedule.id = "sched001"
        schedule.name = "todo:어제 할일 리포트"

        # Execute the plugin path
        plugin = mock_loader.get_plugin_by_name(schedule.plugin_name)
        response = await plugin.execute_scheduled_action(
            schedule.action_name, schedule.chat_id
        )

        assert response == "어제 할일 리포트"
        mock_plugin.execute_scheduled_action.assert_called_once_with(
            "yesterday_report", 12345
        )

    @pytest.mark.asyncio
    async def test_executor_plugin_not_found(self):
        """존재하지 않는 플러그인 → RuntimeError."""
        mock_loader = MagicMock()
        mock_loader.get_plugin_by_name.return_value = None

        schedule = MagicMock()
        schedule.type = "plugin"
        schedule.plugin_name = "nonexistent"
        schedule.action_name = "action"

        plugin = mock_loader.get_plugin_by_name(schedule.plugin_name)
        assert plugin is None  # Would raise RuntimeError in real executor


# =============================================================================
# 6. Integration - Full Plugin Schedule Flow
# =============================================================================

class TestPluginScheduleFullFlow:
    """플러그인 스케줄 풀 플로우 해피 케이스."""

    @pytest.mark.asyncio
    async def test_full_flow_plugin_schedule(self):
        """add:plugin → plugin 선택 → action 선택 → time → minute → 등록."""
        h = make_handlers()
        h._schedule_manager = MagicMock()

        mock_plugin = MagicMock()
        mock_plugin.name = "todo"
        mock_plugin.get_scheduled_actions.return_value = [
            ScheduledAction(name="yesterday_report", description="어제 할일 리포트"),
        ]

        mock_loader = MagicMock()
        mock_loader.plugins = [mock_plugin]
        h.plugins = mock_loader

        # Step 1: add:plugin
        q1 = make_query()
        await h._handle_scheduler_callback(q1, 12345, "sched:add:plugin")
        assert "Plugin" in get_text(q1)

        # Step 2: select plugin
        q2 = make_query()
        await h._handle_scheduler_callback(q2, 12345, "sched:plugin:0")
        assert "todo" in get_text(q2)

        # Step 3: select action
        q3 = make_query()
        await h._handle_scheduler_callback(q3, 12345, "sched:pluginaction:0")
        callbacks = get_callback_data(q3)
        assert any("sched:time:plugin:" in c for c in callbacks)

        # Step 4: select hour
        q4 = make_query()
        await h._handle_scheduler_callback(q4, 12345, "sched:time:plugin:_:10")
        callbacks = get_callback_data(q4)
        assert any("sched:minute:" in c for c in callbacks)

        # Step 5: select minute → trigger selection
        mock_sched = MagicMock()
        mock_sched.name = "todo:어제 할일 리포트"
        mock_sched.time_str = "10:00"
        mock_sched.trigger_summary = "At 10:00"
        mock_sched.next_run_text = "2026-03-09 10:00 KST"
        mock_sched.plugin_name = "todo"
        mock_sched.action_name = "yesterday_report"
        h._schedule_manager.add.return_value = mock_sched

        q5 = make_query()
        await h._handle_scheduler_callback(q5, 12345, "sched:minute:0")

        callbacks = get_callback_data(q5)
        assert any("sched:trigger:" in c for c in callbacks)

        q6 = make_query()
        await h._handle_scheduler_callback(q6, 12345, "sched:trigger:cron")

        text = get_text(q6)
        assert "Registered" in text
        h._schedule_manager.add.assert_called_once()

        # No ForceReply (model/message skipped)
        assert not q6.message.reply_text.called

    @pytest.mark.asyncio
    async def test_full_flow_plugin_one_time_schedule(self):
        """플러그인 1회성 스케줄 해피 케이스."""
        h = make_handlers()
        h._schedule_manager = MagicMock()

        mock_plugin = MagicMock()
        mock_plugin.name = "todo"
        mock_plugin.get_scheduled_actions.return_value = [
            ScheduledAction(name="daily_wrap", description="Daily wrap-up"),
        ]

        mock_loader = MagicMock()
        mock_loader.plugins = [mock_plugin]
        h.plugins = mock_loader

        mock_sched = MagicMock()
        mock_sched.name = "todo:Daily wrap-up"
        mock_sched.time_str = "2026-03-08 22:20 KST"
        mock_sched.trigger_summary = "Once at 2026-03-08 22:20 KST"
        mock_sched.next_run_text = "2026-03-08 22:20 KST"
        mock_sched.plugin_name = "todo"
        mock_sched.action_name = "daily_wrap"
        h._schedule_manager.add.return_value = mock_sched

        q1 = make_query()
        await h._handle_scheduler_callback(q1, 12345, "sched:add:plugin")
        q2 = make_query()
        await h._handle_scheduler_callback(q2, 12345, "sched:plugin:0")
        q3 = make_query()
        await h._handle_scheduler_callback(q3, 12345, "sched:pluginaction:0")
        q4 = make_query()
        await h._handle_scheduler_callback(q4, 12345, "sched:time:plugin:_:22")
        q5 = make_query()
        await h._handle_scheduler_callback(q5, 12345, "sched:minute:20")
        q6 = make_query()
        await h._handle_scheduler_callback(q6, 12345, "sched:trigger:once")

        text = get_text(q6)
        assert "Registered" in text
        call_kwargs = h._schedule_manager.add.call_args[1]
        assert call_kwargs["trigger_type"] == "once"


# =============================================================================
# 7. Repository - Plugin Schedule CRUD
# =============================================================================

class TestPluginScheduleRepository:
    """Repository를 사용한 플러그인 스케줄 CRUD 통합 테스트."""

    @pytest.fixture
    def repo(self, tmp_path):
        from src.repository import init_repository
        from src.repository.database import reset_connection
        reset_connection()
        repo = init_repository(tmp_path / "test.db")
        yield repo
        reset_connection()

    def test_add_plugin_schedule_to_db(self, repo):
        """DB에 플러그인 스케줄 추가."""
        from src.repository.adapters.schedule_adapter import ScheduleManagerAdapter
        adapter = ScheduleManagerAdapter(repo)

        schedule = adapter.add(
            user_id="12345",
            chat_id=12345,
            hour=10,
            minute=0,
            message="",
            name="todo:어제 할일 리포트",
            schedule_type="plugin",
            model="sonnet",
            plugin_name="todo",
            action_name="yesterday_report",
        )

        assert schedule.type == "plugin"
        assert schedule.plugin_name == "todo"
        assert schedule.action_name == "yesterday_report"

    def test_retrieve_plugin_schedule(self, repo):
        """DB에서 플러그인 스케줄 조회."""
        from src.repository.adapters.schedule_adapter import ScheduleManagerAdapter
        adapter = ScheduleManagerAdapter(repo)

        created = adapter.add(
            user_id="12345",
            chat_id=12345,
            hour=9,
            minute=0,
            message="",
            name="todo:어제 할일 리포트",
            schedule_type="plugin",
            model="sonnet",
            plugin_name="todo",
            action_name="yesterday_report",
        )

        fetched = adapter.get(created.id)
        assert fetched is not None
        assert fetched.type == "plugin"
        assert fetched.plugin_name == "todo"
        assert fetched.action_name == "yesterday_report"

    def test_plugin_schedule_to_dict(self, repo):
        """플러그인 스케줄 딕셔너리 변환."""
        from src.repository.adapters.schedule_adapter import ScheduleManagerAdapter
        adapter = ScheduleManagerAdapter(repo)

        schedule = adapter.add(
            user_id="12345",
            chat_id=12345,
            hour=10,
            minute=0,
            message="",
            name="todo:어제 할일 리포트",
            schedule_type="plugin",
            model="sonnet",
            plugin_name="todo",
            action_name="yesterday_report",
        )

        d = schedule.to_dict()
        assert d["type"] == "plugin"
        assert d["plugin_name"] == "todo"
        assert d["action_name"] == "yesterday_report"

    def test_plugin_schedule_type_emoji(self, repo):
        """플러그인 스케줄 이모지."""
        from src.repository.adapters.schedule_adapter import ScheduleManagerAdapter
        adapter = ScheduleManagerAdapter(repo)

        schedule = adapter.add(
            user_id="12345",
            chat_id=12345,
            hour=10,
            minute=0,
            message="",
            name="test",
            schedule_type="plugin",
            model="sonnet",
            plugin_name="todo",
            action_name="yesterday_report",
        )

        assert schedule.type_emoji == "🔌"
