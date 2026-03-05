"""Migration 테스트."""

import json
import tempfile
from pathlib import Path

import pytest

from src.repository import init_repository, shutdown_repository, reset_connection
from src.repository.migrations import migrate_all


@pytest.fixture
def temp_data_dir():
    """임시 데이터 디렉토리 생성."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        yield data_dir
    reset_connection()


class TestSessionsMigration:
    """세션 마이그레이션 테스트."""

    def test_migrate_sessions_json(self, temp_data_dir):
        """sessions.json 마이그레이션."""
        # JSON 파일 생성
        sessions_data = {
            "user123": {
                "current": "sess_abc",
                "previous_session": "sess_prev",
                "sessions": {
                    "sess_abc": {
                        "created_at": "2024-01-15T10:00:00",
                        "last_used": "2024-01-15T12:00:00",
                        "history": [
                            {"message": "Hello", "timestamp": "2024-01-15T10:00:00", "processed": True, "processor": "claude"},
                            {"message": "World", "timestamp": "2024-01-15T10:01:00", "processed": False, "processor": None},
                        ],
                        "model": "opus",
                        "name": "My Session",
                        "workspace_path": "/path/to/project",
                        "deleted": False,
                    }
                }
            }
        }

        sessions_file = temp_data_dir / "sessions.json"
        sessions_file.write_text(json.dumps(sessions_data), encoding="utf-8")

        # 마이그레이션 실행
        db_path = temp_data_dir / "bot.db"
        repo = init_repository(db_path)

        result = migrate_all(repo, temp_data_dir)

        assert result["users"] == 1
        assert result["sessions"] == 1
        assert result["history"] == 2

        # 데이터 확인
        user = repo.get_user("user123")
        assert user["current_session_id"] == "sess_abc"

        session = repo.get_session("sess_abc")
        assert session.model == "opus"
        assert session.name == "My Session"

        history = repo.get_session_history_entries("sess_abc")
        assert len(history) == 2

        # 백업 파일 확인
        assert (temp_data_dir / "sessions.json.bak").exists()

        shutdown_repository()

    def test_migration_idempotent(self, temp_data_dir):
        """마이그레이션 중복 실행 방지."""
        sessions_data = {"user1": {"current": None, "sessions": {}}}
        sessions_file = temp_data_dir / "sessions.json"
        sessions_file.write_text(json.dumps(sessions_data), encoding="utf-8")

        db_path = temp_data_dir / "bot.db"
        repo = init_repository(db_path)

        # 첫 번째 실행
        result1 = migrate_all(repo, temp_data_dir)
        assert result1["users"] == 1

        # 두 번째 실행 (스킵되어야 함)
        result2 = migrate_all(repo, temp_data_dir)
        assert result2["users"] == 0  # 이미 적용됨

        shutdown_repository()


class TestSchedulesMigration:
    """스케줄 마이그레이션 테스트."""

    def test_migrate_schedules_json(self, temp_data_dir):
        """schedules.json 마이그레이션."""
        schedules_data = {
            "schedules": [
                {
                    "id": "sched1",
                    "user_id": "user1",
                    "chat_id": 12345,
                    "hour": 9,
                    "minute": 0,
                    "message": "Good morning",
                    "name": "Morning",
                    "type": "claude",
                    "model": "sonnet",
                    "workspace_path": None,
                    "enabled": True,
                    "created_at": "2024-01-15T00:00:00",
                    "last_run": None,
                    "last_error": None,
                    "run_count": 0,
                }
            ]
        }

        schedules_file = temp_data_dir / "schedules.json"
        schedules_file.write_text(json.dumps(schedules_data), encoding="utf-8")

        db_path = temp_data_dir / "bot.db"
        repo = init_repository(db_path)

        result = migrate_all(repo, temp_data_dir)

        assert result["schedules"] == 1

        schedules = repo.list_all_schedules()
        assert len(schedules) == 1
        assert schedules[0].name == "Morning"

        shutdown_repository()


class TestMemosMigration:
    """메모 마이그레이션 테스트."""

    def test_migrate_memos(self, temp_data_dir):
        """메모 디렉토리 마이그레이션."""
        memo_dir = temp_data_dir / "memo"
        memo_dir.mkdir()

        memos_data = [
            {"id": 1, "content": "First memo", "created_at": "2024-01-15T10:00:00"},
            {"id": 2, "content": "Second memo", "created_at": "2024-01-15T11:00:00"},
        ]

        memo_file = memo_dir / "12345.json"
        memo_file.write_text(json.dumps(memos_data), encoding="utf-8")

        db_path = temp_data_dir / "bot.db"
        repo = init_repository(db_path)

        result = migrate_all(repo, temp_data_dir)

        assert result["memos"] == 2

        memos = repo.list_memos(12345)
        assert len(memos) == 2

        shutdown_repository()


class TestWeatherMigration:
    """날씨 위치 마이그레이션 테스트."""

    def test_migrate_weather(self, temp_data_dir):
        """날씨 위치 디렉토리 마이그레이션."""
        weather_dir = temp_data_dir / "weather"
        weather_dir.mkdir()

        location_data = {
            "name": "Seoul",
            "country": "South Korea",
            "lat": 37.5665,
            "lon": 126.9780,
        }

        weather_file = weather_dir / "12345.json"
        weather_file.write_text(json.dumps(location_data), encoding="utf-8")

        db_path = temp_data_dir / "bot.db"
        repo = init_repository(db_path)

        result = migrate_all(repo, temp_data_dir)

        assert result["weather"] == 1

        loc = repo.get_weather_location(12345)
        assert loc.name == "Seoul"
        assert loc.lat == 37.5665

        shutdown_repository()
