"""Todo 스케줄러 - 어제 할일 리포트."""

from datetime import date, time, timedelta
from typing import Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.logging_config import logger
from src.scheduler_manager import scheduler_manager

if TYPE_CHECKING:
    from telegram.ext import Application
    from src.repository import Repository

KST = ZoneInfo("Asia/Seoul")


class TodoScheduler:
    """어제 할일 리포트 스케줄러."""

    OWNER = "TodoScheduler"

    def __init__(self, repository: "Repository", chat_ids: list[int]):
        self.repository = repository
        self.chat_ids = chat_ids
        self._app: Optional["Application"] = None

    def setup_jobs(self, app: "Application") -> None:
        """스케줄 작업 설정."""
        self._app = app
        scheduler_manager.unregister_by_owner(self.OWNER)

        scheduler_manager.register_daily(
            name="todo_yesterday_report",
            callback=self._yesterday_report_callback,
            time_of_day=time(9, 0, tzinfo=KST),
            owner=self.OWNER,
        )

        logger.info("Todo 스케줄러 설정 완료 - 어제 할일 리포트 (09:00)")

    async def _yesterday_report_callback(self, context) -> None:
        """09:00 - 어제 할일 리포트."""
        logger.info("어제 할일 리포트 시작")
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        for chat_id in self.chat_ids:
            try:
                todos = self.repository.list_todos_by_date(chat_id, yesterday)
                if not todos:
                    continue

                lines = [f"📋 <b>어제({yesterday}) 할일 리포트</b>\n"]
                pending_count = 0
                for todo in todos:
                    status = "✅" if todo.done else "⬜"
                    lines.append(f"{status} {todo.text}")
                    if not todo.done:
                        pending_count += 1

                done_count = len(todos) - pending_count
                lines.append(f"\n📊 {done_count}/{len(todos)} 완료")

                buttons = []
                if pending_count > 0:
                    lines.append(f"\n미완료 {pending_count}개 항목을 오늘로 이전할 수 있어요.")
                    buttons.append([
                        InlineKeyboardButton("📋 미완료 항목 이전", callback_data="td:yday"),
                    ])
                buttons.append([
                    InlineKeyboardButton("📄 오늘 할일", callback_data="td:list"),
                ])

                await context.bot.send_message(
                    chat_id=chat_id,
                    text="\n".join(lines),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                logger.info(f"어제 할일 리포트 전송: chat_id={chat_id}")

            except Exception as e:
                logger.error(f"어제 할일 리포트 실패: chat_id={chat_id}, error={e}")
