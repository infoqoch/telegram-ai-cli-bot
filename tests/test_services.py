"""Tests for service layer."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from src.services.session_service import SessionService
from src.services.message_service import MessageService
from src.services.schedule_service import ScheduleService


class TestSessionService:
    """SessionService tests."""

    @pytest.fixture
    def mock_repo(self):
        """Create mock repository."""
        repo = MagicMock()
        repo.get_current_session_id.return_value = "session123"
        repo.get_session.return_value = MagicMock(
            id="session123",
            user_id="user1",
            model="sonnet",
            name="Test Session",
            workspace_path=None,
            created_at="2024-01-01T00:00:00",
            last_used="2024-01-01T00:00:00",
            deleted=False,
        )
        return repo

    @pytest.fixture
    def service(self, mock_repo):
        """Create session service."""
        return SessionService(mock_repo, session_timeout_hours=24)

    def test_get_current_session_id(self, service, mock_repo):
        """Test get current session ID."""
        # Mock non-expired session
        mock_repo.get_session.return_value.last_used = "2099-01-01T00:00:00"
        result = service.get_current_session_id("user1")
        assert result == "session123"

    def test_get_current_session_id_deleted(self, service, mock_repo):
        """Test returns None for deleted session."""
        mock_repo.get_session.return_value.deleted = True
        result = service.get_current_session_id("user1")
        assert result is None

    def test_create_session(self, service, mock_repo):
        """Test create session."""
        service.create_session(
            user_id="user1",
            session_id="new_session",
            model="opus",
            name="New Session",
            first_message="Hello"
        )
        mock_repo.create_session.assert_called_once()
        mock_repo.add_message.assert_called_once()

    def test_delete_session(self, service, mock_repo):
        """Test delete session."""
        mock_repo.soft_delete_session.return_value = True
        mock_repo.get_current_session_id.return_value = "session123"
        mock_repo.get_previous_session_id.return_value = "prev_session"

        result = service.delete_session("user1", "session123")

        assert result is True
        mock_repo.soft_delete_session.assert_called_once_with("session123")

    def test_switch_session(self, service, mock_repo):
        """Test switch session."""
        mock_repo.switch_session.return_value = True
        result = service.switch_session("user1", "other_session")
        assert result is True
        mock_repo.switch_session.assert_called_once_with("user1", "other_session")

    def test_add_message(self, service, mock_repo):
        """Test add message."""
        service.add_message("session123", "Hello", processed=True, processor="claude")
        mock_repo.add_message.assert_called_once_with("session123", "Hello", True, "claude")

    def test_get_session_info(self, service, mock_repo):
        """Test get session info."""
        result = service.get_session_info("session123")
        assert "session1" in result
        assert "Test Session" in result

    def test_get_session_info_no_name(self, service, mock_repo):
        """Test get session info without name."""
        mock_repo.get_session.return_value.name = None
        result = service.get_session_info("session123")
        assert result == "session1"

    def test_list_sessions(self, service, mock_repo):
        """Test list sessions."""
        mock_repo.list_sessions.return_value = [
            MagicMock(
                id="session123",
                created_at="2024-01-01T00:00:00",
                last_used="2024-01-01T00:00:00",
                model="sonnet",
                name="Test",
                workspace_path=None,
                deleted=False,
            )
        ]
        mock_repo.get_session_history_entries.return_value = []

        result = service.list_sessions("user1")

        assert len(result) == 1
        assert result[0]["id"] == "session123"


class TestMessageService:
    """MessageService tests."""

    @pytest.fixture
    def mock_session_service(self):
        """Create mock session service."""
        return MagicMock()

    @pytest.fixture
    def mock_claude(self):
        """Create mock Claude client."""
        client = MagicMock()
        client.chat = AsyncMock(return_value="Hello from Claude!")
        client.create_session = AsyncMock(return_value="new_session_id")
        return client

    @pytest.fixture
    def mock_plugins(self):
        """Create mock plugin loader."""
        loader = MagicMock()
        loader.process_message = AsyncMock(return_value=MagicMock(
            handled=False,
            response=None,
            reply_markup=None,
            plugin_name=None,
        ))
        return loader

    @pytest.fixture
    def service(self, mock_session_service, mock_claude, mock_plugins):
        """Create message service."""
        return MessageService(mock_session_service, mock_claude, mock_plugins)

    @pytest.mark.asyncio
    async def test_process_with_plugin_not_handled(self, service, mock_plugins):
        """Test plugin not handling message."""
        result = await service.process_with_plugin("hello", 123)
        assert result is None

    @pytest.mark.asyncio
    async def test_process_with_plugin_handled(self, service, mock_plugins):
        """Test plugin handling message."""
        mock_plugins.process_message.return_value = MagicMock(
            handled=True,
            response="Plugin response",
            reply_markup=None,
            plugin_name="test_plugin",
        )

        result = await service.process_with_plugin("hello", 123)

        assert result is not None
        assert result["handled"] is True
        assert result["response"] == "Plugin response"
        assert "test_plugin" in result["processor"]

    @pytest.mark.asyncio
    async def test_process_with_claude(self, service, mock_claude):
        """Test Claude processing."""
        result = await service.process_with_claude(
            message="Hello",
            session_id="session123",
            model="sonnet",
        )
        assert result == "Hello from Claude!"
        mock_claude.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_session_and_chat(self, service, mock_claude):
        """Test create session and chat."""
        session_id, response = await service.create_session_and_chat(
            user_id="user1",
            message="Hello",
            model="opus",
        )
        assert session_id == "new_session_id"
        assert response == "Hello from Claude!"

    def test_record_message(self, service, mock_session_service):
        """Test record message."""
        service.record_message("session123", "Hello", "claude")
        mock_session_service.add_message.assert_called_once()


class TestScheduleService:
    """ScheduleService tests."""

    @pytest.fixture
    def mock_repo(self):
        """Create mock repository."""
        repo = MagicMock()
        return repo

    @pytest.fixture
    def mock_claude(self):
        """Create mock Claude client."""
        client = MagicMock()
        client.chat = AsyncMock(return_value="Scheduled response")
        return client

    @pytest.fixture
    def mock_scheduler_manager(self):
        """Create mock scheduler manager."""
        return MagicMock()

    @pytest.fixture
    def service(self, mock_repo, mock_claude, mock_scheduler_manager):
        """Create schedule service."""
        return ScheduleService(mock_repo, mock_claude, mock_scheduler_manager)

    def test_add_schedule(self, service, mock_repo, mock_scheduler_manager):
        """Test add schedule."""
        mock_repo.add_schedule.return_value = MagicMock(
            id="schedule1",
            hour=10,
            minute=0,
        )

        result = service.add_schedule(
            user_id="user1",
            chat_id=123,
            hour=10,
            minute=0,
            message="Daily task",
            name="Morning Task",
        )

        mock_repo.add_schedule.assert_called_once()
        mock_scheduler_manager.register_daily.assert_called_once()

    def test_remove_schedule(self, service, mock_repo, mock_scheduler_manager):
        """Test remove schedule."""
        mock_repo.remove_schedule.return_value = True

        result = service.remove_schedule("schedule1")

        assert result is True
        mock_scheduler_manager.unregister.assert_called_once()

    def test_toggle_schedule_enable(self, service, mock_repo, mock_scheduler_manager):
        """Test toggle schedule to enabled."""
        mock_repo.toggle_schedule.return_value = True
        mock_repo.get_schedule.return_value = MagicMock(
            id="schedule1",
            hour=10,
            minute=0,
        )

        result = service.toggle_schedule("schedule1")

        assert result is True
        mock_scheduler_manager.register_daily.assert_called_once()

    def test_toggle_schedule_disable(self, service, mock_repo, mock_scheduler_manager):
        """Test toggle schedule to disabled."""
        mock_repo.toggle_schedule.return_value = False
        mock_repo.get_schedule.return_value = MagicMock(id="schedule1")

        result = service.toggle_schedule("schedule1")

        assert result is False
        mock_scheduler_manager.unregister.assert_called_once()

    def test_get_status_text_empty(self, service, mock_repo):
        """Test status text with no schedules."""
        mock_repo.list_schedules_by_user.return_value = []

        result = service.get_status_text("user1")

        assert "No scheduled tasks" in result

    def test_get_status_text_with_schedules(self, service, mock_repo):
        """Test status text with schedules."""
        mock_repo.list_schedules_by_user.return_value = [
            MagicMock(
                name="Morning Task",
                hour=10,
                minute=0,
                enabled=True,
                type="claude",
                time_str="10:00 KST",
            )
        ]

        result = service.get_status_text("user1")

        assert "Morning Task" in result
        assert "10:00 KST" in result
