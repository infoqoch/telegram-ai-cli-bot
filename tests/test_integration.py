"""통합 테스트.

실제 앱 시작 및 주요 컴포넌트 통합 검증.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


class TestAppStartup:
    """앱 시작 통합 테스트."""

    @pytest.mark.asyncio
    async def test_create_app_succeeds(self):
        """create_app이 에러 없이 실행되는지 검증."""
        from src.main import create_app

        settings = SimpleNamespace(
            telegram_token="test-token",
            base_dir="/tmp/test-bot",
            effective_working_dir="/tmp/test-bot",
            require_auth=False,
            allowed_chat_ids=[],
            admin_chat_id=0,
        )

        mock_app = MagicMock()
        mock_app.job_queue = MagicMock()
        mock_app.add_handler = MagicMock()
        mock_app.add_error_handler = MagicMock()
        mock_app.bot = MagicMock()

        handlers = MagicMock()
        handlers.cleanup_detached_jobs = AsyncMock(return_value=0)

        schedule_manager = MagicMock()
        schedule_manager.set_scheduler_manager = MagicMock()
        schedule_manager.set_executor = MagicMock()
        schedule_manager.register_all_to_scheduler = MagicMock()

        runtime = SimpleNamespace(
            handlers=handlers,
            plugin_loader=SimpleNamespace(plugins=[]),
            schedule_manager=schedule_manager,
            ai_registry=MagicMock(),
            workspace_registry=MagicMock(),
        )

        mock_builder = MagicMock()
        mock_builder.token.return_value = mock_builder
        mock_builder.concurrent_updates.return_value = mock_builder
        mock_builder.post_init.return_value = mock_builder
        mock_builder.build.return_value = mock_app

        with patch("src.main.Application") as MockApplication, \
             patch("src.main.build_bot_runtime", return_value=runtime), \
             patch("src.main.ScheduleExecutionService") as MockScheduleExecutionService, \
             patch("src.main.scheduler_manager", MagicMock()):
            MockApplication.builder.return_value = mock_builder
            MockScheduleExecutionService.return_value.execute = MagicMock()

            app = create_app(settings)
            assert app is mock_app

    def test_scheduler_manager_has_no_scheduler_attribute(self):
        """SchedulerManager에 scheduler 속성이 없음을 확인.

        main.py에서 scheduler_manager.scheduler로 접근하면 안됨.
        """
        from src.scheduler_manager import SchedulerManager

        # SchedulerManager 클래스 검증 (싱글톤 인스턴스가 아닌 클래스 레벨에서)
        assert hasattr(SchedulerManager, "register_daily")

        # scheduler 속성은 존재하지 않아야 함
        assert not hasattr(SchedulerManager, "scheduler")

    def test_main_py_does_not_use_scheduler_manager_scheduler(self):
        """main.py에서 scheduler_manager.scheduler를 사용하지 않는지 검증."""
        from pathlib import Path

        main_py = Path(__file__).parent.parent / "src" / "main.py"
        content = main_py.read_text()

        # scheduler_manager.scheduler 패턴이 없어야 함
        assert "scheduler_manager.scheduler" not in content

    def test_build_bot_commands_is_limited_to_five_entries(self):
        """Telegram slash-command sync should publish only the compact picker set."""
        from src.bot.command_catalog import build_bot_commands

        commands = build_bot_commands(has_plugins=True, is_admin=True)

        assert [command.command for command in commands] == [
            "menu",
            "session",
            "new",
            "sl",
            "tasks",
        ]
