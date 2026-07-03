"""Unit tests for schedule execution runtime service."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TimedOut

from src.ai import get_default_model
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
        client.chat = AsyncMock(return_value=("응답 텍스트", None, "provider-sess-uuid"))
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
    def mock_repo(self):
        repo = MagicMock()
        repo.insert_schedule_delivery_log.return_value = 42
        return repo

    @pytest.fixture
    def service(self, mock_bot, mock_ai_registry, mock_plugins, mock_schedule_manager, mock_repo):
        return ScheduleExecutionService(
            bot=mock_bot,
            ai_registry=mock_ai_registry,
            plugin_loader=mock_plugins,
            schedule_manager=mock_schedule_manager,
            repo=mock_repo,
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
    async def test_execute_agy_schedule_uses_agy_client(self, service, mock_ai_registry):
        schedule = MagicMock()
        schedule.id = "schedule-1"
        schedule.type = "chat"
        schedule.workspace_path = None
        schedule.ai_provider = "agy"
        schedule.message = "테스트"
        schedule.model = "agy-pro-high"
        schedule.chat_id = 12345
        schedule.name = "안티그래비티"

        await service.execute(schedule)

        mock_ai_registry.get_client.assert_called_once_with("agy")
        mock_ai_registry.get_client.return_value.chat.assert_called_once_with(
            message="테스트",
            session_id=None,
            model="agy-pro-high",
            workspace_path=None,
        )

    @pytest.mark.asyncio
    async def test_execute_normalizes_incompatible_schedule_model(
        self, service, mock_ai_registry, mock_repo
    ):
        schedule = MagicMock()
        schedule.id = "schedule-1"
        schedule.type = "chat"
        schedule.workspace_path = None
        schedule.ai_provider = "agy"
        schedule.message = "테스트"
        schedule.model = "sonnet"
        schedule.chat_id = 12345
        schedule.name = "안티그래비티"
        expected_model = get_default_model("agy")

        await service.execute(schedule)

        mock_ai_registry.get_client.assert_called_once_with("agy")
        mock_ai_registry.get_client.return_value.chat.assert_called_once_with(
            message="테스트",
            session_id=None,
            model=expected_model,
            workspace_path=None,
        )
        mock_repo.insert_schedule_delivery_log.assert_called_once()
        assert mock_repo.insert_schedule_delivery_log.call_args.kwargs["model"] == expected_model

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
        plugin.execute_scheduled_action.assert_called_once_with(
            "daily_wrap",
            12345,
            schedule=schedule,
        )
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
    async def test_execute_does_not_plain_fallback_on_network_error(
        self, service, mock_bot, mock_schedule_manager, mock_repo
    ):
        mock_bot.send_message = AsyncMock(side_effect=TimedOut("Timed out"))
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

        assert mock_bot.send_message.await_count == 1
        mock_schedule_manager.update_run.assert_called_once()
        assert "telegram:" in mock_schedule_manager.update_run.call_args.kwargs["last_error"]
        mock_repo.mark_message_delivery_failed.assert_called_once()
        mock_repo.insert_schedule_run.assert_called_once()
        assert mock_repo.insert_schedule_run.call_args.kwargs["status"] == "delivery_failed"
        assert mock_repo.insert_schedule_run.call_args.kwargs["message_log_id"] == 42

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
        mock_repo = service._repo
        mock_repo.insert_schedule_run.assert_called_once()
        assert mock_repo.insert_schedule_run.call_args.kwargs["status"] == "failed"
        assert mock_repo.insert_schedule_run.call_args.kwargs["result_type"] == "plugin"

    @pytest.mark.asyncio
    async def test_execute_records_no_output_run_without_message_log(
        self, service, mock_plugins, mock_repo, mock_bot, mock_schedule_manager
    ):
        """응답이 없는 정상 실행은 message_log 없이 schedule_runs에 no_output으로 남긴다."""
        mock_plugins.get_plugin_by_name.return_value = MagicMock(
            execute_scheduled_action=AsyncMock(return_value=None)
        )
        schedule = MagicMock()
        schedule.id = "schedule-empty"
        schedule.type = "plugin"
        schedule.plugin_name = "calendar"
        schedule.action_name = "upcoming"
        schedule.chat_id = 12345
        schedule.name = "캘린더"

        await service.execute(schedule)

        mock_schedule_manager.update_run.assert_called_once_with("schedule-empty")
        mock_repo.insert_schedule_delivery_log.assert_not_called()
        mock_repo.insert_schedule_run.assert_called_once()
        kwargs = mock_repo.insert_schedule_run.call_args.kwargs
        assert kwargs["schedule_id"] == "schedule-empty"
        assert kwargs["status"] == "no_output"
        assert kwargs["result_type"] == "none"
        assert kwargs["message_log_id"] is None
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_inserts_delivery_log_for_ai_schedule(
        self, service, mock_repo, mock_bot, mock_schedule_manager
    ):
        """AI 스케줄 실행 시 message_log에 pending delivery를 저장하고 Session 버튼을 포함한다."""
        schedule = MagicMock()
        schedule.id = "schedule-1"
        schedule.type = "chat"
        schedule.ai_provider = "claude"
        schedule.message = "안녕"
        schedule.model = "sonnet"
        schedule.chat_id = 12345
        schedule.name = "테스트"
        schedule.workspace_path = None

        await service.execute(schedule)

        mock_repo.insert_schedule_delivery_log.assert_called_once()
        kwargs = mock_repo.insert_schedule_delivery_log.call_args.kwargs
        assert kwargs["chat_id"] == 12345
        assert kwargs["schedule_id"] == "schedule-1"
        assert kwargs["request"] == "안녕"
        assert kwargs["response"] == "응답 텍스트"
        assert kwargs["model"] == "sonnet"
        assert kwargs["workspace_path"] is None
        assert kwargs["provider_session_id"] == "provider-sess-uuid"
        assert "⏰ <b>테스트</b>" in kwargs["delivery_text"]
        assert "응답 텍스트" in kwargs["delivery_text"]
        mock_repo.set_message_delivery_markup.assert_called_once()
        mock_repo.mark_message_delivered.assert_called_once_with(42)
        mock_repo.increment_delivery_attempts.assert_called_once_with(42)
        mock_repo.insert_schedule_run.assert_called_once()
        assert mock_repo.insert_schedule_run.call_args.kwargs["status"] == "success"
        assert mock_repo.insert_schedule_run.call_args.kwargs["result_type"] == "ai"
        assert mock_repo.insert_schedule_run.call_args.kwargs["message_log_id"] == 42
        # Session button should be in the response
        send_call = mock_bot.send_message.call_args
        markup = send_call.kwargs.get("reply_markup")
        assert markup is not None
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "resp:sched:42" in callbacks

    @pytest.mark.asyncio
    async def test_execute_plugin_schedule_persists_delivery_log(
        self, service, mock_repo, mock_schedule_manager
    ):
        """플러그인 스케줄도 전송 재시도를 위해 message_log에 저장한다."""
        schedule = MagicMock()
        schedule.id = "schedule-1"
        schedule.type = "plugin"
        schedule.plugin_name = "todo"
        schedule.action_name = "daily_wrap"
        schedule.chat_id = 12345
        schedule.name = "플러그인"
        schedule.message = ""

        await service.execute(schedule)

        mock_repo.insert_schedule_delivery_log.assert_called_once()
        kwargs = mock_repo.insert_schedule_delivery_log.call_args.kwargs
        assert kwargs["chat_id"] == 12345
        assert kwargs["schedule_id"] == "schedule-1"
        assert kwargs["request"] == ""
        assert kwargs["response"] == "플러그인 응답"
        assert kwargs["model"] == "plugin"
        mock_schedule_manager.update_run.assert_called_once_with("schedule-1")

    @pytest.mark.asyncio
    async def test_execute_plugin_rich_schedule_persists_retry_markup(
        self, service, mock_plugins, mock_repo, mock_bot
    ):
        """플러그인 rich response 버튼도 retry 가능한 JSON으로 저장한다."""
        mock_plugins.get_plugin_by_name.return_value = MagicMock(
            execute_scheduled_action=AsyncMock(
                return_value={
                    "text": "<b>리치 응답</b>",
                    "reply_markup": InlineKeyboardMarkup([[
                        InlineKeyboardButton("열기", callback_data="plugin:open"),
                    ]]),
                }
            )
        )
        schedule = MagicMock()
        schedule.id = "schedule-rich"
        schedule.type = "plugin"
        schedule.plugin_name = "todo"
        schedule.action_name = "daily_wrap"
        schedule.chat_id = 12345
        schedule.name = "리치"
        schedule.message = ""

        await service.execute(schedule)

        kwargs = mock_repo.insert_schedule_delivery_log.call_args.kwargs
        assert kwargs["delivery_markup_json"] == '[[{"text": "열기", "callback_data": "plugin:open"}]]'
        assert "<b>리치 응답</b>" in kwargs["delivery_text"]
        send_call = mock_bot.send_message.call_args
        callbacks = [
            btn.callback_data
            for row in send_call.kwargs["reply_markup"].inline_keyboard
            for btn in row
        ]
        assert callbacks == ["plugin:open"]

    @pytest.mark.asyncio
    async def test_execute_schedule_delivery_failure_preserves_log_for_retry(
        self, service, mock_bot, mock_repo, mock_schedule_manager
    ):
        """Telegram 전송 실패 시 schedule delivery 로그를 failed로 남긴다."""
        mock_bot.send_message = AsyncMock(side_effect=TimedOut("Timed out"))
        schedule = MagicMock()
        schedule.id = "schedule-1"
        schedule.type = "chat"
        schedule.ai_provider = "claude"
        schedule.message = "안녕"
        schedule.model = "sonnet"
        schedule.chat_id = 12345
        schedule.name = "테스트"
        schedule.workspace_path = None

        await service.execute(schedule)

        mock_repo.insert_schedule_delivery_log.assert_called_once()
        mock_repo.increment_delivery_attempts.assert_called_once_with(42)
        mock_repo.mark_message_delivery_failed.assert_called_once()
        mock_repo.mark_message_delivered.assert_not_called()
        assert "telegram:" in mock_schedule_manager.update_run.call_args.kwargs["last_error"]

    @pytest.mark.asyncio
    async def test_execute_command_schedule_runs_script_persists_and_sends_html(
        self, service, mock_bot, mock_ai_registry, mock_repo, tmp_path
    ):
        script = tmp_path / "cmd_test.py"
        script.write_text("print('<b>OK</b> <code>1234</code>')\n", encoding="utf-8")

        schedule = MagicMock()
        schedule.id = "schedule-command"
        schedule.type = "command"
        schedule.schedule_type = "command"
        schedule.message = f"python {script.name}"
        schedule.chat_id = 12345
        schedule.name = "Command Test"
        schedule.workspace_path = str(tmp_path)

        await service.execute(schedule)

        mock_ai_registry.get_client.assert_not_called()
        mock_repo.insert_schedule_delivery_log.assert_called_once()
        kwargs = mock_repo.insert_schedule_delivery_log.call_args.kwargs
        assert kwargs["model"] == "command"
        assert "<b>OK</b> <code>1234</code>" in kwargs["delivery_text"]
        mock_repo.mark_message_delivered.assert_called_once_with(42)
        mock_bot.send_message.assert_called_once()
        send_call = mock_bot.send_message.call_args.kwargs
        assert send_call["parse_mode"] == "HTML"
        assert "<b>OK</b> <code>1234</code>" in send_call["text"]

    @pytest.mark.asyncio
    async def test_execute_command_schedule_with_empty_output_stays_silent(
        self, service, mock_bot, mock_ai_registry, mock_repo, tmp_path
    ):
        script = tmp_path / "cmd_empty.py"
        script.write_text("pass\n", encoding="utf-8")

        schedule = MagicMock()
        schedule.id = "schedule-command"
        schedule.type = "command"
        schedule.schedule_type = "command"
        schedule.message = f"python {script.name}"
        schedule.chat_id = 12345
        schedule.name = "Command Test"
        schedule.workspace_path = str(tmp_path)

        await service.execute(schedule)

        mock_ai_registry.get_client.assert_not_called()
        mock_repo.insert_schedule_delivery_log.assert_not_called()
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_no_repo_still_sends_response(
        self, mock_bot, mock_ai_registry, mock_plugins, mock_schedule_manager
    ):
        """repo 없이도 응답은 정상 전송된다."""
        service = ScheduleExecutionService(
            bot=mock_bot,
            ai_registry=mock_ai_registry,
            plugin_loader=mock_plugins,
            schedule_manager=mock_schedule_manager,
            repo=None,
        )

        schedule = MagicMock()
        schedule.id = "schedule-1"
        schedule.type = "chat"
        schedule.ai_provider = "claude"
        schedule.message = "안녕"
        schedule.model = "sonnet"
        schedule.chat_id = 12345
        schedule.name = "테스트"
        schedule.workspace_path = None

        await service.execute(schedule)

        mock_bot.send_message.assert_called_once()
        send_call = mock_bot.send_message.call_args
        assert send_call.kwargs.get("reply_markup") is None
