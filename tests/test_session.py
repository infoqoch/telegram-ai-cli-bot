"""세션 저장소 테스트.

SessionStore 클래스의 핵심 기능 검증:
- 세션 생성 및 저장
- 메시지 추가
- 세션 목록 및 전환
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
        """세션 생성 확인."""
        user_id = "123"
        session_id = "claude-session-abc"

        session_store.create_session(user_id, session_id, "첫 메시지")

        current = session_store.get_current_session_id(user_id)
        assert current == session_id

    def test_add_message(self, session_store):
        """메시지 추가 확인."""
        user_id = "123"
        session_id = "claude-session-abc"

        session_store.create_session(user_id, session_id, "첫 메시지")
        session_store.add_message(user_id, session_id, "두 번째 메시지")

        history = session_store.get_session_history(user_id, session_id)
        assert len(history) == 2
        assert history[0] == "첫 메시지"
        assert history[1] == "두 번째 메시지"

    def test_list_sessions(self, session_store):
        """세션 목록 확인."""
        user_id = "123"

        session_store.create_session(user_id, "session-1", "메시지1")
        session_store.create_session(user_id, "session-2", "메시지2")

        sessions = session_store.list_sessions(user_id)
        assert len(sessions) == 2

    def test_switch_session(self, session_store):
        """세션 전환 확인."""
        user_id = "123"

        session_store.create_session(user_id, "session-1-abc", "메시지1")
        session_store.create_session(user_id, "session-2-def", "메시지2")

        # session-2가 current
        assert session_store.get_current_session_id(user_id) == "session-2-def"

        # session-1로 전환
        result = session_store.switch_session(user_id, "session-1")
        assert result is True
        assert session_store.get_current_session_id(user_id) == "session-1-abc"

    def test_get_session_info(self, session_store):
        """세션 정보 확인."""
        user_id = "123"
        session_id = "abcd1234-5678-90ab-cdef-1234567890ab"

        session_store.create_session(user_id, session_id, "테스트")

        info = session_store.get_session_info(user_id, session_id)
        assert info == "abcd1234"

    def test_get_history_count(self, session_store):
        """히스토리 카운트 확인."""
        user_id = "123"
        session_id = "test-session"

        session_store.create_session(user_id, session_id, "메시지1")
        session_store.add_message(user_id, session_id, "메시지2")
        session_store.add_message(user_id, session_id, "메시지3")

        count = session_store.get_history_count(user_id, session_id)
        assert count == 3

    def test_clear_current(self, session_store):
        """현재 세션 클리어 확인."""
        user_id = "123"

        session_store.create_session(user_id, "test-session", "메시지")
        assert session_store.get_current_session_id(user_id) is not None

        session_store.clear_current(user_id)
        assert session_store.get_current_session_id(user_id) is None


class TestAtomicWrite:
    """Atomic write 기능 테스트."""

    def test_save_creates_temp_file(self, session_store, temp_session_file):
        """save 시 .tmp 파일이 생성되는지 확인."""
        user_id = "123"
        session_store.create_session(user_id, "test-session", "메시지")

        # .tmp 파일은 replace() 후 삭제되므로 존재하지 않아야 함
        temp_file = temp_session_file.with_suffix('.tmp')
        assert not temp_file.exists()
        assert temp_session_file.exists()

    def test_save_atomic_on_error(self, session_store, temp_session_file, monkeypatch):
        """write 실패 시 기존 데이터가 손상되지 않는지 확인."""
        user_id = "123"
        session_store.create_session(user_id, "test-session", "원본 메시지")

        # 원본 데이터 확인
        with open(temp_session_file, 'r', encoding='utf-8') as f:
            original_data = json.load(f)

        # json.dump을 실패하도록 mock
        def fail_dump(*args, **kwargs):
            raise IOError("Mock write failure")

        monkeypatch.setattr(json, "dump", fail_dump)

        # save 시도 (실패해야 함)
        result = session_store._save()
        assert result is False

        # 원본 파일이 그대로 유지되는지 확인
        with open(temp_session_file, 'r', encoding='utf-8') as f:
            current_data = json.load(f)
        assert current_data == original_data

    def test_save_returns_bool(self, session_store):
        """_save()가 True/False를 반환하는지 확인."""
        user_id = "123"
        session_store.create_session(user_id, "test-session", "메시지")

        # 성공 케이스는 create_session에서 이미 테스트됨
        # 여기서는 반환값이 bool임을 명시적으로 확인
        result = session_store._save()
        assert isinstance(result, bool)
        assert result is True


class TestDateTimeParsingErrors:
    """DateTime 파싱 에러 처리 테스트."""

    def test_get_current_session_invalid_timestamp(self, session_store):
        """잘못된 timestamp가 있을 때 None 반환."""
        user_id = "123"
        session_id = "test-session"

        # 정상 세션 생성 후 timestamp를 손상
        session_store.create_session(user_id, session_id, "메시지")
        session_store._data[user_id]["sessions"][session_id]["last_used"] = "invalid-timestamp"

        result = session_store.get_current_session_id(user_id)
        assert result is None

    def test_get_current_session_missing_last_used(self, session_store):
        """last_used 필드가 없을 때 None 반환."""
        user_id = "123"
        session_id = "test-session"

        # 정상 세션 생성 후 last_used 삭제
        session_store.create_session(user_id, session_id, "메시지")
        del session_store._data[user_id]["sessions"][session_id]["last_used"]

        result = session_store.get_current_session_id(user_id)
        assert result is None

    def test_get_current_session_expired(self, session_store):
        """만료된 세션은 None 반환."""
        from datetime import datetime, timedelta

        user_id = "123"
        session_id = "test-session"

        # 정상 세션 생성 후 last_used를 과거로 설정
        session_store.create_session(user_id, session_id, "메시지")
        expired_time = datetime.now() - timedelta(hours=25)  # timeout_hours=24보다 긴 시간
        session_store._data[user_id]["sessions"][session_id]["last_used"] = expired_time.isoformat()

        result = session_store.get_current_session_id(user_id)
        assert result is None


class TestEdgeCases:
    """Edge case 테스트."""

    def test_add_message_nonexistent_user(self, session_store):
        """존재하지 않는 유저에게 메시지 추가 시 아무 일도 일어나지 않음."""
        session_store.add_message("nonexistent-user", "session-id", "메시지")
        # 에러 없이 통과해야 함
        assert "nonexistent-user" not in session_store._data

    def test_add_message_nonexistent_session(self, session_store):
        """존재하지 않는 세션에 메시지 추가 시 아무 일도 일어나지 않음."""
        user_id = "123"
        session_store.create_session(user_id, "existing-session", "메시지")

        # 존재하지 않는 세션에 메시지 추가
        session_store.add_message(user_id, "nonexistent-session", "메시지")

        # existing-session만 존재해야 함
        assert "nonexistent-session" not in session_store._data[user_id]["sessions"]
        assert len(session_store._data[user_id]["sessions"]) == 1

    def test_set_current_nonexistent_session(self, session_store):
        """존재하지 않는 세션을 current로 설정 시 아무 일도 일어나지 않음."""
        user_id = "123"
        session_store.create_session(user_id, "existing-session", "메시지")

        # 존재하지 않는 세션을 current로 설정
        session_store.set_current(user_id, "nonexistent-session")

        # current는 변경되지 않아야 함
        assert session_store.get_current_session_id(user_id) == "existing-session"

    def test_get_session_by_prefix_not_found(self, session_store):
        """존재하지 않는 prefix로 검색 시 None 반환."""
        user_id = "123"
        session_store.create_session(user_id, "session-abc", "메시지")

        result = session_store.get_session_by_prefix(user_id, "xyz")
        assert result is None

    def test_get_session_history_nonexistent_user(self, session_store):
        """존재하지 않는 유저의 히스토리 조회 시 빈 리스트 반환."""
        result = session_store.get_session_history("nonexistent-user", "session-id")
        assert result == []

    def test_list_sessions_nonexistent_user(self, session_store):
        """존재하지 않는 유저의 세션 목록 조회 시 빈 리스트 반환."""
        result = session_store.list_sessions("nonexistent-user")
        assert result == []


class TestPersistence:
    """데이터 영속성 테스트."""

    def test_data_persists_after_reload(self, temp_session_file):
        """SessionStore 재생성 후에도 데이터가 유지되는지 확인."""
        user_id = "123"
        session_id = "test-session"

        # 첫 번째 인스턴스에서 데이터 생성
        store1 = SessionStore(file_path=temp_session_file, timeout_hours=24)
        store1.create_session(user_id, session_id, "첫 메시지")
        store1.add_message(user_id, session_id, "두 번째 메시지")

        # 두 번째 인스턴스 생성 (파일에서 로드)
        store2 = SessionStore(file_path=temp_session_file, timeout_hours=24)

        # 데이터가 동일한지 확인
        assert store2.get_current_session_id(user_id) == session_id
        history = store2.get_session_history(user_id, session_id)
        assert len(history) == 2
        assert history[0] == "첫 메시지"
        assert history[1] == "두 번째 메시지"

    def test_load_corrupted_file(self, temp_session_file):
        """손상된 JSON 파일 로드 시 빈 dict 반환."""
        # 파일에 잘못된 JSON 작성
        with open(temp_session_file, 'w', encoding='utf-8') as f:
            f.write("{invalid json content")

        # 로드 시 빈 dict로 초기화되어야 함
        store = SessionStore(file_path=temp_session_file, timeout_hours=24)
        assert store._data == {}
