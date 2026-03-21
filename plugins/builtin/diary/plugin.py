"""Diary plugin - daily journal with date-based entries."""

import re
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

WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]


def _format_date_display(date_str: str) -> str:
    """Format date string to Korean display format: 2026년 3월 17일 (월)."""
    d = _date.fromisoformat(date_str)
    weekday = WEEKDAY_NAMES[d.weekday()]
    return f"{d.year}년 {d.month}월 {d.day}일 ({weekday})"


def _format_date_short(date_str: str) -> str:
    """Format date string to short display: 3/17 (월)."""
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

    async def get_ai_dynamic_context(self, chat_id: int) -> str:
        from src.time_utils import app_now
        today = app_now()
        entries = self.store.list_by_month(chat_id, today.year, today.month)
        if not entries:
            return f"{today.year}년 {today.month}월에 작성된 일기가 없습니다."
        lines = [f"{today.year}년 {today.month}월 일기 ({len(entries)}개):"]
        for d in entries:
            lines.append(f"  - {d.date}: {d.content[:200]}")
        return "\n".join(lines)

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
        else:
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
                return {"text": "⚠️ 잘못된 요청입니다.", "edit": True}
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
            ScheduledAction(name="daily_diary", description="📓 오늘의 일기 작성 알림"),
        ]

    async def execute_scheduled_action(self, action_name: str, chat_id: int) -> str | dict:
        if action_name == "daily_diary":
            today = app_today()
            date = today.isoformat()
            existing = self.store.get_by_date(chat_id, date)

            if existing:
                buttons = [
                    [
                        InlineKeyboardButton("✏️ 수정", callback_data=f"diary:edit:{existing.id}"),
                        InlineKeyboardButton("👁 보기", callback_data=f"diary:view:{existing.id}"),
                    ],
                    [InlineKeyboardButton("📄 목록", callback_data="diary:list")],
                ]
                return {
                    "text": (
                        f"🔔 <i>오늘의 일기 작성 알림</i>\n\n"
                        f"📓 오늘의 일기는 이미 작성되었습니다.\n\n"
                        f"<b>{_format_date_display(date)}</b>\n"
                        f"{escape_html(existing.content[:100])}{'...' if len(existing.content) > 100 else ''}"
                    ),
                    "reply_markup": InlineKeyboardMarkup(buttons),
                }

            buttons = [
                [
                    InlineKeyboardButton("📝 쓰기", callback_data="diary:write"),
                    InlineKeyboardButton("⏪ 어제 쓰기", callback_data="diary:write_yesterday"),
                ],
                [InlineKeyboardButton("📄 목록", callback_data="diary:list")],
            ]
            return {
                "text": (
                    f"🔔 <i>오늘의 일기 작성 알림</i>\n\n"
                    f"📓 <b>일기 쓰기</b>\n\n"
                    f"{_format_date_display(date)}\n\n"
                    f"오늘 하루를 기록해보세요."
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
            target_date = state.get("target_date")
            return self._process_write(chat_id, content, target_date=target_date)

    # ==================== Write ====================

    def _handle_write_check(self, chat_id: int, target_date: str | None = None) -> dict:
        """Check for existing entry before starting write flow."""
        today = app_today()
        date = target_date or today.isoformat()
        is_yesterday = target_date is not None and target_date == (today - timedelta(days=1)).isoformat()
        label = "어제의 일기" if is_yesterday else "오늘의 일기"
        placeholder = "어제 하루는 어땠나요?" if is_yesterday else "오늘 하루는 어땠나요?"
        prompt = f"📓 {label}를 입력하세요:"
        existing = self.store.get_by_date(chat_id, date)

        if existing:
            buttons = [
                [
                    InlineKeyboardButton("✏️ 수정", callback_data=f"diary:edit:{existing.id}"),
                    InlineKeyboardButton("👁 보기", callback_data=f"diary:view:{existing.id}"),
                ],
                [InlineKeyboardButton("◀️ 메뉴", callback_data="diary:menu")],
            ]
            return {
                "text": f"📓 {label}는 이미 작성되었습니다.\n\n<b>{_format_date_display(date)}</b>\n{escape_html(existing.content[:100])}{'...' if len(existing.content) > 100 else ''}",
                "reply_markup": InlineKeyboardMarkup(buttons),
                "edit": True,
            }

        return {
            "text": f"📓 <b>일기 쓰기</b>\n\n{_format_date_display(date)}\n\n{'어제 하루를' if is_yesterday else '오늘 하루를'} 기록해보세요.",
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
        label = "어제의 일기" if is_yesterday else "오늘의 일기"

        # Race condition guard
        existing = self.store.get_by_date(chat_id, date)
        if existing:
            buttons = [
                [
                    InlineKeyboardButton("✏️ 수정", callback_data=f"diary:edit:{existing.id}"),
                    InlineKeyboardButton("👁 보기", callback_data=f"diary:view:{existing.id}"),
                ],
            ]
            return {
                "text": f"📓 {label}는 이미 작성되었습니다.",
                "reply_markup": InlineKeyboardMarkup(buttons),
            }

        diary = self.store.add(chat_id, date, content)

        buttons = [[
            InlineKeyboardButton("👁 보기", callback_data=f"diary:view:{diary.id}"),
            InlineKeyboardButton("📄 목록", callback_data="diary:list"),
        ]]

        return {
            "text": f"✅ 일기가 저장되었습니다!\n\n<b>{_format_date_display(date)}</b>",
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
            "text": f"✏️ <b>일기 수정</b>\n\n<b>{_format_date_display(diary.date)}</b>\n\n현재 내용:\n<code>{escape_html(diary.content)}</code>",
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

    def _handle_list(self, chat_id: int, year: int | None = None, month: int | None = None) -> dict:
        """Show month-based diary list."""
        today = app_today()
        if year is None or month is None:
            year, month = today.year, today.month

        entries = self.store.list_by_month(chat_id, year, month)
        total = self.store.count_by_chat(chat_id)

        month_display = f"{year}년 {month}월"

        if not entries and total == 0:
            buttons = [
                [
                    InlineKeyboardButton("📝 쓰기", callback_data="diary:write"),
                    InlineKeyboardButton("⏪ 어제 쓰기", callback_data="diary:write_yesterday"),
                ],
            ]
            return {
                "text": "📭 작성된 일기가 없습니다.",
                "reply_markup": InlineKeyboardMarkup(buttons),
                "edit": True,
            }

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

        if not entries:
            lines.append("이 달에 작성된 일기가 없습니다.")

        # Month navigation
        nav_buttons = []
        prev_month = month - 1
        prev_year = year
        if prev_month < 1:
            prev_month, prev_year = 12, year - 1
        nav_buttons.append(
            InlineKeyboardButton(f"◀️ {prev_month}월", callback_data=f"diary:list:{prev_year}:{prev_month}")
        )

        if year != today.year or month != today.month:
            nav_buttons.append(
                InlineKeyboardButton("📅 이번달", callback_data="diary:list")
            )

        next_month = month + 1
        next_year = year
        if next_month > 12:
            next_month, next_year = 1, year + 1
        if _date(next_year, next_month, 1) <= today:
            nav_buttons.append(
                InlineKeyboardButton(f"{next_month}월 ▶️", callback_data=f"diary:list:{next_year}:{next_month}")
            )

        buttons.append(nav_buttons)

        lines.append(f"📊 이 달 {len(entries)}개 · 전체 {total}개")

        buttons.append([InlineKeyboardButton("✨ AI와 작업하기", callback_data="aiwork:diary")])
        buttons.append([
            InlineKeyboardButton("📝 쓰기", callback_data="diary:write"),
            InlineKeyboardButton("⏪ 어제 쓰기", callback_data="diary:write_yesterday"),
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
            "text": f"📓 <b>{date_display}</b>\n\n<code>{escape_html(diary.content)}</code>",
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

        result = self._handle_list(chat_id)
        result["text"] = f"🗑 <b>{date_display}</b> 일기가 삭제되었습니다.\n\n" + result["text"]
        return result
