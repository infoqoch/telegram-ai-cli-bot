"""Todo plugin - Repository-based todo management."""

import re
from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply

from src.logging_config import logger
from src.plugins.loader import Plugin, PluginResult, ScheduledAction
from src.time_utils import app_today


class TodoPlugin(Plugin):
    """Todo management plugin."""

    name = "todo"
    description = "Todo management"
    usage = (
        "📋 <b>Todo Plugin</b>\n\n"
        "<b>Getting Started</b>\n"
        "• <code>/todo</code> - Open todo list\n\n"
        "<b>Features</b>\n"
        "• 📄 List - View today's todos\n"
        "• ➕ Add - Add new todo\n"
        "• Tap item - Complete / Delete / Move to tomorrow"
    )

    TRIGGER_KEYWORDS = ["todo", "할일", "투두"]

    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇|뭔)",
        r"영어로|번역|translate",
        r"어떻게|왜|언제|어디",
        r"알려줘|설명|뜻",
    ]

    CALLBACK_PREFIX = "td:"
    FORCE_REPLY_MARKER = "td:add"

    def get_schema(self) -> str:
        return """
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    slot TEXT NOT NULL DEFAULT 'default',
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_todos_chat_id ON todos(chat_id);
CREATE INDEX IF NOT EXISTS idx_todos_date ON todos(date);
CREATE INDEX IF NOT EXISTS idx_todos_chat_date ON todos(chat_id, date);
CREATE TRIGGER IF NOT EXISTS update_todos_timestamp
AFTER UPDATE ON todos
BEGIN
    UPDATE todos SET updated_at = datetime('now') WHERE id = NEW.id;
END;
"""

    def __init__(self):
        super().__init__()
        self._multi_selections: dict[int, set[int]] = {}
        self._yesterday_selections: dict[int, set[int]] = {}

    async def can_handle(self, message: str, chat_id: int) -> bool:
        """Check if message is todo-related."""
        msg = message.strip().lower()

        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return False

        for keyword in self.TRIGGER_KEYWORDS:
            if msg.startswith(keyword):
                return True

        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        """Handle message - show list."""
        logger.info(f"Todo plugin handling: '{message[:50]}' (chat_id={chat_id})")
        result = self._handle_list(chat_id)
        return PluginResult(
            handled=True,
            response=result["text"],
            reply_markup=result.get("reply_markup"),
        )

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        """Handle callback_data."""
        logger.info(f"Todo callback: {callback_data} (chat_id={chat_id})")

        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ Invalid request.", "edit": True}

        action = parts[1]
        handlers = {
            "list": lambda: self._handle_list(chat_id),
            "add": lambda: self._handle_add(chat_id),
            "item": lambda: self._handle_item_menu(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "done": lambda: self._handle_done(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "del": lambda: self._handle_delete(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "tomorrow": lambda: self._handle_tomorrow(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "back": lambda: self._handle_list(chat_id),
            "multi": lambda: self._handle_multi_select(chat_id),
            "multi_toggle": lambda: self._handle_multi_toggle(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "multi_done": lambda: self._handle_multi_done(chat_id),
            "multi_del": lambda: self._handle_multi_delete(chat_id),
            "multi_carry": lambda: self._handle_multi_carry(chat_id),
            "multi_clear": lambda: self._handle_multi_clear(chat_id),
            "date": lambda: self._handle_date_view(chat_id, parts[2] if len(parts) > 2 else None),
            "week": lambda: self._handle_week_view(chat_id, parts[2] if len(parts) > 2 else None),
            "yday": lambda: self._handle_yesterday(chat_id),
            "yday_toggle": lambda: self._handle_yesterday_toggle(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "yday_carry": lambda: self._handle_yesterday_carry(chat_id),
            "yday_all": lambda: self._handle_yesterday_all(chat_id),
        }

        handler = handlers.get(action)
        if handler:
            return handler()
        return {"text": "❌ Unknown command.", "edit": True}

    def get_scheduled_actions(self) -> list[ScheduledAction]:
        """List of schedulable actions."""
        return [
            ScheduledAction(name="yesterday_report", description="Yesterday's Report"),
            ScheduledAction(name="daily_wrap", description="Daily Wrap-up"),
        ]

    async def execute_scheduled_action(self, action_name: str, chat_id: int) -> str:
        """Execute scheduled action."""
        if action_name == "yesterday_report":
            return self._generate_yesterday_report(chat_id)
        if action_name == "daily_wrap":
            return self._generate_daily_wrap(chat_id)
        raise NotImplementedError(f"Action '{action_name}' not implemented")

    def _generate_yesterday_report(self, chat_id: int) -> str:
        """Generate yesterday's todo report text."""
        yesterday = (app_today() - timedelta(days=1)).isoformat()
        todos = self.repository.list_todos_by_date(chat_id, yesterday)
        if not todos:
            return ""

        lines = [f"📋 <b>Yesterday's Todos ({yesterday})</b>\n"]
        pending_count = 0
        for todo in todos:
            status = "✅" if todo.done else "⬜"
            lines.append(f"{status} {todo.text}")
            if not todo.done:
                pending_count += 1

        done_count = len(todos) - pending_count
        lines.append(f"\n📊 {done_count}/{len(todos)} completed")

        if pending_count > 0:
            lines.append(f"\nTap below to carry over {pending_count} incomplete items to today.")

        return "\n".join(lines)

    def _generate_daily_wrap(self, chat_id: int) -> str:
        """Generate daily wrap-up report text."""
        today = self._today()
        stats = self.repository.get_todo_stats(chat_id, today)
        if stats["total"] == 0:
            return ""

        lines = ["🌙 <b>Daily Wrap-up</b>\n"]

        if stats["pending"] == 0:
            lines.append("🎉 All todos completed today!")
        else:
            lines.append(f"📊 Today's progress: {stats['done']}/{stats['total']} completed\n")
            lines.append("<b>Incomplete:</b>")

            pending = self.repository.get_pending_todos(chat_id, today)
            for todo in pending:
                lines.append(f"  ⬜ {todo.text}")

            lines.append("\nAny items to move to tomorrow?")

        return "\n".join(lines)

    def _today(self) -> str:
        return app_today().isoformat()

    # ==================== List / Add ====================

    def _handle_list(self, chat_id: int) -> dict:
        """Show todo list."""
        today = self._today()
        todos = self.repository.list_todos_by_date(chat_id, today)

        lines = [f"📋 <b>Todos for {today}</b>\n"]
        buttons = []

        for idx, todo in enumerate(todos, 1):
            status = "✅" if todo.done else "⬜"
            lines.append(f"{status} {idx}. {todo.text}")

            if not todo.done:
                preview = todo.text[:20] + "..." if len(todo.text) > 20 else todo.text
                buttons.append([
                    InlineKeyboardButton(
                        f"{idx}. {preview}",
                        callback_data=f"td:item:{todo.id}"
                    )
                ])

        stats = self.repository.get_todo_stats(chat_id, today)
        if stats["total"] == 0:
            lines.append("\nNo todos yet.")
        else:
            lines.append(f"\n📊 {stats['done']}/{stats['total']} completed")

        if stats["pending"] > 0:
            buttons.append([
                InlineKeyboardButton("📋 Multi-select", callback_data="td:multi"),
            ])

        today = app_today()
        yesterday = (today - timedelta(days=1)).isoformat()
        tomorrow = (today + timedelta(days=1)).isoformat()
        buttons.append([
            InlineKeyboardButton("◀️ Prev", callback_data=f"td:date:{yesterday}"),
            InlineKeyboardButton("📅 Week", callback_data=f"td:week:{today}"),
            InlineKeyboardButton("Next ▶️", callback_data=f"td:date:{tomorrow}"),
        ])
        buttons.append([
            InlineKeyboardButton("➕ Add", callback_data="td:add"),
            InlineKeyboardButton("🔄 Refresh", callback_data="td:list"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_add(self, chat_id: int) -> dict:
        """Add todo ForceReply."""
        return {
            "text": "📝 <b>Add Todo</b>\n\nSeparate multiple items with line breaks.",
            "force_reply_prompt": "📝 Enter todos (one per line):",
            "force_reply": ForceReply(selective=True, input_field_placeholder="Enter todo..."),
            "edit": False,
        }

    def _handle_item_menu(self, chat_id: int, todo_id: int) -> dict:
        """Item detail menu."""
        todo = self.repository.get_todo(todo_id)
        if not todo:
            return {"text": "❌ Item not found.", "edit": True}

        keyboard = [
            [
                InlineKeyboardButton("✅ Done", callback_data=f"td:done:{todo_id}"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"td:del:{todo_id}"),
            ],
            [InlineKeyboardButton("📅 Tomorrow", callback_data=f"td:tomorrow:{todo_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="td:list")],
        ]

        return {
            "text": f"📌 Todo\n\n<b>{todo.text}</b>",
            "reply_markup": InlineKeyboardMarkup(keyboard),
            "edit": True,
        }

    def _handle_done(self, chat_id: int, todo_id: int) -> dict:
        """Mark as done."""
        if self.repository.mark_todo_done(todo_id):
            result = self._handle_list(chat_id)
            result["text"] = "✅ Marked as done!\n\n" + result["text"]
            return result
        return {"text": "❌ Failed", "edit": True}

    def _handle_delete(self, chat_id: int, todo_id: int) -> dict:
        """Delete item."""
        if self.repository.delete_todo(todo_id):
            result = self._handle_list(chat_id)
            result["text"] = "🗑️ Deleted!\n\n" + result["text"]
            return result
        return {"text": "❌ Delete failed", "edit": True}

    def _handle_tomorrow(self, chat_id: int, todo_id: int) -> dict:
        """Move to tomorrow."""
        tomorrow = (app_today() + timedelta(days=1)).isoformat()
        if self.repository.move_todos_to_date([todo_id], tomorrow):
            result = self._handle_list(chat_id)
            result["text"] = "📅 Moved to tomorrow!\n\n" + result["text"]
            return result
        return {"text": "❌ Move failed", "edit": True}

    # ==================== Multi-select ====================

    def _handle_multi_select(self, chat_id: int) -> dict:
        """Multi-select mode."""
        self._multi_selections[chat_id] = set()
        return self._render_multi_view(chat_id)

    def _render_multi_view(self, chat_id: int) -> dict:
        """Multi-select view."""
        today = self._today()
        pending = self.repository.get_pending_todos(chat_id, today)
        selections = self._multi_selections.get(chat_id, set())

        if not pending:
            return {
                "text": "✅ No incomplete todos!",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Back", callback_data="td:list")
                ]]),
                "edit": True,
            }

        lines = ["📋 <b>Multi-select</b>\n", "Tap items to select/deselect.\n"]
        buttons = []

        for todo in pending:
            selected = todo.id in selections
            mark = "☑️" if selected else "⬜"
            lines.append(f"{mark} {todo.text}")

            preview = todo.text[:18] + "..." if len(todo.text) > 18 else todo.text
            buttons.append([
                InlineKeyboardButton(
                    f"{'☑️' if selected else '⬜'} {preview}",
                    callback_data=f"td:multi_toggle:{todo.id}"
                )
            ])

        count = len(selections)
        lines.append(f"\n📌 {count} selected")

        if count > 0:
            buttons.append([
                InlineKeyboardButton(f"✅ Done({count})", callback_data="td:multi_done"),
                InlineKeyboardButton(f"🗑️ Delete({count})", callback_data="td:multi_del"),
                InlineKeyboardButton(f"📅 Tomorrow({count})", callback_data="td:multi_carry"),
            ])

        buttons.append([
            InlineKeyboardButton("🔄 Deselect all", callback_data="td:multi_clear"),
            InlineKeyboardButton("⬅️ Back", callback_data="td:list"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_multi_toggle(self, chat_id: int, todo_id: int) -> dict:
        """Toggle selection."""
        if chat_id not in self._multi_selections:
            self._multi_selections[chat_id] = set()

        if todo_id in self._multi_selections[chat_id]:
            self._multi_selections[chat_id].discard(todo_id)
        else:
            self._multi_selections[chat_id].add(todo_id)

        return self._render_multi_view(chat_id)

    def _handle_multi_done(self, chat_id: int) -> dict:
        """Mark selected as done."""
        selections = self._multi_selections.get(chat_id, set())
        count = 0
        for todo_id in selections:
            if self.repository.mark_todo_done(todo_id):
                count += 1

        self._multi_selections.pop(chat_id, None)
        result = self._handle_list(chat_id)
        result["text"] = f"✅ {count} marked as done!\n\n" + result["text"]
        return result

    def _handle_multi_delete(self, chat_id: int) -> dict:
        """Delete selected."""
        selections = self._multi_selections.get(chat_id, set())
        count = 0
        for todo_id in selections:
            if self.repository.delete_todo(todo_id):
                count += 1

        self._multi_selections.pop(chat_id, None)
        result = self._handle_list(chat_id)
        result["text"] = f"🗑️ {count} deleted!\n\n" + result["text"]
        return result

    def _handle_multi_carry(self, chat_id: int) -> dict:
        """Move selected to tomorrow."""
        selections = self._multi_selections.get(chat_id, set())
        tomorrow = (app_today() + timedelta(days=1)).isoformat()
        count = self.repository.move_todos_to_date(list(selections), tomorrow)

        self._multi_selections.pop(chat_id, None)
        result = self._handle_list(chat_id)
        result["text"] = f"📅 {count} moved to tomorrow!\n\n" + result["text"]
        return result

    def _handle_multi_clear(self, chat_id: int) -> dict:
        """Clear selection."""
        self._multi_selections.pop(chat_id, None)
        return self._render_multi_view(chat_id)

    # ==================== Yesterday carry-over ====================

    def _handle_yesterday(self, chat_id: int) -> dict:
        """Yesterday incomplete items multi-select."""
        self._yesterday_selections[chat_id] = set()
        return self._render_yesterday_view(chat_id)

    def _render_yesterday_view(self, chat_id: int) -> dict:
        """Yesterday incomplete items selection view."""
        yesterday = (app_today() - timedelta(days=1)).isoformat()
        pending = self.repository.get_pending_todos(chat_id, yesterday)
        selections = self._yesterday_selections.get(chat_id, set())

        if not pending:
            return {
                "text": "✅ No incomplete items from yesterday!",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("📄 Today", callback_data="td:list")
                ]]),
                "edit": True,
            }

        lines = [f"📋 <b>Incomplete from {yesterday}</b>\n",
                 "Select items to carry over to today.\n"]
        buttons = []

        for todo in pending:
            selected = todo.id in selections
            mark = "☑️" if selected else "⬜"
            lines.append(f"{mark} {todo.text}")

            preview = todo.text[:18] + "..." if len(todo.text) > 18 else todo.text
            buttons.append([
                InlineKeyboardButton(
                    f"{'☑️' if selected else '⬜'} {preview}",
                    callback_data=f"td:yday_toggle:{todo.id}"
                )
            ])

        count = len(selections)
        lines.append(f"\n📌 {count} selected")

        action_buttons = []
        if count > 0:
            action_buttons.append(
                InlineKeyboardButton(f"📅 Carry selected({count})", callback_data="td:yday_carry")
            )
        action_buttons.append(
            InlineKeyboardButton(f"📅 Carry all({len(pending)})", callback_data="td:yday_all")
        )
        buttons.append(action_buttons)

        buttons.append([
            InlineKeyboardButton("📄 Today", callback_data="td:list"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_yesterday_toggle(self, chat_id: int, todo_id: int) -> dict:
        """Toggle yesterday item selection."""
        if chat_id not in self._yesterday_selections:
            self._yesterday_selections[chat_id] = set()

        if todo_id in self._yesterday_selections[chat_id]:
            self._yesterday_selections[chat_id].discard(todo_id)
        else:
            self._yesterday_selections[chat_id].add(todo_id)

        return self._render_yesterday_view(chat_id)

    def _handle_yesterday_carry(self, chat_id: int) -> dict:
        """Carry selected yesterday items to today."""
        selections = self._yesterday_selections.get(chat_id, set())
        today = self._today()
        count = self.repository.move_todos_to_date(list(selections), today)

        self._yesterday_selections.pop(chat_id, None)
        result = self._handle_list(chat_id)
        result["text"] = f"📅 {count} carried over to today!\n\n" + result["text"]
        return result

    def _handle_yesterday_all(self, chat_id: int) -> dict:
        """Carry all yesterday incomplete items to today."""
        yesterday = (app_today() - timedelta(days=1)).isoformat()
        pending = self.repository.get_pending_todos(chat_id, yesterday)
        today = self._today()
        ids = [t.id for t in pending]
        count = self.repository.move_todos_to_date(ids, today)

        self._yesterday_selections.pop(chat_id, None)
        result = self._handle_list(chat_id)
        result["text"] = f"📅 {count} carried over to today!\n\n" + result["text"]
        return result

    # ==================== Date navigation ====================

    def _handle_date_view(self, chat_id: int, date_str: str | None) -> dict:
        """View specific date."""
        try:
            target = date.fromisoformat(date_str) if date_str else app_today()
        except ValueError:
            target = app_today()

        target_str = target.isoformat()
        todos = self.repository.list_todos_by_date(chat_id, target_str)
        is_today = target == app_today()
        date_label = "Today" if is_today else target.strftime("%m/%d")

        lines = [f"📋 <b>Todos for {target_str} ({date_label})</b>\n"]

        total, done_count = 0, 0
        for todo in todos:
            total += 1
            status = "✅" if todo.done else "⬜"
            if todo.done:
                done_count += 1
            lines.append(f"{status} {todo.text}")

        if total == 0:
            lines.append("\nNo todos yet.")
        else:
            lines.append(f"\n📊 {done_count}/{total} completed")

        prev_date = (target - timedelta(days=1)).isoformat()
        next_date = (target + timedelta(days=1)).isoformat()

        buttons = [
            [
                InlineKeyboardButton("◀️ Prev", callback_data=f"td:date:{prev_date}"),
                InlineKeyboardButton("📅 Today", callback_data="td:list"),
                InlineKeyboardButton("Next ▶️", callback_data=f"td:date:{next_date}"),
            ],
            [InlineKeyboardButton("📅 Week", callback_data=f"td:week:{target_str}")],
        ]

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_week_view(self, chat_id: int, date_str: str | None) -> dict:
        """Weekly view."""
        try:
            center = date.fromisoformat(date_str) if date_str else app_today()
        except ValueError:
            center = app_today()

        start = center - timedelta(days=3)
        end = center + timedelta(days=3)
        today = app_today()

        todos_by_date = self.repository.get_todos_by_date_range(
            chat_id, start.isoformat(), end.isoformat()
        )

        lines = [f"📅 <b>Weekly Todos</b> ({start.strftime('%m/%d')} ~ {end.strftime('%m/%d')})\n"]
        buttons = []
        row = []

        current = start
        while current <= end:
            d_str = current.isoformat()
            is_today = current == today
            day_mark = "👉 " if is_today else ""
            weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][current.weekday()]

            todos = todos_by_date.get(d_str, [])
            total = len(todos)
            done = sum(1 for t in todos if t.done)

            if total == 0:
                status = "—"
            elif done == total:
                status = f"✅ {done}/{total}"
            else:
                status = f"⬜ {done}/{total}"

            lines.append(f"{day_mark}<b>{current.strftime('%m/%d')}({weekday})</b>: {status}")

            label = f"{'📍' if is_today else ''}{current.day}({weekday})"
            row.append(InlineKeyboardButton(label, callback_data=f"td:date:{d_str}"))
            if len(row) == 4:
                buttons.append(row)
                row = []

            current += timedelta(days=1)

        if row:
            buttons.append(row)

        prev_week = (center - timedelta(days=7)).isoformat()
        next_week = (center + timedelta(days=7)).isoformat()
        buttons.append([
            InlineKeyboardButton("◀️ Prev week", callback_data=f"td:week:{prev_week}"),
            InlineKeyboardButton("📅 Today", callback_data="td:list"),
            InlineKeyboardButton("Next week ▶️", callback_data=f"td:week:{next_week}"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    # ==================== ForceReply handling ====================

    def handle_force_reply(self, message: str, chat_id: int) -> dict:
        """ForceReply response - add todos."""
        today = self._today()

        tasks = [t.strip() for t in message.split("\n") if t.strip()]
        if not tasks:
            return {"text": "❌ No todo entered.", "reply_markup": None}

        for task_text in tasks:
            self.repository.add_todo(chat_id, today, task_text)

        lines = [f"✅ {len(tasks)} added!\n"]
        for task in tasks:
            lines.append(f"• {task}")

        keyboard = [[
            InlineKeyboardButton("📄 View list", callback_data="td:list"),
            InlineKeyboardButton("➕ Add more", callback_data="td:add"),
        ]]

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(keyboard),
        }
