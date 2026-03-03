"""Todo 스케줄러 - 시간대별 리마인더 관리."""

import asyncio
from datetime import datetime, time
from typing import Callable, Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from src.logging_config import logger

if TYPE_CHECKING:
    from telegram.ext import Application

from .manager import TodoManager, TimeSlot


# 한국 시간대
KST = ZoneInfo("Asia/Seoul")

# 스케줄 시간 설정
SCHEDULE_TIMES = {
    "morning_ask": time(8, 0),      # 08:00 - 오늘 할일 질문
    "morning_check": time(10, 0),   # 10:00 - 오전 할일 체크
    "afternoon_check": time(15, 0), # 15:00 - 오후 할일 체크
    "evening_check": time(19, 0),   # 19:00 - 저녁 할일 체크
}


class TodoScheduler:
    """할일 스케줄러."""

    def __init__(
        self,
        todo_manager: TodoManager,
        chat_ids: list[int],
        claude_parser: Optional[Callable] = None,
    ):
        """
        Args:
            todo_manager: 할일 관리자
            chat_ids: 알림 받을 채팅 ID 목록
            claude_parser: AI 파서 함수 (할일 텍스트 -> 시간대별 분류)
        """
        self.manager = todo_manager
        self.chat_ids = chat_ids
        self.claude_parser = claude_parser
        self._app: Optional["Application"] = None
        self._jobs = []

    def set_app(self, app: "Application") -> None:
        """텔레그램 앱 설정."""
        self._app = app

    def register_chat_id(self, chat_id: int) -> None:
        """채팅 ID 등록."""
        if chat_id not in self.chat_ids:
            self.chat_ids.append(chat_id)
            logger.info(f"Todo 스케줄러에 chat_id 등록: {chat_id}")

    def setup_jobs(self, app: "Application") -> None:
        """스케줄 작업 설정."""
        self._app = app
        job_queue = app.job_queue

        if job_queue is None:
            logger.error("job_queue가 없습니다. APScheduler가 설치되어 있는지 확인하세요.")
            return

        # 기존 작업 제거
        for job in self._jobs:
            job.schedule_removal()
        self._jobs.clear()

        # 08:00 - 오늘 할일 질문
        job = job_queue.run_daily(
            self._morning_ask_callback,
            time=SCHEDULE_TIMES["morning_ask"],
            name="todo_morning_ask",
        )
        self._jobs.append(job)
        logger.info(f"스케줄 등록: 08:00 오늘 할일 질문")

        # 10:00 - 오전 할일 체크
        job = job_queue.run_daily(
            self._morning_check_callback,
            time=SCHEDULE_TIMES["morning_check"],
            name="todo_morning_check",
        )
        self._jobs.append(job)
        logger.info(f"스케줄 등록: 10:00 오전 할일 체크")

        # 15:00 - 오후 할일 체크
        job = job_queue.run_daily(
            self._afternoon_check_callback,
            time=SCHEDULE_TIMES["afternoon_check"],
            name="todo_afternoon_check",
        )
        self._jobs.append(job)
        logger.info(f"스케줄 등록: 15:00 오후 할일 체크")

        # 19:00 - 저녁 할일 체크
        job = job_queue.run_daily(
            self._evening_check_callback,
            time=SCHEDULE_TIMES["evening_check"],
            name="todo_evening_check",
        )
        self._jobs.append(job)
        logger.info(f"스케줄 등록: 19:00 저녁 할일 체크")

        logger.info(f"Todo 스케줄러 설정 완료 - {len(self._jobs)}개 작업")

    async def _morning_ask_callback(self, context) -> None:
        """08:00 - 오늘 할일 질문."""
        logger.info("🌅 아침 할일 질문 시작")

        message = (
            "🌅 <b>좋은 아침이에요!</b>\n\n"
            "오늘 할 일이 뭐예요?\n"
            "편하게 말해주세요. 시간대별로 정리해드릴게요.\n\n"
            "<i>예: 오전에 회의하고, 점심에 친구 만나고, 저녁엔 운동해야해</i>"
        )

        for chat_id in self._get_active_chat_ids():
            try:
                # 입력 대기 상태로 설정
                self.manager.set_pending_input(chat_id, True)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="HTML"
                )
                logger.info(f"아침 질문 전송 완료: chat_id={chat_id}")
            except Exception as e:
                logger.error(f"아침 질문 전송 실패: chat_id={chat_id}, error={e}")

    async def _morning_check_callback(self, context) -> None:
        """10:00 - 오전 할일 체크."""
        await self._send_slot_reminder(context, TimeSlot.MORNING, "🌅 오전")

    async def _afternoon_check_callback(self, context) -> None:
        """15:00 - 오후 할일 체크."""
        await self._send_slot_reminder(context, TimeSlot.AFTERNOON, "☀️ 오후")

    async def _evening_check_callback(self, context) -> None:
        """19:00 - 저녁 할일 체크."""
        await self._send_slot_reminder(context, TimeSlot.EVENING, "🌙 저녁")

    async def _send_slot_reminder(self, context, slot: TimeSlot, slot_name: str) -> None:
        """시간대별 리마인더 전송."""
        logger.info(f"{slot_name} 할일 체크 시작")

        for chat_id in self._get_active_chat_ids():
            try:
                daily = self.manager.get_today(chat_id)
                tasks = daily.get_tasks(slot)

                if not tasks:
                    # 할일이 없으면 스킵
                    continue

                pending = [t for t in tasks if not t.done]
                if not pending:
                    # 모두 완료됨
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"{slot_name} 할일 모두 완료! 👏",
                        parse_mode="HTML"
                    )
                    continue

                # 미완료 할일 알림
                lines = [f"<b>{slot_name} 할일 어때요?</b>\n"]
                for i, task in enumerate(tasks, 1):
                    status = "✅" if task.done else "⬜"
                    lines.append(f"{status} {i}. {task.text}")

                lines.append(f"\n완료한 건 말해주세요!")
                lines.append(f"<i>예: 회의 끝났어, 1번 완료</i>")

                await context.bot.send_message(
                    chat_id=chat_id,
                    text="\n".join(lines),
                    parse_mode="HTML"
                )
                logger.info(f"{slot_name} 리마인더 전송: chat_id={chat_id}")

            except Exception as e:
                logger.error(f"{slot_name} 리마인더 실패: chat_id={chat_id}, error={e}")

    def _get_active_chat_ids(self) -> list[int]:
        """활성 채팅 ID 목록 (등록된 + 오늘 데이터 있는)."""
        # 설정된 chat_ids + 데이터가 있는 chat_ids
        registered = set(self.manager.get_registered_chat_ids())
        configured = set(self.chat_ids)
        return list(registered | configured)

    async def send_immediate_reminder(self, chat_id: int) -> str:
        """즉시 리마인더 전송 (테스트용)."""
        return self.manager.get_daily_summary(chat_id)

    def get_next_schedules(self) -> list[dict]:
        """다음 스케줄 목록."""
        now = datetime.now(KST)
        schedules = []

        for name, scheduled_time in SCHEDULE_TIMES.items():
            scheduled_dt = datetime.combine(now.date(), scheduled_time, tzinfo=KST)
            if scheduled_dt < now:
                # 이미 지났으면 다음 날
                from datetime import timedelta
                scheduled_dt += timedelta(days=1)

            schedules.append({
                "name": name,
                "time": scheduled_time.strftime("%H:%M"),
                "next": scheduled_dt.isoformat(),
            })

        return sorted(schedules, key=lambda x: x["next"])
