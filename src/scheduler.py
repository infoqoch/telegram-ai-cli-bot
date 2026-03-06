"""세션 스케줄러 - 매니저 세션 자동 compact."""

from datetime import time
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

from src.logging_config import logger
from src.scheduler_manager import scheduler_manager

if TYPE_CHECKING:
    from telegram.ext import Application
    from src.services.session_service import SessionService
    from src.claude.client import ClaudeClient


# 한국 시간대
KST = ZoneInfo("Asia/Seoul")

# Compact 스케줄 시간 (22:00 KST - TodoScheduler와 충돌 방지)
COMPACT_TIME = time(22, 0, tzinfo=KST)


class SessionScheduler:
    """세션 관리 스케줄러."""

    OWNER = "SessionScheduler"

    def __init__(
        self,
        session_store: "SessionService",
        claude_client: "ClaudeClient",
        admin_chat_id: Optional[int] = None,
    ):
        """
        Args:
            session_store: 세션 서비스
            claude_client: Claude CLI 클라이언트
            admin_chat_id: 보고 받을 채팅 ID (None이면 보고 안함)
        """
        self.sessions = session_store
        self.claude = claude_client
        self.admin_chat_id = admin_chat_id
        self._app: Optional["Application"] = None

    def setup_jobs(self, app: "Application") -> None:
        """스케줄 작업 설정 (SchedulerManager 사용)."""
        self._app = app

        # 기존 작업 제거
        scheduler_manager.unregister_by_owner(self.OWNER)

        logger.info("SessionScheduler: 매니저 세션 제거로 인해 스케줄 작업 없음")
