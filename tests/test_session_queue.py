"""SessionQueue 테스트.

세션 큐 관리의 단일 진실 소스로서의 동작을 검증.
"""

import asyncio
import pytest
import time

from src.bot.session_queue import (
    SessionQueueManager,
    SessionState,
    SessionStatus,
    QueuedMessage,
)


class TestQueuedMessage:
    """QueuedMessage 테스트."""

    def test_create_message(self):
        """메시지 생성 테스트."""
        msg = QueuedMessage(
            user_id="user1",
            chat_id=12345,
            message="Hello",
            session_id="session1",
            model="sonnet",
        )
        assert msg.user_id == "user1"
        assert msg.chat_id == 12345
        assert msg.message == "Hello"
        assert msg.session_id == "session1"
        assert msg.model == "sonnet"
        assert not msg.is_expired()

    def test_message_expiration(self):
        """메시지 만료 테스트."""
        msg = QueuedMessage(
            user_id="user1",
            chat_id=12345,
            message="Hello",
            session_id="session1",
            model="sonnet",
            expires_at=time.time() - 1,  # 이미 만료
        )
        assert msg.is_expired()


class TestSessionState:
    """SessionState 테스트."""

    def test_initial_state(self):
        """초기 상태 테스트."""
        state = SessionState(session_id="test_session")
        assert state.session_id == "test_session"
        assert state.status == SessionStatus.IDLE
        assert not state.is_locked()
        assert state.get_queue_size() == 0

    def test_lock_unlock(self):
        """락/언락 테스트."""
        state = SessionState(session_id="test_session")

        # 락 획득
        state.lock("user1", "test message")
        assert state.is_locked()
        assert state.current_user_id == "user1"
        assert state.current_message == "test message"
        assert state.started_at is not None

        # 언락
        state.unlock()
        assert not state.is_locked()
        assert state.current_user_id is None
        assert state.current_message is None
        assert state.started_at is None

    def test_queue_operations(self):
        """큐 작업 테스트."""
        state = SessionState(session_id="test_session")

        # 메시지 추가
        msg1 = QueuedMessage(
            user_id="user1",
            chat_id=12345,
            message="Message 1",
            session_id="test_session",
            model="sonnet",
        )
        msg2 = QueuedMessage(
            user_id="user2",
            chat_id=67890,
            message="Message 2",
            session_id="test_session",
            model="opus",
        )

        pos1 = state.add_to_queue(msg1)
        pos2 = state.add_to_queue(msg2)

        assert pos1 == 1
        assert pos2 == 2
        assert state.get_queue_size() == 2

        # 메시지 꺼내기 (FIFO)
        popped = state.pop_from_queue()
        assert popped.user_id == "user1"
        assert state.get_queue_size() == 1

        popped = state.pop_from_queue()
        assert popped.user_id == "user2"
        assert state.get_queue_size() == 0

        # 빈 큐에서 꺼내기
        popped = state.pop_from_queue()
        assert popped is None

    def test_skip_expired_messages(self):
        """만료된 메시지 스킵 테스트."""
        state = SessionState(session_id="test_session")

        # 만료된 메시지
        expired_msg = QueuedMessage(
            user_id="user1",
            chat_id=12345,
            message="Expired",
            session_id="test_session",
            model="sonnet",
            expires_at=time.time() - 1,
        )
        # 유효한 메시지
        valid_msg = QueuedMessage(
            user_id="user2",
            chat_id=67890,
            message="Valid",
            session_id="test_session",
            model="opus",
        )

        state.add_to_queue(expired_msg)
        state.add_to_queue(valid_msg)

        # 만료된 메시지는 스킵하고 유효한 메시지 반환
        popped = state.pop_from_queue()
        assert popped.user_id == "user2"

    def test_clear_expired(self):
        """만료 메시지 정리 테스트."""
        state = SessionState(session_id="test_session")

        # 만료된 메시지 2개, 유효한 메시지 1개
        for i in range(2):
            state.add_to_queue(QueuedMessage(
                user_id=f"user{i}",
                chat_id=i,
                message=f"Expired {i}",
                session_id="test_session",
                model="sonnet",
                expires_at=time.time() - 1,
            ))
        state.add_to_queue(QueuedMessage(
            user_id="user_valid",
            chat_id=999,
            message="Valid",
            session_id="test_session",
            model="sonnet",
        ))

        assert state.get_queue_size() == 3

        cleared = state.clear_expired()
        assert cleared == 2
        assert state.get_queue_size() == 1


class TestSessionQueueManager:
    """SessionQueueManager 테스트."""

    @pytest.fixture
    def manager(self):
        """새 매니저 인스턴스."""
        return SessionQueueManager()

    def test_is_locked_empty(self, manager):
        """빈 매니저에서 락 상태 확인."""
        assert not manager.is_locked("nonexistent_session")

    @pytest.mark.asyncio
    async def test_try_lock_success(self, manager):
        """락 획득 성공 테스트."""
        result = await manager.try_lock("session1", "user1", "test message")
        assert result is True
        assert manager.is_locked("session1")

    @pytest.mark.asyncio
    async def test_try_lock_fail_already_locked(self, manager):
        """이미 락된 세션 락 시도 실패 테스트."""
        await manager.try_lock("session1", "user1", "first")
        result = await manager.try_lock("session1", "user2", "second")
        assert result is False

    @pytest.mark.asyncio
    async def test_unlock_returns_next_message(self, manager):
        """언락 시 대기열 메시지 반환 테스트."""
        # 락 획득
        await manager.try_lock("session1", "user1", "first")

        # 대기열에 추가
        await manager.add_to_waiting(
            session_id="session1",
            user_id="user2",
            chat_id=12345,
            message="waiting message",
            model="sonnet",
        )

        # 언락 시 대기 메시지 반환
        next_msg = await manager.unlock("session1")
        assert next_msg is not None
        assert next_msg.user_id == "user2"
        assert next_msg.message == "waiting message"

    @pytest.mark.asyncio
    async def test_unlock_empty_queue(self, manager):
        """빈 대기열에서 언락 테스트."""
        await manager.try_lock("session1", "user1", "test")
        next_msg = await manager.unlock("session1")
        assert next_msg is None
        assert not manager.is_locked("session1")

    @pytest.mark.asyncio
    async def test_add_to_waiting(self, manager):
        """대기열 추가 테스트."""
        position = await manager.add_to_waiting(
            session_id="session1",
            user_id="user1",
            chat_id=12345,
            message="test",
            model="sonnet",
        )
        assert position == 1
        assert manager.get_queue_size("session1") == 1

        position = await manager.add_to_waiting(
            session_id="session1",
            user_id="user2",
            chat_id=67890,
            message="test2",
            model="opus",
        )
        assert position == 2
        assert manager.get_queue_size("session1") == 2

    @pytest.mark.asyncio
    async def test_force_unlock(self, manager):
        """강제 언락 테스트."""
        await manager.try_lock("session1", "user1", "test")
        assert manager.is_locked("session1")

        result = await manager.force_unlock("session1")
        assert result is True
        assert not manager.is_locked("session1")

    @pytest.mark.asyncio
    async def test_force_unlock_not_locked(self, manager):
        """락 안된 세션 강제 언락 테스트."""
        result = await manager.force_unlock("session1")
        assert result is False

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, manager):
        """만료 메시지 정리 테스트."""
        # 만료된 메시지 추가
        manager._sessions["session1"] = SessionState(session_id="session1")
        manager._sessions["session1"].add_to_queue(QueuedMessage(
            user_id="user1",
            chat_id=12345,
            message="expired",
            session_id="session1",
            model="sonnet",
            expires_at=time.time() - 1,
        ))

        cleared = await manager.cleanup_expired()
        assert cleared == 1

    def test_get_all_sessions_status(self, manager):
        """전체 세션 상태 조회 테스트."""
        manager._sessions["session1"] = SessionState(session_id="session1")
        manager._sessions["session1"].lock("user1", "test")
        manager._sessions["session2"] = SessionState(session_id="session2")

        status = manager.get_all_sessions_status()
        assert "session1"[:8] in status
        assert status["session1"[:8]]["status"] == "processing"
        assert "session2"[:8] in status
        assert status["session2"[:8]]["status"] == "idle"


class TestConcurrency:
    """동시성 테스트."""

    @pytest.mark.asyncio
    async def test_concurrent_lock_attempts(self):
        """동시 락 시도 테스트."""
        manager = SessionQueueManager()
        results = []

        async def try_lock(user_id):
            result = await manager.try_lock("session1", user_id, f"msg from {user_id}")
            results.append((user_id, result))

        # 10개의 동시 락 시도
        await asyncio.gather(*[try_lock(f"user{i}") for i in range(10)])

        # 하나만 성공해야 함
        success_count = sum(1 for _, result in results if result)
        assert success_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_queue_operations(self):
        """동시 큐 작업 테스트."""
        manager = SessionQueueManager()

        async def add_message(i):
            return await manager.add_to_waiting(
                session_id="session1",
                user_id=f"user{i}",
                chat_id=i,
                message=f"message {i}",
                model="sonnet",
            )

        # 10개의 동시 추가
        positions = await asyncio.gather(*[add_message(i) for i in range(10)])

        # 모든 위치가 1~10 사이여야 함
        assert sorted(positions) == list(range(1, 11))
        assert manager.get_queue_size("session1") == 10
