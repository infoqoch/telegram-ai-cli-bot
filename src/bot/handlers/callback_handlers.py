"""Callback query handlers."""
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from src.ai import (
    get_default_model,
    get_profile_label,
    get_profile_short_label,
    get_provider_label,
    get_provider_profiles,
    is_supported_model,
)
from src.logging_config import logger, clear_context
from src.constants import AVAILABLE_HOURS
from ..constants import get_model_emoji, get_model_badge
from ..formatters import truncate_message
from .base import BaseHandler


class CallbackHandlers(BaseHandler):
    """Callback query handlers."""

    async def callback_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline button callbacks."""
        query = update.callback_query
        if not query:
            return

        chat_id = query.message.chat_id if query.message else None
        if not chat_id:
            return

        self._setup_request_context(chat_id)
        callback_data = query.data
        logger.info(f"Callback query: {callback_data} (chat_id={chat_id})")

        if not self._is_authorized(chat_id):
            logger.debug("Callback denied - unauthorized")
            await query.answer("⛔ Access denied.", show_alert=True)
            clear_context()
            return

        if not self._is_authenticated(str(chat_id)):
            logger.debug("Callback denied - auth required")
            await query.answer("🔒 Authentication required.\n/auth <key>", show_alert=True)
            clear_context()
            return

        await query.answer()

        # Plugin auto-routing (CALLBACK_PREFIX 기반)
        if self.plugins:
            plugin = self.plugins.get_plugin_for_callback(callback_data)
            if plugin:
                await self._handle_plugin_callback(query, chat_id, callback_data, plugin)
                return

        if callback_data.startswith("ai:"):
            await self._handle_ai_callback(query, chat_id, callback_data)
            return

        # Session callback
        if callback_data.startswith("sess:"):
            await self._handle_session_callback(query, chat_id, callback_data)
            return

        # Tasks callback
        if callback_data.startswith("tasks:"):
            await self._handle_tasks_callback(query, chat_id)
            return

        # Scheduler callback
        if callback_data.startswith("sched:"):
            await self._handle_scheduler_callback(query, chat_id, callback_data)
            return

        # Workspace callback
        if callback_data.startswith("ws:"):
            await self._handle_workspace_callback(query, chat_id, callback_data)
            return

        # Session queue callback (new method)
        if callback_data.startswith("sq:"):
            await self._handle_session_queue_callback(query, chat_id, callback_data)
            return

        logger.warning(f"Unknown callback: {callback_data}")

    async def _handle_todo_force_reply(self, update: Update, chat_id: int, message: str) -> None:
        """Handle Todo ForceReply response."""
        logger.info(f"Todo ForceReply processing: msg={message[:50]}")

        todo_plugin = None
        if self.plugins:
            todo_plugin = self.plugins.get_plugin_by_name("todo")

        if not todo_plugin or not hasattr(todo_plugin, 'handle_force_reply'):
            await update.message.reply_text("Todo plugin not found.")
            return

        result = todo_plugin.handle_force_reply(message, chat_id)

        await update.message.reply_text(
            text=result.get("text", ""),
            reply_markup=result.get("reply_markup"),
            parse_mode="HTML"
        )

    async def _handle_new_session_force_reply(self, update: Update, chat_id: int, name: str, model: str) -> None:
        """Handle session creation ForceReply response."""
        logger.info(f"Session creation ForceReply processing: model={model}, name={name}")

        user_id = str(chat_id)
        provider = self._get_selected_ai_provider(user_id)
        model_name = model if is_supported_model(provider, model) else get_default_model(provider)

        session_name = name.strip()[:50] if name.strip() else ""

        session_id = self.sessions.create_session(
            user_id=user_id,
            ai_provider=provider,
            model=model_name,
            name=session_name,
            first_message="(new session)",
        )
        short_id = session_id[:8]

        model_emoji = get_model_emoji(model_name)
        name_line = f"\n<b>Name:</b> {session_name}" if session_name else ""

        keyboard = [[
            InlineKeyboardButton("Session List", callback_data="sess:list"),
        ]]

        await update.message.reply_text(
            text=f"New session created!\n\n"
                 f"<b>AI:</b> {get_provider_label(provider)}\n"
                 f"{model_emoji} <b>Model:</b> {get_profile_label(provider, model_name)}\n"
                 f"<b>ID:</b> <code>{short_id}</code>{name_line}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_rename_force_reply(self, update: Update, chat_id: int, new_name: str, session_id: str) -> None:
        """Handle session rename ForceReply response."""
        logger.info(f"Rename ForceReply processing: session={session_id[:8]}, name={new_name}")

        new_name = new_name.strip()
        if not new_name:
            await update.message.reply_text("❌ Name cannot be empty.")
            return

        if len(new_name) > 50:
            await update.message.reply_text("❌ Name too long. (max 50 chars)")
            return

        if self.sessions.rename_session(session_id, new_name):
            logger.info(f"Session renamed: {session_id[:8]} -> {new_name}")
            await update.message.reply_text(
                f"✅ Session renamed!\n\n"
                f"- Session: <code>{session_id[:8]}</code>\n"
                f"- Name: {new_name}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ Rename failed.")

    async def _handle_memo_force_reply(self, update: Update, chat_id: int, message: str) -> None:
        """Handle memo add ForceReply response."""
        logger.info(f"Memo ForceReply processing: msg={message[:50]}")

        memo_plugin = None
        if self.plugins:
            memo_plugin = self.plugins.get_plugin_by_name("memo")

        if not memo_plugin or not hasattr(memo_plugin, 'handle_force_reply'):
            await update.message.reply_text("Memo plugin not found.")
            return

        result = memo_plugin.handle_force_reply(message, chat_id)

        await update.message.reply_text(
            text=result.get("text", ""),
            reply_markup=result.get("reply_markup"),
            parse_mode="HTML"
        )

    async def _handle_schedule_force_reply(self, update: Update, chat_id: int, message: str) -> None:
        """Handle schedule message input ForceReply response."""
        user_id = str(chat_id)
        pending = self._sched_pending.get(user_id)

        if not pending:
            await update.message.reply_text("Schedule input expired. Please try again.")
            return

        if not self._schedule_manager:
            await update.message.reply_text("Schedule feature disabled.")
            del self._sched_pending[user_id]
            return

        schedule_type = pending.get("type", "claude")
        workspace_path = pending.get("workspace_path") if schedule_type == "workspace" else None
        name = pending.get("name", "Schedule")
        model = pending.get("model", "sonnet")
        ai_provider = pending.get("ai_provider", self._get_selected_ai_provider(user_id))

        if schedule_type == "claude" and name == "Schedule":
            name = message[:15].strip() + ("..." if len(message) > 15 else "")

        schedule = self._schedule_manager.add(
            user_id=user_id,
            chat_id=chat_id,
            name=name,
            hour=pending["hour"],
            minute=pending.get("minute", 0),
            message=message,
            schedule_type=schedule_type,
            ai_provider=ai_provider,
            model=model,
            workspace_path=workspace_path,
        )

        del self._sched_pending[user_id]

        keyboard = [[
            InlineKeyboardButton("Schedule List", callback_data="sched:refresh"),
        ]]

        type_label = "workspace" if schedule_type == "workspace" else "schedule"
        path_info = f"\nPath: <code>{workspace_path}</code>" if workspace_path else ""

        await update.message.reply_text(
            f"<b>Schedule Registered!</b>\n\n"
            f"{schedule.type_emoji} <b>{schedule.name}</b> ({type_label})\n"
            f"Time: <b>{schedule.time_str}</b> (daily)\n"
            f"AI: <b>{get_provider_label(ai_provider)}</b>\n"
            f"Model: <b>{get_profile_label(ai_provider, model)}</b>{path_info}\n"
            f"Message: <i>{message[:50]}{'...' if len(message) > 50 else ''}</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

        logger.info(f"Schedule registered: {schedule.name} @ {schedule.time_str} (type={schedule_type})")

    async def _handle_plugin_callback(self, query, chat_id: int, callback_data: str, plugin) -> None:
        """Handle plugin callback with auto-routing."""
        try:
            result = await plugin.handle_callback_async(callback_data, chat_id)

            # ForceReply 처리
            if result.get("force_reply"):
                await query.edit_message_text(
                    text=result.get("text", "Enter input"),
                    parse_mode="HTML"
                )
                marker_text = result.get("force_reply_marker", plugin.FORCE_REPLY_MARKER or f"{plugin.name}_add")
                await query.message.reply_text(
                    text=marker_text,
                    reply_markup=result["force_reply"],
                    parse_mode="HTML"
                )
                return

            # 메시지 편집/전송
            if result.get("edit", True) and query.message:
                await query.edit_message_text(
                    text=result.get("text", ""),
                    reply_markup=result.get("reply_markup"),
                    parse_mode="HTML"
                )
            else:
                await query.message.reply_text(
                    text=result.get("text", ""),
                    reply_markup=result.get("reply_markup"),
                    parse_mode="HTML"
                )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                logger.warning(f"{plugin.name} callback BadRequest: {e}")
        except Exception as e:
            logger.exception(f"{plugin.name} callback error: {e}")
            try:
                await query.edit_message_text(
                    text=f"Error occurred.\n\n<code>{str(e)}</code>",
                    parse_mode="HTML"
                )
            except:
                pass

    async def _handle_ai_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle provider selection callbacks."""
        user_id = str(chat_id)
        parts = callback_data.split(":")
        action = parts[1] if len(parts) > 1 else ""

        if action == "cancel":
            await query.edit_message_text("Provider selection cancelled.")
            return

        if action == "open":
            provider = self._get_selected_ai_provider(user_id)
            keyboard = self._build_ai_selector_keyboard(provider)
            keyboard.append([
                InlineKeyboardButton("📋 Session List", callback_data="sess:list"),
                InlineKeyboardButton("🆕 New Session", callback_data="sess:new"),
            ])
            await query.edit_message_text(
                f"<b>Select AI</b>\n\n"
                f"Current AI: <b>{get_provider_label(provider)}</b>\n\n"
                f"Choose which provider `/new`, `/sl`, `/session`, `/model`, `/ai`, and normal chat should use.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            return

        if action == "select" and len(parts) > 2:
            provider = parts[2]
            self._set_selected_ai_provider(user_id, provider)
            current_session_id = self.sessions.get_current_session_id(user_id, provider)
            current_line = (
                f"Current session: <code>{current_session_id[:8]}</code>"
                if current_session_id else
                "Current session: none"
            )
            keyboard = [
                [InlineKeyboardButton("📋 Session List", callback_data="sess:list")],
                [InlineKeyboardButton("🆕 New Session", callback_data="sess:new")],
            ]
            await query.edit_message_text(
                f"✅ Current AI switched to <b>{get_provider_label(provider)}</b>.\n\n"
                f"{current_line}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            return

        await query.edit_message_text("Unknown AI selection request.")

    async def _handle_session_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle session callbacks."""
        try:
            parts = callback_data.split(":")
            if len(parts) < 2:
                await query.edit_message_text("Invalid request")
                return

            action = parts[1]
            user_id = str(chat_id)
            selected_provider = self._get_selected_ai_provider(user_id)

            if action == "new":
                model = parts[2] if len(parts) > 2 else get_default_model(selected_provider)
                await self._handle_new_session_name_prompt(query, chat_id, model)

            elif action == "new_confirm":
                model = parts[2] if len(parts) > 2 else get_default_model(selected_provider)
                await self._handle_new_session_callback(query, chat_id, model, "")

            elif action == "switch":
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_switch_session_callback(query, chat_id, session_id)

            elif action == "delete":
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_delete_session_confirm(query, chat_id, session_id)

            elif action == "confirm_del":
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_delete_session_execute(query, chat_id, session_id)

            elif action == "history":
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_history_callback(query, chat_id, session_id)

            elif action == "list":
                await self._handle_session_list_callback(query, chat_id)

            elif action == "rename":
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_rename_prompt_callback(query, chat_id, session_id)

            elif action == "model":
                model = parts[2] if len(parts) > 2 else "sonnet"
                session_id = parts[3] if len(parts) > 3 else ""
                await self._handle_model_change_callback(query, chat_id, model, session_id)

            elif action == "cancel":
                await self._handle_session_list_callback(query, chat_id)

            else:
                await query.edit_message_text("Unknown command")

        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                logger.warning(f"Session callback BadRequest: {e}")
        except Exception as e:
            logger.exception(f"Session callback error: {e}")
            try:
                await query.edit_message_text(
                    text=f"Error occurred.\n\n<code>{str(e)}</code>",
                    parse_mode="HTML"
                )
            except:
                pass

    async def _handle_new_session_name_prompt(self, query, chat_id: int, model: str) -> None:
        """Prompt for new session name."""
        provider = self._get_selected_ai_provider(str(chat_id))
        normalized_model = model if is_supported_model(provider, model) else get_default_model(provider)
        model_emoji = get_model_emoji(normalized_model)

        await query.edit_message_text(
            text=f"{model_emoji} <b>{get_profile_label(provider, normalized_model)}</b> session creation\n\n"
                 f"Current AI: <b>{get_provider_label(provider)}</b>\n\n"
                 f"Enter session name:",
            parse_mode="HTML"
        )

        await query.message.reply_text(
            text=f"Enter session name (sess_name:{normalized_model})",
            reply_markup=ForceReply(selective=True, input_field_placeholder="Session name...")
        )

    async def _handle_rename_prompt_callback(self, query, chat_id: int, session_id: str) -> None:
        """Handle rename button - prompt for new name via ForceReply."""
        session_name = self.sessions.get_session_name(session_id) or "(unnamed)"

        await query.edit_message_text(
            text=f"✏️ <b>Rename Session</b>\n\n"
            f"- Current: {session_name}\n"
            f"- ID: <code>{session_id[:8]}</code>\n\n"
            f"Enter new name below:",
            parse_mode="HTML"
        )

        await query.message.reply_text(
            text=f"Enter new name (sess_rename:{session_id})",
            reply_markup=ForceReply(selective=True, input_field_placeholder="New session name...")
        )

    async def _handle_new_session_callback(self, query, chat_id: int, model: str, name: str = "") -> None:
        """Handle new session creation callback."""
        user_id = str(chat_id)
        provider = self._get_selected_ai_provider(user_id)
        model_name = model if is_supported_model(provider, model) else get_default_model(provider)

        session_id = self.sessions.create_session(
            user_id=user_id,
            ai_provider=provider,
            model=model_name,
            name=name,
            first_message="(new session)",
        )
        short_id = session_id[:8]

        model_emoji = get_model_emoji(model_name)

        keyboard = [
            [
                InlineKeyboardButton("Session List", callback_data="sess:list"),
            ]
        ]

        await query.edit_message_text(
            text=f"New session created!\n\n"
                 f"<b>AI:</b> {get_provider_label(provider)}\n"
                 f"{model_emoji} <b>Model:</b> {get_profile_label(provider, model_name)}\n"
                 f"<b>ID:</b> <code>{short_id}</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_switch_session_callback(self, query, chat_id: int, session_id: str) -> None:
        """Handle session switch callback - shows full session info like /session."""
        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ Session not found.")
            return

        full_session_id = session.get("full_session_id", session_id)
        self.sessions.switch_session(user_id, full_session_id)
        short_id = full_session_id[:8]
        session_name = session.get("name") or ""
        model = session.get("model", "sonnet")
        provider = session.get("ai_provider", self._get_selected_ai_provider(user_id))
        model_emoji = get_model_emoji(model)

        history_entries = self.sessions.get_session_history_entries(full_session_id)
        count = len(history_entries)

        recent = history_entries[-10:]
        history_lines = []
        start_idx = len(history_entries) - len(recent) + 1

        for i, entry in enumerate(recent, start=start_idx):
            msg = entry.get("message", "") if isinstance(entry, dict) else str(entry)
            processor = entry.get("processor", "claude") if isinstance(entry, dict) else "claude"
            emoji = "[plugin]" if processor.startswith("plugin:") else {"command": "[cmd]", "rejected": "[x]"}.get(processor, "")
            short_q = truncate_message(msg, 35)
            history_lines.append(f"{i}. {emoji} {short_q}")

        history_text = "\n".join(history_lines) if history_lines else "(empty)"
        name_line = f"- Name: {session_name}\n" if session_name else ""

        model_buttons = [
            InlineKeyboardButton(profile.button_label, callback_data=f"sess:model:{profile.key}:{full_session_id}")
            for profile in get_provider_profiles(provider)
        ]
        keyboard = [
            model_buttons,
            [
                InlineKeyboardButton("✏️ Rename", callback_data=f"sess:rename:{full_session_id}"),
                InlineKeyboardButton("📜 History", callback_data=f"sess:history:{full_session_id}"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"sess:delete:{full_session_id}"),
            ],
            [
                InlineKeyboardButton("📋 Session List", callback_data="sess:list"),
                InlineKeyboardButton("Switch AI", callback_data="ai:open"),
            ]
        ]

        await query.edit_message_text(
            text=f"✅ <b>Session switched!</b>\n\n"
                 f"- AI: {get_provider_label(provider)}\n"
                 f"- ID: <code>{short_id}</code>\n"
                 f"{name_line}"
                 f"- Model: {model_emoji} {get_profile_label(provider, model)}\n"
                 f"- Messages: {count}\n\n"
                 f"<b>History</b> (last 10)\n{history_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_delete_session_confirm(self, query, chat_id: int, session_id: str) -> None:
        """Handle session delete confirmation."""
        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ Session not found.")
            return

        full_session_id = session.get("full_session_id", session_id)
        short_id = full_session_id[:8]
        name = session.get("name") or f"Session {short_id}"

        current_session_id = self.sessions.get_current_session_id(user_id)
        if current_session_id == full_session_id:
            keyboard = [[InlineKeyboardButton("Back", callback_data="sess:list")]]
            await query.edit_message_text(
                text=f"<b>Cannot Delete</b>\n\n"
                     f"<b>{name}</b> is currently in use.\n\n"
                     f"Switch to another session before deleting.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        keyboard = [
            [
                InlineKeyboardButton("Delete", callback_data=f"sess:confirm_del:{full_session_id}"),
                InlineKeyboardButton("Cancel", callback_data="sess:cancel"),
            ]
        ]

        await query.edit_message_text(
            text=f"<b>Delete Session Confirmation</b>\n\n"
                 f"<b>{name}</b>\n"
                 f"ID: <code>{short_id}</code>\n\n"
                 f"Are you sure?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_delete_session_execute(self, query, chat_id: int, session_id: str) -> None:
        """Execute session deletion."""
        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ Session not found.")
            return

        full_session_id = session.get("full_session_id", session_id)
        short_id = full_session_id[:8]
        name = session.get("name") or f"Session {short_id}"

        current_session_id = self.sessions.get_current_session_id(user_id)
        if current_session_id == full_session_id:
            keyboard = [[InlineKeyboardButton("Back", callback_data="sess:list")]]
            await query.edit_message_text(
                text=f"<b>Cannot Delete</b>\n\n"
                     f"<b>{name}</b> is currently in use.\n\n"
                     f"Switch to another session before deleting.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        self.sessions.delete_session(user_id, full_session_id)

        await self._handle_session_list_callback(query, chat_id, f"<s>{name}</s> deleted!\n\n")

    async def _handle_history_callback(self, query, chat_id: int, session_id: str) -> None:
        """Handle session history callback."""
        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ Session not found.")
            return

        full_session_id = session.get("full_session_id", session_id)
        short_id = full_session_id[:8]
        name = session.get("name") or f"Session {short_id}"
        history = self.sessions.get_session_history_entries(full_session_id)

        lines = [f"<b>{name}</b> History\n"]

        if not history:
            lines.append("(no history)")
        else:
            for i, entry in enumerate(history[-10:], 1):
                msg = entry.get("message", "")[:50] if isinstance(entry, dict) else str(entry)[:50]
                if len(entry.get("message", "") if isinstance(entry, dict) else str(entry)) > 50:
                    msg += "..."
                lines.append(f"{i}. {msg}")

        keyboard = [
            [
                InlineKeyboardButton("Switch", callback_data=f"sess:switch:{full_session_id}"),
                InlineKeyboardButton("List", callback_data="sess:list"),
            ]
        ]

        await query.edit_message_text(
            text="\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_session_list_callback(self, query, chat_id: int, prefix: str = "") -> None:
        """Handle session list callback."""
        user_id = str(chat_id)
        provider = self._get_selected_ai_provider(user_id)
        provider_label = get_provider_label(provider)
        sessions = self.sessions.list_sessions(user_id, ai_provider=provider)
        current_session_id = self.sessions.get_current_session_id(user_id, provider)

        timestamp = datetime.now().strftime("%H:%M:%S")
        lines = [f"{prefix}<b>Session List - {provider_label}</b> <i>({timestamp})</i>\n"]
        buttons = []

        if not sessions:
            lines.append("No sessions.")
        else:
            for session in sessions[:10]:
                sid = session["full_session_id"]
                short_id = session["session_id"]
                name = session.get("name") or f"Session {short_id}"
                model = session.get("model", "sonnet")
                model_badge = get_model_badge(model)
                model_label = get_profile_short_label(provider, model)

                is_current = "> " if sid == current_session_id else ""
                is_locked = self._is_session_locked(sid)
                lock_indicator = " 🔒" if is_locked else ""
                lines.append(
                    f"{is_current}{model_badge} <b>{name}</b> "
                    f"({model_label}, <code>{short_id}</code>){lock_indicator}"
                )

                buttons.append([
                    InlineKeyboardButton(f"{name[:10]}", callback_data=f"sess:switch:{sid}"),
                    InlineKeyboardButton("History", callback_data=f"sess:history:{sid}"),
                    InlineKeyboardButton("Del", callback_data=f"sess:delete:{sid}"),
                ])

        buttons.append(self._build_model_buttons(provider, "sess:new:"))
        buttons.append([
            InlineKeyboardButton("Refresh", callback_data="sess:list"),
            InlineKeyboardButton("Tasks", callback_data="tasks:refresh"),
        ])
        buttons.append([
            InlineKeyboardButton("Switch AI", callback_data="ai:open"),
        ])

        await query.edit_message_text(
            text="\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )

    async def _handle_model_change_callback(self, query, chat_id: int, model: str, session_id: str) -> None:
        """Handle model change callback."""
        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ Session not found.")
            return

        full_session_id = session.get("full_session_id", session_id)
        provider = session.get("ai_provider", self._get_selected_ai_provider(user_id))
        if not is_supported_model(provider, model):
            await query.edit_message_text("❌ Unsupported model for this AI.")
            return

        self.sessions.update_session_model(full_session_id, model)

        short_id = full_session_id[:8]
        name = session.get("name") or f"Session {short_id}"
        model_emoji = get_model_emoji(model)

        model_buttons = [
            InlineKeyboardButton(profile.button_label, callback_data=f"sess:model:{profile.key}:{full_session_id}")
            for profile in get_provider_profiles(provider)
        ]
        keyboard = [
            model_buttons,
            [
                InlineKeyboardButton("Session List", callback_data="sess:list"),
            ]
        ]

        await query.edit_message_text(
            text=f"Model changed!\n\n"
                 f"<b>{name}</b>\n"
                 f"AI: <b>{get_provider_label(provider)}</b>\n"
                 f"{model_emoji} Model: <b>{get_profile_label(provider, model)}</b>\n"
                 f"ID: <code>{short_id}</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_tasks_callback(self, query, chat_id: int) -> None:
        """Handle task status callback - same as /tasks."""
        user_id = str(chat_id)
        text, keyboard = self._build_tasks_status(user_id)

        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_scheduler_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle scheduler callbacks."""
        user_id = str(chat_id)
        action = callback_data[6:]  # Remove "sched:"

        if not self._schedule_manager:
            await query.answer("Schedule feature disabled")
            return

        # Refresh
        if action == "refresh":
            from src.scheduler_manager import scheduler_manager

            provider = self._get_selected_ai_provider(user_id)
            text = (
                f"<b>Scheduler</b>\n"
                f"Current AI: <b>{get_provider_label(provider)}</b>\n\n"
                f"{self._schedule_manager.get_status_text(user_id)}"
            )
            text += scheduler_manager.get_system_jobs_text()
            keyboard = self._build_scheduler_keyboard(user_id)
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            await query.answer("Refreshed")
            return

        # Toggle
        if action.startswith("toggle:"):
            from src.scheduler_manager import scheduler_manager

            schedule_id = action[7:]
            new_state = self._schedule_manager.toggle(schedule_id)
            if new_state is not None:
                status = "ON" if new_state else "OFF"
                await query.answer(f"{status}")
                # Return to detail view
                await self._handle_scheduler_callback(query, chat_id, f"sched:detail:{schedule_id}")
            else:
                await query.answer("Schedule not found")
            return

        # Delete
        if action.startswith("delete:"):
            from src.scheduler_manager import scheduler_manager

            schedule_id = action[7:]
            if self._schedule_manager.remove(schedule_id):
                await query.answer("Deleted")
                provider = self._get_selected_ai_provider(user_id)
                text = (
                    f"<b>Scheduler</b>\n"
                    f"Current AI: <b>{get_provider_label(provider)}</b>\n\n"
                    f"{self._schedule_manager.get_status_text(user_id)}"
                )
                text += scheduler_manager.get_system_jobs_text()
                keyboard = self._build_scheduler_keyboard(user_id)
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
            else:
                await query.answer("Delete failed")
            return

        # Schedule detail view
        if action.startswith("detail:"):
            schedule_id = action[7:]
            schedule = self._schedule_manager.get(schedule_id)
            if not schedule:
                await query.answer("Schedule not found")
                return

            status_text = "ON" if schedule.enabled else "OFF"
            toggle_label = "⏸ OFF" if schedule.enabled else "✅ ON"
            path_info = f"\nPath: <code>{schedule.workspace_path}</code>" if schedule.workspace_path else ""

            buttons = [
                [InlineKeyboardButton(toggle_label, callback_data=f"sched:toggle:{schedule_id}")],
                [InlineKeyboardButton(f"⏰ Change Time ({schedule.time_str})", callback_data=f"sched:chtime:{schedule_id}")],
                [InlineKeyboardButton("🗑 Delete", callback_data=f"sched:delete:{schedule_id}")],
                [InlineKeyboardButton("← Back", callback_data="sched:refresh")],
            ]

            await query.edit_message_text(
                f"{schedule.type_emoji} <b>{schedule.name}</b>\n\n"
                f"Status: <b>{status_text}</b>\n"
                f"Time: <b>{schedule.time_str}</b> (daily)\n"
                f"AI: <b>{get_provider_label(schedule.ai_provider)}</b>\n"
                f"Model: <b>{get_profile_label(schedule.ai_provider, schedule.model)}</b> "
                f"(<code>{schedule.model}</code>){path_info}\n"
                f"Message: <i>{schedule.message[:80]}{'...' if len(schedule.message) > 80 else ''}</i>\n"
                f"Runs: {schedule.run_count}",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Change time - hour selection
        if action.startswith("chtime:"):
            schedule_id = action[7:]
            schedule = self._schedule_manager.get(schedule_id)
            if not schedule:
                await query.answer("Schedule not found")
                return

            buttons = []
            row = []
            for hour in AVAILABLE_HOURS:
                row.append(InlineKeyboardButton(
                    f"{hour:02d}h",
                    callback_data=f"sched:chtime_hour:{schedule_id}:{hour}"
                ))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton("Cancel", callback_data="sched:refresh")
            ])

            await query.edit_message_text(
                f"<b>Change Time</b>\n\n"
                f"{schedule.type_emoji} <b>{schedule.name}</b>\n"
                f"Current: <b>{schedule.time_str}</b>\n\n"
                f"Select new hour:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Change time - minute selection
        if action.startswith("chtime_hour:"):
            parts = action[12:].split(":")
            schedule_id, hour = parts[0], int(parts[1])

            buttons = []
            row = []
            for minute in range(0, 60, 5):
                row.append(InlineKeyboardButton(
                    f":{minute:02d}",
                    callback_data=f"sched:chtime_min:{schedule_id}:{hour}:{minute}"
                ))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton("Cancel", callback_data="sched:refresh")
            ])

            await query.edit_message_text(
                f"<b>Change Time</b>\n\n"
                f"New hour: <b>{hour:02d}h</b>\n\n"
                f"Select minute:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Change time - apply
        if action.startswith("chtime_min:"):
            from src.scheduler_manager import scheduler_manager

            parts = action[11:].split(":")
            schedule_id, hour, minute = parts[0], int(parts[1]), int(parts[2])

            result = self._schedule_manager.update_time(schedule_id, hour, minute)
            if result:
                await query.answer(f"Changed to {hour:02d}:{minute:02d}")
            else:
                await query.answer("Update failed")

            provider = self._get_selected_ai_provider(user_id)
            text = (
                f"<b>Scheduler</b>\n"
                f"Current AI: <b>{get_provider_label(provider)}</b>\n\n"
                f"{self._schedule_manager.get_status_text(user_id)}"
            )
            text += scheduler_manager.get_system_jobs_text()
            keyboard = self._build_scheduler_keyboard(user_id)
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        # Add - current AI type (time selection)
        if action in ("add:ai", "add:claude"):
            provider = self._get_selected_ai_provider(user_id)
            buttons = []
            row = []
            for hour in AVAILABLE_HOURS:
                row.append(InlineKeyboardButton(
                    f"{hour:02d}h",
                    callback_data=f"sched:time:claude:_:{hour}"
                ))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton("Cancel", callback_data="sched:refresh")
            ])

            self._sched_pending[user_id] = {
                "type": "claude",
                "ai_provider": provider,
            }

            await query.edit_message_text(
                f"<b>Add {get_provider_label(provider)} Schedule</b>\n\n"
                f"Regular {get_provider_label(provider)} conversation (new session)\n\n"
                "Select time (daily repeat):",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Add - Workspace type (path selection)
        if action == "add:workspace":
            if not self._workspace_registry:
                await query.answer("Workspace feature not initialized.")
                return

            workspaces = self._workspace_registry.list_by_user(user_id)
            if not workspaces:
                await query.edit_message_text(
                    "<b>No workspaces registered.</b>\n\n"
                    "Register one first at /workspace.",
                    parse_mode="HTML"
                )
                await query.answer()
                return

            buttons = []
            ws_map = {}
            for i, ws in enumerate(workspaces):
                ws_map[i] = {"path": ws.path, "name": ws.name}
                buttons.append([
                    InlineKeyboardButton(
                        f"{ws.name}",
                        callback_data=f"sched:wspath:{i}"
                    )
                ])

            self._sched_pending[user_id] = {
                "workspaces": ws_map,
                "ai_provider": self._get_selected_ai_provider(user_id),
            }

            buttons.append([
                InlineKeyboardButton("Cancel", callback_data="sched:refresh")
            ])

            await query.edit_message_text(
                "<b>Add Workspace Schedule</b>\n\n"
                "Select workspace:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Plugin schedule - show plugins with scheduled actions
        if action == "add:plugin":
            if not self.plugins or not self.plugins.plugins:
                await query.edit_message_text(
                    "<b>No plugins loaded.</b>",
                    parse_mode="HTML"
                )
                await query.answer()
                return

            buttons = []
            plugin_map = {}
            idx = 0
            for plugin in self.plugins.plugins:
                actions = plugin.get_scheduled_actions()
                if actions:
                    plugin_map[idx] = {"name": plugin.name, "actions": actions}
                    buttons.append([
                        InlineKeyboardButton(
                            f"🔌 {plugin.name} ({len(actions)} actions)",
                            callback_data=f"sched:plugin:{idx}"
                        )
                    ])
                    idx += 1

            if not buttons:
                await query.edit_message_text(
                    "<b>No schedulable plugins.</b>\n\n"
                    "Implement <code>get_scheduled_actions()</code> in your plugin.",
                    parse_mode="HTML"
                )
                await query.answer()
                return

            self._sched_pending[user_id] = {"plugin_map": plugin_map}
            buttons.append([
                InlineKeyboardButton("Cancel", callback_data="sched:refresh")
            ])

            await query.edit_message_text(
                "<b>Add Plugin Schedule</b>\n\n"
                "Select plugin:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Plugin selected - show actions
        if action.startswith("plugin:") and not action.startswith("pluginaction:"):
            plugin_idx = int(action[7:])
            pending = self._sched_pending.get(user_id, {})
            plugin_map = pending.get("plugin_map", {})
            plugin_info = plugin_map.get(plugin_idx)

            if not plugin_info:
                await query.answer("Invalid plugin")
                return

            pending["selected_plugin"] = plugin_info["name"]
            self._sched_pending[user_id] = pending

            buttons = []
            for i, act in enumerate(plugin_info["actions"]):
                buttons.append([
                    InlineKeyboardButton(
                        f"{act.description}",
                        callback_data=f"sched:pluginaction:{i}"
                    )
                ])
            buttons.append([
                InlineKeyboardButton("Cancel", callback_data="sched:refresh")
            ])

            await query.edit_message_text(
                f"<b>🔌 {plugin_info['name']}</b>\n\n"
                f"Select action:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Plugin action selected - time selection
        if action.startswith("pluginaction:"):
            action_idx = int(action[13:])
            pending = self._sched_pending.get(user_id, {})
            plugin_name = pending.get("selected_plugin")
            plugin_map = pending.get("plugin_map", {})

            # Find the plugin's actions
            actions = []
            for info in plugin_map.values():
                if info["name"] == plugin_name:
                    actions = info["actions"]
                    break

            if action_idx >= len(actions):
                await query.answer("Invalid action")
                return

            selected_action = actions[action_idx]
            pending["type"] = "plugin"
            pending["plugin_name"] = plugin_name
            pending["action_name"] = selected_action.name
            pending["name"] = f"{plugin_name}:{selected_action.description}"
            self._sched_pending[user_id] = pending

            buttons = []
            row = []
            for hour in AVAILABLE_HOURS:
                row.append(InlineKeyboardButton(
                    f"{hour:02d}h",
                    callback_data=f"sched:time:plugin:_:{hour}"
                ))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton("Cancel", callback_data="sched:refresh")
            ])

            await query.edit_message_text(
                f"<b>Add Plugin Schedule</b>\n\n"
                f"🔌 <b>{plugin_name}</b> - {selected_action.description}\n\n"
                f"Select time (daily repeat):",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Workspace selected - time selection
        if action.startswith("wspath:"):
            ws_idx = int(action[7:])
            pending = self._sched_pending.get(user_id, {})
            ws_map = pending.get("workspaces", {})

            ws_info = ws_map.get(ws_idx)
            if not ws_info:
                await query.answer("Invalid workspace")
                return

            workspace_path = ws_info["path"]
            workspace_name = ws_info["name"]
            path_idx = ws_idx

            buttons = []
            row = []
            for hour in AVAILABLE_HOURS:
                row.append(InlineKeyboardButton(
                    f"{hour:02d}h",
                    callback_data=f"sched:time:workspace:{path_idx}:{hour}"
                ))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton("Cancel", callback_data="sched:refresh")
            ])

            await query.edit_message_text(
                f"<b>Add Workspace Schedule</b>\n\n"
                f"Workspace: <b>{workspace_name}</b>\n"
                f"<code>{workspace_path}</code>\n\n"
                f"Select time (daily repeat):",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Time (hour) selected - minute selection
        if action.startswith("time:"):
            parts = action[5:].split(":")
            if len(parts) != 3:
                await query.answer("Invalid request")
                return

            schedule_type, path_idx, hour = parts[0], parts[1], int(parts[2])

            pending = self._sched_pending.get(user_id, {})
            pending["type"] = schedule_type
            pending["hour"] = hour
            pending.setdefault("ai_provider", self._get_selected_ai_provider(user_id))

            if schedule_type == "workspace" and path_idx != "_":
                ws_map = pending.get("workspaces", {})
                idx = int(path_idx)
                ws_info = ws_map.get(idx)
                if ws_info:
                    pending["workspace_path"] = ws_info["path"]
                    pending["name"] = ws_info["name"]

            self._sched_pending[user_id] = pending

            # Minute selection buttons (00~55, 5-min intervals)
            buttons = []
            row = []
            for minute in range(0, 60, 5):
                row.append(InlineKeyboardButton(
                    f":{minute:02d}",
                    callback_data=f"sched:minute:{minute}"
                ))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton("Cancel", callback_data="sched:refresh")
            ])

            type_label = "Workspace" if schedule_type == "workspace" else f"{get_provider_label(pending['ai_provider'])} Schedule"
            path_info = f"\nPath: <code>{pending.get('workspace_path', '')}</code>" if schedule_type == "workspace" else ""

            await query.edit_message_text(
                f"<b>Add {type_label} Schedule</b>\n\n"
                f"Hour: <b>{hour:02d}h</b>{path_info}\n\n"
                f"Select minute:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Minute selected - model selection (or direct register for plugin)
        if action.startswith("minute:"):
            minute = int(action[7:])

            pending = self._sched_pending.get(user_id, {})
            pending["minute"] = minute
            self._sched_pending[user_id] = pending

            hour = pending.get("hour", 9)
            schedule_type = pending.get("type", "claude")
            ai_provider = pending.get("ai_provider", self._get_selected_ai_provider(user_id))

            # Plugin type: skip model/message, register directly
            if schedule_type == "plugin":
                if not self._schedule_manager:
                    await query.edit_message_text("Schedule feature disabled.")
                    del self._sched_pending[user_id]
                    return

                schedule = self._schedule_manager.add(
                    user_id=user_id,
                    chat_id=chat_id,
                    name=pending.get("name", "Plugin Schedule"),
                    hour=hour,
                    minute=minute,
                    message="",  # plugin doesn't need message
                    schedule_type="plugin",
                    ai_provider=ai_provider,
                    model="sonnet",  # unused for plugin
                    plugin_name=pending.get("plugin_name"),
                    action_name=pending.get("action_name"),
                )

                del self._sched_pending[user_id]

                keyboard = [[
                    InlineKeyboardButton("Schedule List", callback_data="sched:refresh"),
                ]]

                await query.edit_message_text(
                    f"<b>Plugin Schedule Registered!</b>\n\n"
                    f"🔌 <b>{schedule.name}</b>\n"
                    f"Time: <b>{schedule.time_str}</b> (daily)\n"
                    f"Plugin: <b>{schedule.plugin_name}</b>\n"
                    f"Action: <b>{schedule.action_name}</b>",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
                logger.info(f"Plugin schedule registered: {schedule.name} @ {schedule.time_str}")
                return

            buttons = [
                self._build_model_buttons(ai_provider, "sched:model:"),
                [InlineKeyboardButton("Cancel", callback_data="sched:refresh")],
            ]

            type_label = "Workspace" if schedule_type == "workspace" else f"{get_provider_label(ai_provider)} Schedule"
            path_info = f"\nPath: <code>{pending.get('workspace_path', '')}</code>" if schedule_type == "workspace" else ""

            await query.edit_message_text(
                f"<b>Add {type_label} Schedule</b>\n\n"
                f"Time: <b>{hour:02d}:{minute:02d}</b>{path_info}\n\n"
                f"Select model:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Model selected - message input (ForceReply)
        if action.startswith("model:"):
            model = action[6:]
            pending = self._sched_pending.get(user_id, {})
            ai_provider = pending.get("ai_provider", self._get_selected_ai_provider(user_id))
            if not is_supported_model(ai_provider, model):
                await query.edit_message_text("❌ Unsupported model for the selected AI.")
                return
            pending["model"] = model
            self._sched_pending[user_id] = pending

            schedule_type = pending.get("type", "claude")
            hour = pending.get("hour", 9)
            minute = pending.get("minute", 0)
            type_label = "Workspace" if schedule_type == "workspace" else f"{get_provider_label(ai_provider)} Schedule"
            path_info = f"\nPath: <code>{pending.get('workspace_path', '')}</code>" if schedule_type == "workspace" else ""

            await query.edit_message_text(
                f"<b>Add {type_label} Schedule</b>\n\n"
                f"Time: <b>{hour:02d}:{minute:02d}</b>\n"
                f"AI: <b>{get_provider_label(ai_provider)}</b>\n"
                f"Model: <b>{get_profile_label(ai_provider, model)}</b> (<code>{model}</code>){path_info}\n\n"
                f"Enter scheduled message below:",
                parse_mode="HTML"
            )

            await query.message.reply_text(
                "Enter scheduled message (schedule_input):",
                reply_markup=ForceReply(selective=True, input_field_placeholder="e.g., Summarize today's tasks")
            )
            await query.answer()
            return

        await query.answer("Unknown action")

    async def _handle_session_queue_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle session queue callbacks (new method).

        callback_data format (with pending_key):
        - sq:wait:{pending_key}:{session_id} - Wait in this session
        - sq:switch:{pending_key}:{session_id} - Switch to another session
        - sq:new:{pending_key}:{model} - Create new session
        - sq:cancel:{pending_key} - Cancel
        """
        user_id = str(query.from_user.id)
        parts = callback_data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        pending_key = parts[2] if len(parts) > 2 else ""

        # Look up pending data by key
        pending = self._temp_pending.get(pending_key) if pending_key else None
        if not pending or pending.get("user_id") != user_id:
            await query.edit_message_text(
                "<b>Request expired</b>\n\nPlease resend the message.",
                parse_mode="HTML"
            )
            return

        message = pending["message"]
        model = pending["model"]
        is_new_session = pending["is_new_session"]
        workspace_path = pending["workspace_path"]
        current_session_id = pending["current_session_id"]
        bot = query.get_bot()

        if action == "cancel":
            self._delete_temp_pending(pending_key)
            await query.edit_message_text("Request cancelled.")
            return

        if action == "wait":
            target_session_id = current_session_id
            session_prefix = parts[3] if len(parts) > 3 else ""
            if session_prefix:
                for s in self.sessions.list_sessions(user_id):
                    if s["full_session_id"].startswith(session_prefix):
                        target_session_id = s["full_session_id"]
                        break

            repo = self._repository
            if not repo:
                await query.edit_message_text("Queue unavailable.")
                return

            if not self._is_session_locked(target_session_id):
                self._delete_temp_pending(pending_key)
                try:
                    _, start_error = self._start_detached_job(
                        chat_id=chat_id,
                        session_id=target_session_id,
                        message=message,
                        model=model,
                        workspace_path=workspace_path,
                    )
                except Exception:
                    await query.edit_message_text("❌ Failed to start detached worker.")
                    return

                if not start_error:
                    await query.edit_message_text(
                        f"<b>Processing immediately</b>\n\n"
                        f"<code>{truncate_message(message, 40)}</code>",
                        parse_mode="HTML"
                    )
                    return

                logger.warning(f"Session locked during wait callback fallback: session={target_session_id[:8]}")

            repo.save_queued_message(
                session_id=target_session_id,
                user_id=user_id,
                chat_id=chat_id,
                message=message,
                model=model,
                is_new_session=is_new_session,
                workspace_path=workspace_path or "",
            )
            position = len(repo.get_queued_messages_by_session(target_session_id))

            session_info = self.sessions.get_session_info(target_session_id)
            model_badge = get_model_badge(model)

            self._delete_temp_pending(pending_key)
            await query.edit_message_text(
                f"<b>Added to queue</b>\n\n"
                f"<code>{truncate_message(message, 40)}</code>\n\n"
                f"Session: {model_badge} <b>{session_info}</b>\n"
                f"Position: #{position}\n"
                f"Will be processed automatically after current task completes.",
                parse_mode="HTML"
            )
            return

        if action == "switch":
            target_prefix = parts[3] if len(parts) > 3 else ""
            target_session = None
            for s in self.sessions.list_sessions(user_id):
                if s["full_session_id"].startswith(target_prefix):
                    target_session = s
                    break

            if not target_session:
                await query.edit_message_text("❌ Session not found.")
                return

            target_session_id = target_session["full_session_id"]
            target_model = target_session.get("model", "sonnet")

            if self._is_session_locked(target_session_id):
                self._delete_temp_pending(pending_key)
                await self._show_session_selection_ui(
                    update=None,
                    user_id=user_id,
                    message=message,
                    current_session_id=target_session_id,
                    model=target_model,
                    is_new_session=False,
                    workspace_path=target_session.get("workspace_path") or "",
                    bot=bot,
                    chat_id=chat_id,
                )
                await query.edit_message_text("Selected session became busy. Check the new prompt below.")
                return

            self.sessions.switch_session(user_id, target_session_id)

            self._delete_temp_pending(pending_key)
            try:
                _, start_error = self._start_detached_job(
                    chat_id=chat_id,
                    session_id=target_session_id,
                    message=message,
                    model=target_model,
                    workspace_path=target_session.get("workspace_path"),
                )
            except Exception:
                await query.edit_message_text("❌ Failed to start detached worker.")
                return

            if start_error == "session_locked":
                await query.edit_message_text("❌ Selected session became busy. Please retry.")
                return

            await query.edit_message_text(
                f"<b>Session switched</b>\n\n"
                f"<code>{truncate_message(message, 40)}</code>\n\n"
                f"Starting detached processing...",
                parse_mode="HTML"
            )
            return

        if action == "new":
            provider = self.sessions.get_session_ai_provider(current_session_id) or self._get_selected_ai_provider(user_id)
            new_model = parts[3] if len(parts) > 3 else get_default_model(provider)
            if not is_supported_model(provider, new_model):
                new_model = get_default_model(provider)

            self._delete_temp_pending(pending_key)
            await query.edit_message_text(
                f"<b>Creating new {get_profile_label(provider, new_model)} session...</b>\n\n"
                f"<code>{truncate_message(message, 40)}</code>",
                parse_mode="HTML"
            )

            new_session_id = self.sessions.create_session(
                user_id=user_id,
                ai_provider=provider,
                model=new_model,
                first_message="(new session)",
            )

            try:
                _, start_error = self._start_detached_job(
                    chat_id=chat_id,
                    session_id=new_session_id,
                    message=message,
                    model=new_model,
                    workspace_path=None,
                )
            except Exception:
                await query.message.reply_text("❌ Failed to start detached worker.")
                return

            if start_error == "session_locked":
                await query.message.reply_text("❌ New session became busy unexpectedly. Please resend the message.")

            return

        await query.edit_message_text("Unknown command.")
