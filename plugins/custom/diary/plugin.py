"""Diary plugin - daily journal with date-based entries."""

import re
from typing import Optional, cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply

from src.bot.formatters import escape_html
from src.plugins.loader import Plugin, PluginInteraction, PluginResult, ScheduledAction
from src.plugins.storage import DiaryStore
from src.repository.adapters import RepositoryDiaryStore
from src.time_utils import app_today

WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]
PAGE_SIZE = 10


def _format_date_display(date_str: str) -> str:
    """Format date string to Korean display format: 2026년 3월 17일 (월)."""
    from datetime import date as _date
    d = _date.fromisoformat(date_str)
    weekday = WEEKDAY_NAMES[d.weekday()]
    return f"{d.year}년 {d.month}월 {d.day}일 ({weekday})"


def _format_date_short(date_str: str) -> str:
    """Format date string to short display: 3/17 (월)."""
    from datetime import date as _date
    d = _date.fromisoformat(date_str)
    weekday = WEEKDAY_NAMES[d.weekday()]
    return f"{d.month}/{d.day} ({weekday})"


class DiaryPlugin(Plugin):
    """Daily diary plugin - one entry per day."""

    name = "diary"
    description = "Daily diary management"
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

    PATTERNS = [r"^일기$", r"^일기\s+(쓰기|목록|보기)"]

    EXCLUDE_PATTERNS = [
        r"(란|이란)\s*뭐",
        r"(가|이)\s*뭐",
    ]

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

    async def can_handle(self, message: str, chat_id: int) -> bool:
        msg = message.strip()

        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return False

        for pattern in self.PATTERNS:
            if re.search(pattern, msg):
                return True

        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        msg = message.strip()

        if re.search(r"쓰기", msg):
            result = self._handle_write_check(chat_id)
        elif re.search(r"목록|보기", msg):
            result = self._handle_list(chat_id, offset=0)
        else:
            result = self._handle_menu(chat_id)

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
            return self._handle_menu(chat_id)
        elif action == "write":
            return self._handle_write_check(chat_id)
        elif action == "list":
            offset = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_list(chat_id, offset=offset)
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
            ScheduledAction(name="daily_diary", description="📓 오늘의 일기 작성 알림"),
        ]

    async def execute_scheduled_action(self, action_name: str, chat_id: int) -> str:
        if action_name == "daily_diary":
            today = app_today().isoformat()
            existing = self.store.get_by_date(chat_id, today)
            if existing:
                return "📓 오늘의 일기는 이미 작성되었습니다."
            return "📓 오늘 하루는 어땠나요? 일기를 작성해보세요.\n/diary 또는 <code>일기 쓰기</code>로 시작하세요."
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
                "text": "❌ 내용이 비어 있습니다.",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("📓 메뉴", callback_data="diary:menu"),
                ]]),
            }

        action = interaction.action if interaction else "force_reply"
        state = interaction.state if interaction else {}

        if action == "edit":
            diary_id = state.get("diary_id", 0)
            return self._process_edit(chat_id, diary_id, content)
        else:
            return self._process_write(chat_id, content)

    # ==================== Menu ====================

    def _handle_menu(self, chat_id: int) -> dict:
        today = app_today().isoformat()
        existing = self.store.get_by_date(chat_id, today)
        total = self.store.count_by_chat(chat_id)

        if existing:
            today_status = f"✅ 오늘 일기 작성됨 ({_format_date_short(today)})"
        else:
            today_status = f"📝 오늘 일기 미작성 ({_format_date_short(today)})"

        buttons = [
            [
                InlineKeyboardButton("📝 쓰기", callback_data="diary:write"),
                InlineKeyboardButton("📄 목록", callback_data="diary:list"),
            ]
        ]

        return {
            "text": f"📓 <b>일기</b>\n\n{today_status}\n총 {total}개 기록",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    # ==================== Write ====================

    def _handle_write_check(self, chat_id: int) -> dict:
        """Check for existing entry before starting write flow."""
        today = app_today().isoformat()
        existing = self.store.get_by_date(chat_id, today)

        if existing:
            buttons = [
                [
                    InlineKeyboardButton("✏️ 수정", callback_data=f"diary:edit:{existing.id}"),
                    InlineKeyboardButton("👁 보기", callback_data=f"diary:view:{existing.id}"),
                ],
                [InlineKeyboardButton("◀️ 메뉴", callback_data="diary:menu")],
            ]
            return {
                "text": f"📓 오늘 일기는 이미 작성되었습니다.\n\n<b>{_format_date_display(today)}</b>\n{escape_html(existing.content[:100])}{'...' if len(existing.content) > 100 else ''}",
                "reply_markup": InlineKeyboardMarkup(buttons),
                "edit": True,
            }

        return {
            "text": f"📓 <b>일기 쓰기</b>\n\n{_format_date_display(today)}\n\n오늘 하루를 기록해보세요.",
            "force_reply_prompt": "📓 오늘의 일기를 입력하세요:",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="오늘 하루는 어땠나요?",
            ),
            "interaction_action": "write",
            "edit": False,
        }

    def _process_write(self, chat_id: int, content: str) -> dict:
        """Process new diary entry from ForceReply."""
        today = app_today().isoformat()

        # Race condition guard
        existing = self.store.get_by_date(chat_id, today)
        if existing:
            buttons = [
                [
                    InlineKeyboardButton("✏️ 수정", callback_data=f"diary:edit:{existing.id}"),
                    InlineKeyboardButton("👁 보기", callback_data=f"diary:view:{existing.id}"),
                ],
            ]
            return {
                "text": "📓 오늘 일기는 이미 작성되었습니다.",
                "reply_markup": InlineKeyboardMarkup(buttons),
            }

        diary = self.store.add(chat_id, today, content)

        buttons = [[
            InlineKeyboardButton("👁 보기", callback_data=f"diary:view:{diary.id}"),
            InlineKeyboardButton("📄 목록", callback_data="diary:list"),
        ]]

        return {
            "text": f"✅ 일기가 저장되었습니다!\n\n<b>{_format_date_display(today)}</b>",
            "reply_markup": InlineKeyboardMarkup(buttons),
        }

    # ==================== Edit ====================

    def _handle_edit_prompt(self, chat_id: int, diary_id: int) -> dict:
        """Send ForceReply for editing."""
        diary = self.store.get(diary_id)
        if not diary:
            return {"text": "❌ 일기를 찾을 수 없습니다.", "edit": True}

        if diary.chat_id != chat_id:
            return {"text": "❌ 권한이 없습니다.", "edit": True}

        return {
            "text": f"✏️ <b>일기 수정</b>\n\n<b>{_format_date_display(diary.date)}</b>\n\n현재 내용:\n{escape_html(diary.content)}",
            "force_reply_prompt": "✏️ 수정할 내용을 입력하세요:",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="수정할 내용을 입력하세요...",
            ),
            "interaction_action": "edit",
            "interaction_state": {"diary_id": diary_id},
            "edit": False,
        }

    def _process_edit(self, chat_id: int, diary_id: int, content: str) -> dict:
        """Process diary edit from ForceReply."""
        diary = self.store.get(diary_id)
        if not diary:
            return {"text": "❌ 일기를 찾을 수 없습니다."}

        if diary.chat_id != chat_id:
            return {"text": "❌ 권한이 없습니다."}

        self.store.update(diary_id, content)

        buttons = [[
            InlineKeyboardButton("👁 보기", callback_data=f"diary:view:{diary_id}"),
            InlineKeyboardButton("📄 목록", callback_data="diary:list"),
        ]]

        return {
            "text": f"✅ 일기가 수정되었습니다!\n\n<b>{_format_date_display(diary.date)}</b>",
            "reply_markup": InlineKeyboardMarkup(buttons),
        }

    # ==================== List ====================

    def _handle_list(self, chat_id: int, offset: int = 0) -> dict:
        """Show paginated diary list."""
        total = self.store.count_by_chat(chat_id)
        entries = self.store.list_by_chat(chat_id, limit=PAGE_SIZE, offset=offset)

        if not entries:
            buttons = [
                [InlineKeyboardButton("📝 쓰기", callback_data="diary:write")],
                [InlineKeyboardButton("◀️ 메뉴", callback_data="diary:menu")],
            ]
            return {
                "text": "📭 작성된 일기가 없습니다.",
                "reply_markup": InlineKeyboardMarkup(buttons),
                "edit": True,
            }

        # Determine month range for display
        first_date = entries[-1].date
        last_date = entries[0].date
        from datetime import date as _date
        first_d = _date.fromisoformat(first_date)
        last_d = _date.fromisoformat(last_date)
        if first_d.month == last_d.month and first_d.year == last_d.year:
            month_display = f"{last_d.year}년 {last_d.month}월"
        else:
            month_display = f"{first_d.month}월 ~ {last_d.month}월"

        lines = [f"📓 <b>일기 목록</b> ({month_display})\n"]

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

        # Pagination
        nav_buttons = []
        if offset > 0:
            prev_offset = max(0, offset - PAGE_SIZE)
            nav_buttons.append(
                InlineKeyboardButton("◀️ 이전", callback_data=f"diary:list:{prev_offset}")
            )
        if offset + PAGE_SIZE < total:
            next_offset = offset + PAGE_SIZE
            nav_buttons.append(
                InlineKeyboardButton("다음 ▶️", callback_data=f"diary:list:{next_offset}")
            )
        if nav_buttons:
            buttons.append(nav_buttons)

        lines.append(f"📊 총 {total}개 ({offset + 1}~{min(offset + PAGE_SIZE, total)})")

        buttons.append([
            InlineKeyboardButton("📝 쓰기", callback_data="diary:write"),
            InlineKeyboardButton("◀️ 메뉴", callback_data="diary:menu"),
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
            return {"text": "❌ 일기를 찾을 수 없습니다.", "edit": True}

        if diary.chat_id != chat_id:
            return {"text": "❌ 권한이 없습니다.", "edit": True}

        date_display = _format_date_display(diary.date)

        buttons = [
            [
                InlineKeyboardButton("✏️ 수정", callback_data=f"diary:edit:{diary_id}"),
                InlineKeyboardButton("🗑 삭제", callback_data=f"diary:del:{diary_id}"),
            ],
            [InlineKeyboardButton("◀️ 목록", callback_data="diary:list")],
        ]

        return {
            "text": f"📓 <b>{date_display}</b>\n\n{escape_html(diary.content)}",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    # ==================== Delete ====================

    def _handle_delete_confirm(self, chat_id: int, diary_id: int) -> dict:
        """Show delete confirmation."""
        diary = self.store.get(diary_id)
        if not diary:
            return {"text": "❌ 일기를 찾을 수 없습니다.", "edit": True}

        if diary.chat_id != chat_id:
            return {"text": "❌ 권한이 없습니다.", "edit": True}

        date_display = _format_date_display(diary.date)
        preview = escape_html(diary.content[:50]) + ("..." if len(diary.content) > 50 else "")

        buttons = [
            [
                InlineKeyboardButton("✅ 삭제", callback_data=f"diary:del_confirm:{diary_id}"),
                InlineKeyboardButton("❌ 취소", callback_data=f"diary:view:{diary_id}"),
            ]
        ]

        return {
            "text": f"🗑 <b>정말 삭제하시겠습니까?</b>\n\n<b>{date_display}</b>\n{preview}",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_delete_execute(self, chat_id: int, diary_id: int) -> dict:
        """Execute diary deletion."""
        diary = self.store.get(diary_id)
        if not diary:
            return {"text": "❌ 일기를 찾을 수 없습니다.", "edit": True}

        if diary.chat_id != chat_id:
            return {"text": "❌ 권한이 없습니다.", "edit": True}

        date_display = _format_date_display(diary.date)
        self.store.delete(diary_id)

        result = self._handle_list(chat_id, offset=0)
        result["text"] = f"🗑 <b>{date_display}</b> 일기가 삭제되었습니다.\n\n" + result["text"]
        return result
