"""Diary plugin - daily journal with date-based entries."""

from datetime import date as _date, timedelta
from typing import Optional, cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply

from src.bot.formatters import escape_html
from src.plugins.loader import (
    PLUGIN_SURFACE_CATALOG,
    PLUGIN_SURFACE_MAIN_MENU,
    Plugin,
    PluginInteraction,
    PluginMenuEntry,
    PluginResult,
    ScheduledAction,
)
from src.plugins.storage import DiaryStore
from src.repository.adapters import RepositoryDiaryStore
from src.time_utils import app_today

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _format_date_display(date_str: str) -> str:
    """Format date string to display format: 2026/03/17 (Mon)."""
    d = _date.fromisoformat(date_str)
    weekday = WEEKDAY_NAMES[d.weekday()]
    return f"{d.year}/{d.month:02d}/{d.day:02d} ({weekday})"


def _format_date_short(date_str: str) -> str:
    """Format date string to short display: 3/17 (Mon)."""
    d = _date.fromisoformat(date_str)
    weekday = WEEKDAY_NAMES[d.weekday()]
    return f"{d.month}/{d.day} ({weekday})"


class DiaryPlugin(Plugin):
    """Daily diary plugin - one entry per day."""

    name = "diary"
    description = "Daily diary management"
    display_name = "Diary"
    MENU_ENTRY = PluginMenuEntry(
        label="📓 Diary",
        surfaces=(PLUGIN_SURFACE_CATALOG, PLUGIN_SURFACE_MAIN_MENU),
        priority=40,
        default_promoted=True,
    )
    usage = (
        "📓 <b>Diary Plugin</b>\n\n"
        "<code>일기</code> or <code>/diary</code> - Open diary\n\n"
        "<b>Features</b>\n"
        "• 📝 Write today's diary\n"
        "• 📄 Browse past entries\n"
        "• ✏️ Edit / 🗑 Delete entries"
    )

    CALLBACK_PREFIX = "diary:"
    FORCE_REPLY_MARKER = "diary_write"

    TRIGGER_KEYWORDS = ["diary", "일기"]

    EXCLUDE_PATTERNS = [
        r"(란|이란)\s*뭐",
        r"(가|이)\s*뭐",
    ]

    async def can_handle(self, message: str, chat_id: int) -> bool:
        """Match exact keywords or keyword followed by a sub-command word (e.g. '일기 쓰기')."""
        import re as _re
        msg = message.strip().lower()
        for pattern in self.EXCLUDE_PATTERNS:
            if _re.search(pattern, msg, _re.IGNORECASE):
                return False
        for keyword in self.TRIGGER_KEYWORDS:
            kw = keyword.lower()
            if msg == kw:
                return True
            if msg.startswith(kw) and len(msg) > len(kw) and msg[len(kw)].isspace():
                return True
        return False

    def get_schema(self) -> str:
        return """
CREATE TABLE IF NOT EXISTS diaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_diaries_chat_date
    ON diaries(chat_id, date);
CREATE INDEX IF NOT EXISTS idx_diaries_chat_id
    ON diaries(chat_id);
CREATE TRIGGER IF NOT EXISTS update_diaries_timestamp
AFTER UPDATE ON diaries
BEGIN
    UPDATE diaries SET updated_at = datetime('now') WHERE id = NEW.id;
END;
"""

    @property
    def store(self) -> DiaryStore:
        """Diary storage adapter bound by the plugin runtime."""
        return cast(DiaryStore, self.storage)

    def build_storage(self, repository):
        """Bind diary persistence through a bounded adapter."""
        return RepositoryDiaryStore(repository)

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        result = self._handle_list(chat_id)
        return PluginResult(
            handled=True,
            response=result["text"],
            reply_markup=result.get("reply_markup"),
        )

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ Invalid request.", "edit": True}

        action = parts[1]

        if action == "menu":
            return self._handle_list(chat_id)
        elif action == "write":
            return self._handle_write_check(chat_id)
        elif action == "write_yesterday":
            yesterday = (app_today() - timedelta(days=1)).isoformat()
            return self._handle_write_check(chat_id, target_date=yesterday)
        elif action == "list":
            try:
                year = int(parts[2]) if len(parts) > 2 else None
                month = int(parts[3]) if len(parts) > 3 else None
            except (ValueError, IndexError):
                return {"text": "⚠️ Invalid request.", "edit": True}
            return self._handle_list(chat_id, year=year, month=month)
        elif action == "view":
            diary_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_view(chat_id, diary_id)
        elif action == "edit":
            diary_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_edit_prompt(chat_id, diary_id)
        elif action == "del":
            diary_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_delete_confirm(chat_id, diary_id)
        elif action == "del_confirm":
            diary_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_delete_execute(chat_id, diary_id)
        else:
            return {"text": "❌ Unknown command.", "edit": True}

    def get_scheduled_actions(self) -> list[ScheduledAction]:
        return [
            ScheduledAction(name="daily_diary", description="📓 Daily diary reminder"),
        ]

    async def execute_scheduled_action(self, action_name: str, chat_id: int, schedule=None) -> str | dict:
        del schedule
        if action_name == "daily_diary":
            today = app_today()
            date = today.isoformat()
            existing = self.store.get_by_date(chat_id, date)

            if existing:
                buttons = [
                    [
                        InlineKeyboardButton("✏️ Edit", callback_data=f"diary:edit:{existing.id}"),
                        InlineKeyboardButton("👁 View", callback_data=f"diary:view:{existing.id}"),
                    ],
                    [InlineKeyboardButton("📄 List", callback_data="diary:list")],
                ]
                return {
                    "text": (
                        f"🔔 <i>Daily diary reminder</i>\n\n"
                        f"📓 Today's diary has already been written.\n\n"
                        f"<b>{_format_date_display(date)}</b>\n"
                        f"{escape_html(existing.content[:100])}{'...' if len(existing.content) > 100 else ''}"
                    ),
                    "reply_markup": InlineKeyboardMarkup(buttons),
                }

            buttons = [
                [
                    InlineKeyboardButton("📝 Write", callback_data="diary:write"),
                    InlineKeyboardButton("⏪ Yesterday", callback_data="diary:write_yesterday"),
                ],
                [InlineKeyboardButton("📄 List", callback_data="diary:list")],
            ]
            return {
                "text": (
                    f"🔔 <i>Daily diary reminder</i>\n\n"
                    f"📓 <b>Write Diary</b>\n\n"
                    f"{_format_date_display(date)}\n\n"
                    f"Record today in your diary."
                ),
                "reply_markup": InlineKeyboardMarkup(buttons),
            }
        raise NotImplementedError(f"Action '{action_name}' not implemented")

    def handle_interaction(
        self,
        message: str,
        chat_id: int,
        interaction: Optional[PluginInteraction] = None,
    ) -> dict:
        """Handle ForceReply responses for both write and edit."""
        content = message.strip()

        if not content:
            return {
                "text": "❌ Content cannot be empty.",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("📓 Menu", callback_data="diary:menu"),
                ]]),
            }

        action = interaction.action if interaction else "force_reply"
        state = interaction.state if interaction else {}

        if action == "edit":
            diary_id = state.get("diary_id", 0)
            return self._process_edit(chat_id, diary_id, content)
        else:
            target_date = state.get("target_date")
            return self._process_write(chat_id, content, target_date=target_date)

    # ==================== Write ====================

    def _handle_write_check(self, chat_id: int, target_date: str | None = None) -> dict:
        """Check for existing entry before starting write flow."""
        today = app_today()
        date = target_date or today.isoformat()
        is_yesterday = target_date is not None and target_date == (today - timedelta(days=1)).isoformat()
        label = "Yesterday's diary" if is_yesterday else "Today's diary"
        placeholder = "How was yesterday?" if is_yesterday else "How was today?"
        prompt = f"📓 Enter your {label.lower()}:"
        existing = self.store.get_by_date(chat_id, date)

        if existing:
            buttons = [
                [
                    InlineKeyboardButton("✏️ Edit", callback_data=f"diary:edit:{existing.id}"),
                    InlineKeyboardButton("👁 View", callback_data=f"diary:view:{existing.id}"),
                ],
                [InlineKeyboardButton("◀️ Menu", callback_data="diary:menu")],
            ]
            return {
                "text": f"📓 {label} has already been written.\n\n<b>{_format_date_display(date)}</b>\n{escape_html(existing.content[:100])}{'...' if len(existing.content) > 100 else ''}",
                "reply_markup": InlineKeyboardMarkup(buttons),
                "edit": True,
            }

        return {
            "text": f"📓 <b>Write Diary</b>\n\n{_format_date_display(date)}\n\n{'Record yesterday' if is_yesterday else 'Record today'} in your diary.",
            "force_reply_prompt": prompt,
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder=placeholder,
            ),
            "interaction_action": "write",
            "interaction_state": {"target_date": date},
            "edit": False,
        }

    def _process_write(self, chat_id: int, content: str, target_date: str | None = None) -> dict:
        """Process new diary entry from ForceReply."""
        today = app_today()
        date = target_date or today.isoformat()
        is_yesterday = target_date is not None and target_date == (today - timedelta(days=1)).isoformat()
        label = "Yesterday's diary" if is_yesterday else "Today's diary"

        # Race condition guard
        existing = self.store.get_by_date(chat_id, date)
        if existing:
            buttons = [
                [
                    InlineKeyboardButton("✏️ Edit", callback_data=f"diary:edit:{existing.id}"),
                    InlineKeyboardButton("👁 View", callback_data=f"diary:view:{existing.id}"),
                ],
            ]
            return {
                "text": f"📓 {label} has already been written.",
                "reply_markup": InlineKeyboardMarkup(buttons),
            }

        diary = self.store.add(chat_id, date, content)

        buttons = [[
            InlineKeyboardButton("👁 View", callback_data=f"diary:view:{diary.id}"),
            InlineKeyboardButton("📄 List", callback_data="diary:list"),
        ]]

        return {
            "text": f"✅ Diary saved!\n\n<b>{_format_date_display(date)}</b>",
            "reply_markup": InlineKeyboardMarkup(buttons),
        }

    # ==================== Edit ====================

    def _handle_edit_prompt(self, chat_id: int, diary_id: int) -> dict:
        """Send ForceReply for editing."""
        diary = self.store.get(diary_id)
        if not diary:
            return {"text": "❌ Diary entry not found.", "edit": True}

        if diary.chat_id != chat_id:
            return {"text": "❌ Permission denied.", "edit": True}

        return {
            "text": f"✏️ <b>Edit Diary</b>\n\n<b>{_format_date_display(diary.date)}</b>\n\nCurrent content:\n<code>{escape_html(diary.content)}</code>",
            "force_reply_prompt": "✏️ Enter new content:",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="Enter new content...",
            ),
            "interaction_action": "edit",
            "interaction_state": {"diary_id": diary_id},
            "edit": False,
        }

    def _process_edit(self, chat_id: int, diary_id: int, content: str) -> dict:
        """Process diary edit from ForceReply."""
        diary = self.store.get(diary_id)
        if not diary:
            return {"text": "❌ Diary entry not found."}

        if diary.chat_id != chat_id:
            return {"text": "❌ Permission denied."}

        self.store.update(diary_id, content)

        buttons = [[
            InlineKeyboardButton("👁 View", callback_data=f"diary:view:{diary_id}"),
            InlineKeyboardButton("📄 List", callback_data="diary:list"),
        ]]

        return {
            "text": f"✅ Diary updated!\n\n<b>{_format_date_display(diary.date)}</b>",
            "reply_markup": InlineKeyboardMarkup(buttons),
        }

    # ==================== List ====================

    def _handle_list(self, chat_id: int, year: int | None = None, month: int | None = None) -> dict:
        """Show month-based diary list."""
        today = app_today()
        if year is None or month is None:
            year, month = today.year, today.month

        entries = self.store.list_by_month(chat_id, year, month)
        total = self.store.count_by_chat(chat_id)

        month_display = f"{year}/{month:02d}"

        if not entries and total == 0:
            buttons = [
                [
                    InlineKeyboardButton("📝 Write", callback_data="diary:write"),
                    InlineKeyboardButton("⏪ Yesterday", callback_data="diary:write_yesterday"),
                ],
            ]
            return {
                "text": "📭 No diary entries yet.",
                "reply_markup": InlineKeyboardMarkup(buttons),
                "edit": True,
            }

        lines = [f"📓 <b>Diary List</b> ({month_display})\n"]

        buttons = []
        for entry in entries:
            preview = entry.content[:30] + "..." if len(entry.content) > 30 else entry.content
            preview = escape_html(preview).replace("\n", " ")
            date_short = _format_date_short(entry.date)

            buttons.append([
                InlineKeyboardButton(
                    f"{date_short} {preview}",
                    callback_data=f"diary:view:{entry.id}",
                )
            ])

        if not entries:
            lines.append("No entries this month.")

        # Month navigation
        nav_buttons = []
        prev_month = month - 1
        prev_year = year
        if prev_month < 1:
            prev_month, prev_year = 12, year - 1
        nav_buttons.append(
            InlineKeyboardButton(f"◀️ {prev_month}", callback_data=f"diary:list:{prev_year}:{prev_month}")
        )

        if year != today.year or month != today.month:
            nav_buttons.append(
                InlineKeyboardButton("📅 This month", callback_data="diary:list")
            )

        next_month = month + 1
        next_year = year
        if next_month > 12:
            next_month, next_year = 1, year + 1
        if _date(next_year, next_month, 1) <= today:
            nav_buttons.append(
                InlineKeyboardButton(f"{next_month} ▶️", callback_data=f"diary:list:{next_year}:{next_month}")
            )

        buttons.append(nav_buttons)

        lines.append(f"📊 This month: {len(entries)} · Total: {total}")

        buttons.append([InlineKeyboardButton("✨ Work with AI", callback_data="aiwork:diary")])
        buttons.append([
            InlineKeyboardButton("📝 Write", callback_data="diary:write"),
            InlineKeyboardButton("⏪ Yesterday", callback_data="diary:write_yesterday"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    # ==================== View ====================

    def _handle_view(self, chat_id: int, diary_id: int) -> dict:
        """View single diary entry."""
        diary = self.store.get(diary_id)
        if not diary:
            return {"text": "❌ Diary entry not found.", "edit": True}

        if diary.chat_id != chat_id:
            return {"text": "❌ Permission denied.", "edit": True}

        date_display = _format_date_display(diary.date)

        buttons = [
            [
                InlineKeyboardButton("✏️ Edit", callback_data=f"diary:edit:{diary_id}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"diary:del:{diary_id}"),
            ],
            [InlineKeyboardButton("◀️ List", callback_data="diary:list")],
        ]

        return {
            "text": f"📓 <b>{date_display}</b>\n\n<code>{escape_html(diary.content)}</code>",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    # ==================== Delete ====================

    def _handle_delete_confirm(self, chat_id: int, diary_id: int) -> dict:
        """Show delete confirmation."""
        diary = self.store.get(diary_id)
        if not diary:
            return {"text": "❌ Diary entry not found.", "edit": True}

        if diary.chat_id != chat_id:
            return {"text": "❌ Permission denied.", "edit": True}

        date_display = _format_date_display(diary.date)
        preview = escape_html(diary.content[:50]) + ("..." if len(diary.content) > 50 else "")

        buttons = [
            [
                InlineKeyboardButton("✅ Delete", callback_data=f"diary:del_confirm:{diary_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"diary:view:{diary_id}"),
            ]
        ]

        return {
            "text": f"🗑 <b>Delete this entry?</b>\n\n<b>{date_display}</b>\n{preview}",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_delete_execute(self, chat_id: int, diary_id: int) -> dict:
        """Execute diary deletion."""
        diary = self.store.get(diary_id)
        if not diary:
            return {"text": "❌ Diary entry not found.", "edit": True}

        if diary.chat_id != chat_id:
            return {"text": "❌ Permission denied.", "edit": True}

        date_display = _format_date_display(diary.date)
        self.store.delete(diary_id)

        result = self._handle_list(chat_id)
        result["text"] = f"🗑 <b>{date_display}</b> diary deleted.\n\n" + result["text"]
        return result
