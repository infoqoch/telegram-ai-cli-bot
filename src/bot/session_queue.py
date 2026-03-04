"""Session Queue Manager - 세션별 메시지 큐 관리 (단일 진실 소스).

상태 관리를 단순화하여 락, 대기열, 태스크를 하나로 통합.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional, Callable, Awaitable

from src.logging_config import logger

if TYPE_CHECKING:
    from telegram import Bot


class SessionStatus(Enum):
    """세션 상태."""
    IDLE = "idle"           # 대기 중 (처리 가능)
    PROCESSING = "processing"  # 처리 중 (락)


@dataclass
class QueuedMessage:
    """대기 중인 메시지."""
    user_id: str
    chat_id: int
    message: str
    session_id: str  # 처리할 세션
    model: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 300)  # 5분 후 만료
    is_new_session: bool = False
    project_path: str = ""

    def is_expired(self) -> bool:
        """만료 여부 확인."""
        return time.time() > self.expires_at


@dataclass
class SessionState:
    """세션 상태 - 단일 진실 소스."""
    session_id: str
    status: SessionStatus = SessionStatus.IDLE
    current_user_id: Optional[str] = None  # 처리 중인 유저
    current_message: Optional[str] = None  # 처리 중인 메시지 (미리보기용)
    started_at: Optional[float] = None  # 처리 시작 시간
    waiting_queue: list[QueuedMessage] = field(default_factory=list)

    def is_locked(self) -> bool:
        """락 상태 확인."""
        return self.status == SessionStatus.PROCESSING

    def lock(self, user_id: str, message: str) -> None:
        """세션 락."""
        self.status = SessionStatus.PROCESSING
        self.current_user_id = user_id
        self.current_message = message[:50] if message else ""
        self.started_at = time.time()
        logger.debug(f"[SessionQueue] 락 획득 - session={self.session_id[:8]}, user={user_id}")

    def unlock(self) -> None:
        """세션 언락."""
        logger.debug(f"[SessionQueue] 락 해제 - session={self.session_id[:8]}")
        self.status = SessionStatus.IDLE
        self.current_user_id = None
        self.current_message = None
        self.started_at = None

    def add_to_queue(self, msg: QueuedMessage) -> int:
        """대기열에 메시지 추가. 대기 순번 반환."""
        self.waiting_queue.append(msg)
        position = len(self.waiting_queue)
        logger.debug(f"[SessionQueue] 대기열 추가 - session={self.session_id[:8]}, position={position}")
        return position

    def pop_from_queue(self) -> Optional[QueuedMessage]:
        """대기열에서 다음 메시지 꺼내기. 만료된 메시지는 스킵."""
        while self.waiting_queue:
            msg = self.waiting_queue.pop(0)
            if not msg.is_expired():
                logger.debug(f"[SessionQueue] 대기열 팝 - session={self.session_id[:8]}, user={msg.user_id}")
                return msg
            logger.debug(f"[SessionQueue] 만료된 메시지 스킵 - session={self.session_id[:8]}")
        return None

    def get_queue_size(self) -> int:
        """대기열 크기."""
        return len(self.waiting_queue)

    def clear_expired(self) -> int:
        """만료된 메시지 정리. 정리된 수 반환."""
        before = len(self.waiting_queue)
        self.waiting_queue = [msg for msg in self.waiting_queue if not msg.is_expired()]
        cleared = before - len(self.waiting_queue)
        if cleared:
            logger.debug(f"[SessionQueue] 만료 메시지 정리 - session={self.session_id[:8]}, cleared={cleared}")
        return cleared


class SessionQueueManager:
    """세션 큐 매니저 - 전역 싱글톤으로 사용."""

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}  # session_id -> SessionState
        self._lock = asyncio.Lock()  # 동시성 보호

    def _get_or_create(self, session_id: str) -> SessionState:
        """세션 상태 가져오기 (없으면 생성)."""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(session_id=session_id)
            logger.trace(f"[SessionQueue] 새 세션 상태 생성 - session={session_id[:8]}")
        return self._sessions[session_id]

    def is_locked(self, session_id: str) -> bool:
        """세션이 락 상태인지 확인."""
        if session_id not in self._sessions:
            return False
        return self._sessions[session_id].is_locked()

    def get_status(self, session_id: str) -> Optional[SessionState]:
        """세션 상태 조회."""
        return self._sessions.get(session_id)

    async def try_lock(self, session_id: str, user_id: str, message: str) -> bool:
        """락 획득 시도. 성공하면 True, 이미 락이면 False."""
        async with self._lock:
            state = self._get_or_create(session_id)
            if state.is_locked():
                logger.debug(f"[SessionQueue] 락 획득 실패 (이미 락) - session={session_id[:8]}")
                return False
            state.lock(user_id, message)
            return True

    async def unlock(self, session_id: str) -> Optional[QueuedMessage]:
        """락 해제 및 대기열에서 다음 메시지 반환."""
        async with self._lock:
            state = self._sessions.get(session_id)
            if not state:
                return None
            state.unlock()
            return state.pop_from_queue()

    async def add_to_waiting(
        self,
        session_id: str,
        user_id: str,
        chat_id: int,
        message: str,
        model: str,
        is_new_session: bool = False,
        project_path: str = "",
    ) -> int:
        """대기열에 메시지 추가. 대기 순번 반환."""
        async with self._lock:
            state = self._get_or_create(session_id)
            queued = QueuedMessage(
                user_id=user_id,
                chat_id=chat_id,
                message=message,
                session_id=session_id,
                model=model,
                is_new_session=is_new_session,
                project_path=project_path,
            )
            return state.add_to_queue(queued)

    def get_queue_size(self, session_id: str) -> int:
        """세션 대기열 크기."""
        state = self._sessions.get(session_id)
        return state.get_queue_size() if state else 0

    def get_all_sessions_status(self) -> dict[str, dict]:
        """모든 세션 상태 요약."""
        result = {}
        for sid, state in self._sessions.items():
            result[sid[:8]] = {
                "status": state.status.value,
                "queue_size": len(state.waiting_queue),
                "current_user": state.current_user_id,
            }
        return result

    async def cleanup_expired(self) -> int:
        """모든 세션의 만료된 메시지 정리."""
        async with self._lock:
            total_cleared = 0
            for state in self._sessions.values():
                total_cleared += state.clear_expired()
            return total_cleared

    async def force_unlock(self, session_id: str) -> bool:
        """강제 락 해제 (좀비 태스크 정리용)."""
        async with self._lock:
            state = self._sessions.get(session_id)
            if not state:
                return False
            if state.is_locked():
                logger.warning(f"[SessionQueue] 강제 락 해제 - session={session_id[:8]}")
                state.unlock()
                return True
            return False


# 전역 인스턴스
session_queue_manager = SessionQueueManager()
