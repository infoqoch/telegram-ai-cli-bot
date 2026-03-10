"""Tests for service layer."""

import pytest
from unittest.mock import MagicMock

from src.services.session_service import SessionService


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

    def test_create_session_requires_keyword_options(self, service):
        """Session creation options after session_id stay keyword-only."""
        with pytest.raises(TypeError):
            service.create_session("user1", "new_session", "claude", "provider-id")

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
        mock_repo.list_sessions_with_counts.return_value = [
            (
                MagicMock(
                    id="session123",
                    created_at="2024-01-01T00:00:00",
                    last_used="2024-01-01T00:00:00",
                    model="sonnet",
                    ai_provider="claude",
                    name="Test",
                    workspace_path=None,
                    deleted=False,
                ),
                0,
            )
        ]

        result = service.list_sessions("user1")

        assert len(result) == 1
        assert result[0]["id"] == "session123"
        assert result[0]["history_count"] == 0

    def test_get_session_by_provider_session_id(self, service, mock_repo):
        """기존 external session 연결 조회."""
        mock_repo.get_session_by_provider_session_id.return_value = (
            MagicMock(
                id="session123",
                created_at="2024-01-01T00:00:00",
                last_used="2024-01-01T00:00:00",
                model="sonnet",
                ai_provider="claude",
                name="Imported",
                workspace_path="/tmp/project",
                provider_session_id="external-1",
            ),
            2,
        )

        result = service.get_session_by_provider_session_id("user1", "claude", "external-1")

        assert result is not None
        assert result["full_session_id"] == "session123"
        assert result["provider_session_id"] == "external-1"
        assert result["history_count"] == 2
