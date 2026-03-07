"""State persistence 테스트.

AuthManager, temp_pending의 DB 영속화 및 복원을 검증한다.
"""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.bot.middleware import AuthManager
from src.repository.database import init_schema
from src.repository.repository import Repository


@pytest.fixture
def repo(tmp_path):
    """임시 DB를 사용하는 Repository fixture."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    schema_path = Path(__file__).parent.parent / "src" / "repository" / "schema.sql"
    init_schema(conn, schema_path)

    return Repository(conn)


# ---------------------------------------------------------------------------
# AuthManager persistence
# ---------------------------------------------------------------------------

class TestAuthManagerPersistence:
    """AuthManager의 DB 영속화 테스트."""

    def test_authenticate_saves_to_db(self, repo):
        """인증 시 DB에 세션이 저장된다."""
        auth = AuthManager(secret_key="test123", timeout_minutes=30, repository=repo)
        auth.authenticate("user1", "test123")

        session = repo.get_auth_session("user1")
        assert session is not None
        assert isinstance(session, datetime)

    def test_restore_from_db(self, repo):
        """새 AuthManager가 DB에서 세션을 복원한다."""
        auth1 = AuthManager(secret_key="test123", timeout_minutes=30, repository=repo)
        auth1.authenticate("user1", "test123")

        # 새 AuthManager 인스턴스 (봇 재시작 시나리오)
        auth2 = AuthManager(secret_key="test123", timeout_minutes=30, repository=repo)
        assert "user1" not in auth2._sessions  # 메모리에 없음

        count = auth2.restore_from_db()
        assert count == 1
        assert "user1" in auth2._sessions  # 메모리에 복원됨
        assert auth2.is_authenticated("user1")

    def test_expired_session_not_restored(self, repo):
        """만료된 세션은 복원되지 않는다."""
        # 과거 시간으로 직접 저장
        past = datetime.now() - timedelta(minutes=60)
        repo.save_auth_session("user1", past)

        auth = AuthManager(secret_key="test123", timeout_minutes=30, repository=repo)
        count = auth.restore_from_db()
        assert count == 0
        assert not auth.is_authenticated("user1")

    def test_is_authenticated_checks_db(self, repo):
        """메모리에 없을 때 DB에서 세션을 확인한다."""
        # DB에 직접 저장 (다른 프로세스가 저장한 시나리오)
        repo.save_auth_session("user1", datetime.now())

        auth = AuthManager(secret_key="test123", timeout_minutes=30, repository=repo)
        # 메모리엔 없지만 DB에서 복원해서 True
        assert auth.is_authenticated("user1")

    def test_cleanup_expired_removes_from_db(self, repo):
        """만료 정리 시 DB에서도 삭제된다."""
        past = datetime.now() - timedelta(minutes=60)
        repo.save_auth_session("user1", past)

        auth = AuthManager(secret_key="test123", timeout_minutes=30, repository=repo)
        auth._sessions["user1"] = past  # 메모리에도 넣기
        auth.cleanup_expired()

        assert repo.get_auth_session("user1") is None

    def test_multiple_users_restore(self, repo):
        """여러 사용자 세션이 모두 복원된다."""
        auth1 = AuthManager(secret_key="key", timeout_minutes=30, repository=repo)
        auth1.authenticate("user1", "key")
        auth1.authenticate("user2", "key")

        auth2 = AuthManager(secret_key="key", timeout_minutes=30, repository=repo)
        count = auth2.restore_from_db()
        assert count == 2
        assert auth2.is_authenticated("user1")
        assert auth2.is_authenticated("user2")


# ---------------------------------------------------------------------------
# Repository CRUD: pending_messages
# ---------------------------------------------------------------------------

class TestPendingMessagesPersistence:
    """pending_messages CRUD 테스트."""

    def test_save_and_get_pending_message(self, repo):
        """pending message 저장 및 조회."""
        repo.save_pending_message(
            key="abc123",
            user_id="user1",
            chat_id=12345,
            message="Hello",
            model="sonnet",
            is_new_session=False,
            created_at=time.time(),
        )

        msg = repo.get_pending_message("abc123")
        assert msg is not None
        assert msg["user_id"] == "user1"
        assert msg["message"] == "Hello"

    def test_delete_pending_message(self, repo):
        """pending message 삭제."""
        repo.save_pending_message(
            key="abc123", user_id="user1", chat_id=12345,
            message="Hello", created_at=time.time(),
        )
        repo.delete_pending_message("abc123")
        assert repo.get_pending_message("abc123") is None

    def test_get_all_pending_messages(self, repo):
        """모든 pending messages 조회."""
        now = time.time()
        repo.save_pending_message(key="k1", user_id="u1", chat_id=1, message="m1", created_at=now)
        repo.save_pending_message(key="k2", user_id="u2", chat_id=2, message="m2", created_at=now)

        all_msgs = repo.get_all_pending_messages()
        assert len(all_msgs) == 2
        assert "k1" in all_msgs
        assert "k2" in all_msgs

    def test_clear_expired_pending_messages(self, repo):
        """TTL 초과 메시지만 삭제."""
        old = time.time() - 600  # 10분 전
        new = time.time()

        repo.save_pending_message(key="old", user_id="u", chat_id=1, message="old", created_at=old)
        repo.save_pending_message(key="new", user_id="u", chat_id=1, message="new", created_at=new)

        deleted = repo.clear_expired_pending_messages(ttl_seconds=300)
        assert deleted == 1
        assert repo.get_pending_message("old") is None
        assert repo.get_pending_message("new") is not None

    def test_is_new_session_bool_conversion(self, repo):
        """is_new_session이 bool로 변환된다."""
        repo.save_pending_message(
            key="k1", user_id="u", chat_id=1, message="m",
            is_new_session=True, created_at=time.time(),
        )
        all_msgs = repo.get_all_pending_messages()
        assert all_msgs["k1"]["is_new_session"] is True


# ---------------------------------------------------------------------------
# Repository CRUD: auth_sessions
# ---------------------------------------------------------------------------

class TestAuthSessionsRepository:
    """auth_sessions 테이블 CRUD 테스트."""

    def test_save_and_get(self, repo):
        """세션 저장 및 조회."""
        now = datetime.now()
        repo.save_auth_session("user1", now)
        result = repo.get_auth_session("user1")
        assert result is not None
        assert abs((result - now).total_seconds()) < 1

    def test_get_nonexistent(self, repo):
        """없는 세션 조회 시 None."""
        assert repo.get_auth_session("nobody") is None

    def test_delete(self, repo):
        """세션 삭제."""
        repo.save_auth_session("user1", datetime.now())
        repo.delete_auth_session("user1")
        assert repo.get_auth_session("user1") is None

    def test_get_all(self, repo):
        """모든 세션 조회."""
        repo.save_auth_session("u1", datetime.now())
        repo.save_auth_session("u2", datetime.now())
        all_sessions = repo.get_all_auth_sessions()
        assert len(all_sessions) == 2

    def test_upsert(self, repo):
        """같은 user_id로 두 번 저장하면 업데이트."""
        t1 = datetime(2026, 1, 1, 12, 0)
        t2 = datetime(2026, 1, 1, 13, 0)
        repo.save_auth_session("user1", t1)
        repo.save_auth_session("user1", t2)

        result = repo.get_auth_session("user1")
        assert result == t2


# ---------------------------------------------------------------------------
# Repository CRUD: queued_messages
# ---------------------------------------------------------------------------

class TestQueuedMessagesRepository:
    """queued_messages 테이블 CRUD 테스트."""

    def test_save_and_get(self, repo):
        """큐 메시지 저장 및 조회."""
        qid = repo.save_queued_message(
            session_id="sess1", user_id="u1", chat_id=123,
            message="hello", model="sonnet", is_new_session=False,
        )
        assert qid > 0

        msgs = repo.get_queued_messages_by_session("sess1")
        assert len(msgs) == 1
        assert msgs[0]["message"] == "hello"

    def test_delete(self, repo):
        """큐 메시지 삭제."""
        qid = repo.save_queued_message(
            session_id="sess1", user_id="u1", chat_id=123,
            message="hello", model="sonnet", is_new_session=False,
        )
        repo.delete_queued_message(qid)
        assert len(repo.get_queued_messages_by_session("sess1")) == 0

    def test_expired_not_returned(self, repo):
        """만료된 큐 메시지는 조회되지 않는다."""
        repo.save_queued_message(
            session_id="sess1", user_id="u1", chat_id=123,
            message="hello", model="sonnet", is_new_session=False,
            expires_minutes=0,  # 즉시 만료
        )
        msgs = repo.get_queued_messages_by_session("sess1")
        assert len(msgs) == 0

    def test_clear_expired(self, repo):
        """만료된 큐 메시지 정리."""
        repo.save_queued_message(
            session_id="sess1", user_id="u1", chat_id=123,
            message="hello", model="sonnet", is_new_session=False,
            expires_minutes=0,
        )
        deleted = repo.clear_expired_queued_messages()
        assert deleted == 1
