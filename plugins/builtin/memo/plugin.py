"""Memo plugin - button-based single entry point."""

import re
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply

from src.plugins.loader import Plugin, PluginResult


class MemoPlugin(Plugin):
    """Button-based memo plugin - single entry point."""

    name = "memo"
    description = "Save, view, and delete memos"
    usage = (
        "📝 <b>Memo Plugin</b>\n\n"
        "<code>memo</code> or <code>/memo</code>"
    )

    CALLBACK_PREFIX = "memo:"
    FORCE_REPLY_MARKER = "memo_add"
    MAX_MEMOS = 30

    TRIGGER_KEYWORDS = ["메모", "memo"]

    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇|뭔)",
        r"영어로|번역|translate",
        r"어떻게|왜|언제|어디",
        r"알려줘|설명|뜻",
    ]

    def __init__(self):
        super().__init__()
        self._selected: dict[int, set[int]] = {}  # chat_id -> set of memo_ids

    async def can_handle(self, message: str, chat_id: int) -> bool:
        """Check if message is memo-related."""
        msg = message.strip().lower()

        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return False

        for keyword in self.TRIGGER_KEYWORDS:
            if msg == keyword:
                return True

        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        """Show memo main screen."""
        result = self._handle_main(chat_id)
        return PluginResult(
            handled=True,
            response=result["text"],
            reply_markup=result.get("reply_markup")
        )

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        """Handle callback_data."""
        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ Invalid request.", "edit": True}

        action = parts[1]

        if action == "main":
            self._clear_selection(chat_id)
            return self._handle_main(chat_id)
        elif action == "list":
            return self._handle_list(chat_id)
        elif action == "add":
            return self._handle_add_prompt(chat_id)
        elif action == "del":
            memo_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_delete(chat_id, memo_id)
        elif action == "confirm_del":
            memo_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_confirm_delete(chat_id, memo_id)
        elif action == "cancel":
            return self._handle_list(chat_id)
        elif action == "select":
            return self._handle_select_mode(chat_id)
        elif action == "toggle":
            memo_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_toggle_selection(chat_id, memo_id)
        elif action == "del_selected":
            return self._handle_delete_selected(chat_id)
        elif action == "confirm_del_selected":
            return self._handle_confirm_delete_selected(chat_id)
        elif action == "cancel_select":
            self._clear_selection(chat_id)
            return self._handle_list(chat_id)
        else:
            return {"text": "❌ Unknown command.", "edit": True}

    def _clear_selection(self, chat_id: int) -> None:
        """Clear selection."""
        self._selected.pop(chat_id, None)

    def _get_selection(self, chat_id: int) -> set[int]:
        """Selected memo IDs."""
        return self._selected.get(chat_id, set())

    def _handle_main(self, chat_id: int) -> dict:
        """Main menu."""
        memos = self.repository.list_memos(chat_id)
        count = len(memos)

        buttons = [
            [
                InlineKeyboardButton("📄 List", callback_data="memo:list"),
                InlineKeyboardButton("➕ Add", callback_data="memo:add"),
            ]
        ]

        limit_text = f" (max {self.MAX_MEMOS})" if count >= self.MAX_MEMOS else ""

        return {
            "text": f"📝 <b>Memo</b>\n\nSaved: {count}{limit_text}",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_list(self, chat_id: int) -> dict:
        """Memo list."""
        memos = self.repository.list_memos(chat_id)

        if not memos:
            buttons = [
                [InlineKeyboardButton("➕ Add", callback_data="memo:add")],
                [InlineKeyboardButton("⬅️ Back", callback_data="memo:main")],
            ]
            return {
                "text": "📭 No saved memos.",
                "reply_markup": InlineKeyboardMarkup(buttons),
                "edit": True,
            }

        lines = ["📝 <b>Memo List</b>\n"]
        buttons = []

        for memo in memos:
            created = memo.created_at[:10]
            content_preview = memo.content[:30] + "..." if len(memo.content) > 30 else memo.content
            lines.append(f"<b>#{memo.id}</b> {memo.content}\n<i>{created}</i>")

            buttons.append([
                InlineKeyboardButton(
                    f"🗑️ #{memo.id} {content_preview[:15]}",
                    callback_data=f"memo:del:{memo.id}"
                )
            ])

        # Multi-select delete button (2+ memos)
        if len(memos) >= 2:
            buttons.append([
                InlineKeyboardButton("☑️ Multi-delete", callback_data="memo:select"),
            ])

        buttons.append([
            InlineKeyboardButton("➕ Add", callback_data="memo:add"),
            InlineKeyboardButton("🔄 Refresh", callback_data="memo:list"),
        ])
        buttons.append([
            InlineKeyboardButton("⬅️ Back", callback_data="memo:main"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_select_mode(self, chat_id: int) -> dict:
        """Multi-select mode."""
        memos = self.repository.list_memos(chat_id)
        selected = self._get_selection(chat_id)

        if not memos:
            return self._handle_list(chat_id)

        lines = ["☑️ <b>Select Memos to Delete</b>\n\nTap to select."]
        buttons = []

        for memo in memos:
            is_selected = memo.id in selected
            check = "✅" if is_selected else "⬜"
            content_preview = memo.content[:20] + "..." if len(memo.content) > 20 else memo.content

            buttons.append([
                InlineKeyboardButton(
                    f"{check} #{memo.id} {content_preview}",
                    callback_data=f"memo:toggle:{memo.id}"
                )
            ])

        selected_count = len(selected)
        if selected_count > 0:
            buttons.append([
                InlineKeyboardButton(
                    f"🗑️ Delete {selected_count}",
                    callback_data="memo:del_selected"
                ),
            ])

        buttons.append([
            InlineKeyboardButton("❌ Cancel", callback_data="memo:cancel_select"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_toggle_selection(self, chat_id: int, memo_id: int) -> dict:
        """Toggle memo selection."""
        if chat_id not in self._selected:
            self._selected[chat_id] = set()

        if memo_id in self._selected[chat_id]:
            self._selected[chat_id].discard(memo_id)
        else:
            self._selected[chat_id].add(memo_id)

        return self._handle_select_mode(chat_id)

    def _handle_delete_selected(self, chat_id: int) -> dict:
        """Confirm selected memo deletion."""
        selected = self._get_selection(chat_id)

        if not selected:
            return self._handle_select_mode(chat_id)

        memos = self.repository.list_memos(chat_id)
        selected_memos = [m for m in memos if m.id in selected]

        lines = [f"🗑️ <b>Delete {len(selected_memos)} Memos?</b>\n"]
        for memo in selected_memos:
            content_preview = memo.content[:30] + "..." if len(memo.content) > 30 else memo.content
            lines.append(f"• #{memo.id} {content_preview}")

        lines.append("\nAre you sure?")

        keyboard = [
            [
                InlineKeyboardButton("✅ Delete", callback_data="memo:confirm_del_selected"),
                InlineKeyboardButton("❌ Cancel", callback_data="memo:cancel_select"),
            ]
        ]

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(keyboard),
            "edit": True,
        }

    def _handle_confirm_delete_selected(self, chat_id: int) -> dict:
        """Execute selected memo deletion."""
        selected = self._get_selection(chat_id)

        if not selected:
            return self._handle_list(chat_id)

        deleted_count = 0
        for memo_id in selected:
            if self.repository.delete_memo(memo_id):
                deleted_count += 1

        self._clear_selection(chat_id)

        result = self._handle_list(chat_id)
        result["text"] = f"🗑️ {deleted_count} memos deleted\n\n" + result["text"]
        return result

    def _handle_add_prompt(self, chat_id: int) -> dict:
        """Add memo - ForceReply."""
        memos = self.repository.list_memos(chat_id)
        if len(memos) >= self.MAX_MEMOS:
            keyboard = [
                [InlineKeyboardButton("📄 List", callback_data="memo:list")],
                [InlineKeyboardButton("⬅️ Back", callback_data="memo:main")],
            ]
            return {
                "text": f"❌ Maximum {self.MAX_MEMOS} memos reached.\nDelete some before adding new ones.",
                "reply_markup": InlineKeyboardMarkup(keyboard),
                "edit": True,
            }

        return {
            "text": "📝 <b>Add Memo</b>\n\nEnter your memo below.",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="Enter memo..."
            ),
            "force_reply_marker": "memo_add",
            "edit": False,
        }

    def _handle_delete(self, chat_id: int, memo_id: int) -> dict:
        memo = self.repository.get_memo(memo_id)

        if not memo:
            return {"text": f"❌ Memo #{memo_id} not found.", "edit": True}

        keyboard = [
            [
                InlineKeyboardButton("✅ Delete", callback_data=f"memo:confirm_del:{memo_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="memo:cancel"),
            ]
        ]

        return {
            "text": f"🗑️ <b>Delete?</b>\n\n<b>#{memo.id}</b> {memo.content}",
            "reply_markup": InlineKeyboardMarkup(keyboard),
            "edit": True,
        }

    def _handle_confirm_delete(self, chat_id: int, memo_id: int) -> dict:
        memo = self.repository.get_memo(memo_id)

        if not memo:
            return {"text": f"❌ Memo #{memo_id} not found.", "edit": True}

        content = memo.content
        self.repository.delete_memo(memo_id)

        result = self._handle_list(chat_id)
        result["text"] = f"🗑️ Deleted: <s>{content[:20]}</s>\n\n" + result["text"]
        return result

    def handle_force_reply(self, message: str, chat_id: int) -> dict:
        content = message.strip()

        if not content:
            return {
                "text": "❌ Memo content is empty.",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("📝 Try again", callback_data="memo:add"),
                ]]),
            }

        memos = self.repository.list_memos(chat_id)
        if len(memos) >= self.MAX_MEMOS:
            return {
                "text": f"❌ Maximum {self.MAX_MEMOS} memos reached.\nDelete some before adding new ones.",
                "reply_markup": InlineKeyboardMarkup([
                    [InlineKeyboardButton("📄 List", callback_data="memo:list")],
                ]),
            }

        memo = self.repository.add_memo(chat_id, content)

        keyboard = [
            [
                InlineKeyboardButton("📄 List", callback_data="memo:list"),
                InlineKeyboardButton("➕ Add", callback_data="memo:add"),
            ]
        ]

        return {
            "text": f"✅ Memo saved!\n\n<b>#{memo.id}</b> {content}",
            "reply_markup": InlineKeyboardMarkup(keyboard),
        }
