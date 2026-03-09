"""Todo scheduler - yesterday report and daily wrap-up."""

from datetime import date, time, timedelta
from typing import Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.formatters import escape_html
from src.logging_config import logger
from src.plugins.storage import TodoStore
from src.scheduler_manager import scheduler_manager
from src.time_utils import app_today, get_app_timezone

if TYPE_CHECKING:
    from telegram.ext import Application

KST = get_app_timezone()


class TodoScheduler:
    """Yesterday report and daily wrap-up scheduler."""

    OWNER = "TodoScheduler"

    def __init__(self, store: TodoStore, chat_ids: list[int]):
        self.store = store
        self.chat_ids = chat_ids
        self._app: Optional["Application"] = None

    def setup_jobs(self, app: "Application") -> None:
        """Set up scheduled jobs."""
        self._app = app
        scheduler_manager.unregister_by_owner(self.OWNER)

        scheduler_manager.register_daily(
            name="todo_yesterday_report",
            callback=self._yesterday_report_callback,
            time_of_day=time(9, 0, tzinfo=KST),
            owner=self.OWNER,
        )

        scheduler_manager.register_daily(
            name="todo_daily_wrap",
            callback=self._daily_wrap_callback,
            time_of_day=time(21, 0, tzinfo=KST),
            owner=self.OWNER,
        )

        logger.info("Todo scheduler setup complete - Yesterday report(09:00), Daily wrap-up(21:00)")

    async def _yesterday_report_callback(self, context) -> None:
        """09:00 - Yesterday's todo report."""
        logger.info("Yesterday todo report starting")
        yesterday = (app_today() - timedelta(days=1)).isoformat()

        for chat_id in self.chat_ids:
            try:
                todos = self.store.list_by_date(chat_id, yesterday)
                if not todos:
                    continue

                lines = [f"📋 <b>Yesterday's Todos ({yesterday})</b>\n"]
                pending_count = 0
                for todo in todos:
                    status = "✅" if todo.done else "⬜"
                    lines.append(f"{status} {escape_html(todo.text)}")
                    if not todo.done:
                        pending_count += 1

                done_count = len(todos) - pending_count
                lines.append(f"\n📊 {done_count}/{len(todos)} completed")

                buttons = []
                if pending_count > 0:
                    lines.append(f"\n{pending_count} incomplete items can be carried over to today.")
                    buttons.append([
                        InlineKeyboardButton("📋 Carry over", callback_data="td:yday"),
                    ])
                buttons.append([
                    InlineKeyboardButton("📄 Today", callback_data="td:list"),
                ])

                await context.bot.send_message(
                    chat_id=chat_id,
                    text="\n".join(lines),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                logger.info(f"Yesterday todo report sent: chat_id={chat_id}")

            except Exception as e:
                logger.error(f"Yesterday todo report failed: chat_id={chat_id}, error={e}")

    async def _daily_wrap_callback(self, context) -> None:
        """21:00 - Daily wrap-up."""
        logger.info("Daily wrap-up starting")
        today = app_today().isoformat()

        for chat_id in self.chat_ids:
            try:
                stats = self.store.stats_for_date(chat_id, today)
                if stats["total"] == 0:
                    continue

                lines = ["🌙 <b>Daily Wrap-up</b>\n"]

                if stats["pending"] == 0:
                    lines.append("🎉 All todos completed today!")
                else:
                    lines.append(f"📊 Today's progress: {stats['done']}/{stats['total']} completed\n")
                    lines.append("<b>Incomplete:</b>")

                    pending = self.store.pending_for_date(chat_id, today)
                    for todo in pending:
                        lines.append(f"  ⬜ {escape_html(todo.text)}")

                    lines.append("\nAny items to move to tomorrow?")

                buttons = []
                if stats["pending"] > 0:
                    buttons.append([
                        InlineKeyboardButton("📋 Multi-select", callback_data="td:multi"),
                    ])
                buttons.append([
                    InlineKeyboardButton("📄 List", callback_data="td:list"),
                ])

                await context.bot.send_message(
                    chat_id=chat_id,
                    text="\n".join(lines),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                logger.info(f"Daily wrap-up sent: chat_id={chat_id}")

            except Exception as e:
                logger.error(f"Daily wrap-up failed: chat_id={chat_id}, error={e}")
