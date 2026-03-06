"""Update Queue Manager - chat_id별 Update 순차 처리.

텔레그램 Update를 chat_id별로 큐잉하여 순차 처리.
각 chat_id마다 워커 태스크가 생성되어 큐를 소비.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

from telegram import Update
from telegram.ext import ContextTypes

from src.logging_config import logger

if TYPE_CHECKING:
    from src.bot.update_dispatcher import UpdateDispatcher


class UpdateType(Enum):
    """Update 유형."""
    COMMAND = "command"
    MESSAGE = "message"
    CALLBACK = "callback"
    FORCE_REPLY = "force_reply"


@dataclass
class QueuedUpdate:
    """큐잉된 Update."""
    update: Update
    context: ContextTypes.DEFAULT_TYPE
    update_type: UpdateType
    enqueued_at: float = field(default_factory=time.time)
    timeout_at: float = field(default=0.0)

    def __post_init__(self):
        if self.timeout_at == 0.0:
            self.timeout_at = self.enqueued_at + 60  # 60초 타임아웃

    def is_expired(self) -> bool:
        """타임아웃 여부 확인."""
        return time.time() > self.timeout_at


class UpdateQueueManager:
    """Update 큐 매니저 - chat_id별 순차 처리."""

    def __init__(self, max_queue_size: int = 50, item_timeout: int = 60):
        """초기화.

        Args:
            max_queue_size: chat_id당 최대 큐 크기
            item_timeout: 큐 항목 타임아웃 (초)
        """
        self._max_queue_size = max_queue_size
        self._item_timeout = item_timeout
        self._queues: dict[int, asyncio.Queue[QueuedUpdate]] = {}
        self._workers: dict[int, asyncio.Task] = {}
        self._dispatcher: Optional["UpdateDispatcher"] = None
        self._running = False
        self._lock = asyncio.Lock()

    def set_dispatcher(self, dispatcher: "UpdateDispatcher") -> None:
        """Dispatcher 설정."""
        self._dispatcher = dispatcher
        logger.debug("[UpdateQueue] Dispatcher 설정됨")

    def start(self) -> None:
        """매니저 시작."""
        if self._running:
            return
        self._running = True
        logger.info("[UpdateQueue] UpdateQueueManager started")

    def stop(self) -> None:
        """매니저 중지 - 모든 워커 취소."""
        self._running = False
        for chat_id, task in list(self._workers.items()):
            if not task.done():
                task.cancel()
                logger.debug(f"[UpdateQueue] Worker cancelled - chat_id={chat_id}")
        self._workers.clear()
        self._queues.clear()
        logger.info("[UpdateQueue] UpdateQueueManager stopped")

    async def enqueue(
        self,
        chat_id: int,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        update_type: UpdateType,
    ) -> bool:
        """Update를 큐에 추가.

        Args:
            chat_id: 채팅 ID
            update: 텔레그램 Update
            context: 텔레그램 Context
            update_type: Update 유형

        Returns:
            성공 여부 (큐가 가득 차면 False)
        """
        if not self._running:
            logger.warning("[UpdateQueue] 매니저가 실행 중이 아님")
            return False

        async with self._lock:
            # 큐 생성 (없으면)
            if chat_id not in self._queues:
                self._queues[chat_id] = asyncio.Queue(maxsize=self._max_queue_size)
                logger.debug(f"[UpdateQueue] 새 큐 생성 - chat_id={chat_id}")

            queue = self._queues[chat_id]

            # 큐가 가득 찼는지 확인
            if queue.full():
                logger.warning(f"[UpdateQueue] 큐 가득 참 - chat_id={chat_id}, size={queue.qsize()}")
                return False

            # 큐에 추가
            queued = QueuedUpdate(
                update=update,
                context=context,
                update_type=update_type,
                timeout_at=time.time() + self._item_timeout,
            )
            await queue.put(queued)
            logger.debug(f"[UpdateQueue] Enqueued - chat_id={chat_id}, type={update_type.value}, size={queue.qsize()}")

            # 워커 보장
            await self._ensure_worker(chat_id)

        return True

    async def _ensure_worker(self, chat_id: int) -> None:
        """워커 태스크 보장 (없거나 종료되었으면 생성)."""
        task = self._workers.get(chat_id)
        if task is None or task.done():
            self._workers[chat_id] = asyncio.create_task(
                self._process_chat_queue(chat_id),
                name=f"update-worker-{chat_id}"
            )
            logger.debug(f"[UpdateQueue] 워커 생성 - chat_id={chat_id}")

    async def _process_chat_queue(self, chat_id: int) -> None:
        """특정 chat_id의 큐 처리."""
        logger.debug(f"[UpdateQueue] 워커 시작 - chat_id={chat_id}")
        queue = self._queues.get(chat_id)
        if not queue:
            logger.warning(f"[UpdateQueue] 큐 없음 - chat_id={chat_id}")
            return

        idle_start: Optional[float] = None
        IDLE_TIMEOUT = 30  # 30초간 비면 워커 종료

        while self._running:
            try:
                # 큐에서 항목 꺼내기 (1초 타임아웃)
                try:
                    queued = await asyncio.wait_for(queue.get(), timeout=1.0)
                    idle_start = None  # 항목 받으면 idle 리셋
                except asyncio.TimeoutError:
                    # 큐가 비어있음
                    if idle_start is None:
                        idle_start = time.time()
                    elif time.time() - idle_start > IDLE_TIMEOUT:
                        logger.debug(f"[UpdateQueue] 워커 종료 (idle timeout) - chat_id={chat_id}")
                        break
                    continue

                # 타임아웃 체크
                if queued.is_expired():
                    logger.debug(
                        f"[UpdateQueue] 만료된 업데이트 스킵 - chat_id={chat_id}, "
                        f"type={queued.update_type.value}, age={time.time() - queued.enqueued_at:.1f}s"
                    )
                    queue.task_done()
                    continue

                # Dispatcher 호출
                if self._dispatcher:
                    try:
                        await self._dispatcher.dispatch(
                            queued.update,
                            queued.context,
                            queued.update_type,
                        )
                    except Exception as e:
                        logger.exception(
                            f"[UpdateQueue] Dispatch 에러 - chat_id={chat_id}, "
                            f"type={queued.update_type.value}: {e}"
                        )
                else:
                    logger.warning("[UpdateQueue] Dispatcher 미설정 - 업데이트 무시")

                queue.task_done()

            except asyncio.CancelledError:
                logger.debug(f"[UpdateQueue] 워커 취소됨 - chat_id={chat_id}")
                break
            except Exception as e:
                logger.exception(f"[UpdateQueue] 워커 루프 에러 - chat_id={chat_id}: {e}")
                await asyncio.sleep(0.5)  # 에러 시 잠시 대기

        # 워커 종료 정리
        async with self._lock:
            if chat_id in self._workers:
                del self._workers[chat_id]
            # 빈 큐도 정리
            if chat_id in self._queues and self._queues[chat_id].empty():
                del self._queues[chat_id]
                logger.debug(f"[UpdateQueue] 빈 큐 정리 - chat_id={chat_id}")

        logger.debug(f"[UpdateQueue] 워커 종료 - chat_id={chat_id}")

    def get_queue_status(self, chat_id: int) -> dict:
        """특정 chat_id의 큐 상태 조회."""
        queue = self._queues.get(chat_id)
        worker = self._workers.get(chat_id)
        return {
            "queue_size": queue.qsize() if queue else 0,
            "worker_active": worker is not None and not worker.done() if worker else False,
        }

    def get_all_status(self) -> dict:
        """전체 상태 조회."""
        result = {
            "running": self._running,
            "total_queues": len(self._queues),
            "total_workers": len([t for t in self._workers.values() if not t.done()]),
            "chats": {},
        }
        for chat_id in set(self._queues.keys()) | set(self._workers.keys()):
            result["chats"][chat_id] = self.get_queue_status(chat_id)
        return result
