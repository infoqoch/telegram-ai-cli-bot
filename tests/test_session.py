"""세션 저장소 테스트.

SessionStore 클래스의 핵심 기능 검증:
- 세션 생성/조회
- 메시지 추가
- 멀티 세션 관리
- 세션 전환
- 기존 데이터 마이그레이션
"""

import json
import tempfile
from pathlib import Path

import pytest

from src.claude.session import SessionStore


@pytest.fixture
def temp_session_file():
    """임시 세션 파일 생성."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({}, f)
        return Path(f.name)


@pytest.fixture
def session_store(temp_session_file):
    """테스트용 세션 저장소 생성."""
    return SessionStore(file_path=temp_session_file, timeout_hours=24)


class TestSessionStore:
    """SessionStore 단위 테스트."""

    def test_create_session(self, session_store):
        """새 세션 생성 시 UUID 반환 및 현재 세션 설정 확인."""
        user_id = "test_user"
        session_id = session_store.create_session(user_id, "Hello")
        
        assert session_id is not None
        assert len(session_id) == 36  # UUID 길이
        assert session_store.get_current_session_id(user_id) == session_id
    
    def test_add_message(self, session_store):
        """메시지 추가 시 히스토리에 정상 저장 확인."""
        user_id = "test_user"
        session_store.create_session(user_id, "First message")
        session_store.add_message(user_id, "Second message")
        
        history = session_store.get_history(user_id)
        assert len(history) == 2
        assert history[0] == "First message"
        assert history[1] == "Second message"
    
    def test_list_sessions(self, session_store):
        """여러 세션 생성 후 목록 조회 확인."""
        user_id = "test_user"
        
        session_store.create_session(user_id, "Session 1")
        session_store.clear_current(user_id)
        session_store.create_session(user_id, "Session 2")
        
        sessions = session_store.list_sessions(user_id)
        assert len(sessions) == 2
    
    def test_switch_session(self, session_store):
        """세션 전환 기능 확인."""
        user_id = "test_user"
        
        first_id = session_store.create_session(user_id, "First")
        session_store.clear_current(user_id)
        second_id = session_store.create_session(user_id, "Second")
        
        assert session_store.switch_session(user_id, first_id[:8])
        assert session_store.get_current_session_id(user_id) == first_id
    
    def test_get_session_info(self, session_store):
        """세션 정보 조회 (짧은 ID) 확인."""
        user_id = "test_user"
        
        assert session_store.get_current_session_info(user_id) == "없음"
        
        session_id = session_store.create_session(user_id, "Test")
        assert session_store.get_current_session_info(user_id) == session_id[:8]
    
    def test_migration_old_format(self, temp_session_file):
        """기존 단일 세션 형식 → 멀티 세션 형식 마이그레이션 확인."""
        old_data = {
            "user123": {
                "session_id": "abc-123-def",
                "created_at": "2024-01-01T00:00:00",
                "last_used": "2024-01-01T00:00:00",
                "history": ["test message"]
            }
        }
        with open(temp_session_file, 'w') as f:
            json.dump(old_data, f)
        
        store = SessionStore(file_path=temp_session_file, timeout_hours=24)
        
        sessions = store.list_sessions("user123")
        assert len(sessions) == 1
        assert sessions[0]["full_session_id"] == "abc-123-def"
