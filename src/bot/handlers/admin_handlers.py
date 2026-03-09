"""Admin command handlers."""

import html
import re
from datetime import datetime, timezone
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.ai import get_provider_label
from src.logging_config import logger, clear_context
from src.ui_emoji import BUTTON_BACK, BUTTON_REFRESH, BUTTON_SESSION_LIST
from ..constants import MAX_LOCK_STATUS_PREVIEW
from ..middleware import authorized_only, authenticated_only
from .base import BaseHandler


class AdminHandlers(BaseHandler):
    """Admin command handlers."""

    _TASK_HEADER_RE = re.compile(r"^\[(Claude|Codex)\b[^\]\n]*\|#\d+\]$")

    @staticmethod
    def _format_task_elapsed(elapsed_seconds: float) -> str:
        """Format elapsed seconds for the task dashboard."""
        total_seconds = max(0, int(elapsed_seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes or hours:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)

    def _summarize_task_preview(self, text: str, max_length: int) -> str:
        """Normalize multi-line task text into a compact one-line preview."""
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if lines and self._TASK_HEADER_RE.fullmatch(lines[0]):
            lines = lines[1:]

        normalized = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if not normalized:
            normalized = "(no message)"
        if len(normalized) > max_length:
            normalized = normalized[:max_length].rstrip() + "..."
        return html.escape(normalized)

    @authorized_only
    @authenticated_only
    async def tasks_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /tasks command - show active tasks with buttons."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        user_id = str(chat_id)
        logger.info("/tasks command received")

        text, keyboard = self._build_tasks_status(user_id)

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        logger.trace("/tasks complete")
        clear_context()

    @authorized_only
    @authenticated_only
    async def scheduler_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /scheduler command - manage schedules."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        user_id = str(chat_id)
        logger.info("/scheduler command received")

        if not self._schedule_manager:
            await update.message.reply_text("Schedule feature not initialized.")
            clear_context()
            return

        from src.scheduler_manager import scheduler_manager

        provider = self._get_selected_ai_provider(user_id)
        text = (
            f"<b>Scheduler</b>\n"
            f"Current AI: <b>{get_provider_label(provider)}</b>\n\n"
            f"{self._schedule_manager.get_status_text(user_id)}"
        )
        text += scheduler_manager.get_system_jobs_text()
        keyboard = self._build_scheduler_keyboard(user_id)

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        logger.trace("/scheduler complete")
        clear_context()

    @authorized_only
    async def chatid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /chatid command - show user's chat ID."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/chatid command received")

        user = update.effective_user
        logger.trace(f"effective_user={user}")

        user_info = ""
        if user:
            if user.username:
                user_info = f"\n- Username: @{user.username}"
            if user.first_name:
                user_info += f"\n- Name: {user.first_name}"

        logger.trace("Sending response")
        await update.message.reply_text(
            f"<b>My Info</b>\n\n"
            f"- Chat ID: <code>{chat_id}</code>{user_info}\n\n"
            f"Add this ID to <code>ALLOWED_CHAT_IDS</code>.",
            parse_mode="HTML"
        )
        logger.trace("/chatid complete")
        clear_context()

    @authorized_only
    @authenticated_only
    async def plugins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /plugins command - show plugin list."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/plugins command received")

        if not self.plugins or not self.plugins.plugins:
            logger.trace("No plugins loaded")
            await update.message.reply_text("No plugins loaded.")
            clear_context()
            return

        await update.message.reply_text(
            self._build_plugins_text(),
            reply_markup=self._build_plugins_markup(),
            parse_mode="HTML",
        )
        logger.trace("/plugins complete")
        clear_context()

    @authorized_only
    @authenticated_only
    async def reload_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /reload command - hot reload plugins."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/reload command received")

        if not self.plugins:
            await update.message.reply_text("No plugin loader available.")
            clear_context()
            return

        if context.args:
            # 특정 플러그인 리로드: /reload memo
            plugin_name = context.args[0]
            success = self.plugins.reload_plugin(plugin_name)
            if success:
                await update.message.reply_text(
                    f"Plugin <code>{plugin_name}</code> reloaded.",
                    parse_mode="HTML"
                )
            else:
                await update.message.reply_text(
                    f"Failed to reload <code>{plugin_name}</code>.",
                    parse_mode="HTML"
                )
        else:
            # 전체 리로드: /reload
            success, failed = self.plugins.reload_all()
            lines = ["<b>Plugin Reload</b>\n"]
            if success:
                lines.append(f"Reloaded: {', '.join(success)}")
            if failed:
                lines.append(f"Failed: {', '.join(failed)}")
            if not success and not failed:
                lines.append("No plugins to reload.")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")

        clear_context()

    @authorized_only
    @authenticated_only
    async def plugin_help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /plugin_name command - redirect to the canonical help topic."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        if not self.plugins:
            logger.trace("No plugin loader")
            clear_context()
            return

        text = update.message.text.strip()
        if not text.startswith("/"):
            clear_context()
            return
        plugin_name = text[1:].split()[0]
        logger.info(f"Plugin help request: /{plugin_name}")

        plugin = self.plugins.get_plugin_by_name(plugin_name)
        if plugin:
            logger.trace(f"Plugin found: {plugin.name}")
            await update.message.reply_text(
                f"Use <code>/help_{plugin.name}</code> for docs.\n"
                f"Open <code>/plugins</code> or <code>/menu</code> for the interactive UI.",
                parse_mode="HTML",
            )
        else:
            logger.trace(f"Plugin not found: {plugin_name}")

        clear_context()

    @authorized_only
    async def auth_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /auth command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/auth command received")

        user_id = str(chat_id)

        if not context.args:
            logger.trace("/auth no args")
            await update.message.reply_text("Usage: /auth <secret_key>")
            clear_context()
            return

        key = context.args[0]
        logger.trace(f"Auth attempt - key_length={len(key)}")

        if self.auth.authenticate(user_id, key):
            logger.info("Auth success")
            await update.message.reply_text(f"✅ Authenticated! Valid for {self.auth.timeout_minutes} minutes.")
        else:
            logger.warning("Auth failed - wrong key")
            await update.message.reply_text("❌ Authentication failed. Wrong key.")

        clear_context()

    @authorized_only
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/status command received")

        user_id = str(chat_id)

        if self.auth.is_authenticated(user_id):
            remaining = self.auth.get_remaining_minutes(user_id)
            logger.trace(f"Authenticated - remaining={remaining}m")
            await update.message.reply_text(f"✅ Authenticated ({remaining}m remaining)")
        else:
            logger.trace("Auth required")
            await update.message.reply_text("🔒 Authentication required.\nUse /auth <key> to authenticate.")

        clear_context()

    def _group_plugins(self) -> dict[str, list]:
        """Return plugins grouped by builtin/custom for launcher UIs."""
        grouped_plugins = {"builtin": [], "custom": []}
        if not self.plugins or not self.plugins.plugins:
            return grouped_plugins

        for plugin in sorted(
            self.plugins.plugins,
            key=lambda item: (self._get_plugin_source_group(item), item.name),
        ):
            grouped_plugins[self._get_plugin_source_group(plugin)].append(plugin)
            logger.trace(f"Plugin: {plugin.name} - {plugin.description}")
        return grouped_plugins

    def _build_plugins_text(self) -> str:
        """Render a compact plugin hub summary."""
        if not self.plugins or not self.plugins.plugins:
            return "No plugins loaded."

        logger.trace(f"Building plugin list - {len(self.plugins.plugins)}")
        grouped_plugins = self._group_plugins()

        lines = ["<b>Plugins</b>\n"]
        for group_key, title in (("builtin", "Builtin"), ("custom", "Custom")):
            plugins = grouped_plugins[group_key]
            if plugins:
                command_list = " ".join(f"<code>/{plugin.name}</code>" for plugin in plugins)
            else:
                command_list = "-"
            lines.append(f"<b>{title}</b>: {command_list}")

        lines.extend([
            "",
            "Tap a plugin button below.",
            "Docs: /help_extend",
        ])
        return "\n".join(lines)

    def _build_plugins_markup(self, *, launcher_context: Optional[str] = None) -> InlineKeyboardMarkup:
        """Return dynamic plugin buttons for `/plugins` and `/menu`."""
        buttons: list[list[InlineKeyboardButton]] = []
        grouped_plugins = self._group_plugins()

        for group_key in ("builtin", "custom"):
            row: list[InlineKeyboardButton] = []
            for plugin in grouped_plugins[group_key]:
                row.append(
                    InlineKeyboardButton(
                        f"/{plugin.name}",
                        callback_data=f"plug:open:{plugin.name}:{launcher_context or 'standalone'}",
                    )
                )
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

        if launcher_context == "menu":
            buttons.append([InlineKeyboardButton(BUTTON_BACK, callback_data="menu:open")])

        return InlineKeyboardMarkup(buttons)

    def _build_tasks_status(self, user_id: str) -> tuple[str, list]:
        """Build lock status text and buttons (including queue)."""
        lines = []
        repo = self._repository
        processing_rows = repo.list_processing_messages_by_user(user_id) if repo else []
        active_rows = []

        for row in processing_rows:
            if self._get_live_session_lock(row["session_id"]):
                active_rows.append(row)

        waiting_rows = repo.list_queued_messages_by_user(user_id) if repo else []
        total_waiting = len(waiting_rows)

        if not active_rows and total_waiting == 0:
            lines.append(f"<b>No active tasks</b>")
        elif active_rows:
            lines.append(f"<b>Processing</b> ({len(active_rows)})")

            for i, row in enumerate(active_rows, 1):
                started_at = datetime.fromisoformat(row["request_at"])
                elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
                elapsed_str = self._format_task_elapsed(elapsed)

                session_name = row.get("session_name") or row["session_id"][:8]
                msg_preview = self._summarize_task_preview(row.get("request", ""), MAX_LOCK_STATUS_PREVIEW)
                session_name = html.escape(session_name)

                lines.append(
                    f"\n\n<b>{i}.</b> <b>{session_name}</b>\n"
                    f"<i>{elapsed_str} elapsed</i>\n"
                    f"{msg_preview}"
                )

        waiting_details = []

        for i, queued in enumerate(waiting_rows[:8], 1):
            session_name = queued.get("session_name") or queued["session_id"][:8]
            msg_preview = self._summarize_task_preview(queued.get("message", ""), 30)
            session_name = html.escape(session_name)
            waiting_details.append(
                f"\n\n<b>{i}.</b> <b>{session_name}</b>\n"
                f"{msg_preview}"
            )

        if total_waiting > 0:
            lines.append(f"\n\n<b>Queue</b> ({total_waiting})")
            lines.extend(waiting_details)
            if total_waiting > len(waiting_details):
                lines.append(f"\n  ... and {total_waiting - len(waiting_details)} more")
        text = "".join(lines) if lines else "No status info"

        keyboard = [[
            InlineKeyboardButton(BUTTON_REFRESH, callback_data="tasks:refresh"),
            InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="sess:list"),
        ]]

        return text, keyboard
