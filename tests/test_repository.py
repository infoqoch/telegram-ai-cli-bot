"""Repository 테스트."""

import tempfile
from pathlib import Path

import pytest

from src.repository import init_repository, shutdown_repository, reset_connection
from src.repository.repository import Repository


@pytest.fixture
def temp_db():
    """임시 데이터베이스 생성."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield db_path
    reset_connection()


@pytest.fixture
def repo(temp_db):
    """Repository 인스턴스 생성."""
    repository = init_repository(temp_db)
    yield repository
    shutdown_repository()


class TestUserOperations:
    """사용자 관련 테스트."""

    def test_get_or_create_user(self, repo):
        """사용자 생성 및 조회."""
        user = repo.get_or_create_user("user1")
        assert user["id"] == "user1"
        assert user["current_session_id"] is None

        # 중복 호출해도 동일 사용자
        user2 = repo.get_or_create_user("user1")
        assert user2["id"] == "user1"

    def test_update_user_current_session(self, repo):
        """현재 세션 업데이트."""
        repo.get_or_create_user("user1")
        repo.update_user_current_session("user1", "session1", "session0")

        user = repo.get_user("user1")
        assert user["current_session_id"] == "session1"
        assert user["previous_session_id"] == "session0"


class TestSessionOperations:
    """세션 관련 테스트."""

    def test_create_session(self, repo):
        """세션 생성."""
        session = repo.create_session(
            user_id="user1",
            session_id="sess1",
            model="opus",
            name="Test Session"
        )
        assert session.id == "sess1"
        assert session.model == "opus"
        assert session.name == "Test Session"

        # 현재 세션으로 설정됨
        current = repo.get_current_session_id("user1")
        assert current == "sess1"

    def test_create_session_without_switch(self, repo):
        """세션 생성 (전환 없이)."""
        repo.create_session("user1", "sess1")
        repo.create_session_without_switch("user1", "sess2")

        current = repo.get_current_session_id("user1")
        assert current == "sess1"  # 여전히 sess1

    def test_list_sessions(self, repo):
        """세션 목록."""
        repo.create_session("user1", "sess1")
        repo.create_session("user1", "sess2")
        repo.create_session("user1", "sess3")

        sessions = repo.list_sessions("user1")
        assert len(sessions) == 3

    def test_soft_delete_session(self, repo):
        """소프트 삭제."""
        repo.create_session("user1", "sess1")
        repo.soft_delete_session("sess1")

        sessions = repo.list_sessions("user1", include_deleted=False)
        assert len(sessions) == 0

        sessions = repo.list_sessions("user1", include_deleted=True)
        assert len(sessions) == 1
        assert sessions[0].deleted is True

    def test_restore_session(self, repo):
        """세션 복원."""
        repo.create_session("user1", "sess1")
        repo.soft_delete_session("sess1")
        repo.restore_session("sess1")

        sessions = repo.list_sessions("user1")
        assert len(sessions) == 1
        assert sessions[0].deleted is False

    def test_switch_session(self, repo):
        """세션 전환."""
        repo.create_session("user1", "sess1")
        repo.create_session("user1", "sess2")

        assert repo.get_current_session_id("user1") == "sess2"

        repo.switch_session("user1", "sess1")
        assert repo.get_current_session_id("user1") == "sess1"

    def test_workspace_session(self, repo):
        """워크스페이스 세션."""
        repo.create_session(
            "user1", "sess1",
            workspace_path="/path/to/project"
        )

        assert repo.is_workspace_session("sess1") is True
        assert repo.get_session_workspace_path("sess1") == "/path/to/project"


class TestHistoryOperations:
    """히스토리 관련 테스트."""

    def test_add_and_get_history(self, repo):
        """히스토리 추가 및 조회."""
        repo.create_session("user1", "sess1")

        repo.add_message("sess1", "Hello")
        repo.add_message("sess1", "World", processed=True, processor="claude")

        history = repo.get_session_history("sess1")
        assert len(history) == 2
        assert history[0] == "Hello"
        assert history[1] == "World"

    def test_get_history_entries(self, repo):
        """히스토리 엔트리 조회."""
        repo.create_session("user1", "sess1")
        repo.add_message("sess1", "Test", processed=True, processor="plugin:memo")

        entries = repo.get_session_history_entries("sess1")
        assert len(entries) == 1
        assert entries[0].message == "Test"
        assert entries[0].processed is True
        assert entries[0].processor == "plugin:memo"

    def test_clear_history(self, repo):
        """히스토리 삭제."""
        repo.create_session("user1", "sess1")
        repo.add_message("sess1", "Msg1")
        repo.add_message("sess1", "Msg2")

        count = repo.clear_session_history("sess1")
        assert count == 2

        history = repo.get_session_history("sess1")
        assert len(history) == 0


class TestScheduleOperations:
    """스케줄 관련 테스트."""

    def test_add_schedule(self, repo):
        """스케줄 추가."""
        schedule = repo.add_schedule(
            user_id="user1",
            chat_id=12345,
            hour=9,
            minute=0,
            message="Good morning",
            name="Morning Greeting"
        )

        assert schedule.hour == 9
        assert schedule.minute == 0
        assert schedule.enabled is True

    def test_toggle_schedule(self, repo):
        """스케줄 토글."""
        schedule = repo.add_schedule(
            user_id="user1",
            chat_id=12345,
            hour=9,
            minute=0,
            message="Test",
            name="Test"
        )

        new_state = repo.toggle_schedule(schedule.id)
        assert new_state is False

        new_state = repo.toggle_schedule(schedule.id)
        assert new_state is True

    def test_list_enabled_schedules(self, repo):
        """활성화된 스케줄 목록."""
        repo.add_schedule("user1", 12345, 9, 0, "Msg1", "S1")
        s2 = repo.add_schedule("user1", 12345, 10, 0, "Msg2", "S2")
        repo.toggle_schedule(s2.id)  # 비활성화

        enabled = repo.list_enabled_schedules()
        assert len(enabled) == 1


class TestWorkspaceOperations:
    """워크스페이스 관련 테스트."""

    def test_add_workspace(self, repo):
        """워크스페이스 추가."""
        ws = repo.add_workspace(
            user_id="user1",
            path="/home/user/project",
            name="My Project",
            description="Test project",
            keywords=["python", "web"]
        )

        assert ws.name == "My Project"
        assert ws.keywords == ["python", "web"]

    def test_get_by_path(self, repo):
        """경로로 워크스페이스 조회."""
        repo.add_workspace("user1", "/home/user/project", "Project")

        ws = repo.get_workspace_by_path("/home/user/project")
        assert ws is not None
        assert ws.name == "Project"

    def test_mark_used(self, repo):
        """사용 기록."""
        ws = repo.add_workspace("user1", "/path", "Project")
        assert ws.use_count == 0

        repo.mark_workspace_used(ws.id)

        ws = repo.get_workspace(ws.id)
        assert ws.use_count == 1
        assert ws.last_used is not None


class TestMemoOperations:
    """메모 관련 테스트."""

    def test_add_and_list_memos(self, repo):
        """메모 추가 및 조회."""
        memo1 = repo.add_memo(12345, "First memo")
        memo2 = repo.add_memo(12345, "Second memo")

        memos = repo.list_memos(12345)
        assert len(memos) == 2
        # 최신 순 정렬
        assert memos[0].content == "Second memo"
        assert memos[1].content == "First memo"

    def test_delete_memo(self, repo):
        """메모 삭제."""
        memo = repo.add_memo(12345, "To delete")
        assert repo.delete_memo(memo.id) is True

        memos = repo.list_memos(12345)
        assert len(memos) == 0


class TestTodoOperations:
    """Todo 관련 테스트."""

    def test_add_and_list_todos(self, repo):
        """Todo 추가 및 조회."""
        repo.add_todo(12345, "2024-01-15", "morning", "Wake up")
        repo.add_todo(12345, "2024-01-15", "afternoon", "Lunch")

        todos = repo.list_todos_by_date(12345, "2024-01-15")
        assert len(todos) == 2

    def test_toggle_todo(self, repo):
        """Todo 토글."""
        todo = repo.add_todo(12345, "2024-01-15", "morning", "Task")
        assert todo.done is False

        new_state = repo.toggle_todo(todo.id)
        assert new_state is True

        todo = repo.get_todo(todo.id)
        assert todo.done is True


class TestWeatherOperations:
    """날씨 위치 관련 테스트."""

    def test_set_and_get_location(self, repo):
        """위치 설정 및 조회."""
        repo.set_weather_location(
            chat_id=12345,
            name="Seoul",
            lat=37.5665,
            lon=126.9780,
            country="South Korea"
        )

        loc = repo.get_weather_location(12345)
        assert loc is not None
        assert loc.name == "Seoul"
        assert loc.lat == 37.5665

    def test_update_location(self, repo):
        """위치 업데이트."""
        repo.set_weather_location(12345, "Seoul", 37.5665, 126.9780)
        repo.set_weather_location(12345, "Busan", 35.1796, 129.0756)

        loc = repo.get_weather_location(12345)
        assert loc.name == "Busan"
