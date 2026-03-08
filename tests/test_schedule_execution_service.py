"""Unit tests for schedule execution runtime service."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from src.services.schedule_execution_service import ScheduleExecutionService


class TestScheduleExecutionService:
    """ScheduleExecutionService tests."""

    @pytest.fixture
    def mock_bot(self):
        bot = MagicMock()
        bot.send_message = AsyncMock()
        return bot

    @pytest.fixture
    def mock_ai_registry(self):
        registry = MagicMock()
        client = MagicMock()
        client.chat = AsyncMock(return_value=("응답 텍스트", None, None))
        registry.get_client.return_value = client
        return registry

    @pytest.fixture
    def mock_plugins(self):
        loader = MagicMock()
        loader.get_plugin_by_name.return_value = MagicMock(
            execute_scheduled_action=AsyncMock(return_value="플러그인 응답")
        )
        return loader

    @pytest.fixture
    def mock_schedule_manager(self):
        return MagicMock()

    @pytest.fixture
    def service(self, mock_bot, mock_ai_registry, mock_plugins, mock_schedule_manager):
        return ScheduleExecutionService(
            bot=mock_bot,
            ai_registry=mock_ai_registry,
            plugin_loader=mock_plugins,
            schedule_manager=mock_schedule_manager,
        )

    @pytest.mark.asyncio
    async def test_execute_workspace_schedule_uses_workspace_path(
        self, service, mock_ai_registry, mock_schedule_manager, mock_bot
    ):
        schedule = MagicMock()
        schedule.id = "schedule-1"
        schedule.type = "workspace"
        schedule.workspace_path = "/Users/test/project"
        schedule.ai_provider = "claude"
        schedule.message = "테스트"
        schedule.model = "sonnet"
        schedule.chat_id = 12345
        schedule.name = "워크스페이스"

        await service.execute(schedule)

        mock_ai_registry.get_client.return_value.chat.assert_called_once_with(
            message="테스트",
            session_id=None,
            model="sonnet",
            workspace_path="/Users/test/project",
        )
        mock_schedule_manager.update_run.assert_called_once_with("schedule-1")
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_plugin_schedule_uses_plugin_action(
        self, service, mock_plugins, mock_schedule_manager
    ):
        schedule = MagicMock()
        schedule.id = "schedule-1"
        schedule.type = "plugin"
        schedule.plugin_name = "todo"
        schedule.action_name = "daily_wrap"
        schedule.chat_id = 12345
        schedule.name = "플러그인"

        await service.execute(schedule)

        mock_plugins.get_plugin_by_name.assert_called_once_with("todo")
        plugin = mock_plugins.get_plugin_by_name.return_value
        plugin.execute_scheduled_action.assert_called_once_with("daily_wrap", 12345)
        mock_schedule_manager.update_run.assert_called_once_with("schedule-1")

    @pytest.mark.asyncio
    async def test_execute_falls_back_to_plain_text_when_html_send_fails(
        self, service, mock_bot
    ):
        mock_bot.send_message = AsyncMock(side_effect=[BadRequest("bad html"), None])
        schedule = MagicMock()
        schedule.id = "schedule-1"
        schedule.type = "workspace"
        schedule.workspace_path = None
        schedule.ai_provider = "claude"
        schedule.message = "테스트"
        schedule.model = "sonnet"
        schedule.chat_id = 12345
        schedule.name = "워크스페이스"

        await service.execute(schedule)

        assert mock_bot.send_message.await_count == 2
        first_call = mock_bot.send_message.await_args_list[0].kwargs
        second_call = mock_bot.send_message.await_args_list[1].kwargs
        assert first_call["parse_mode"] == "HTML"
        assert "parse_mode" not in second_call

    @pytest.mark.asyncio
    async def test_execute_records_error_when_plugin_missing(
        self, service, mock_plugins, mock_schedule_manager, mock_bot
    ):
        mock_plugins.get_plugin_by_name.return_value = None
        schedule = MagicMock()
        schedule.id = "schedule-1"
        schedule.type = "plugin"
        schedule.plugin_name = "missing"
        schedule.action_name = "daily_wrap"
        schedule.chat_id = 12345
        schedule.name = "플러그인"

        await service.execute(schedule)

        mock_schedule_manager.update_run.assert_called_once()
        assert mock_schedule_manager.update_run.call_args.kwargs["last_error"] == "Plugin 'missing' not found"
        mock_bot.send_message.assert_not_called()
